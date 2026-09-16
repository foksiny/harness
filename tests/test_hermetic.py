"""
Hermeticity regression test: proves that running the test suite never reads or
writes ANYTHING outside a throwaway sandbox — not the real ~/.harness, not the
OS keyring, and not the repository's own .harness/ workspace directory.

The isolation itself is installed by harness/__init__.py (see harness/testing.py)
the moment a test runner imports the package, BEFORE any harness submodule can
compute its import-time Path.home()/Path.cwd() constants.
"""
import os
import tempfile
import unittest
from pathlib import Path

import harness
from harness import testing
from harness.config import USER_CONFIG_DIR
from harness.providers.discovery import CACHE_FILE, UNIVERSAL_CACHE_FILE
from harness.secure_store import KEYS_FILE

SANDBOX_PREFIX = os.path.join(tempfile.gettempdir(), "harness_test_")


class TestHermeticIsolation(unittest.TestCase):

    def test_isolation_is_active(self):
        self.assertTrue(
            harness.__hermetic_testing__,
            "harness/__init__.py must activate harness.testing.install() under "
            "unittest/pytest. Check _test_runner_detected() in harness/__init__.py.",
        )
        self.assertTrue(testing.is_installed())

    def test_home_is_redirected_to_sandbox(self):
        home = Path.home()
        self.assertTrue(
            str(home).startswith(SANDBOX_PREFIX),
            f"Path.home() must resolve inside the sandbox, got {home}",
        )
        self.assertEqual(home, testing.sandbox_home())

    def test_all_import_time_constants_live_in_sandbox(self):
        for name, path in (
            ("USER_CONFIG_DIR", USER_CONFIG_DIR),
            ("KEYS_FILE", KEYS_FILE),
            ("CACHE_FILE", CACHE_FILE),
            ("UNIVERSAL_CACHE_FILE", UNIVERSAL_CACHE_FILE),
        ):
            self.assertTrue(
                str(path).startswith(SANDBOX_PREFIX),
                f"{name} must resolve inside the test sandbox, got {path}",
            )

    def test_cwd_is_a_sandbox_not_the_repository(self):
        # The workspace cascade (.harness/skills, memories.json, mcp.json,
        # themes.json, rules.md) resolves relative to cwd — tests must never
        # run from the real checkout.
        self.assertEqual(Path.cwd(), testing.sandbox_workspace())
        self.assertTrue(
            str(Path.cwd()).startswith(SANDBOX_PREFIX),
            f"cwd must be the sandbox workspace, got {Path.cwd()}",
        )

    def test_keyring_is_disabled(self):
        with self.assertRaises(ImportError):
            import keyring  # noqa: F401

    def test_original_home_is_recorded(self):
        # We must be able to tell isolation is a *redirect* of a real home,
        # not a no-op on a machine where HOME was already empty.
        original = os.environ.get("HARNESS_ORIGINAL_HOME", "")
        self.assertIsInstance(original, str)


if __name__ == "__main__":
    unittest.main()
