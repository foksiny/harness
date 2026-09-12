"""
OpenAI-Compatible Streaming Provider for Harness.
Powers OpenAI, OpenRouter, NVIDIA NIM, OpenCode Zen/Go, Groq, DeepSeek,
Mistral, xAI, Ollama, Together, Fireworks, and Perplexity.
"""
import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Iterator
from harness.providers.base import BaseProvider, LLMChunk, ToolCallDelta

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
    ):
        super().__init__(api_key=api_key, base_url=base_url)
        self.name = name
        self.display_name = display_name
        self.default_model = default_model
        self.extra_headers = extra_headers or {}

    def _build_messages_payload(self, messages: List[Dict[str, Any]], system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = []
        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            item: Dict[str, Any] = {"role": role, "content": content or ""}
            if "tool_calls" in msg and msg["tool_calls"]:
                item["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg and msg["tool_call_id"]:
                item["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg and msg["name"]:
                item["name"] = msg["name"]
            payload.append(item)
        return payload

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
        headers = {
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

        data_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")

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
                            if data_str == "[DONE]":
                                rem_text, rem_reasoning = think_parser.flush()
                                if rem_text or rem_reasoning:
                                    yield LLMChunk(delta_text=rem_text, delta_reasoning=rem_reasoning)
                                yield LLMChunk(finish_reason="stop")
                                return

                            try:
                                chunk_json = json.loads(data_str)
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
                                if "tool_calls" in delta:
                                    for tc in delta["tool_calls"]:
                                        fn = tc.get("function", {})
                                        tool_calls.append(ToolCallDelta(
                                            index=tc.get("index", 0),
                                            id=tc.get("id"),
                                            name=fn.get("name"),
                                            arguments_delta=fn.get("arguments", ""),
                                        ))

                                if text_delta or reasoning_delta or tool_calls or finish:
                                    yield LLMChunk(
                                        delta_text=text_delta,
                                        delta_reasoning=reasoning_delta,
                                        tool_calls=tool_calls,
                                        finish_reason=finish,
                                        usage=chunk_json.get("usage"),
                                    )
                            except Exception:
                                continue

        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            yield LLMChunk(delta_text=f"\n[HTTP Error {he.code} from {self.display_name}: {err_body}]\n", finish_reason="error")
        except urllib.error.URLError as ue:
            yield LLMChunk(delta_text=f"\n[Connection Error with {self.display_name}: {str(ue)}]\n", finish_reason="error")
        except Exception as ex:
            yield LLMChunk(delta_text=f"\n[Unexpected Error from {self.display_name}: {str(ex)}]\n", finish_reason="error")
