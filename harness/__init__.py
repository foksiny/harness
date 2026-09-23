"""
Harness - The Ultimate Agentic AI Coding & Task Orchestration CLI.
"""

__version__ = "0.10.1"
__author__ = "Harness AI Team"
__version_info__ = (0, 10, 1)
__status__ = "beta"  # 0.10.1 (beta)

import os as _os
import sys as _sys


def _test_runner_detected() -> bool:
    """Detect unittest/pytest (or HARNESS_TESTING) so the suite runs hermetically.

    See ``harness/testing.py``. Kept inline and cheap: normal CLI startup only
    pays for two or three string comparisons.
    """
    env = _os.environ.get("HARNESS_TESTING")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    argv0 = (_sys.argv[0] if _sys.argv else "") or ""
    base = _os.path.basename(argv0).lower()
    if base in ("unittest", "pytest", "py.test"):
        return True
    if base == "__main__.py" and _os.path.basename(_os.path.dirname(argv0)).lower() in ("unittest", "pytest"):
        return True
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    return "unittest" in _sys.modules


# Hermetic testing: when a test runner imports harness, redirect all global
# state (HOME, cwd, OS keyring) into a throwaway sandbox BEFORE any submodule
# computes its import-time paths. Tests can then never read or write the real
# ~/.harness, the OS keychain, or the repo-local .harness/.
__hermetic_testing__ = False
if _test_runner_detected():
    from harness.testing import install as _install_hermetic
    __hermetic_testing__ = _install_hermetic()
