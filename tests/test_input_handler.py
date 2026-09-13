"""Tests for InputHandler keybind classification in the readline-less fallback."""
import unittest

from harness.tui.input_handler import (
    InputHandler,
    SENTINEL_OPEN_AGENTS,
    SLASH_COMMANDS,
    VIEW_PARENT,
)


class TestKeybindClassification(unittest.TestCase):
    def setUp(self):
        self.handler = InputHandler()

    def test_f2_csi_sequence(self):
        self.assertEqual(
            self.handler.classify_keypress(b"\x1b[12~", VIEW_PARENT),
            SENTINEL_OPEN_AGENTS,
        )

    def test_f2_ss3_sequence(self):
        self.assertEqual(
            self.handler.classify_keypress(b"\x1bOQ", VIEW_PARENT),
            SENTINEL_OPEN_AGENTS,
        )

    def test_f2_vt_modifier_sequence(self):
        self.assertEqual(
            self.handler.classify_keypress(b"\x1b[1;2Q", VIEW_PARENT),
            SENTINEL_OPEN_AGENTS,
        )

    def test_ctrl_g_opens_agents(self):
        self.assertEqual(
            self.handler.classify_keypress(b"\x07", VIEW_PARENT),
            SENTINEL_OPEN_AGENTS,
        )

    def test_other_function_keys_swallowed(self):
        self.assertEqual(self.handler.classify_keypress(b"\x1b[13~", VIEW_PARENT), "")
        self.assertEqual(self.handler.classify_keypress(b"\x1bOP", VIEW_PARENT), "")

    def test_arrow_keys_swallowed(self):
        self.assertEqual(self.handler.classify_keypress(b"\x1b[A", VIEW_PARENT), "")
        self.assertEqual(self.handler.classify_keypress(b"\x1b[B", VIEW_PARENT), "")

    def test_lone_escape_is_incomplete(self):
        self.assertIsNone(self.handler.classify_keypress(b"\x1b", VIEW_PARENT))

    def test_csi_prefixes_are_incomplete(self):
        self.assertIsNone(self.handler.classify_keypress(b"\x1b[", VIEW_PARENT))
        self.assertIsNone(self.handler.classify_keypress(b"\x1b[1", VIEW_PARENT))
        self.assertIsNone(self.handler.classify_keypress(b"\x1b[12", VIEW_PARENT))
        self.assertIsNone(self.handler.classify_keypress(b"\x1b[1;", VIEW_PARENT))

    def test_plain_text_is_not_a_keybind(self):
        self.assertIsNone(self.handler.classify_keypress(b"a", VIEW_PARENT))

    def test_learn_in_slash_commands_completion(self):
        self.assertIn("/learn", SLASH_COMMANDS)


if __name__ == "__main__":
    unittest.main()