"""
Tests for Model Context Protocol (MCP) Integration.
"""
import unittest
import tempfile
import json
from pathlib import Path
from harness.mcp.manager import MCPManager
from harness.tools import ToolRegistry

class TestMCP(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mcp_path = Path(self.temp_dir.name) / "mcp.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mcp_config_persistence(self):
        registry = ToolRegistry()
        manager = MCPManager(registry)
        manager.global_config_path = self.mcp_path

        # Add an MCP server definition
        success = manager.add_server(
            name="sqlite_server",
            command="python3",
            args=["-m", "sqlite_mcp"],
            env={"SQLITE_PATH": "test.db"},
            workspace=False,
        )
        self.assertTrue(success)
        self.assertTrue(self.mcp_path.exists())

        # Load back
        servers = manager.get_configured_servers()
        self.assertIn("sqlite_server", servers)
        self.assertEqual(servers["sqlite_server"]["command"], "python3")
        self.assertEqual(servers["sqlite_server"]["args"], ["-m", "sqlite_mcp"])

    def test_lazy_connect_does_not_start(self):
        from unittest.mock import MagicMock
        registry = ToolRegistry()
        manager = MCPManager(registry, lazy=True)
        manager.global_config_path = self.mcp_path
        manager.workspace_config_path = Path(self.temp_dir.name) / "nope.json"
        manager.add_server("svc", "python3", ["-m", "x"], workspace=False)
        # Lazy load records pending but starts nothing.
        self.assertEqual(manager.load_and_connect(), 0)
        self.assertEqual(manager.clients, {})
        self.assertIn("svc", manager._pending_servers)

    def test_lazy_disabled_servers_skipped(self):
        registry = ToolRegistry()
        manager = MCPManager(registry, lazy=True)
        manager.global_config_path = self.mcp_path
        manager.workspace_config_path = Path(self.temp_dir.name) / "nope.json"
        self.mcp_path.write_text(json.dumps({"mcpServers": {"on": {"command": "python3"}, "off": {"command": "python3", "disabled": True}}}))
        servers = manager.get_configured_servers()
        filtered = manager._filter_servers(servers)
        self.assertIn("on", filtered)
        self.assertNotIn("off", filtered)

    def test_mcp_toolcaps_output(self):
        from harness.mcp.client import MCPTool
        t = MCPTool("s", {"name": "x", "inputSchema": {"type": "object"}}, lambda n, a: "A" * 70000)
        out = t.execute()
        self.assertLess(len(out), 64001)
        self.assertIn("middle elided", out)

if __name__ == "__main__":
    unittest.main()
