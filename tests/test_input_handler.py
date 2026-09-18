"""Tests for InputHandler keybind classification in the readline-less fallback."""
import os
import sys
import unittest

from harness.tui.input_handler import (
    InputHandler,
    SENTINEL_OPEN_AGENTS,
    SLASH_COMMANDS,
    VIEW_PARENT,
    is_command_input,
    no_echo_stdin,
)

KNOWN = {c.lstrip("/") for c in SLASH_COMMANDS}


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
        self.assertIn("/queue", SLASH_COMMANDS)
        self.assertIn("/sidebar", SLASH_COMMANDS)
        self.assertIn("/status", SLASH_COMMANDS)
        from harness.tui.input_handler import COMMAND_DESCRIPTIONS
        self.assertIn("/queue", COMMAND_DESCRIPTIONS)
        self.assertIn("/sidebar", COMMAND_DESCRIPTIONS)
        self.assertIn("/status", COMMAND_DESCRIPTIONS)


class TestCommandVsPathRouting(unittest.TestCase):
    def test_known_command_routes_as_command(self):
        self.assertTrue(is_command_input("/help", KNOWN))
        self.assertTrue(is_command_input("/config set provider openai", KNOWN))

    def test_unknown_command_word_still_errors(self):
        self.assertTrue(is_command_input("/foobar", KNOWN))

    def test_absolute_file_path_routes_to_prompt(self):
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "harness_route.png")
        with open(path, "wb") as f:
            f.write(b"x")
        try:
            self.assertFalse(is_command_input(f"{path} what image is this?", KNOWN))
            self.assertFalse(is_command_input(f"{path}", KNOWN))
        finally:
            os.remove(path)

    def test_non_existent_slash_prefixed_words_vary(self):
        # A bare dir like /tmp exists -> path routing, not a command.
        self.assertFalse(is_command_input("/tmp quick look", KNOWN))
        # Double slash is never a command.
        self.assertFalse(is_command_input("//give a command", KNOWN))

    def test_scheme_and_relative_markers_route_to_prompt(self):
        self.assertFalse(is_command_input("file:///tmp/x.png look", KNOWN))
        self.assertFalse(is_command_input("./img.png", KNOWN))
        self.assertFalse(is_command_input("../img.png", KNOWN))

    def test_plain_prompt_is_never_a_command(self):
        self.assertFalse(is_command_input("just say hi", KNOWN))


class TestNoEchoGuard(unittest.TestCase):
    def test_echo_disabled_during_run_and_restored_after(self):
        import pty
        import termios

        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            self.assertTrue(before[3] & termios.ECHO)

            with no_echo_stdin(fd=slave):
                muted = termios.tcgetattr(slave)
                self.assertFalse(muted[3] & termios.ECHO)

            after = termios.tcgetattr(slave)
            self.assertTrue(after[3] & termios.ECHO)
            self.assertEqual(after, before)
        finally:
            os.close(master)
            os.close(slave)

    def test_guard_still_restores_on_exception(self):
        import pty
        import termios

        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            with self.assertRaises(RuntimeError):
                with no_echo_stdin(fd=slave):
                    termios.tcgetattr(slave)
                    raise RuntimeError("boom")
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_guard_noop_without_controlling_tty(self):
        import io
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("")  # not a tty
            with no_echo_stdin():
                pass  # must not raise
        finally:
            sys.stdin = old_stdin


if __name__ == "__main__":
    unittest.main()