"""
Model Context Protocol (MCP) Client for Harness.
Implements JSON-RPC 2.0 client transport over stdio processes and SSE/HTTP endpoints.
"""
import os
import json
import subprocess
import threading
import time
from typing import Dict, Any, List, Optional
from harness.tools.base import Tool

class MCPTool(Tool):
    """Dynamic tool wrapper for an MCP server-provided tool (H5: permission + cap)."""

    permission_manager = None  # set by MCPManager

    def __init__(self, server_name: str, tool_def: Dict[str, Any], invoker):
        self.server_name = server_name
        self.raw_name = tool_def.get("name", "unnamed_tool")
        self.name = f"mcp_{server_name}_{self.raw_name}"
        self.description = f"[{server_name}] " + tool_def.get("description", "No description provided.")
        self.parameters = tool_def.get("inputSchema", {"type": "object", "properties": {}})
        self.action_type = "mcp"
        self.is_read_only = False
        self._invoker = invoker

    def execute(self, **kwargs) -> Any:
        if self.permission_manager is not None:
            try:
                from harness.core.permissions import PermissionLevel as _PL
                if self.permission_manager.level == _PL.SECURE:
                    ok = self.permission_manager.check_permission(
                        "mcp", {"tool": self.name, "summary": f"MCP {self.name}"}
                    )
                    if not ok:
                        return f"Error: {self.name} rejected by security policy."
            except Exception:
                pass
        res = self._invoker(self.raw_name, kwargs)
        # H6: cap MCP output at the hard ceiling.
        if isinstance(res, str) and len(res) > 64000:
            res = res[:32000] + "\n...[middle elided]\n" + res[-31000:]
        return res

class StdioMCPClient:
    """JSON-RPC 2.0 transport over subprocess stdio (H5: timeout + drained stderr)."""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, timeout: float = 10.0):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout
        self.process: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._pending_requests: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.is_running = False
        self._stderr_thread = None

    def start(self) -> bool:
        cmd = [self.command] + self.args
        merged_env = os.environ.copy()
        merged_env.update(self.env)

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=merged_env,
            )
            self.is_running = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
            # Perform MCP initialization handshake
            return self._initialize()
        except Exception as e:
            self.is_running = False
            return False

    def _drain_stderr(self) -> None:
        """H5: drain stderr so a chatty server cannot deadlock on a full pipe."""
        try:
            while self.is_running and self.process and self.process.stderr:
                line = self.process.stderr.readline()
                if not line:
                    break
        except Exception:
            pass

    def _read_loop(self):
        while self.is_running and self.process and self.process.stdout:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if "id" in data:
                    req_id = data["id"]
                    with self._lock:
                        self._pending_requests[req_id] = data
            except Exception:
                break
        self.is_running = False

    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        if not self.is_running or not self.process or not self.process.stdin:
            return None

        with self._lock:
            self._req_id += 1
            curr_id = self._req_id

        req = {
            "jsonrpc": "2.0",
            "id": curr_id,
            "method": method,
            "params": params or {},
        }

        try:
            self.process.stdin.write(json.dumps(req) + "\n")
            self.process.stdin.flush()
        except Exception:
            return None

        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if curr_id in self._pending_requests:
                    return self._pending_requests.pop(curr_id)
            time.sleep(0.02)
        # Timeout: drop any late response so _pending_requests never leaks.
        with self._lock:
            self._pending_requests.pop(curr_id, None)
        return None

    def _initialize(self) -> bool:
        from harness import __version__ as _ver
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Harness", "version": _ver},
        }
        res = self._send_request("initialize", init_params, timeout=5.0)
        if res and "result" in res:
            # Send initialized notification
            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            try:
                if self.process and self.process.stdin:
                    self.process.stdin.write(json.dumps(notif) + "\n")
                    self.process.stdin.flush()
            except Exception:
                pass
            return True
        return False

    def list_tools(self) -> List[Dict[str, Any]]:
        res = self._send_request("tools/list", timeout=5.0)
        if res and "result" in res:
            return res["result"].get("tools", [])
        return []

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        params = {"name": tool_name, "arguments": arguments}
        res = self._send_request("tools/call", params, timeout=30.0)
        if not res:
            return f"Error: MCP call to '{tool_name}' timed out."
        if "error" in res:
            return f"MCP Error: {res['error'].get('message', str(res['error']))}"
        result = res.get("result", {})
        content_blocks = result.get("content", [])
        texts = []
        for block in content_blocks:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts) if texts else json.dumps(result)

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                pass
            try:
                # Kill fallback: terminate() alone leaves zombies when the
                # child ignores SIGTERM; kill + wait reaps it.
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
            except Exception:
                pass
