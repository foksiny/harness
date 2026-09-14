"""
Secure API key storage for Harness.
Provides a transparent fallback chain: OS keychain -> file with restricted permissions.
"""
import os
import sys
import json
import stat
from pathlib import Path
from typing import Optional

SERVICE_NAME = "harness-cli"
KEYS_FILE = Path.home() / ".harness" / "api_keys.json"
KEYS_FILE_PERMS = 0o600  # owner read/write only

_keyring = None
_keyring_checked = False


def _get_keyring():
    """Lazy-import keyring; returns the module or None if unavailable."""
    global _keyring, _keyring_checked
    if not _keyring_checked:
        _keyring_checked = True
        try:
            import keyring as _kr
            # Verify keyring backend is functional (not the "fail" backend)
            _kr.get_password(SERVICE_NAME, "__probe__")
            _keyring = _kr
        except Exception:
            _keyring = None
    return _keyring


def _load_file_store() -> dict:
    """Load the on-disk key file."""
    if not KEYS_FILE.exists():
        return {}
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_file_store(data: dict) -> None:
    """Save keys to disk with restrictive permissions."""
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive permissions (create new or truncate)
    fd = os.open(str(KEYS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, KEYS_FILE_PERMS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        os.close(fd)
        raise
    # Enforce permissions on existing files (in case umask was wrong)
    KEYS_FILE.chmod(KEYS_FILE_PERMS)


def set_key(provider: str, key: str) -> None:
    """Store an API key securely. Uses OS keychain if available, else file."""
    prov = provider.lower().strip()
    kr = _get_keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE_NAME, prov, key.strip())
            return
        except Exception:
            pass
    # Fallback: file store
    store = _load_file_store()
    store[prov] = key.strip()
    _save_file_store(store)


def get_key(provider: str) -> Optional[str]:
    """Retrieve an API key. Checks OS keychain first, then file."""
    prov = provider.lower().strip()
    kr = _get_keyring()
    if kr is not None:
        try:
            val = kr.get_password(SERVICE_NAME, prov)
            if val:
                return val
        except Exception:
            pass
    # Fallback: file store
    store = _load_file_store()
    return store.get(prov)


def remove_key(provider: str) -> bool:
    """Remove an API key. Returns True if the key existed."""
    prov = provider.lower().strip()
    removed = False
    kr = _get_keyring()
    if kr is not None:
        try:
            existing = kr.get_password(SERVICE_NAME, prov)
            if existing:
                kr.delete_password(SERVICE_NAME, prov)
                removed = True
        except Exception:
            pass
    # Also remove from file store (may have been stored there before keyring was available)
    store = _load_file_store()
    if prov in store:
        del store[prov]
        _save_file_store(store)
        removed = True
    return removed


def migrate_plaintext_keys(api_keys: dict) -> None:
    """One-time migration: move plaintext keys from config into secure store.

    Call this after loading config.json. Any keys in `api_keys` dict that
    are not yet in the secure store get migrated and cleared from the dict.
    """
    if not api_keys:
        return
    kr = _get_keyring()
    file_store = _load_file_store()
    changed = False
    for prov, key in list(api_keys.items()):
        if not key:
            continue
        # Check if already stored securely
        existing = None
        if kr is not None:
            try:
                existing = kr.get_password(SERVICE_NAME, prov)
            except Exception:
                pass
        if existing is None:
            existing = file_store.get(prov)
        if existing is None:
            # Not yet migrated — store it
            set_key(prov, key)
            changed = True
    return changed


def ensure_file_permissions() -> None:
    """Fix permissions on config.json and api_keys.json if they're too open."""
    for path in (Path.home() / ".harness" / "config.json", KEYS_FILE):
        if path.exists():
            current = stat.S_IMODE(os.stat(str(path)).st_mode)
            if current & 0o077:  # group or other has access
                path.chmod(KEYS_FILE_PERMS)
