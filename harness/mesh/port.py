"""
Port allocation for Harness Mesh.

Spec:  <first 3 digits based on workspace+username>  <next 2 digits 00-99>
Every instance gets a unique port in the range base*100 … base*100+99
(base 100-555 = hash(workspace+user) %456+100 => ports 10000..55599).
The base is deterministic from username+workspace, so instances for the
same user+workspace naturally cluster in the same 100-port block, making
peer discovery cheap (scan that block).
"""
import hashlib
import getpass
import os
import socket
from pathlib import Path

MESH_DIR = Path.home() / ".harness" / "mesh"
MESH_DIR.mkdir(parents=True, exist_ok=True)

def compute_base_port(workspace: str | None = None, username: str | None = None) -> int:
    """Deterministic 3-digit base (100-555) from workspace + username."""
    ws = (workspace or os.getcwd()).strip()
    # Resolve to absolute, canonical form so /a/b and /a/b/ map together.
    try:
        ws = str(Path(ws).resolve())
    except Exception:
        ws = os.path.abspath(ws)
    user = (username or getpass.getuser() or "harness").strip().lower()
    # Include hostname to reduce cross-machine collisions while keeping
    # workspace+user as the primary key for local discovery.
    try:
        host = socket.gethostname().strip().lower()
    except Exception:
        host = "localhost"
    # Stable hash: username + workspace is the cluster key, hostname is a
    # secondary salt that keeps the base within the same 100-block for same
    # user+workspace on same host, but differs across hosts (no matter).
    key = f"{user}:{ws}"
    # We deliberately do NOT include host in the base hash so that same
    # user+workspace on same machine maps to same block; host only affects
    # discovery filtering, not port math.
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # Use first 8 hex chars => 32 bits.
    n = int(h[:8], 16)
    # Keep within valid TCP range when multiplied by 100: 100..555 => ports 10000..55599
    # (base*100+99 must stay <= 65535). 456 values => -555 inclusive.
    base = (n % 456) + 100  # 100..555
    return base

def port_range_for_base(base: int):
    """Yield 100 ports for a base: base*100 .. base*100+99 (100..555 => 10000..55599)."""
    start = base * 100
    for i in range(100):
        p = start + i
        if 1024 <= p <= 65535:
            yield p

def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is free on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False

def is_port_connectable(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """Check if something is listening on port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except Exception:
            return False

def find_free_port(workspace: str | None = None, username: str | None = None, host: str = "127.0.0.1") -> tuple[int, int]:
    """Find first free port in the workspace+user block.

    Returns (port, base). Raises RuntimeError if no free slot in block.
    Attempts fallback scanning outside block if block exhausted.
    """
    base = compute_base_port(workspace, username)
    for p in port_range_for_base(base):
        if is_port_free(p, host):
            return p, base
    # Fallback: try random high ports outside block (still ensure free)
    for p in range(41000, 42000):
        if is_port_free(p, host):
            return p, base
    raise RuntimeError(f"No free port found for mesh (base {base}). All 100 slots busy.")
