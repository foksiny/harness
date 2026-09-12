"""
Offline Mock Provider for Harness.
Used for deterministic testing, unit test suites, and offline development.
"""
from typing import Dict, Any, List, Optional, Iterator
from harness.providers.base import BaseProvider, LLMChunk, ToolCallDelta
from harness.providers.detector import ModelSpec

class MockProvider(BaseProvider):
    name = "mock"
    display_name = "Offline Mock Engine"
    default_model = "mock-harness-model"

    def __init__(self, responses: Optional[List[LLMChunk]] = None):
        super().__init__(api_key="mock-key", base_url="http://localhost")
        self.preset_responses = responses or []
        self.call_history: List[Dict[str, Any]] = []

    def get_model_spec(self, model_name: Optional[str] = None) -> ModelSpec:
        m = model_name or self.default_model
        from harness.providers.detector import inspect_model
        return inspect_model(m, self.name)

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        self.call_history.append({
            "messages": list(messages),
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        })

        if self.preset_responses:
            for chunk in self.preset_responses:
                yield chunk
            return

        # Default simulated thinking and response
        yield LLMChunk(delta_reasoning="Analyzing the codebase context and requested objectives...")
        yield LLMChunk(delta_reasoning=" Verified parameters. Formulating solution.")

        last_msg = messages[-1] if messages else {}
        if last_msg.get("role") == "tool":
            yield LLMChunk(delta_text="Finished executing tool. Here are the results: " + str(last_msg.get("content", ""))[:80] + "...")
            yield LLMChunk(finish_reason="stop")
            return

        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break

        if "list" in last_user.lower() or "files" in last_user.lower():
            # Trigger list_dir tool call
            yield LLMChunk(
                tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_mock_1",
                    name="list_dir",
                    arguments_delta='{"path": "."}',
                )]
            )
        else:
            words = [
                "Harness ", "has ", "analyzed ", "your ", "request: ",
                f"'{last_user[:50]}...'. ",
                "System ", "is ", "operating ", "nominally ", "with ", "full ", "capabilities."
            ]
            for w in words:
                yield LLMChunk(delta_text=w)
            yield LLMChunk(finish_reason="stop")
