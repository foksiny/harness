"""
Anthropic Claude Provider for Harness.
Full support for Claude 3.7 Sonnet, Claude 3.5 Sonnet, Claude 3.5 Haiku,
dynamic thinking budget tokens, and native tool calling.
"""
import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Iterator
from harness.providers.base import BaseProvider, LLMChunk, ToolCallDelta

class AnthropicProvider(BaseProvider):
    name = "anthropic"
    display_name = "Anthropic Claude"
    default_model = "claude-3-7-sonnet"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key=api_key, base_url=base_url or "https://api.anthropic.com/v1")

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        anthropic_msgs = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue # Handled via top-level system parameter

            content = msg.get("content")
            if role == "user":
                anthropic_msgs.append({"role": "user", "content": content or ""})
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

        data_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")

        current_tool_id = None
        current_tool_name = None
        current_tool_idx = 0

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buffer = ""
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace")
                    buffer += line

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
                                        yield LLMChunk(delta_text=delta.get("text", ""))
                                    elif dtype == "thinking_delta":
                                        yield LLMChunk(delta_reasoning=delta.get("thinking", ""))
                                    elif dtype == "input_json_delta":
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
                                    yield LLMChunk(
                                        finish_reason=delta.get("stop_reason"),
                                        usage=ev.get("usage"),
                                    )

                            except Exception:
                                continue

        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            yield LLMChunk(delta_text=f"\n[HTTP Error {he.code} from Anthropic: {err_body}]\n", finish_reason="error")
        except urllib.error.URLError as ue:
            yield LLMChunk(delta_text=f"\n[Connection Error with Anthropic: {str(ue)}]\n", finish_reason="error")
        except Exception as ex:
            yield LLMChunk(delta_text=f"\n[Unexpected Error from Anthropic: {str(ex)}]\n", finish_reason="error")
