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

if __name__ == "__main__":
    unittest.main()
