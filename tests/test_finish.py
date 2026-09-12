"""
Tests for the explicit `finish` stop tool and graceful iteration stopping.
"""
import unittest

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode, is_tool_allowed_in_mode
from harness.tools import ToolRegistry
from harness.tools.finish import FinishTool
from harness.providers.mock_provider import MockProvider
from harness.providers.base import LLMChunk, ToolCallDelta


class ScriptedProvider(MockProvider):
    """Mock provider that replays a per-call script, then emits a plain stop reply."""

    def __init__(self, script):
        super().__init__(responses=[])
        self.script = list(script)
        self.call_count = 0

    def stream_chat(self, messages, model=None, thinking_effort="high", tools=None, system_prompt=None, **kwargs):
        self.call_history.append({
            "messages": list(messages),
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        })
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.script):
            for chunk in self.script[idx]:
                yield chunk
        else:
            yield LLMChunk(delta_text="done.", finish_reason="stop")


class TestFinishTool(unittest.TestCase):

    def test_finish_tool_is_registered_and_allowed(self):
        registry = ToolRegistry()
        self.assertIsInstance(registry.get("finish"), FinishTool)
        self.assertTrue(is_tool_allowed_in_mode("finish", Mode.PLAN))
        self.assertTrue(is_tool_allowed_in_mode("finish", Mode.BUILD))
        self.assertTrue(is_tool_allowed_in_mode("finish", Mode.SUPER))
        schemas = [t for t in registry.get_openai_schemas(Mode.BUILD)]
        finish_schema = next(s for s in schemas if s["function"]["name"] == "finish")
        self.assertEqual(finish_schema["function"]["parameters"]["required"], ["summary"])

    def test_finish_tool_returns_summary(self):
        tool = FinishTool()
        self.assertEqual(tool.execute(summary="Everything is done."), "Everything is done.")
        self.assertEqual(tool.execute(), "Task complete.")

    def test_finish_stops_iteration_and_delivers_summary(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        script = [[
            LLMChunk(tool_calls=[ToolCallDelta(
                index=0,
                id="call_fin_1",
                name="finish",
                arguments_delta='{"summary": "All done. Tests passed."}',
            )]),
        ]]
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.provider = provider

        events = list(agent.step("implement and verify the feature"))

        # The loop must stop after `finish` — exactly one provider request.
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(any(ev.type == "text_delta" and ev.data == "All done. Tests passed." for ev in events))
        self.assertTrue(any(ev.type == "step_end" and ev.data.get("complete") for ev in events))
        self.assertTrue(any(ev.type == "turn_complete" for ev in events))

        # The finish call and its tool result must be persisted in history.
        finish_calls = [
            m for m in agent.session.messages
            if m.get("role") == "assistant" and any(tc["function"]["name"] == "finish" for tc in m.get("tool_calls") or [])
        ]
        self.assertEqual(len(finish_calls), 1)
        finish_results = [m for m in agent.session.messages if m.get("role") == "tool" and m.get("name") == "finish"]
        self.assertEqual(len(finish_results), 1)
        self.assertEqual(finish_results[0]["content"], "All done. Tests passed.")

    def test_empty_after_work_recovers_final_text_gracefully(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        script = [
            [
                LLMChunk(delta_text="Progress note: implementation complete.", finish_reason="stop"),
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_grep_1",
                    name="grep_search",
                    arguments_delta='{"pattern": "def main", "path": "."}',
                )]),
            ],
            [LLMChunk()],
            [LLMChunk()],
            [LLMChunk()],
        ]
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.provider = provider

        events = list(agent.step("do the work"))

        nudges = [m for m in agent.session.messages if str(m.get("content", "")).startswith("[SYSTEM]: Your previous response was empty")]
        self.assertEqual(len(nudges), 2)
        # The recovered final text is surfaced instead of a raw error.
        self.assertTrue(any(ev.type == "text_delta" and "Progress note" in str(ev.data) for ev in events))


if __name__ == "__main__":
    unittest.main()