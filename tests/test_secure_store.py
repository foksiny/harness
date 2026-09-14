"""
Unit tests for Secure API Key Store.
Tests the file-based fallback path (keyring mocked as unavailable).
"""
import unittest
import tempfile
import os
import json
import stat
from pathlib import Path
from unittest.mock import patch


class TestSecureStoreFileFallback(unittest.TestCase):
    """Test secure_store with keyring mocked as unavailable (file fallback)."""

    def setUp(self):
        # Create a temp directory for test key files
        self.tmpdir = tempfile.mkdtemp()
        self.keys_file = Path(self.tmpdir) / "api_keys.json"
        # Patch KEYS_FILE and _keyring_checked to reset state
        self._patches = [
            patch("harness.secure_store.KEYS_FILE", self.keys_file),
            patch("harness.secure_store._keyring", None),
            patch("harness.secure_store._keyring_checked", True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        # Cleanup
        if self.keys_file.exists():
            self.keys_file.unlink()
        os.rmdir(self.tmpdir)

    def test_set_and_get_key(self):
        from harness.secure_store import set_key, get_key
        set_key("anthropic", "sk-ant-test123")
        self.assertEqual(get_key("anthropic"), "sk-ant-test123")

    def test_get_missing_key_returns_none(self):
        from harness.secure_store import get_key
        self.assertIsNone(get_key("nonexistent"))

    def test_remove_key(self):
        from harness.secure_store import set_key, get_key, remove_key
        set_key("openai", "sk-test")
        self.assertTrue(remove_key("openai"))
        self.assertIsNone(get_key("openai"))

    def test_remove_missing_key_returns_false(self):
        from harness.secure_store import remove_key
        self.assertFalse(remove_key("nonexistent"))

    def test_provider_name_normalization(self):
        from harness.secure_store import set_key, get_key
        set_key("  Anthropic  ", "sk-ant-test")
        self.assertEqual(get_key("anthropic"), "sk-ant-test")
        self.assertEqual(get_key("ANTHROPIC"), "sk-ant-test")

    def test_file_permissions_restrictive(self):
        from harness.secure_store import set_key
        set_key("anthropic", "sk-ant-test")
        mode = stat.S_IMODE(os.stat(str(self.keys_file)).st_mode)
        # Should be 0o600 (owner rw only)
        self.assertEqual(mode, 0o600)

    def test_file_permissions_fix_existing(self):
        # Create a file with lax permissions
        self.keys_file.write_text("{}")
        os.chmod(str(self.keys_file), 0o644)
        from harness.secure_store import ensure_file_permissions
        ensure_file_permissions()
        mode = stat.S_IMODE(os.stat(str(self.keys_file)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_multiple_providers(self):
        from harness.secure_store import set_key, get_key
        set_key("anthropic", "sk-ant")
        set_key("openai", "sk-openai")
        set_key("gemini", "gem-key")
        self.assertEqual(get_key("anthropic"), "sk-ant")
        self.assertEqual(get_key("openai"), "sk-openai")
        self.assertEqual(get_key("gemini"), "gem-key")

    def test_overwrite_key(self):
        from harness.secure_store import set_key, get_key
        set_key("anthropic", "old-key")
        set_key("anthropic", "new-key")
        self.assertEqual(get_key("anthropic"), "new-key")

    def test_migrate_plaintext_keys(self):
        from harness.secure_store import migrate_plaintext_keys, get_key
        old_keys = {"anthropic": "sk-ant-migrate", "openai": "sk-oai-migrate"}
        migrate_plaintext_keys(old_keys)
        self.assertEqual(get_key("anthropic"), "sk-ant-migrate")
        self.assertEqual(get_key("openai"), "sk-oai-migrate")

    def test_migrate_skips_already_stored(self):
        from harness.secure_store import set_key, migrate_plaintext_keys, get_key
        set_key("anthropic", "already-here")
        migrate_plaintext_keys({"anthropic": "different-key"})
        # Should NOT overwrite
        self.assertEqual(get_key("anthropic"), "already-here")

    def test_migrate_ignores_empty_keys(self):
        from harness.secure_store import migrate_plaintext_keys, get_key
        migrate_plaintext_keys({"anthropic": "", "openai": None})
        self.assertIsNone(get_key("anthropic"))
        self.assertIsNone(get_key("openai"))


class TestSecureStoreKeyringPath(unittest.TestCase):
    """Test secure_store when keyring IS available."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.keys_file = Path(self.tmpdir) / "api_keys.json"
        # Create a fake keyring module
        self.fake_store = {}
        self.fake_keyring = type("FakeKeyring", (), {
            "set_password": staticmethod(lambda s, k, v: self.fake_store.update({k: v})),
            "get_password": staticmethod(lambda s, k: self.fake_store.get(k)),
            "delete_password": staticmethod(lambda s, k: self.fake_store.pop(k, None)),
        })()
        self._patches = [
            patch("harness.secure_store.KEYS_FILE", self.keys_file),
            patch("harness.secure_store._keyring", self.fake_keyring),
            patch("harness.secure_store._keyring_checked", True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        if self.keys_file.exists():
            self.keys_file.unlink()
        os.rmdir(self.tmpdir)

    def test_keyring_preferred_over_file(self):
        from harness.secure_store import set_key, get_key
        set_key("anthropic", "sk-ant-keyring")
        # Key should be in keyring, not file
        self.assertIn("anthropic", self.fake_store)
        self.assertEqual(get_key("anthropic"), "sk-ant-keyring")

    def test_keyring_remove(self):
        from harness.secure_store import set_key, remove_key, get_key
        set_key("anthropic", "sk-ant")
        self.assertTrue(remove_key("anthropic"))
        self.assertIsNone(get_key("anthropic"))
        self.assertNotIn("anthropic", self.fake_store)


if __name__ == "__main__":
    unittest.main()
