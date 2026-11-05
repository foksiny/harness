"""Tests for TUI/UX upgrades: cached hot paths, /copy /undo /redo,
session auto-titles, and the agent repeat-loop watchdog.
"""
import unittest
from unittest.mock import patch

from harness.commands.registry import CommandRegistry
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.providers.mock_provider import MockProvider
from harness.tui import interactive as inter
from harness.tui.input_handler import (
    SLASH_COMMANDS,
    COMMAND_DESCRIPTIONS,
    SENTINEL_CLEAR,
)
from harness.tui.queue import QueuedItem


class FakeRenderer:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []
        self.successes = []

    def print_info(self, msg):
        self.infos.append(str(msg))

    def print_warning(self, msg):
        self.warnings.append(str(msg))

    def print_error(self, msg):
        self.errors.append(str(msg))

    def print_success(self, msg):
        self.successes.append(str(msg))


def _agent(**cfg_overrides):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.learning_enabled = False
    cfg.provider_retry_base_delay = 0.01
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    return agent


class ScriptedProvider(MockProvider):
    def __init__(self, scripts):
        super().__init__(responses=[])
        self.scripts = list(scripts)

    def stream_chat(self, messages, model=None, **kwargs):
        self.call_history.append({"messages": list(messages), "model": model})
        script = self.scripts.pop(0) if self.scripts else [LLMChunk(delta_text="done", finish_reason="stop")]
        if isinstance(script, Exception):
            raise script
        for chunk in script:
            yield chunk


def _tool_script(name, args_json, call_id="c1"):
    return [
        LLMChunk(tool_calls=[ToolCallDelta(index=0, id=call_id, name=name, arguments_delta=args_json)]),
        LLMChunk(finish_reason="stop"),
    ]


class TestHotPathCaches(unittest.TestCase):
    def test_git_branch_cached(self):
        from harness.tui.terminal import TerminalRenderer
        r = TerminalRenderer.__new__(TerminalRenderer)
        with patch("subprocess.check_output", return_value=b"main\n") as mock:
            self.assertEqual(r._get_git_branch(), "main")
            self.assertEqual(r._get_git_branch(), "main")
            self.assertEqual(mock.call_count, 1)

    def test_git_branch_failure_cached(self):
        from harness.tui.terminal import TerminalRenderer
        r = TerminalRenderer.__new__(TerminalRenderer)
        with patch("subprocess.check_output", side_effect=Exception("no git")) as mock:
            self.assertIsNone(r._get_git_branch())
            self.assertIsNone(r._get_git_branch())
            self.assertEqual(mock.call_count, 1)

    def test_cached_history_tokens(self):
        msgs = [{"role": "user", "content": "hello " * 100}] * 10
        inter._tokens_cache.update({"ts": 0.0, "key": None, "value": 0})
        first = inter.cached_history_tokens(msgs)
        self.assertGreater(first, 0)
        # Second call with same content hits the cache (same value, no recompute)
        with patch("harness.tui.interactive.calculate_history_tokens",
                   side_effect=AssertionError("should be cached")):
            self.assertEqual(inter.cached_history_tokens(msgs), first)
        # Different length recomputes
        self.assertNotEqual(inter.cached_history_tokens(msgs + [{"role": "user", "content": "x"}]), 0)


class TestNewCommands(unittest.TestCase):
    def test_registered(self):
        reg = CommandRegistry()
        for cmd in ("copy", "undo", "redo"):
            self.assertIn(cmd, reg.commands)
        for cmd in ("/copy", "/undo", "/redo"):
            self.assertIn(cmd, SLASH_COMMANDS)
            self.assertIn(cmd, COMMAND_DESCRIPTIONS)

    def test_copy_no_response(self):
        agent = _agent()
        reg = CommandRegistry()
        renderer = FakeRenderer()
        from harness.commands.registry import CommandContext
        reg.handle("/copy", agent, renderer)
        self.assertTrue(renderer.warnings)
        self.assertFalse(renderer.successes)

    def test_copy_last_response(self):
        agent = _agent()
        agent.session.messages.append({"role": "user", "content": "hi"})
        agent.session.messages.append({"role": "assistant", "content": "hello world"})
        reg = CommandRegistry()
        renderer = FakeRenderer()
        with patch.object(CommandRegistry, "_copy_to_clipboard", return_value=(True, "mock")):
            reg.handle("/copy", agent, renderer)
        self.assertTrue(renderer.successes)
        self.assertIn("11 chars", renderer.successes[0])

    def test_copy_to_clipboard_never_raises(self):
        ok, method = CommandRegistry._copy_to_clipboard("test")
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(method, str)

    def test_undo_redo_empty(self):
        agent = _agent()
        reg = CommandRegistry()
        renderer = FakeRenderer()
        reg.handle("/undo", agent, renderer)
        reg.handle("/redo", agent, renderer)
        # Nothing to undo/redo — warnings, no crash
        self.assertEqual(len(renderer.warnings), 2)


class TestSessionAutoTitle(unittest.TestCase):
    def test_title_from_first_prompt(self):
        prov = ScriptedProvider([[LLMChunk(delta_text="done", finish_reason="stop")]])
        agent = _agent()
        agent.provider = prov
        # Fresh session still has the default title
        agent.session.title = "New Session"
        events = list(agent.step("Build a shiny new widget for the dashboard"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        self.assertEqual(agent.session.title, "Build a shiny new widget for the dashboard")

    def test_long_title_truncated(self):
        prov = ScriptedProvider([[LLMChunk(delta_text="done", finish_reason="stop")]])
        agent = _agent()
        agent.provider = prov
        agent.session.title = "New Session"
        list(agent.step("x" * 100))
        self.assertEqual(len(agent.session.title), 61)  # 60 chars + ellipsis
        self.assertTrue(agent.session.title.endswith("…"))

    def test_existing_title_preserved(self):
        prov = ScriptedProvider([[LLMChunk(delta_text="done", finish_reason="stop")]])
        agent = _agent()
        agent.provider = prov
        agent.session.title = "My custom title"
        list(agent.step("Some other prompt"))
        self.assertEqual(agent.session.title, "My custom title")


class TestRepeatLoopWatchdog(unittest.TestCase):
    def test_identical_calls_break_turn(self):
        # Same mutating call every step — the watchdog must end the turn
        # well before the 120-step budget. (Pure read-only spins belong to
        # the exploration watchdog; execute_python is an action call.)
        scripts = [_tool_script("execute_python", '{"code": "print(1)"}', f"c{i}") for i in range(30)]
        prov = ScriptedProvider(scripts)
        agent = _agent()
        agent.provider = prov
        events = list(agent.step("run the same snippet repeatedly"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        # 1 initial + 6 repeated steps (streak 0..5) then break
        self.assertLess(len(prov.call_history), 10)
        texts = [str(e.data) for e in events if e.type == "text_delta"]
        self.assertTrue(any("Repeat loop" in t for t in texts))

    def test_varied_calls_no_false_positive(self):
        scripts = [
            _tool_script("view_file", '{"path": "a.py"}', "c1"),
            _tool_script("view_file", '{"path": "b.py"}', "c2"),
            [LLMChunk(delta_text="all done", finish_reason="stop")],
        ]
        prov = ScriptedProvider(scripts)
        agent = _agent()
        agent.provider = prov
        events = list(agent.step("look at two files"))
        texts = [str(e.data) for e in events if e.type == "text_delta"]
        self.assertFalse(any("Repeat loop" in t for t in texts))
        self.assertTrue(any(e.type == "turn_complete" for e in events))


class TestInputSentinels(unittest.TestCase):
    def test_clear_sentinel_distinct(self):
        from harness.tui.input_handler import SENTINEL_OPEN_AGENTS, SENTINEL_BACK
        self.assertNotEqual(SENTINEL_CLEAR, SENTINEL_OPEN_AGENTS)
        self.assertNotEqual(SENTINEL_CLEAR, SENTINEL_BACK)

    def test_ctrl_l_classification(self):
        from harness.tui.input_handler import InputHandler
        h = InputHandler()
        self.assertEqual(h.classify_keypress(b"\x0c"), SENTINEL_CLEAR)


class TestPromptEcho(unittest.TestCase):
    def test_typed_prompt_is_not_rendered_twice(self):
        item = QueuedItem(id=1, prompt="hello", metadata={"echoed": True})
        self.assertTrue(inter._prompt_already_echoed(item))

    def test_remote_or_command_prompt_still_gets_its_card(self):
        remote = QueuedItem(id=2, prompt="remote", metadata={"origin": "discord"})
        command = QueuedItem(id=3, prompt="AUTONOMOUS GOAL: ship it", mode="super")
        self.assertFalse(inter._prompt_already_echoed(remote))
        self.assertFalse(inter._prompt_already_echoed(command))


if __name__ == "__main__":
    unittest.main()
