"""
Tests for Slash Commands Engine.
"""
import json
import tempfile
import unittest
from pathlib import Path

import harness.config as config_mod
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel

class DummyRenderer(TerminalRenderer):
    def __init__(self):
        super().__init__("cyberpunk")
        self.messages = []

    def print_success(self, msg: str):
        self.messages.append(("success", msg))

    def print_warning(self, msg: str):
        self.messages.append(("warning", msg))

    def print_info(self, msg: str):
        self.messages.append(("info", msg))

    def print_error(self, msg: str):
        self.messages.append(("error", msg))

    def print_markdown(self, md: str):
        self.messages.append(("markdown", md))

class TestCommands(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_user_path = config_mod.USER_CONFIG_PATH
        self._orig_workspace_path = config_mod.WORKSPACE_CONFIG_PATH
        config_mod.USER_CONFIG_PATH = Path(self._tmp_dir.name) / "config.json"
        config_mod.WORKSPACE_CONFIG_PATH = Path(self._tmp_dir.name) / "ws" / "config.json"
        self.cfg = HarnessConfig()
        self.cfg.provider = "mock"
        self.agent = HarnessAgent(self.cfg)
        self.renderer = DummyRenderer()
        self.registry = CommandRegistry()

    def tearDown(self):
        config_mod.USER_CONFIG_PATH = self._orig_user_path
        config_mod.WORKSPACE_CONFIG_PATH = self._orig_workspace_path
        self._tmp_dir.cleanup()

    def _saved_config_json(self):
        path = config_mod.USER_CONFIG_PATH
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_mode_command(self):
        self.registry.handle("/mode plan", self.agent, self.renderer)
        self.assertEqual(self.agent.mode, Mode.PLAN)

        self.registry.handle("/mode build", self.agent, self.renderer)
        self.assertEqual(self.agent.mode, Mode.BUILD)

    def test_perm_command(self):
        self.registry.handle("/perm secure", self.agent, self.renderer)
        self.assertEqual(self.agent.permission_manager.level, PermissionLevel.SECURE)

        self.registry.handle("/perm full", self.agent, self.renderer)
        self.assertEqual(self.agent.permission_manager.level, PermissionLevel.FULL)

    def test_theme_command(self):
        self.registry.handle("/theme dracula", self.agent, self.renderer)
        self.assertEqual(self.renderer.theme.name, "dracula")

    def test_mode_command_persists_to_config(self):
        self.registry.handle("/mode super", self.agent, self.renderer)
        self.assertEqual(self.agent.mode, Mode.SUPER)
        saved = self._saved_config_json()
        self.assertEqual(saved["mode"], "super")

    def test_perm_command_persists_to_config(self):
        self.registry.handle("/perm full", self.agent, self.renderer)
        self.assertEqual(self.agent.permission_manager.level, PermissionLevel.FULL)
        saved = self._saved_config_json()
        self.assertEqual(saved["permission"], "full")

    def test_provider_command_persists_to_config(self):
        self.registry.handle("/provider openrouter", self.agent, self.renderer)
        self.assertEqual(self.agent.provider.name, "openrouter")
        saved = self._saved_config_json()
        self.assertEqual(saved["provider"], "openrouter")
        self.assertEqual(saved["model"], self.agent.config.model)

    def test_login_command_usage_and_unknown(self):
        # No args -> usage help mentioning all four login targets
        self.registry.handle("/login", self.agent, self.renderer)
        md = [m for tag, m in self.renderer.messages if tag == "markdown"]
        self.assertTrue(md and "openaiauth" in md[0] and "claudeauth" in md[0])
        # Unknown provider -> error
        self.registry.handle("/login gemini", self.agent, self.renderer)
        errors = [m for tag, m in self.renderer.messages if tag == "error"]
        self.assertTrue(any("Unknown provider" in m for m in errors))

    def _mock_browser_unavailable(self):
        """Patch the browser login flow to 'unavailable' so tests exercise the manual paste fallback.

        Without this the /login web-session tests would pop a REAL Chrome window
        and poll for a real login (up to 600s).
        """
        from unittest import mock as _mock
        return _mock.patch("harness.providers.web_login.capture_session_token", return_value=None)

    def test_login_openaiauth_stores_session_token(self):
        import harness.secure_store as ss
        from unittest import mock as _mock
        with self._mock_browser_unavailable(), \
             _mock.patch("getpass.getpass", return_value="  tok-web-123  "):
            self.registry.handle("/login openaiauth", self.agent, self.renderer)
        # Stored (trimmed) and retrievable through the standard key path
        self.assertEqual(self.agent.config.api_keys.get("openaiauth"), "tok-web-123")
        successes = [m for tag, m in self.renderer.messages if tag == "success"]
        self.assertTrue(any("/provider openaiauth" in m for m in successes))

    def test_login_openaiauth_browser_flow_stores_captured_token(self):
        # Preferred path: browser captures the cookie -> no getpass involved
        from unittest import mock as _mock
        with _mock.patch("harness.providers.web_login.capture_session_token",
                         return_value="tok-captured-by-browser"), \
             _mock.patch("harness.providers.web_login.verify_session_token", return_value=True):
            self.registry.handle("/login openaiauth", self.agent, self.renderer)
        self.assertEqual(self.agent.config.api_keys.get("openaiauth"), "tok-captured-by-browser")
        successes = [m for tag, m in self.renderer.messages if tag == "success"]
        self.assertTrue(any("Logged in to ChatGPT" in m for m in successes))

    def test_login_claudeauth_normalizes_full_cookie_paste(self):
        from unittest import mock as _mock
        with self._mock_browser_unavailable(), \
             _mock.patch("getpass.getpass", return_value="sessionKey=sk-ant-sid-abc"):
            self.registry.handle("/login claudeauth", self.agent, self.renderer)
        # "sessionKey=<value>" pastes are normalized to the bare value
        self.assertEqual(self.agent.config.api_keys.get("claudeauth"), "sk-ant-sid-abc")

    def test_login_openaiauth_normalizes_full_cookie_paste(self):
        from unittest import mock as _mock
        with self._mock_browser_unavailable(), \
             _mock.patch("getpass.getpass", return_value="__Secure-next-auth.session-token=abc.def"):
            self.registry.handle("/login openaiauth", self.agent, self.renderer)
        self.assertEqual(self.agent.config.api_keys.get("openaiauth"), "abc.def")

    def test_login_api_key_flow_untouched(self):
        # /login claude must behave like the classic API-key login
        from unittest import mock as _mock
        with _mock.patch("getpass.getpass", return_value="sk-ant-api03-test"):
            self.registry.handle("/login claude", self.agent, self.renderer)
        self.assertEqual(self.agent.config.api_keys.get("anthropic"), "sk-ant-api03-test")
        # /login openai also stores a plain API key
        with _mock.patch("getpass.getpass", return_value="sk-openai-test"):
            self.registry.handle("/login openai", self.agent, self.renderer)
        self.assertEqual(self.agent.config.api_keys.get("openai"), "sk-openai-test")

    def test_login_empty_input_cancels(self):
        from unittest import mock as _mock
        with self._mock_browser_unavailable(), \
             _mock.patch("getpass.getpass", return_value=""):
            self.registry.handle("/login openaiauth", self.agent, self.renderer)
        self.assertNotIn("openaiauth", self.agent.config.api_keys)
        warns = [m for tag, m in self.renderer.messages if tag == "warning"]
        self.assertTrue(any("cancelled" in m.lower() for m in warns))

    def test_model_command_persists_to_config(self):
        self.cfg.provider = "mock"
        self.agent.set_provider("mock")
        self.registry.handle("/model custom-test-model", self.agent, self.renderer)
        self.assertEqual(self.agent.config.model, "custom-test-model")
        saved = self._saved_config_json()
        self.assertEqual(saved["model"], "custom-test-model")

    def test_effort_command_persists_to_config(self):
        self.registry.handle("/effort low", self.agent, self.renderer)
        saved = self._saved_config_json()
        self.assertEqual(saved["thinking_effort"], "low")

    def test_runtime_config_changes_do_not_touch_real_config(self):
        real = self._orig_user_path
        before = real.read_bytes() if real.exists() else None
        self.registry.handle("/theme dracula", self.agent, self.renderer)
        self.registry.handle("/mode super", self.agent, self.renderer)
        self.registry.handle("/perm full", self.agent, self.renderer)
        after = real.read_bytes() if real.exists() else None
        self.assertEqual(before, after)

    def test_todo_commands(self):
        self.registry.handle("/todo add Write documentation", self.agent, self.renderer)
        self.assertEqual(len(self.agent.todo_manager.tasks), 1)
        self.assertEqual(self.agent.todo_manager.tasks[0].title, "Write documentation")

    def test_queue_commands(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        # Add item
        self.registry.handle("/queue add Run tests", self.agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 1)
        self.assertEqual(q.peek().prompt, "Run tests")

        # List
        self.registry.handle("/queue list", self.agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 1)

        # Pause and resume
        self.registry.handle("/queue pause", self.agent, self.renderer, queue=q)
        self.assertTrue(q.is_paused)
        self.registry.handle("/queue resume", self.agent, self.renderer, queue=q)
        self.assertFalse(q.is_paused)

        # Drop item
        item_id = q.peek().id
        self.registry.handle(f"/queue drop {item_id}", self.agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 0)

        # Clear
        q.enqueue("Task 1")
        q.enqueue("Task 2")
        self.assertEqual(q.size(), 2)
        self.registry.handle("/queue clear", self.agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 0)

    def test_stop_all_clears_queue(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        q.enqueue("Task 1")
        q.enqueue("Task 2")
        self.assertEqual(q.size(), 2)
        self.registry.handle("/stop all", self.agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 0)

    def test_status_command(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        self.registry.handle("/status", self.agent, self.renderer, queue=q)

    def test_sidebar_and_info_command(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        q.enqueue("Sidebar test task")
        self.registry.handle("/info", self.agent, self.renderer, queue=q)
        self.registry.handle("/sidebar", self.agent, self.renderer, queue=q)

    def test_opencode_visual_rendering(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        q.enqueue("Demo queued task")
        self.renderer.print_welcome_splash(self.agent, queue=q)
        self.renderer.render_user_prompt("Write a binary search algorithm in Python")
        self.renderer.render_turn_capsule("build", "gpt-4o", 1.8)
        panel = self.renderer.render_sidebar(self.agent, queue=q)
        self.assertIsNotNone(panel)

    def test_clear_command(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        # Verify /clear executes cleanly without invoking old hud/banner
        self.registry.handle("/clear", self.agent, self.renderer, queue=q)

    def test_modal_commands(self):
        from harness.tui.queue import ExecutionQueue
        q = ExecutionQueue()
        # Verify all informational modal commands render without errors
        self.registry.handle("/help", self.agent, self.renderer, queue=q)
        self.registry.handle("/session", self.agent, self.renderer, queue=q)
        self.registry.handle("/models", self.agent, self.renderer, queue=q)
        self.registry.handle("/theme", self.agent, self.renderer, queue=q)
        self.registry.handle("/mcp", self.agent, self.renderer, queue=q)

if __name__ == "__main__":
    unittest.main()
