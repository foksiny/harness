"""
Anthropic Claude Provider for Harness.
Full support for Claude 3.7 Sonnet, Claude 3.5 Sonnet, Claude 3.5 Haiku,
dynamic thinking budget tokens, and native tool calling.
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

class AnthropicProvider(BaseProvider):
    name = "anthropic"
    display_name = "Anthropic Claude"
    default_model = "claude-3-7-sonnet"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0, stream_timeout: Optional[float] = None):
        super().__init__(api_key=api_key, base_url=base_url or "https://api.anthropic.com/v1", max_retries=max_retries, base_delay=base_delay, stream_timeout=stream_timeout)

    @staticmethod
    def _anthropic_user_content(content):
        """Convert canonical content (str or block list) to Anthropic blocks."""
        if not isinstance(content, list):
            return content or ""
        blocks = []
        for b in content:
            t = b.get("type")
            if t == "text":
                blocks.append({"type": "text", "text": b.get("text", "")})
                continue
            mime, b64 = b64_payload_for_block(b)
            if mime and b64:
                blocks.append({
                    "type": t,
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                })
            else:
                blocks.append({"type": "text", "text": f"[{t} file unavailable: {b.get('path', '')}]"})
        return blocks

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        anthropic_msgs = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue # Handled via top-level system parameter

            content = msg.get("content")
            if role == "user":
                anthropic_msgs.append({"role": "user", "content": self._anthropic_user_content(content)})
            elif role == "assistant":
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                if "tool_calls" in msg and msg["tool_calls"]:
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", "tool_call_0"),
                            "name": fn.get("name"),
                            "input": args,
                        })
                anthropic_msgs.append({"role": "assistant", "content": blocks or ""})
            elif role == "tool":
                content = msg.get("content")
                if isinstance(content, list):
                    # Multimodal tool result (e.g. browser_screenshot): text and
                    # image blocks are legal inside an Anthropic tool_result.
                    tr_blocks = []
                    for b in content:
                        if not isinstance(b, dict):
                            tr_blocks.append({"type": "text", "text": str(b)})
                            continue
                        t = b.get("type")
                        if t == "text":
                            tr_blocks.append({"type": "text", "text": b.get("text", "")})
                            continue
                        mime, b64 = b64_payload_for_block(b)
                        if t == "image" and mime and b64:
                            tr_blocks.append({
                                "type": "image",
                                "source": {"type": "base64", "media_type": mime, "data": b64},
                            })
                        else:
                            tr_blocks.append({"type": "text", "text": f"[{t} file unavailable: {b.get('path', '')}]"})
                    anthropic_msgs.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": tr_blocks or "",
                        }]
                    })
                else:
                    anthropic_msgs.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": content or "",
                        }]
                    })
        return anthropic_msgs

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

        endpoint = f"{self.base_url.rstrip('/')}/messages"
        if not self.api_key:
            raise RuntimeError(
                f"No API key for Anthropic. Run:  harness keys set anthropic <your-api-key>"
            )
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }

        body: Dict[str, Any] = {
            "model": active_model,
            "messages": self._convert_messages(messages),
            "max_tokens": model_spec.max_output_tokens,
            "stream": True,
        }

        if system_prompt:
            body["system"] = system_prompt

        # Dynamic thinking
        thinking_param = self.normalize_thinking_effort(model_spec, thinking_effort)
        if thinking_param and "thinking" in thinking_param:
            body["thinking"] = thinking_param["thinking"]
            # Ensure max_tokens > budget_tokens
            budget = thinking_param["thinking"]["budget_tokens"]
            if body["max_tokens"] <= budget:
                body["max_tokens"] = budget + 4096

        if tools and model_spec.supports_tools:
            body["tools"] = tools

        current_tool_id = None
        current_tool_name = None
        current_tool_idx = 0
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
                                    ev = json.loads(data_str)
                                    ev_type = ev.get("type")

                                    if ev_type == "content_block_start":
                                        block = ev.get("content_block", {})
                                        if block.get("type") == "tool_use":
                                            current_tool_id = block.get("id")
                                            current_tool_name = block.get("name")
                                            current_tool_idx = ev.get("index", 0)

                                    elif ev_type == "content_block_delta":
                                        delta = ev.get("delta", {})
                                        dtype = delta.get("type")

                                        if dtype == "text_delta":
                                            saw_payload = True
                                            yield LLMChunk(delta_text=delta.get("text", ""))
                                        elif dtype == "thinking_delta":
                                            saw_payload = True
                                            yield LLMChunk(delta_reasoning=delta.get("thinking", ""))
                                        elif dtype == "input_json_delta":
                                            saw_payload = True
                                            yield LLMChunk(
                                                tool_calls=[ToolCallDelta(
                                                    index=current_tool_idx,
                                                    id=current_tool_id,
                                                    name=current_tool_name,
                                                    arguments_delta=delta.get("partial_json", ""),
                                                )]
                                            )

                                    elif ev_type == "message_delta":
                                        delta = ev.get("delta", {})
                                        saw_payload = True
                                        yield LLMChunk(
                                            finish_reason=delta.get("stop_reason"),
                                            usage=ev.get("usage"),
                                        )

                                except Exception:
                                    continue
                    return
            except StreamHTTPError as he:
                err_body = he.body
                if not he.status_code or not self._is_retryable_http_code(he.status_code) or attempt >= self.max_retries:
                    yield LLMChunk(delta_text=f"\n[HTTP Error {he.status_code} from Anthropic: {err_body}]\n", finish_reason="error")
                    return
                # Retryable — exponential backoff
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
                yield LLMChunk(delta_text=f"\n[Unexpected Error from Anthropic: {str(ex)}]\n", finish_reason="error")
                return
        # Exhausted retries (should not reach here, but fallback)
        yield LLMChunk(delta_text=f"\n[Error from Anthropic: exhausted {self.max_retries} retries]\n", finish_reason="error")
