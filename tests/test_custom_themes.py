"""
Custom theme creation, persistence, and /theme command integration tests.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harness.config as config_mod
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.themes import (
    THEMES, BUILTIN_THEME_NAMES, create_custom_theme, delete_custom_theme,
    normalize_theme_name, validate_theme_dict, get_custom_themes_path,
    render_theme_preview, load_custom_themes,
)


class DummyRenderer(TerminalRenderer):
    def __init__(self):
        super().__init__("cyberpunk")
        self.messages = []

    def print_success(self, msg):
        self.messages.append(("success", msg))

    def print_info(self, msg):
        self.messages.append(("info", msg))

    def print_error(self, msg):
        self.messages.append(("error", msg))

    def print_warning(self, msg):
        self.messages.append(("warning", msg))

    def print_markdown(self, md):
        self.messages.append(("markdown", md))


VALID_COLORS = {
    "primary": "#0077ff", "secondary": "#00ffff", "accent": "#7fffd4",
    "success": "#00ff88", "warning": "#ffaa00", "error": "#ff4444",
    "muted": "#4488aa", "text": "#e0f0ff", "border": "#0077ff",
    "thinking": "#88ccff",
}


class TestCustomThemes(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name  # global themes.json lands here
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp.name)            # workspace themes.json lands here
        # Drop themes created by other tests.
        for name in list(THEMES.keys()):
            if name not in BUILTIN_THEME_NAMES:
                del THEMES[name]

    def tearDown(self):
        os.chdir(self._orig_cwd)
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        self._tmp.cleanup()

    def test_create_persists_and_registers(self):
        ok, msg = create_custom_theme("ocean", "Ocean Deep", VALID_COLORS,
                                      code_theme="dracula", global_scope=False)
        self.assertTrue(ok, msg)
        self.assertIn("ocean", THEMES)
        self.assertEqual(THEMES["ocean"].primary, "#0077ff")

        # Persisted to the workspace themes.json
        path = Path(".harness") / "themes.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIn("ocean", data)

        # Clean up registry + file
        ok, msg = delete_custom_theme("ocean")
        self.assertTrue(ok, msg)
        self.assertNotIn("ocean", THEMES)

    def test_name_normalization(self):
        self.assertEqual(normalize_theme_name("Ocean Deep!"), "ocean_deep")
        self.assertEqual(normalize_theme_name("  My-Cool THEME  "), "my_cool_theme")
        self.assertEqual(normalize_theme_name("!!"), "")

        ok, msg = create_custom_theme("Ocean Deep!", "Ocean Deep", VALID_COLORS,
                                      global_scope=False)
        self.assertTrue(ok, msg)
        self.assertIn("ocean_deep", THEMES)
        # Deleting with a differently-formatted name still finds it
        ok, msg = delete_custom_theme("Ocean-Deep")
        self.assertTrue(ok, msg)
        self.assertNotIn("ocean_deep", THEMES)

    def test_builtin_shadowing_blocked(self):
        ok, msg = create_custom_theme("cyberpunk", "Fake", VALID_COLORS, global_scope=False)
        self.assertFalse(ok)
        self.assertIn("built-in", msg)
        self.assertEqual(THEMES["cyberpunk"].primary, "#00ffff", "original must be untouched")

        ok, msg = delete_custom_theme("cyberpunk")
        self.assertFalse(ok)
        self.assertIn("cyberpunk", THEMES)

    def test_validation_requires_all_color_fields(self):
        data = {**VALID_COLORS, "code_theme": "monokai"}
        valid, errors = validate_theme_dict(data)
        self.assertTrue(valid, errors)

        data_missing = dict(data)
        del data_missing["primary"]
        data_missing["code_theme"] = "monokai"
        valid, errors = validate_theme_dict(data_missing)
        self.assertFalse(valid)
        self.assertTrue(any("primary" in e for e in errors), errors)

    def test_create_fills_missing_colors_with_defaults(self):
        ok, msg = create_custom_theme("partial", "Partial",
                                      {"primary": "#ff0000"}, global_scope=False)
        self.assertTrue(ok, msg)
        self.assertEqual(THEMES["partial"].primary, "#ff0000")
        self.assertEqual(THEMES["partial"].secondary, "#ff007f", "missing fields use defaults")
        delete_custom_theme("partial")

    def test_reload_from_disk(self):
        ok, _ = create_custom_theme("persisted", "Persisted", VALID_COLORS,
                                    global_scope=False)
        self.assertTrue(ok)
        del THEMES["persisted"]  # simulate a fresh process
        self.assertNotIn("persisted", THEMES)

        load_custom_themes()
        self.assertIn("persisted", THEMES)
        self.assertEqual(THEMES["persisted"].primary, "#0077ff")
        delete_custom_theme("persisted")

    def test_global_vs_workspace_scope(self):
        ok, _ = create_custom_theme("g", "Global", VALID_COLORS, global_scope=True)
        self.assertTrue(ok)
        self.assertTrue((Path(self._tmp.name) / ".harness" / "themes.json").exists())
        delete_custom_theme("g")

        ok, _ = create_custom_theme("w", "Workspace", VALID_COLORS, global_scope=False)
        self.assertTrue(ok)
        self.assertTrue((Path(".harness") / "themes.json").exists())
        delete_custom_theme("w")

    def test_preview_renders_custom_theme(self):
        ok, _ = create_custom_theme("ocean", "Ocean Deep", VALID_COLORS,
                                     code_theme="dracula", global_scope=False)
        self.assertTrue(ok)
        try:
            from rich.console import Console
            console = Console(width=120, record=True)
            console.print(render_theme_preview("ocean"))
            output = console.export_text()
            self.assertIn("Ocean Deep", output)
        finally:
            delete_custom_theme("ocean")


class TestThemeCommandIntegration(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self._orig_user_path = config_mod.USER_CONFIG_PATH
        config_mod.USER_CONFIG_PATH = Path(self._tmp.name) / "config.json"
        self._orig_ws_path = config_mod.WORKSPACE_CONFIG_PATH
        config_mod.WORKSPACE_CONFIG_PATH = Path(self._tmp.name) / "ws" / "config.json"

        for name in list(THEMES.keys()):
            if name not in BUILTIN_THEME_NAMES:
                del THEMES[name]

        self.cfg = HarnessConfig()
        self.cfg.provider = "mock"
        self.agent = HarnessAgent(self.cfg)
        self.renderer = DummyRenderer()
        self.registry = CommandRegistry()

    def tearDown(self):
        config_mod.USER_CONFIG_PATH = self._orig_user_path
        config_mod.WORKSPACE_CONFIG_PATH = self._orig_ws_path
        os.chdir(self._orig_cwd)
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        self._tmp.cleanup()

    def _handle(self, line):
        # handle() returns the handler's value (None for /theme) — the command
        # IS handled whenever no "Unknown command" error is printed.
        self.registry.handle(line, self.agent, self.renderer)
        return [m[1] if isinstance(m[1], str) else str(m[1]) for m in self.renderer.messages]

    def test_theme_list_marks_custom_themes(self):
        create_custom_theme("mine", "My Theme", VALID_COLORS, global_scope=False)
        self.renderer.messages = []
        outputs = self._handle("/theme list")
        text = "\n".join(outputs)
        self.assertIn("mine", text)
        self.assertIn("custom", text)
        self.assertIn("built-in", text)
        delete_custom_theme("mine")

    def test_theme_delete_command(self):
        create_custom_theme("doomed", "Doomed", VALID_COLORS, global_scope=False)
        self.renderer.messages = []
        outputs = self._handle("/theme delete doomed")
        self.assertTrue(any("deleted" in o for o in outputs), outputs)
        self.assertNotIn("doomed", THEMES)

    def test_theme_delete_builtin_blocked(self):
        self.renderer.messages = []
        outputs = self._handle("/theme delete dracula")
        self.assertTrue(any("built-in" in o.lower() or "cannot" in o.lower() for o in outputs),
                        outputs)
        self.assertIn("dracula", THEMES)

    def test_theme_delete_active_theme_reverts_to_default(self):
        create_custom_theme("active", "Active", VALID_COLORS, global_scope=False)
        self.agent.config.theme = "active"
        self.renderer.set_theme("active")
        self.renderer.messages = []
        self._handle("/theme delete active")
        self.assertEqual(self.agent.config.theme, "cyberpunk",
                         "deleting the active theme must revert to the default")
        self.assertEqual(self.renderer.theme.name, "cyberpunk")

    def test_theme_switch_to_custom_theme(self):
        create_custom_theme("switchable", "Switchable", VALID_COLORS, global_scope=False)
        self.renderer.messages = []
        outputs = self._handle("/theme switchable")
        self.assertTrue(any("Switchable" in o for o in outputs), outputs)
        self.assertEqual(self.agent.config.theme, "switchable")
        delete_custom_theme("switchable")


if __name__ == "__main__":
    unittest.main()
