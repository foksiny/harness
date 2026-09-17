"""
Mailbox for inter-instance messages.

Each mesh instance owns a file  ~/.harness/mesh/mailbox_<port>.jsonl
where inbound messages are appended as JSON lines.  The file is also
mirrored in-memory for fast reads.

Messages are addressed via the mesh HTTP API, not directly via the file,
so every write goes through the server's thread and is append-only.

Tools (mesh_read_messages) read from this file, and the server appends
via append_message().
"""
import json
import time
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from threading import Lock

MESH_DIR = Path.home() / ".harness" / "mesh"

def mailbox_path(port: int) -> Path:
    return MESH_DIR / f"mailbox_{int(port)}.jsonl"

_lock = Lock()
_mem_mailbox: Dict[int, List[Dict[str, Any]]] = {}
_next_id: Dict[int, int] = {}

def _ensure_dir():
    MESH_DIR.mkdir(parents=True, exist_ok=True)

def _load_file(port: int) -> List[Dict[str, Any]]:
    p = mailbox_path(port)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []

def append_message(port: int, sender_port: Optional[int], sender_name: str, message: str, msg_type: str = "chat") -> Dict[str, Any]:
    """Append a message to the mailbox for `port`. Returns the stored record."""
    _ensure_dir()
    with _lock:
        # Ensure in-mem is synced with file at least once
        if port not in _mem_mailbox:
            existing = _load_file(port)
            _mem_mailbox[port] = existing
            max_id = max((m.get("id", 0) for m in existing), default=0)
            _next_id[port] = max_id + 1
        nid = _next_id.get(port, 1)
        _next_id[port] = nid + 1
        rec = {
            "id": nid,
            "sender_port": sender_port,
            "sender_name": sender_name,
            "message": str(message),
            "type": msg_type,
            "timestamp": time.time(),
            "timestamp_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }
        _mem_mailbox[port].append(rec)
        # Append to file
        try:
            with open(mailbox_path(port), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            try:
                os.chmod(mailbox_path(port), 0o600)
            except Exception:
                pass
        except Exception:
            pass
        return rec

def read_messages(port: int, since_id: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    """Read messages for `port` with id > since_id."""
    with _lock:
        if port not in _mem_mailbox:
            existing = _load_file(port)
            _mem_mailbox[port] = existing
            max_id = max((m.get("id", 0) for m in existing), default=0)
            _next_id[port] = max_id + 1
        msgs = [m for m in _mem_mailbox[port] if m.get("id", 0) > since_id]
        if limit:
            msgs = msgs[-limit:] if len(msgs) > limit else msgs
        return list(msgs)

def clear_mailbox(port: int) -> None:
    with _lock:
        _mem_mailbox[port] = []
        _next_id[port] = 1
        try:
            p = mailbox_path(port)
            if p.exists():
                p.unlink()
        except Exception:
            pass

def count_unread(port: int, since_id: int = 0) -> int:
    return len(read_messages(port, since_id=since_id, limit=10000))
