"""
Tests that session surfaces render FULL session ids (users copy them into
/session resume|delete|rename): the /session list card, the interactive
picker subtitle, and the sidebar header line.
"""
import types
import unittest
from unittest.mock import patch

from rich.console import Console

from harness.tui.terminal import TerminalRenderer


FULL_SID = "sess_20260918_073510_a1b2c3"
OTHER_SID = "sess_20260917_101112_def456"
LONG_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

SESSIONS = [
    {"id": FULL_SID, "title": "New Session", "model": LONG_MODEL, "turns": 4},
    {"id": OTHER_SID, "title": "Fusion research", "model": "z-ai/glm-5.3", "turns": 2},
]


def _recording_renderer(width: int = 120) -> tuple:
    r = TerminalRenderer()
    r.console = Console(record=True, width=width, force_terminal=False)
    return r, r.console


class TestSessionListFullIds(unittest.TestCase):

    def test_list_shows_full_ids(self):
        r, console = _recording_renderer()
        r.print_sessions_modal(SESSIONS, active_id=None)
        out = console.export_text()
        self.assertIn(FULL_SID, out)
        self.assertIn(OTHER_SID, out)
        # Not the old truncated form.
        self.assertNotIn("sess_202 ", out)
        self.assertNotIn("sess_202\nd", out)

    def test_active_session_line_has_full_id(self):
        r, console = _recording_renderer()
        r.print_sessions_modal(SESSIONS, active_id=FULL_SID)
        out = console.export_text()
        self.assertIn(FULL_SID, out)

    def test_long_title_shrinks_never_the_id(self):
        long_title_sessions = [
            {"id": FULL_SID, "title": "A very long session title about fusion energy research",
             "model": LONG_MODEL, "turns": 9},
        ]
        r, console = _recording_renderer(width=80)
        r.print_sessions_modal(long_title_sessions, active_id=None)
        out = console.export_text()
        self.assertIn(FULL_SID, out)
        # Title was truncated with an ellipsis, id survived intact.
        self.assertIn("…", out)

    def test_narrow_terminal_still_keeps_full_id(self):
        r, console = _recording_renderer(width=50)
        r.print_sessions_modal(SESSIONS, active_id=None)
        out = console.export_text()
        self.assertIn(FULL_SID, out)

    def test_empty_sessions(self):
        r, console = _recording_renderer()
        r.print_sessions_modal([], active_id=None)
        self.assertIn("No saved sessions found", console.export_text())


class TestInteractivePickerFullIds(unittest.TestCase):

    def test_picker_subtitle_and_id_are_full(self):
        r = TerminalRenderer()
        captured = {}

        def _fake_run(self_modal, console):
            captured["items"] = self_modal.items
            return None

        with patch("harness.tui.modal.InteractiveModal.run", new=_fake_run):
            r.show_sessions_modal(SESSIONS, active_id=FULL_SID)
        items = captured["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, FULL_SID)
        self.assertEqual(items[0].payload, FULL_SID)
        self.assertIn(FULL_SID, items[0].subtitle)
        self.assertTrue(items[0].is_active)
        self.assertEqual(items[1].id, OTHER_SID)
        self.assertIn(OTHER_SID, items[1].subtitle)
        self.assertFalse(items[1].is_active)


class _StubAgent:
    def __init__(self, sid: str):
        self.session = types.SimpleNamespace(id=sid, messages=[])
        self.compactor = types.SimpleNamespace(context_window=128000)
        self.mcp_manager = types.SimpleNamespace(clients={})
        self.skills_manager = types.SimpleNamespace(list_skills=lambda: [])
        self.todo_manager = types.SimpleNamespace(tasks=[])


class TestSidebarFullId(unittest.TestCase):

    def test_sidebar_header_line_has_full_id(self):
        r = TerminalRenderer()
        lines = r._build_sidebar_lines(_StubAgent(FULL_SID))
        self.assertTrue(lines)
        self.assertIn(FULL_SID, lines[0])

    def test_sidebar_no_agent_shows_new_session(self):
        r = TerminalRenderer()
        lines = r._build_sidebar_lines(None)
        self.assertIn("New session", lines[0])


if __name__ == "__main__":
    unittest.main()
