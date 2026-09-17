"""
Server status tool — shows the Harness API server info.

The API server is a localhost (or 0.0.0.0 for VPS) HTTP server that lets
external clients send prompts and receive responses. No inter-instance mesh.
"""
from harness.tools.base import Tool

class MeshStatusTool(Tool):
    name = "mesh_status"
    description = (
        "Show this instance's API server status: bound host/port, workspace, "
        "model/provider, and external API endpoint (POST http://host:port/api/prompt). "
        "Use this to confirm the server is up and to share the endpoint for VPS/server usage."
    )
    action_type = "server"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, **kwargs) -> str:
        try:
            from harness.mesh.server import get_server
            srv = get_server()
            if srv is None:
                # Try get_mesh for backward compat
                try:
                    from harness.mesh.server import get_mesh
                    srv = get_mesh()
                except Exception:
                    srv = None
            if srv is None:
                return "API server not active (no server bound). It starts automatically when harness is opened if server_enabled=true. Check config server_host/server_port."
            status = srv.get_status()
            lines = [
                "=== HARNESS API SERVER STATUS ===",
                f"  Address: http://{status.get('host')}:{status.get('port')}  (base {status.get('base')} => block {status.get('base')}00-{status.get('base')}99)",
                f"  Workspace: {status.get('workspace')}",
                f"  User: {status.get('username')}@{status.get('hostname')}  pid {status.get('pid')}",
                f"  Uptime: {status.get('uptime')}s  busy={status.get('is_busy')}  provider={status.get('provider')}/{status.get('model')}",
                f"  Token required: {status.get('token_required')}",
                f"  API: POST http://{status.get('host')}:{status.get('port')}/api/prompt  {{\"prompt\": \"...\", \"session_id\": \"...\", \"stream\": false}}",
                f"  Health: GET http://{status.get('host')}:{status.get('port')}/health",
                f"  Status: GET http://{status.get('host')}:{status.get('port')}/api/status",
            ]
            if status.get('host') == "127.0.0.1":
                lines.append("  Note: bound to 127.0.0.1 (localhost only). For VPS use, set server_host=0.0.0.0 and server_port, and set HARNESS_API_TOKEN.")
            return "\n".join(lines)
        except Exception as ex:
            return f"Error reading server status: {ex}"

# Backward compat alias
ServerStatusTool = MeshStatusTool

# --- Removed inter-instance mesh tools (stubs for backward compat) ---
class _RemovedMeshTool(Tool):
    is_read_only = True
    parameters = {"type": "object", "properties": {}, "required": []}
    def execute(self, **kwargs) -> str:
        return (
            f"Tool '{self.name}' has been removed — inter-instance mesh collaboration was removed. "
            "The network now exposes a simple HTTP API for servers/VPS: "
            "POST http://host:port/api/prompt {\"prompt\": \"...\"} -> {\"response\": \"...\"}. "
            "See mesh_status / server status for the endpoint, and use `harness serve --host 0.0.0.0 --port 8000` for VPS mode."
        )

class MeshListPeersTool(_RemovedMeshTool):
    name = "mesh_list_peers"
    description = "REMOVED: use the HTTP API at /api/status and /api/prompt instead."
    action_type = "server"

class MeshSendMessageTool(_RemovedMeshTool):
    name = "mesh_send_message"
    description = "REMOVED: use POST /api/prompt instead."
    action_type = "server"

class MeshBroadcastTool(_RemovedMeshTool):
    name = "mesh_broadcast"
    description = "REMOVED: use POST /api/prompt instead."
    action_type = "server"

class MeshReadMessagesTool(_RemovedMeshTool):
    name = "mesh_read_messages"
    description = "REMOVED: use POST /api/prompt instead."
    action_type = "server"
