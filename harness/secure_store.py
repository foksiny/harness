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
    """Fix permissions on config.json and api_keys.json.

    On Unix: enforces 0o600 (owner read/write only).
    On Windows: restricts ACL to the current user only (removes inherited
    access and grants full control to the owner).
    """
    for path in (Path.home() / ".harness" / "config.json", KEYS_FILE):
        if not path.exists():
            continue
        if os.name == "nt":
            _restrict_windows_file(path)
        else:
            current = stat.S_IMODE(os.stat(str(path)).st_mode)
            if current & 0o077:
                path.chmod(KEYS_FILE_PERMS)


def _restrict_windows_file(path: Path) -> None:
    """Restrict a file's ACL to the current user on Windows.

    Uses ctypes to call the Windows Security API directly, avoiding the
    need for the pywin32 package.  On failure (e.g. unsupported FS), this
    is a silent no-op — the file remains accessible but we never crash.
    """
    try:
        import ctypes
        import ctypes.wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        # Get current process token
        token = ctypes.wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0008,  # TOKEN_QUERY
            ctypes.byref(token),
        ):
            return

        try:
            # Get token user (the file owner)
            token_user = ctypes.create_string_buffer(128)
            ret_len = ctypes.wintypes.DWORD()
            if not advapi32.GetTokenInformation(
                token, 1,  # TokenUser
                token_user, len(token_user),
                ctypes.byref(ret_len),
            ):
                return

            # Parse TOKEN_USER: first field is PSID
            psid = ctypes.cast(token_user, ctypes.POINTER(ctypes.c_void_p)).contents.value

            # Build a DACL with only the current user: (NULL ACL = no access for anyone)
            # Use SetNamedSecurityInfoW to set a DACL that only grants the owner full control
            import ctypes.wintypes as wt

            # SECURITY_DESCRIPTOR with owner, group, DACL
            # We'll use SetNamedSecurityInfoW with SET_ACCESS and PROTECTED_DACL
            SID_OWNER_ONLY = 0x00000001
            SE_FILE_OBJECT = 1

            # Build a minimal DACL: just owner with full control
            pSid = psid

            # EXPLICIT_ACCESS_W structure
            class EXPLICIT_ACCESS(ctypes.Structure):
                _fields_ = [
                    ("grfAccessPermissions", ctypes.c_ulong),
                    ("grfAccessMode", ctypes.c_ulong),  # SET_ACCESS = 0
                    ("grfInheritance", ctypes.c_ulong),   # NO_INHERITANCE = 0
                    ("Trustee", ctypes.c_byte * 48),       # TRUSTEE_W (padded)
                ]

            ea = EXPLICIT_ACCESS()
            ea.grfAccessPermissions = 0x001F01FF  # GENERIC_ALL
            ea.grfAccessMode = 0  # SET_ACCESS
            ea.grfInheritance = 0  # NO_INHERITANCE

            # Set TRUSTEE: fill the TrusteeName offset with the SID
            ctypes.memset(ctypes.addressof(ea.Trustee), 0, len(ea.Trustee))
            # TRUSTEE_W fields: pMultipleTrustee(8), MultipleTrusteeOperation(4), TrusteeForm(4)=TRUSTEE_IS_SID=1, TrusteeType(4)=TRUSTEE_USER=1, ptstrName(8)=SID
            ctypes.memmove(ctypes.addressof(ea.Trustee) + 20, ctypes.addressof(ctypes.c_void_p(psid)), ctypes.sizeof(ctypes.c_void_p))

            # Build DACL
            pAcl = ctypes.c_void_p()
            acl_ret = advapi32.SetEntriesInAclW(1, ctypes.byref(ea), None, ctypes.byref(pAcl))
            if acl_ret != 0 or not pAcl:
                return

            try:
                str_path = str(path)
                result = advapi32.SetNamedSecurityInfoW(
                    str_path,
                    SE_FILE_OBJECT,
                    0x00000004,  # DACL_SECURITY_INFORMATION
                    None, None,  # owner, group
                    pAcl,        # DACL
                    None,        # SACL
                )
            finally:
                advapi32.LocalFree(pAcl)
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        pass  # Silent fallback: file remains as-is


def validate_keys_store() -> dict:
    """Validate the keys store: check file permissions, detect plaintext leaks,
    and report any issues. Returns a dict with status info."""
    result = {"ok": True, "issues": [], "migrated": 0}

    # Check api_keys.json permissions
    if KEYS_FILE.exists():
        current = stat.S_IMODE(os.stat(str(KEYS_FILE)).st_mode)
        if current & 0o077:
            result["issues"].append(f"api_keys.json has loose permissions: {oct(current)}")
            KEYS_FILE.chmod(KEYS_FILE_PERMS)
            result["issues"].append("Fixed permissions to 0o600")

    # Check for plaintext keys in config.json that should be in secure store
    config_path = KEYS_FILE.parent / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            api_keys = data.get("api_keys", {})
            discord_token = data.get("discord_bot_token", "")
            if api_keys:
                for prov, key in api_keys.items():
                    if key and key.strip():
                        # Migrate plaintext key to secure store
                        existing = get_key(prov)
                        if not existing:
                            set_key(prov, key.strip())
                            result["migrated"] += 1
                result["issues"].append(f"Found {len(api_keys)} plaintext keys in config.json (migrated to secure store)")
            if discord_token and discord_token.strip():
                existing = get_key("discord")
                if not existing:
                    set_key("discord", discord_token.strip())
                    result["migrated"] += 1
                    result["issues"].append("Migrated plaintext discord token to secure store")
        except Exception:
            pass

    # Check if keyring is functional
    kr = _get_keyring()
    if kr is None:
        result["issues"].append("OS keyring not available; using file-based secure store")

    return result
