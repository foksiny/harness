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
    """Coordinates active MCP server connections."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self.tool_registry = tool_registry
        self.clients: Dict[str, StdioMCPClient] = {}
        self.global_config_path = Path.home() / ".harness" / "mcp.json"
        self.workspace_config_path = Path(".harness") / "mcp.json"

    def load_and_connect(self) -> int:
        """Read mcp.json from global and workspace locations and connect."""
        servers = self.get_configured_servers()
        connected_tools_count = 0

        for name, cfg in servers.items():
            command = cfg.get("command")
            if command:
                client = StdioMCPClient(
                    name=name,
                    command=command,
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                )
                if client.start():
                    self.clients[name] = client
                    tools = client.list_tools()
                    for tdef in tools:
                        if self.tool_registry:
                            mcp_tool = MCPTool(name, tdef, client.call_tool)
                            self.tool_registry.register(mcp_tool)
                            connected_tools_count += 1
        return connected_tools_count

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
