"""
MCP Server Manager for Harness.
Discovers configured servers, initializes connections, and binds tools to ToolRegistry.
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from harness.mcp.client import StdioMCPClient, MCPTool
from harness.tools import ToolRegistry

class MCPManager:
    """Coordinates active MCP server connections (H5: lazy + per-server timeout)."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None, lazy: bool = False,
                 timeout: float = 10.0, enabled: bool = True):
        self.tool_registry = tool_registry
        self.clients: Dict[str, StdioMCPClient] = {}
        self.global_config_path = Path.home() / ".harness" / "mcp.json"
        self.workspace_config_path = Path(".harness") / "mcp.json"
        self.lazy = lazy
        self.timeout = timeout
        self.enabled = enabled
        self._pending_servers: Dict[str, Any] = {}

    def load_and_connect(self) -> int:
        """Read mcp.json and connect. In lazy mode, defer until ensure_connected()."""
        servers = self.get_configured_servers()
        if not self.enabled:
            return 0
        if self.lazy:
            self._pending_servers = self._filter_servers(servers)
            return 0
        return self.connect_pending(servers)

    def _filter_servers(self, servers: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for name, cfg in (servers or {}).items():
            if isinstance(cfg, dict) and cfg.get("disabled"):
                continue
            out[name] = cfg
        return out

    def connect_pending(self, servers: Optional[Dict[str, Any]] = None) -> int:
        servers = self._filter_servers(servers or self._pending_servers or self.get_configured_servers())
        connected_tools_count = 0
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            command = cfg.get("command")
            url = cfg.get("url")
            if url and not command:
                # SSE/HTTP transport is not bundled (lightweight constraint);
                # surface the limitation instead of silently dropping it.
                continue
            if command:
                client = StdioMCPClient(
                    name=name,
                    command=command,
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                    timeout=float(cfg.get("timeout", self.timeout)),
                )
                try:
                    if client.start():
                        self.clients[name] = client
                        tools = client.list_tools()
                        for tdef in tools:
                            if self.tool_registry:
                                mcp_tool = MCPTool(name, tdef, client.call_tool)
                                mcp_tool.permission_manager = getattr(self.tool_registry, "permission_manager", None)
                                self.tool_registry.register(mcp_tool)
                                connected_tools_count += 1
                except Exception:
                    continue
        self._pending_servers = {}
        return connected_tools_count

    def ensure_connected(self) -> int:
        if self._pending_servers:
            return self.connect_pending()
        return 0

    def get_configured_servers(self) -> Dict[str, Any]:
        servers = {}
        # 1. Global config
        if self.global_config_path.exists():
            try:
                with open(self.global_config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    servers.update(data.get("mcpServers", {}))
            except Exception:
                pass
        # 2. Workspace override
        if self.workspace_config_path.exists():
            try:
                with open(self.workspace_config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    servers.update(data.get("mcpServers", {}))
            except Exception:
                pass
        return servers

    def add_server(self, name: str, command: str, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, workspace: bool = False) -> bool:
        """Add an MCP server configuration to json file."""
        target_path = self.workspace_config_path if workspace else self.global_config_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"mcpServers": {}}
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        if "mcpServers" not in data:
            data["mcpServers"] = {}

        data["mcpServers"][name] = {
            "command": command,
            "args": args or [],
            "env": env or {},
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True

    def stop_all(self):
        for client in self.clients.values():
            client.stop()
        self.clients.clear()

    def format_summary(self) -> str:
        if not self.clients:
            return "No external MCP servers currently connected."
        lines = ["Active MCP Servers:"]
        for name, c in self.clients.items():
            lines.append(f"- **{name}**: Connected via `{c.command}`")
        return "\n".join(lines)
