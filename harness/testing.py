"""
Hermetic test isolation for Harness.

``harness/__init__.py`` calls :func:`install` when it detects a test runner
(unittest/pytest discovery, a directly-run ``test_*.py`` script, or
``HARNESS_TESTING=1``) BEFORE any harness submodule is imported. Everything
harness derives from the user's environment at import time or call time then
resolves inside a throwaway sandbox:

- ``$HOME`` / ``$USERPROFILE`` / XDG dirs -> temp dir. All ``Path.home()`` uses
  land there: config.json, api_keys.json, skills/, memories.json, mcp.json,
  themes.json, models_cache.json, universal_models.json, sessions/,
  checkpoints/, sync_bus.jsonl, screenshots/, sandbox/.
- process cwd -> temp workspace, so the repo-local ``.harness/`` workspace
  cascade (skills, memories, mcp, themes, rules.md) is never read or written.
- OS keyring -> disabled (``import keyring`` raises ImportError), so the secure
  store falls back to the sandboxed ``api_keys.json`` file.

The sandbox is removed at interpreter exit. Override with ``HARNESS_TESTING=0``
(never isolate) or ``HARNESS_TESTING=1`` (always isolate). Normal CLI startup
pays nothing: detection is a couple of string checks in ``harness/__init__.py``.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

_installed = False
_home: Optional[Path] = None
_workspace: Optional[Path] = None
_original_cwd: str = ""


def install() -> bool:
    """Redirect HOME/XDG/cwd into a throwaway sandbox and disable the OS keyring.

    Idempotent. Returns True when isolation is active for this process.
    """
    global _installed, _home, _workspace
    if _installed:
        return True
    if os.environ.get("HARNESS_TESTING", "").strip().lower() in ("0", "false", "no", "off"):
        return False

    _home = Path(tempfile.mkdtemp(prefix="harness_test_home_"))
    _workspace = Path(tempfile.mkdtemp(prefix="harness_test_ws_"))

    os.environ["HARNESS_ORIGINAL_HOME"] = os.environ.get("HOME", "")
    os.environ["HOME"] = str(_home)
    os.environ["USERPROFILE"] = str(_home)  # Path.home() on Windows
    os.environ["XDG_CONFIG_HOME"] = str(_home / "config")
    os.environ["XDG_CACHE_HOME"] = str(_home / "cache")
    os.environ["XDG_DATA_HOME"] = str(_home / "data")

    # Kill the OS keyring: `import keyring` raises ImportError from here on, so
    # secure_store transparently falls back to the sandboxed api_keys.json.
    sys.modules["keyring"] = None

    # Never read or write the repository's own .harness/ workspace cascade.
    _original_cwd = os.getcwd()
    os.chdir(_workspace)

    _installed = True
    atexit.register(_cleanup)
    return True


def _cleanup() -> None:
    global _installed
    try:
        if _original_cwd and os.path.isdir(_original_cwd):
            os.chdir(_original_cwd)
    except Exception:
        pass
    for d in (_home, _workspace):
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)
    _installed = False


def is_installed() -> bool:
    """True when the hermetic sandbox is active for this process."""
    return _installed


def sandbox_home() -> Optional[Path]:
    """The throwaway directory acting as $HOME during tests."""
    return _home


def sandbox_workspace() -> Optional[Path]:
    """The throwaway directory acting as the process cwd during tests."""
    return _workspace
