"""Tests for live tool-generating detection (CLI-only tool_call_delta)."""
import unittest

from harness.core.agent import HarnessAgent, AgentEvent
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.tui.terminal import TerminalRenderer


class FakeProvider:
    name = "mock"
    display_name = "Fake"

    def __init__(self, chunks):
        self._chunks = chunks
        self.default_model = "mock-model"

    def get_model_spec(self, model_name=None):
        from harness.providers.detector import inspect_model
        return inspect_model(model_name or self.default_model, "mock")

    def stream_chat(self, messages=None, model=None, thinking_effort="high",
                    tools=None, system_prompt=None, **kwargs):
        if not hasattr(self, "_calls"):
            self._calls = 0
        self._calls += 1
        if self._calls == 1:
            for c in self._chunks:
                yield c
        else:
            yield LLMChunk(delta_text="done", finish_reason="stop")


def make_agent(chunks):
    from harness.config import HarnessConfig
    config = HarnessConfig()
    config.provider = "mock"
    config.learning_enabled = False
    agent = HarnessAgent(config=config)
    agent.provider = FakeProvider(chunks)
    # Silence skill seeding / compaction side effects for deterministic tests
    agent.skills_manager.list_skills = lambda: []
    agent.compactor.should_compact = lambda *a, **k: False
    return agent


class TestToolGeneratingDelta(unittest.TestCase):
    def test_delta_emitted_when_name_appears(self):
        chunks = [
            # First chunk: index known, no name yet (e.g. OpenAI id-only delta)
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="call_1", name=None, arguments_delta="")]),
            # Name arrives
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="call_1", name="view_file", arguments_delta="")]),
            # Args stream — must NOT re-emit delta
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="call_1", name=None, arguments_delta='{"path":')]),
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="call_1", name=None, arguments_delta='"a.py"}')]),
        ]
        agent = make_agent(chunks)
        # Stub tool execution so the turn completes
        agent.tool_registry.execute = lambda name, args, mode: "ok-contents"

        events = list(agent.step("read a file"))
        deltas = [e for e in events if e.type == "tool_call_delta"]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].data["name"], "view_file")
        self.assertEqual(deltas[0].data["index"], 0)
        # tool_call_start must still fire with full args
        starts = [e for e in events if e.type == "tool_call_start"]
        self.assertTrue(any(s.data["name"] == "view_file" for s in starts))

    def test_no_delta_without_name(self):
        chunks = [LLMChunk(delta_text="hello")]
        agent = make_agent(chunks)
        events = list(agent.step("hi"))
        self.assertFalse(any(e.type == "tool_call_delta" for e in events))

    def test_multiple_tools_each_announced_once(self):
        chunks = [
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="c0", name="view_file", arguments_delta="{}")]),
            LLMChunk(tool_calls=[ToolCallDelta(index=1, id="c1", name="list_dir", arguments_delta="{}")]),
        ]
        agent = make_agent(chunks)
        agent.tool_registry.execute = lambda name, args, mode: "ok"
        events = list(agent.step("do two things"))
        deltas = [e for e in events if e.type == "tool_call_delta"]
        self.assertEqual([d.data["name"] for d in deltas], ["view_file", "list_dir"])

    def test_renderer_shows_generating_tool_name(self):
        renderer = TerminalRenderer("minimal")
        outputs = []
        orig_print = renderer.console.print
        renderer.console.print = lambda *a, **k: outputs.append(str(a[0]) if a else "")
        try:
            renderer.render_agent_event(AgentEvent("tool_call_delta", {"index": 0, "id": "c0", "name": "view_file"}))
        finally:
            renderer.console.print = orig_print
        combined = " ".join(outputs)
        self.assertIn("view_file", combined)
        self.assertIn("Generating", combined)

    def test_renderer_dedupes_same_tool_id(self):
        renderer = TerminalRenderer("minimal")
        outputs = []
        orig_print = renderer.console.print
        renderer.console.print = lambda *a, **k: outputs.append(str(a[0]) if a else "")
        try:
            ev = AgentEvent("tool_call_delta", {"index": 0, "id": "c0", "name": "view_file"})
            renderer.render_agent_event(ev)
            renderer.render_agent_event(ev)  # duplicate must be swallowed
        finally:
            renderer.console.print = orig_print
        generating_lines = [o for o in outputs if "Generating" in o]
        self.assertEqual(len(generating_lines), 1)

    def test_renderer_clears_state_on_turn_capsule(self):
        renderer = TerminalRenderer("minimal")
        renderer._generating_announced.add("c0")
        renderer.render_turn_capsule("build", "m", 0.1)
        self.assertEqual(renderer._generating_announced, set())


if __name__ == "__main__":
    unittest.main()
