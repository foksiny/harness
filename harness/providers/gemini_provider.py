"""
Google Gemini Provider for Harness.
Supports Gemini 2.5 Pro, 2.5 Flash, 2.0 Flash, thinking budget, and function declarations.
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
)
from harness.core.attachments import b64_payload_for_block

class GeminiProvider(BaseProvider):
    name = "gemini"
    display_name = "Google Gemini"
    default_model = "gemini-2.5-pro"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0, stream_timeout: Optional[float] = None):
        super().__init__(api_key=api_key, base_url=base_url or "https://generativelanguage.googleapis.com/v1beta", max_retries=max_retries, base_delay=base_delay, stream_timeout=stream_timeout)

    def _user_parts(self, content):
        """Convert canonical content (str or block list) to Gemini parts."""
        if not isinstance(content, list):
            return [{"text": content}]
        parts = []
        for b in content:
            t = b.get("type")
            if t == "text":
                parts.append({"text": b.get("text", "")})
                continue
            mime, b64 = b64_payload_for_block(b)
            if mime and b64:
                parts.append({"inline_data": {"mime_type": mime, "data": b64}})
            else:
                parts.append({"text": f"[{t} file unavailable: {b.get('path', '')}]"})
        return parts

    def _convert_contents(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contents = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue # Handled via systemInstruction

            content = msg.get("content") or ""
            parts = []

            if role == "user":
                parts = self._user_parts(content)
                contents.append({"role": "user", "parts": parts})
            elif role == "assistant":
                if content:
                    parts.append({"text": content})
                if "tool_calls" in msg and msg["tool_calls"]:
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        parts.append({
                            "functionCall": {
                                "name": fn.get("name"),
                                "args": args,
                            }
                        })
                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                if isinstance(content, list):
                    # functionResponse carries text; attached media rides along
                    # as a synthetic follow-up user content with inline_data.
                    texts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    media = [b for b in content
                             if isinstance(b, dict) and b.get("type") in ("image", "video")]
                    parts.append({
                        "functionResponse": {
                            "name": msg.get("name", "tool"),
                            "response": {"output": "\n".join(t for t in texts if t) or ""},
                        }
                    })
                    contents.append({"role": "user", "parts": parts})
                    if media:
                        caption = f"[Image(s) attached by tool '{msg.get('name', 'tool')}' — see below.]"
                        contents.append({"role": "user", "parts": self._user_parts(
                            [{"type": "text", "text": caption}, *media])})
                else:
                    parts.append({
                        "functionResponse": {
                            "name": msg.get("name", "tool"),
                            "response": {"output": content},
                        }
                    })
                    contents.append({"role": "user", "parts": parts})
        return contents

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

        if not self.api_key:
            raise RuntimeError(
                "No API key for Google Gemini. Run:  harness keys set gemini <your-api-key>"
            )
        endpoint = f"{self.base_url.rstrip('/')}/models/{active_model}:streamGenerateContent?alt=sse&key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        body: Dict[str, Any] = {
            "contents": self._convert_contents(messages),
        }

        if system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        gen_config: Dict[str, Any] = {}
        thinking_param = self.normalize_thinking_effort(model_spec, thinking_effort)
        if thinking_param and "thinking_config" in thinking_param:
            cfg = thinking_param["thinking_config"]
            thinking_config: Dict[str, Any] = {
                "thinkingBudget": cfg.get("thinking_budget", 8192),
            }
            if cfg.get("include_thoughts"):
                thinking_config["includeThoughts"] = True
            gen_config["thinkingConfig"] = thinking_config
        if gen_config:
            body["generationConfig"] = gen_config

        if tools:
            body["tools"] = [{"functionDeclarations": tools}]

        abort_gen = getattr(self, "_abort_gen", 0)
        for attempt in range(self.max_retries + 1):
            # Abandoned by a user stop (abort_active_streams bumped the
            # generation): never open a NEW connection for a zombie pump.
            if getattr(self, "_abort_gen", 0) != abort_gen:
                return
            saw_payload = False
            try:
                # httpx streams each SSE line the moment its bytes arrive
                # with a generous read timeout so long thinking stalls
                # don't surface as read timeouts.
                with self._tracked_sse_stream(endpoint, headers, body) as stream:
                    buffer = ""
                    for line in stream:
                        buffer += line + "\n"

                        while "\n" in buffer:
                            line_str, buffer = buffer.split("\n", 1)
                            line_str = line_str.strip()

                            if not line_str or line_str.startswith(":"):
                                continue

                            if line_str.startswith("data: "):
                                data_str = line_str[6:].strip()
                                try:
                                    chunk = json.loads(data_str)
                                    candidates = chunk.get("candidates", [])
                                    if not candidates:
                                        continue
                                    cand = candidates[0]
                                    content_part = cand.get("content", {})
                                    parts = content_part.get("parts", [])

                                    for p in parts:
                                        if p.get("thought"):
                                            saw_payload = True
                                            yield LLMChunk(delta_reasoning=p.get("text", ""))
                                        elif "text" in p:
                                            saw_payload = True
                                            yield LLMChunk(delta_text=p["text"])
                                        elif "functionCall" in p:
                                            saw_payload = True
                                            fc = p["functionCall"]
                                            yield LLMChunk(
                                                tool_calls=[ToolCallDelta(
                                                    index=0,
                                                    id=f"gemini_call_{fc.get('name')}",
                                                    name=fc.get("name"),
                                                    arguments_delta=json.dumps(fc.get("args", {})),
                                                )]
                                            )

                                    finish = cand.get("finishReason")
                                    if finish:
                                        saw_payload = True
                                        yield LLMChunk(finish_reason=finish)

                                except Exception:
                                    continue
                    return
            except StreamHTTPError as he:
                err_body = he.body
                if not he.status_code or not self._is_retryable_http_code(he.status_code) or attempt >= self.max_retries:
                    yield LLMChunk(delta_text=f"\n[HTTP Error {he.status_code} from Gemini: {err_body}]\n", finish_reason="error")
                    return
                delay = self._retry_delay(attempt)
                try:
                    retry_after = he.retry_after()
                    if retry_after:
                        delay = max(delay, float(retry_after))
                except Exception:
                    pass
                time.sleep(delay)
                continue
            except Exception as ex:
                # Transport failure before any payload → retry here with
                # backoff. Mid-stream deaths are surfaced (not retried) so
                # the agent-level retry can discard the partial attempt
                # instead of concatenating a duplicate onto it.
                if is_transient_transport_error(ex) and not saw_payload and attempt < self.max_retries:
                    delay = self._retry_delay(attempt)
                    time.sleep(delay)
                    continue
                yield LLMChunk(delta_text=f"\n[Unexpected Error from Gemini: {str(ex)}]\n", finish_reason="error")
                return
        yield LLMChunk(delta_text=f"\n[Error from Gemini: exhausted {self.max_retries} retries]\n", finish_reason="error")
