"""
Harness API Server — localhost/VPS HTTP API for remote control.

Each Harness instance (TUI or `harness serve`) binds to a deterministic port:

    port = base*100 + 00..99

where base (100-555) is hash(workspace + username) %456+100. The API lets
external clients (curl, VPS, other services) send prompts and receive responses.

Endpoints:
  GET  /health, /api/status
  POST /api/prompt  {prompt, session_id?, stream?}
  GET  /api/sessions
  POST /api/stop
"""
from harness.mesh.port import compute_base_port, find_free_port, MESH_DIR
from harness.mesh.server import MeshServer, start_mesh, stop_mesh, get_mesh, start_server, stop_server, get_server
from harness.mesh.registry import list_instances, register_instance, deregister_instance
# keep find_peers for backward compat but not used
try:
    from harness.mesh.registry import find_peers
except ImportError:
    find_peers = None

__all__ = [
    "compute_base_port", "find_free_port", "MESH_DIR",
    "MeshServer", "start_mesh", "stop_mesh", "get_mesh",
    "start_server", "stop_server", "get_server",
    "list_instances", "register_instance", "deregister_instance",
]
