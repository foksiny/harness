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


class TestFinishDeliveryGate(unittest.TestCase):
    def test_undelivered_gui_text_rejects_finish_once(self):
        from harness.tools.base import Tool

        class _FailedType(Tool):
            name = "computer_type"
            description = "fake"
            action_type = "computer_input"
            is_read_only = False
            parameters = {"type": "object",
                          "properties": {"text": {"type": "string"}},
                          "required": []}

            def execute(self, text="", **kwargs):
                return ('Error: {"ok": false, "delivered": false, '
                        '"kind": "overview_open", "error": "app-search is open"}')

        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        finish_args = '{"summary": "message sent", "evidence": "checked", "verified": true}'
        script = [
            [LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_type_1", name="computer_type",
                arguments_delta='{"text": "hello"}')])],
            [LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_fin_1", name="finish",
                arguments_delta=finish_args)])],
            [LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_fin_2", name="finish",
                arguments_delta=finish_args)])],
        ]
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.provider = provider
        agent.tool_registry.tools["computer_type"] = _FailedType()

        events = list(agent.step("send the message"))

        self.assertTrue(any(
            ev.type == "text_delta" and "GUI text was not delivered" in str(ev.data)
            for ev in events))
        self.assertEqual(provider.call_count, 3)
        finish_results = [
            m for m in agent.session.messages
            if m.get("role") == "tool" and m.get("name") == "finish"
        ]
        self.assertTrue(any("did not complete" in str(m.get("content"))
                            for m in finish_results))
        self.assertTrue(any("message sent" in str(m.get("content"))
                            for m in finish_results))


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
        cfg.learning_enabled = False
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

    def test_empty_after_work_keeps_nudging_and_ends_on_final_text(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
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

        # Empty replies never auto-stop the turn: the model is re-prompted on
        # EVERY silent response and the turn concludes only once it produces
        # a final answer (or calls `finish`).
        nudges = [m for m in agent.session.messages if str(m.get("content", "")).startswith("[SYSTEM]: Your previous response was empty")]
        self.assertEqual(len(nudges), 3)
        self.assertIn("tool results above were delivered", nudges[0]["content"])
        # No "gave up"/"went quiet" recovery framing when tool work happened.
        self.assertFalse(any(ev.type == "text_delta" and "went quiet" in str(ev.data) for ev in events))
        self.assertFalse(any(
            ev.type == "text_delta" and "did not return a final message" in str(ev.data)
            for ev in events
        ))
        self.assertTrue(any(ev.type == "text_delta" and "done." in str(ev.data) for ev in events))
        self.assertTrue(any(ev.type == "step_end" and ev.data.get("complete") for ev in events))


class TestAntiLazinessGuard(unittest.TestCase):
    def test_build_prompt_contains_laziness_guard(self):
        from harness.core.prompt import SystemPromptBuilder
        from harness.core.modes import Mode
        prompt = SystemPromptBuilder(Mode.BUILD).build()
        self.assertIn("BUILD MODE", prompt)
        self.assertIn("Anti-laziness guard", prompt)
        self.assertIn("No stub/placeholder/TODO", prompt)
        self.assertIn("MUST run a verification step", prompt)
        self.assertIn("capped", prompt)

    def test_mutating_no_verification_finish_rejected(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        script = [
            [LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_write", name="write_file",
                arguments_delta='{"path": "tmp/x.txt", "content": "hello"}',
            )])],
            [LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_fin", name="finish",
                arguments_delta='{"summary": "done"}',
            )])],
            # After rejection, script runs out -> provider emits "done." stop.
        ]
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.provider = provider
        events = list(agent.step("make a file"))
        self.assertTrue(any(
            ev.type == "text_delta" and "verification evidence required" in str(ev.data)
            for ev in events))
        self.assertGreaterEqual(provider.call_count, 2)

    def test_exploration_early_nudge_fires_at_six(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        # Six consecutive read-only calls: list_dir each with distinct path so
        # the repeat-loop watchdog does not pre-empt the exploration watchdog.
        calls = []
        for i in range(6):
            calls.append([LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id=f"call_read_{i}", name="list_dir",
                arguments_delta=f'{{"path": "harness/core/agent_{i}.py"}}',
            )])])
        calls.append([LLMChunk(tool_calls=[ToolCallDelta(
            index=0, id="call_fin", name="finish",
            arguments_delta='{"summary": "did reads"}',
        )])])
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(calls)
        agent.provider = provider
        events = list(agent.step("look around"))
        self.assertTrue(any(
            ev.type == "text_delta" and "Exploration streak reached" in str(ev.data)
            for ev in events))
        # The hard 10-streak nudge must NOT have fired yet (early nudge is first).
        self.assertFalse(any(
            ev.type == "text_delta" and "Exploration loop detected" in str(ev.data)
            for ev in events))

if __name__ == "__main__":
    unittest.main()