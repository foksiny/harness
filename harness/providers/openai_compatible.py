"""
OpenAI-Compatible Streaming Provider for Harness.
Powers OpenAI, OpenRouter, NVIDIA NIM, OpenCode Zen, Groq, DeepSeek,
Mistral, xAI, Ollama, Together, Fireworks, and Perplexity.
"""
import json
import time
from typing import Dict, Any, List, Optional, Iterator
from harness.providers.base import (
    BaseProvider,
    LLMChunk,
    ToolCallDelta,
    StreamHTTPError,
    is_transient_transport_error,
    sse_post_stream,
)
from harness.core.attachments import data_url_for_block

DEFAULT_USER_AGENT = "Harness/1.0"


def _is_connection_error(ex: Exception) -> bool:
    """True for socket/transport-level failures (vs unexpected app errors)."""
    try:
        import httpx as _httpx
        if isinstance(ex, _httpx.TransportError):
            return True
    except Exception:
        pass
    return isinstance(ex, (TimeoutError, ConnectionError))


def _is_transient_exception(ex: Exception) -> bool:
    """True for connection-level failures that a fresh request can fix
    (socket read timeouts, resets, aborts). Auth/validation errors are not."""
    return is_transient_transport_error(ex)

def _openai_content_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map canonical attachment blocks to OpenAI-style multimodal content."""
    out = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b.get("text", "")})
            continue
        url = data_url_for_block(b)
        if not url:
            out.append({"type": "text", "text": f"[{t} file unavailable: {b.get('path', '')}]"})
            continue
        if t == "image":
            out.append({"type": "image_url", "image_url": {"url": url}})
        elif t == "video":
            # NVIDIA NIM / OpenAI-compatible visions accept inline video this way.
            out.append({"type": "input_video", "video": url})
        else:
            out.append({"type": "text", "text": f"[{t} file unavailable: {b.get('path', '')}]"})
    return out

class ThinkTagParser:
    """Parses embedded <think>...</think> tags from text content streams."""

    def __init__(self):
        self.in_think: bool = False
        self.tag_buf: str = ""

    def process(self, chunk: str) -> tuple[str, str]:
        """Process a text chunk and return (text_delta, reasoning_delta)."""
        if not chunk:
            return "", ""

        data = self.tag_buf + chunk
        self.tag_buf = ""

        text_out = []
        reasoning_out = []

        while data:
            if not self.in_think:
                if "<think>" in data:
                    before, after = data.split("<think>", 1)
                    if before:
                        text_out.append(before)
                    self.in_think = True
                    data = after
                elif any("<think>"[:i] == data[-i:] for i in range(1, 7)):
                    for i in range(min(6, len(data)), 0, -1):
                        if "<think>"[:i] == data[-i:]:
                            self.tag_buf = data[-i:]
                            text_out.append(data[:-i])
                            data = ""
                            break
                else:
                    text_out.append(data)
                    data = ""
            else:
                if "</think>" in data:
                    thought, after = data.split("</think>", 1)
                    if thought:
                        reasoning_out.append(thought)
                    self.in_think = False
                    data = after
                elif any("</think>"[:i] == data[-i:] for i in range(1, 8)):
                    for i in range(min(7, len(data)), 0, -1):
                        if "</think>"[:i] == data[-i:]:
                            self.tag_buf = data[-i:]
                            reasoning_out.append(data[:-i])
                            data = ""
                            break
                else:
                    reasoning_out.append(data)
                    data = ""

        return "".join(text_out), "".join(reasoning_out)

    def flush(self) -> tuple[str, str]:
        """Flush any residual buffer at stream completion."""
        res = self.tag_buf
        self.tag_buf = ""
        if not res:
            return "", ""
        if self.in_think:
            return "", res
        return res, ""

class OpenAICompatibleProvider(BaseProvider):
    """Generic OpenAI-compatible SSE streaming provider."""

    def __init__(
        self,
        name: str,
        display_name: str,
        default_model: str,
        base_url: str,
        api_key: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        max_retries: int = 3,
        base_delay: float = 5.0,
        stream_timeout: Optional[float] = None,
    ):
        super().__init__(api_key=api_key, base_url=base_url, max_retries=max_retries, base_delay=base_delay, stream_timeout=stream_timeout)
        self.name = name
        self.display_name = display_name
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.user_agent = user_agent or DEFAULT_USER_AGENT

    def _build_messages_payload(self, messages: List[Dict[str, Any]], system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = []
        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            item: Dict[str, Any] = {"role": role}
            if isinstance(content, list):
                if role == "tool":
                    # Tool messages must carry a string for the OpenAI API: keep
                    # the text inline and ship any attached media as a synthetic
                    # follow-up user message with the same image blocks.
                    texts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    media = [b for b in content
                             if isinstance(b, dict) and b.get("type") in ("image", "video")]
                    item["content"] = "\n".join(t for t in texts if t) or ""
                else:
                    item["content"] = _openai_content_blocks(content)
                    media = []
            else:
                item["content"] = content or ""
                media = []
            if "tool_calls" in msg and msg["tool_calls"]:
                item["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg and msg["tool_call_id"]:
                item["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg and msg["name"]:
                item["name"] = msg["name"]
            payload.append(item)
        return payload

    @staticmethod
    def _is_unsupported_param_error(text: str) -> bool:
        """Return True when the provider complains about an unsupported thinking/reasoning param."""
        low = (text or "").lower()
        if "unsupported parameter" not in low and "unsupported" not in low:
            return False
        # Check that the complained param is one of the thinking/reasoning keys we might have sent.
        # Be permissive: if the message says unsupported at all while we sent thinking params, retry.
        return any(k in low for k in ("reason", "thinking", "chat_template"))

    def _strip_thinking_params(self, body: Dict[str, Any]) -> None:
        for k in ("reasoning", "reasoning_effort", "thinking", "thinking_config", "chat_template_kwargs"):
            body.pop(k, None)

    def _is_retryable_error_message(self, msg: str, code: Any = None) -> bool:
        low = (msg or "").lower()
        # Check explicit retryable phrases
        if any(x in low for x in ("rate limit", "too many requests", "overloaded", "try again", "timeout", "temporarily unavailable", "service unavailable", "bad gateway", "gateway timeout", "internal server error")):
            return True
        # Check code
        try:
            c = int(str(code).strip().split()[0]) if code else None
            if c and self._is_retryable_http_code(c):
                return True
        except Exception:
            pass
        return False

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        active_model = model or self.default_model
        model_spec = self.get_model_spec(active_model)
        think_parser = ThinkTagParser()

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"

        if not self.api_key and self.name != "ollama":
            raise RuntimeError(
                f"No API key for {self.display_name}. "
                f"Run:  harness keys set {self.name} <your-api-key>"
            )

        headers = {
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        body: Dict[str, Any] = {
            "model": active_model,
            "messages": self._build_messages_payload(messages, system_prompt),
            "stream": True,
        }

        # Dynamic reasoning/thinking effort
        thinking_params = self.normalize_thinking_effort(model_spec, thinking_effort)
        body.update(thinking_params)

        if tools and model_spec.supports_tools:
            body["tools"] = tools

        # ── Retry handling: unsupported param (immediate) + exponential backoff for transient errors ──
        # Configurable retries: default 3 => waits 5s, 10s, 20s (base * 2^attempt)
        max_retries = getattr(self, "max_retries", 3)
        base_delay = getattr(self, "base_delay", 5.0)
        # Track if we already stripped thinking params (only once)
        thinking_stripped = False
        # Total attempts = 1 initial + max_retries backoff retries + 1 extra for thinking strip if needed
        # We use a manual loop with attempt counter for backoff
        attempt = 0
        while True:
            # Reset per attempt: whether any usable SSE content was yielded yet.
            # Referenced by the exception handlers below — a request that dies
            # before the first payload is transient-retryable; mid-stream deaths
            # are surfaced to the agent, which retries the whole call.
            saw_payload = False
            try:
                # httpx streams each SSE line the moment its bytes arrive
                # (no stdlib buffering delay) with a generous read timeout so
                # long reasoning stalls don't surface as read timeouts.
                with sse_post_stream(endpoint, headers, body, self.stream_timeout) as stream:
                    buffer = ""
                    body_lines = []
                    sse_error_msg: Optional[str] = None
                    sse_retryable = False
                    for line in stream:
                        body_lines.append(line + "\n")
                        buffer += line + "\n"

                        while "\n" in buffer:
                            line_str, buffer = buffer.split("\n", 1)
                            line_str = line_str.strip()

                            if not line_str or line_str.startswith(":"):
                                continue

                            if line_str.startswith("data: "):
                                data_str = line_str[6:].strip()
                                if data_str == "[DONE]":
                                    rem_text, rem_reasoning = think_parser.flush()
                                    if rem_text or rem_reasoning:
                                        saw_payload = True
                                        yield LLMChunk(delta_text=rem_text, delta_reasoning=rem_reasoning)
                                    yield LLMChunk(finish_reason="stop")
                                    return

                                try:
                                    chunk_json = json.loads(data_str)
                                except Exception:
                                    saw_payload = True
                                    yield LLMChunk(delta_text=f"\n[Error from {self.display_name}: {data_str[:500]}]\n", finish_reason="error")
                                    return

                                if isinstance(chunk_json, dict) and "error" in chunk_json:
                                    err = chunk_json["error"]
                                    if isinstance(err, dict):
                                        err_msg = err.get("message") or err.get("type") or str(err)
                                        err_code = err.get("code") or err.get("status") or ""
                                    else:
                                        err_msg = str(err)
                                        err_code = ""
                                    # Check for unsupported param (immediate, no backoff)
                                    if not saw_payload and not thinking_stripped and thinking_params and self._is_unsupported_param_error(err_msg):
                                        sse_error_msg = err_msg
                                        sse_retryable = False
                                        buffer = ""
                                        break
                                    # Check for retryable error in SSE payload (e.g. overloaded)
                                    if not saw_payload and self._is_retryable_error_message(err_msg, err_code) and attempt < max_retries:
                                        sse_error_msg = err_msg
                                        sse_retryable = True
                                        buffer = ""
                                        break
                                    saw_payload = True
                                    yield LLMChunk(delta_text=f"\n[Error from {self.display_name}: {err_msg}]\n", finish_reason="error")
                                    return

                                choices = chunk_json.get("choices", [])
                                if not choices:
                                    continue
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                finish = choice.get("finish_reason")

                                raw_text = delta.get("content") or ""
                                raw_reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""

                                if raw_reasoning:
                                    reasoning_delta = raw_reasoning
                                    text_delta = raw_text
                                elif raw_text:
                                    text_delta, reasoning_delta = think_parser.process(raw_text)
                                else:
                                    text_delta = ""
                                    reasoning_delta = ""

                                tool_calls = []
                                if isinstance(delta.get("tool_calls"), list):
                                    for tc in delta["tool_calls"]:
                                        fn = tc.get("function", {})
                                        tool_calls.append(ToolCallDelta(
                                            index=tc.get("index", 0),
                                            id=tc.get("id"),
                                            name=fn.get("name"),
                                            arguments_delta=fn.get("arguments", ""),
                                        ))

                                if text_delta or reasoning_delta or tool_calls or finish:
                                    saw_payload = True
                                    yield LLMChunk(
                                        delta_text=text_delta,
                                        delta_reasoning=reasoning_delta,
                                        tool_calls=tool_calls,
                                        finish_reason=finish,
                                        usage=chunk_json.get("usage"),
                                    )
                        if sse_error_msg is not None:
                            break

                    if sse_error_msg is not None:
                        if not sse_retryable:
                            # Unsupported param → strip and retry immediately
                            # (body dict is passed fresh to the next attempt)
                            self._strip_thinking_params(body)
                            thinking_params = {}
                            thinking_stripped = True
                            # Do not increment backoff attempt for this, retry immediately
                            continue
                        else:
                            # Retryable SSE error → backoff
                            if attempt < max_retries:
                                delay = self._retry_delay(attempt)
                                time.sleep(delay)
                                attempt += 1
                                continue
                            else:
                                yield LLMChunk(delta_text=f"\n[Error from {self.display_name}: {sse_error_msg}]\n", finish_reason="error")
                                return

                    # Stream ended without any usable SSE content — surface the raw body as the error.
                    if not saw_payload:
                        body_text = "".join(body_lines).strip()[:1000]
                        if body_text:
                            try:
                                body_json = json.loads(body_text)
                                err_obj = body_json.get("error") if isinstance(body_json, dict) else None
                                if isinstance(err_obj, dict):
                                    err_msg = err_obj.get("message") or err_obj.get("type") or str(err_obj)
                                    err_code = err_obj.get("code") or ""
                                elif isinstance(err_obj, str):
                                    err_msg = err_obj
                                    err_code = ""
                                elif isinstance(body_json, dict):
                                    err_msg = body_json.get("message") or body_json.get("type") or str(body_json)
                                    err_code = ""
                                else:
                                    err_msg = body_text
                                    err_code = ""
                            except Exception:
                                err_msg = body_text
                                err_code = ""
                            # Check unsupported param first (no backoff)
                            if not thinking_stripped and thinking_params and self._is_unsupported_param_error(err_msg):
                                self._strip_thinking_params(body)
                                thinking_params = {}
                                thinking_stripped = True
                                continue
                            if self._is_retryable_error_message(err_msg, err_code) and attempt < max_retries:
                                delay = self._retry_delay(attempt)
                                time.sleep(delay)
                                attempt += 1
                                continue
                            yield LLMChunk(delta_text=f"\n[Error from {self.display_name}: {err_msg}]\n", finish_reason="error")
                        else:
                            yield LLMChunk(delta_text=f"\n[Error from {self.display_name}: stream ended without a response]\n", finish_reason="error")
                    return

            except StreamHTTPError as he:
                err_body = he.body
                # Unsupported param → immediate retry (no backoff, doesn't count towards max_retries)
                if not thinking_stripped and thinking_params and self._is_unsupported_param_error(err_body):
                    self._strip_thinking_params(body)
                    thinking_params = {}
                    thinking_stripped = True
                    continue
                # Retryable HTTP codes → exponential backoff
                if self._is_retryable_http_code(he.status_code) and attempt < max_retries:
                    # Respect Retry-After header if present
                    delay = self._retry_delay(attempt)
                    try:
                        retry_after = he.retry_after()
                        if retry_after:
                            delay = max(delay, float(retry_after))
                    except Exception:
                        pass
                    time.sleep(delay)
                    attempt += 1
                    continue
                yield LLMChunk(delta_text=f"\n[HTTP Error {he.status_code} from {self.display_name}: {err_body}]\n", finish_reason="error")
                return
            except Exception as ex:
                # Transient connection failure (read timeout, reset, abort)
                # BEFORE any payload was yielded → retry with backoff here.
                # Mid-stream deaths are NOT retried here (deltas were already
                # yielded; the agent-level retry handles those safely by
                # discarding the partial attempt).
                if not saw_payload and _is_transient_exception(ex) and attempt < max_retries:
                    delay = self._retry_delay(attempt)
                    time.sleep(delay)
                    attempt += 1
                    continue
                if _is_connection_error(ex):
                    yield LLMChunk(delta_text=f"\n[Connection Error with {self.display_name}: {str(ex)}]\n", finish_reason="error")
                else:
                    yield LLMChunk(delta_text=f"\n[Unexpected Error from {self.display_name}: {str(ex)}]\n", finish_reason="error")
                return
