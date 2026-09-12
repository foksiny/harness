"""
Unit tests for Configuration, API Key management, and Theme Engine.
"""
import unittest
import tempfile
import json
from pathlib import Path
from harness.config import HarnessConfig, mask_key, load_config, save_config
from harness.themes import THEMES, get_theme, list_themes, render_theme_preview, load_custom_themes

class TestConfigAndKeys(unittest.TestCase):

    def test_key_masking(self):
        self.assertEqual(mask_key(None), "(not set)")
        self.assertEqual(mask_key(""), "(not set)")
        self.assertEqual(mask_key("12345"), "***")

        masked = mask_key("sk-ant-api03-abcdef1234567890xyz")
        self.assertTrue(masked.startswith("sk-ant"))
        self.assertTrue(masked.endswith("0xyz"))
        self.assertIn("***", masked)

    def test_config_field_conversion(self):
        cfg = HarnessConfig()

        # Int conversion
        self.assertTrue(cfg.set_field("max_subagents", "8"))
        self.assertEqual(cfg.max_subagents, 8)

        # Float conversion
        self.assertTrue(cfg.set_field("compact_threshold", "0.85"))
        self.assertEqual(cfg.compact_threshold, 0.85)

        # Bool conversion
        self.assertTrue(cfg.set_field("auto_compact", "false"))
        self.assertFalse(cfg.auto_compact)
        self.assertTrue(cfg.set_field("auto_compact", "true"))
        self.assertTrue(cfg.auto_compact)

        # String
        self.assertTrue(cfg.set_field("theme", "dracula"))
        self.assertEqual(cfg.theme, "dracula")

    def test_api_key_lifecycle(self):
        cfg = HarnessConfig()
        cfg.set_api_key("openrouter", "sk-or-v1-secretkey12345678")
        self.assertEqual(cfg.get_api_key("openrouter"), "sk-or-v1-secretkey12345678")

        # List keys
        status_list = cfg.list_keys_status()
        or_status = next((s for s in status_list if s["provider"] == "openrouter"), None)
        self.assertIsNotNone(or_status)
        self.assertEqual(or_status["status"], "configured")
        self.assertEqual(or_status["source"], "config")

        # Remove key
        self.assertTrue(cfg.remove_api_key("openrouter"))
        self.assertIsNone(cfg.get_api_key("openrouter"))

    def test_fourteen_builtin_themes(self):
        themes = list_themes()
        self.assertGreaterEqual(len(themes), 14)

        expected = [
            "cyberpunk", "dracula", "nord", "monokai", "catppuccin",
            "matrix", "minimal", "tokyo_night", "solarized_dark",
            "gruvbox", "synthwave", "one_dark", "amber_crt", "rose_pine"
        ]
        for name in expected:
            self.assertIn(name, THEMES, f"Theme '{name}' should exist")
            t = get_theme(name)
            self.assertIsNotNone(t.primary)
            self.assertIsNotNone(t.border)

    def test_theme_preview_rendering(self):
        panel = render_theme_preview("cyberpunk")
        self.assertIsNotNone(panel)
        panel_tokyo = render_theme_preview("tokyo_night")
        self.assertIsNotNone(panel_tokyo)

if __name__ == "__main__":
    unittest.main()
