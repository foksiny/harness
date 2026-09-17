"""
File-based registry for Harness mesh instances.

Stored at ~/.harness/mesh/registry.json
Format: { "instances": [ {port, workspace, username, pid, started_at, hostname, display_name} ] }

Thread- and process-safe via atomic write (tmp + rename) plus advisory
file locking where available (fcntl). Falls back to no-lock on platforms
without fcntl (Windows).
"""
import json
import os
import time
import getpass
import socket
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import fcntl  # type: ignore
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

REGISTRY_PATH = Path.home() / ".harness" / "mesh" / "registry.json"

def _ensure_dir():
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

def _lock_file(f):
    if HAS_FCNTL:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass

def _unlock_file(f):
    if HAS_FCNTL:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False

def load_registry() -> Dict[str, Any]:
    _ensure_dir()
    if not REGISTRY_PATH.exists():
        return {"instances": []}
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            _lock_file(f)
            try:
                data = json.load(f)
                if not isinstance(data, dict) or "instances" not in data:
                    return {"instances": []}
                return data
            finally:
                _unlock_file(f)
    except Exception:
        return {"instances": []}

def save_registry(data: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _lock_file(f)
        try:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock_file(f)
    try:
        tmp.replace(REGISTRY_PATH)
        # ensure perms 600
        try:
            os.chmod(REGISTRY_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass

def prune_dead_instances(instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove entries whose pid is dead or whose port is not connectable and pid dead.

    We keep entries whose pid is still alive even if port not yet listening
    (race on startup).
    """
    from harness.mesh.port import is_port_connectable
    alive = []
    now = time.time()
    for inst in instances:
        pid = inst.get("pid")
        port = inst.get("port")
        # Expire entries older than 7 days even if pid somehow recycled
        started = inst.get("started_at", 0)
        if now - started > 7 * 24 * 3600:
            continue
        if isinstance(pid, int) and _pid_alive(pid):
            # pid alive => keep, even if port not yet bound (startup race)
            alive.append(inst)
        else:
            # pid dead => check if port still connectable (maybe pid reused?)
            if isinstance(port, int) and is_port_connectable(port, timeout=0.2):
                alive.append(inst)
            else:
                # dead
                continue
    return alive

def register_instance(port: int, workspace: str | None = None, username: str | None = None) -> Dict[str, Any]:
    """Register the current instance. Returns the entry dict."""
    _ensure_dir()
    ws = workspace or os.getcwd()
    try:
        ws = str(Path(ws).resolve())
    except Exception:
        ws = os.path.abspath(ws)
    user = (username or getpass.getuser() or "harness").strip()
    try:
        host = socket.gethostname()
    except Exception:
        host = "localhost"
    entry = {
        "port": int(port),
        "workspace": ws,
        "username": user,
        "pid": os.getpid(),
        "started_at": time.time(),
        "hostname": host,
        "display_name": f"{user}@{host}:{ws}#{port}",
    }
    data = load_registry()
    instances = data.get("instances", [])
    # prune first
    instances = prune_dead_instances(instances)
    # remove any existing entry with same port or same pid
    instances = [i for i in instances if i.get("port") != port and i.get("pid") != os.getpid()]
    instances.append(entry)
    data["instances"] = instances
    save_registry(data)
    return entry

def deregister_instance(port: int) -> None:
    """Remove the entry for a given port (and any dead entries)."""
    data = load_registry()
    instances = data.get("instances", [])
    instances = [i for i in instances if i.get("port") != port]
    instances = prune_dead_instances(instances)
    data["instances"] = instances
    save_registry(data)

def list_instances(prune: bool = True) -> List[Dict[str, Any]]:
    data = load_registry()
    instances = data.get("instances", [])
    if prune:
        pruned = prune_dead_instances(instances)
        if len(pruned) != len(instances):
            data["instances"] = pruned
            save_registry(data)
        return pruned
    return instances

def find_peers(workspace: str | None = None, username: str | None = None, exclude_port: Optional[int] = None) -> List[Dict[str, Any]]:
    """Find live peers with same workspace + username (optionally exclude own port)."""
    ws = workspace or os.getcwd()
    try:
        ws = str(Path(ws).resolve())
    except Exception:
        ws = os.path.abspath(ws)
    user = (username or getpass.getuser() or "harness").strip()
    peers = []
    for inst in list_instances(prune=True):
        if inst.get("workspace") != ws:
            continue
        if inst.get("username") != user:
            continue
        if exclude_port is not None and inst.get("port") == exclude_port:
            continue
        peers.append(inst)
    # Sort by started_at
    peers.sort(key=lambda x: x.get("started_at", 0))
    return peers
