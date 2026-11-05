
"""Tests for the cute working-animation helpers (harness/tui/working.py)."""
import unittest

from harness.tui.working import (
    SPINNERS, MASCOT_FRAMES, MASCOT_ORDER, MESSAGES, FUNNY_MESSAGES,
    spinner_frame, mascot_frame, message_for, mascot_name,
    working_line, thinking_line, tool_line, activity_for_tool,
    rotating_message, WorkingAnimator,
)


class WorkingHelpersTests(unittest.TestCase):
    def test_spinner_wraps(self):
        frames = SPINNERS["braille"]
        self.assertEqual(spinner_frame("braille", 0), frames[0])
        self.assertEqual(spinner_frame("braille", len(frames)), frames[0])
        # Unknown spinner falls back to braille.
        self.assertEqual(spinner_frame("nope", 0), frames[0])

    def test_mascot_wraps(self):
        frames = MASCOT_FRAMES["cat"]
        self.assertEqual(mascot_frame("cat", len(frames)), frames[0])
        self.assertEqual(mascot_frame("nope", 0), frames[0])

    def test_message_cycles_slowly(self):
        # Message index advances every 4 ticks, not every tick.
        self.assertEqual(message_for("thinking", 0), message_for("thinking", 3))
        pool = MESSAGES["thinking"]
        self.assertEqual(message_for("thinking", 4), pool[1])
        self.assertEqual(message_for("unknown_activity", 0), message_for("unknown_activity", 0))

    def test_mascot_rotation(self):
        self.assertEqual(mascot_name(0), MASCOT_ORDER[0])
        self.assertEqual(mascot_name(8), MASCOT_ORDER[1 % len(MASCOT_ORDER)])
        self.assertEqual(mascot_name(len(MASCOT_ORDER) * 8), MASCOT_ORDER[0])

    def test_working_line_shape(self):
        line = working_line("thinking", 0)
        self.assertTrue(line.startswith(f"{spinner_frame('braille', 0)} "))
        self.assertTrue(line.endswith("…"))
        self.assertIn(FUNNY_MESSAGES[0], line)

    def test_funny_messages_at_least_15(self):
        self.assertGreaterEqual(len(FUNNY_MESSAGES), 15)
        # The two examples from the request are in the pool.
        joined = " | ".join(FUNNY_MESSAGES)
        self.assertIn("Cooking up your project", joined)
        self.assertIn("Trying not to explode the tests", joined)

    def test_rotating_message_cycles(self):
        seen = {rotating_message(t * 12) for t in range(len(FUNNY_MESSAGES))}
        self.assertEqual(seen, set(FUNNY_MESSAGES))

    def test_thinking_line_has_tokens(self):
        line = thinking_line(0, 1234)
        self.assertIn("1,234 tokens", line)

    def test_tool_line_includes_label(self):
        line = tool_line("reading", 0, label="Read: view_file")
        self.assertIn("Read: view_file", line)
        self.assertIn(rotating_message(0, "reading"), line)

    def test_activity_for_tool(self):
        cases = {
            "view_file": "reading", "read": "reading",
            "edit_file": "writing", "write_file": "writing",
            "grep_search": "searching", "find_files": "searching",
            "run_command": "running", "bash": "running",
            "ask_user": "waiting", "run_tests": "testing",
            "spawn_subagent": "generating", "something_new": "generating",
        }
        for tool, expected in cases.items():
            self.assertEqual(activity_for_tool(tool), expected, tool)


class WorkingAnimatorTests(unittest.TestCase):
    def test_advance_and_line(self):
        a = WorkingAnimator("thinking")
        self.assertEqual(a.tick, 0)
        a.advance()
        self.assertEqual(a.tick, 1)
        self.assertIn(rotating_message(1, "thinking"), a.line(10))
        a.advance(4)
        self.assertEqual(a.tick, 5)

    def test_set_activity_resets_tick(self):
        a = WorkingAnimator("thinking")
        a.advance(10)
        a.set_activity("reading")
        self.assertEqual(a.tick, 0)
        self.assertEqual(a.activity, "reading")
        self.assertIn(rotating_message(0, "reading"), a.line())

    def test_line_without_tokens_for_non_thinking(self):
        a = WorkingAnimator("running")
        line = a.line()  # tokens=None
        self.assertIn(FUNNY_MESSAGES[0], line)

    def test_live_line_self_animates(self):
        a = WorkingAnimator("thinking")
        self.assertFalse(a.running)
        line = a.live_line(7)
        self.assertTrue(a.running)
        self.assertIn("7 tokens", line)
        a.stop()
        self.assertFalse(a.running)

    def test_prompt_message_splits_above_input(self):
        """The cute line must render ABOVE the prompt box (multi-line prompt)."""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.shortcuts.prompt import _split_multiline_prompt

        def get_prompt():
            return HTML('<style color="#e8b64c">CUTE LINE</style>\n<ansicyan><b>|</b></ansicyan> ')

        session = PromptSession(message=get_prompt)
        has, before, first = _split_multiline_prompt(session._get_prompt)
        self.assertTrue(has())
        before_text = "".join(c for _, c in before())
        self.assertIn("CUTE", before_text)
        first_text = "".join(c for _, c in first())
        self.assertIn("|", first_text)


class RendererIntegrationTests(unittest.TestCase):
    def _renderer(self):
        from harness.tui.terminal import TerminalRenderer
        return TerminalRenderer()

    def test_renderer_has_animator_and_live_line(self):
        r = self._renderer()
        self.assertIsInstance(r._animator, WorkingAnimator)
        self.assertEqual(r._live_working_line, "")
        # Legacy mirror attributes still exist for the toolbar.
        self.assertEqual(r._live_thinking_tokens, 0)
        self.assertFalse(r._live_thinking_active)

    def test_thinking_header_populates_live_line(self):
        import io
        from rich.console import Console
        r = self._renderer()
        r.console = Console(file=io.StringIO(), width=120, legacy_windows=False)
        r._current_thinking = "x" * 40  # ~10 tokens
        r._thinking_print_header()
        self.assertTrue(r._live_thinking_active)
        self.assertTrue(r._live_working_line)
        self.assertIn("Cooking up your project", r._live_working_line)

    def test_finish_thinking_stops_animation(self):
        r = self._renderer()
        r._current_thinking = "x" * 40
        r._thinking_print_header()
        r._is_thinking_visible = True
        r._thinking_start_time = 0
        r._start_working_animation()
        self.assertIsNotNone(r._working_thread)
        r._finish_thinking()
        self.assertIsNone(r._working_thread)
        self.assertEqual(r._live_working_line, "")

    def test_tool_events_set_live_line_and_stop(self):
        class Ev:
            def __init__(self, t, d):
                self.type = t
                self.data = d

        r = self._renderer()
        r.render_agent_event(Ev("tool_call_delta", {"name": "view_file", "id": "1"}))
        self.assertTrue(r._live_working_line)
        self.assertIn("view_file", r._live_working_line)
        self.assertIn(rotating_message(r._animator.tick, "reading"), r._live_working_line)
        r.render_agent_event(Ev("tool_call_result", {"result": "ok"}))
        self.assertEqual(r._live_working_line, "")


if __name__ == "__main__":
    unittest.main()
