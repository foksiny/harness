"""
Harness API Server — localhost HTTP API for remote control.

Each Harness instance (TUI or `harness serve`) binds to 127.0.0.1 (or 0.0.0.0 for VPS)
on a deterministic port:

    port = base*100 + 00..99   where base = hash(workspace+user) %456+100

The server exposes a simple JSON API to send prompts to the agent and receive
responses, suitable for servers/VPS usage. No inter-instance peer mesh.

Endpoints (all localhost by default, optional token auth):
  GET  /health              -> {"status":"ok"}
  GET  /api/status          -> agent/workspace status
  POST /api/prompt          -> {prompt, session_id?, stream?} -> {response, session_id}
  POST /prompt              -> alias for /api/prompt (back-compat)
  GET  /api/sessions        -> list sessions
  POST /api/stop            -> request_stop
  GET  /status              -> alias for /api/status

The server runs in a daemon thread so it never blocks the TUI.
"""
import json
import time
import threading
import getpass
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from typing import Optional, Dict, Any

from harness.mesh.port import compute_base_port, find_free_port
from harness.mesh.registry import register_instance, deregister_instance

# Global singleton
_mesh_singleton: Optional["MeshServer"] = None
_mesh_lock = threading.Lock()

def _get_server_token(config=None) -> Optional[str]:
    # Config takes precedence, then env
    if config is not None:
        tok = getattr(config, "server_token", None) or getattr(config, "api_token", None)
        if tok:
            return tok.strip()
    env_tok = os.environ.get("HARNESS_API_TOKEN") or os.environ.get("HARNESS_SERVER_TOKEN")
    if env_tok:
        return env_tok.strip()
    return None

class MeshHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def _check_auth(self) -> bool:
        server: "MeshServer" = self.server.mesh_server  # type: ignore
        token = server.server_token or _get_server_token(getattr(server, "_config", None))
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            got = auth[7:].strip()
            if got == token:
                return True
        # Also allow ?token= query
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("token", [None])[0] == token:
            return True
        return False

    def _json(self, obj: Any, status: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._set_cors()
        self.end_headers()

    def _read_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                raw = self.rfile.read(length)
                if raw:
                    return json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            pass
        return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        server: "MeshServer" = self.server.mesh_server  # type: ignore

        # Public endpoints (no auth)
        if path in ("/", "/health"):
            from harness import __version__ as _ver
            return self._json({"status": "ok", "version": _ver, "port": server.port}, 200)

        if not self._check_auth():
            return self._json({"error": "unauthorized, missing or invalid token"}, 401)

        if path in ("/status", "/api/status"):
            return self._json(server.get_status(), 200)

        if path == "/api/sessions":
            try:
                agent = server._get_agent()
                if agent is None:
                    return self._json({"sessions": []}, 200)
                # Sessions are global but associated to a workspace — scope the
                # listing to this server's workspace.
                sessions = agent.session_manager.list_all(workspace=server.workspace)
                return self._json({"sessions": sessions[:20]}, 200)
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)

        if path == "/api/tools":
            try:
                agent = server._get_agent()
                if agent is None:
                    return self._json({"tools": []}, 200)
                schemas = agent.tool_registry.get_openai_schemas()
                names = [s.get("function", {}).get("name") for s in schemas]
                return self._json({"tools": names}, 200)
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)

        return self._json({"error": f"Unknown GET {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        server: "MeshServer" = self.server.mesh_server  # type: ignore
        body = self._read_body()

        if not self._check_auth():
            return self._json({"error": "unauthorized"}, 401)

        if path in ("/prompt", "/api/prompt", "/api/chat", "/v1/chat/completions"):
            prompt = str(body.get("prompt") or body.get("message") or body.get("text") or body.get("input") or "").strip()
            # Also handle OpenAI-style messages array
            if not prompt and isinstance(body.get("messages"), list):
                # Take last user message
                for m in reversed(body["messages"]):
                    if m.get("role") == "user":
                        c = m.get("content")
                        if isinstance(c, str):
                            prompt = c.strip()
                            break
                        elif isinstance(c, list):
                            # content blocks
                            for b in c:
                                if b.get("type") == "text":
                                    prompt = b.get("text", "").strip()
                                    break
                            break
                if not prompt:
                    # Fallback to last message content
                    last = body["messages"][-1] if body["messages"] else {}
                    prompt = str(last.get("content") or "").strip()
            if not prompt:
                return self._json({"error": "Missing 'prompt' field (or messages)"}, 400)

            session_id = body.get("session_id") or body.get("session")
            stream = bool(body.get("stream"))

            # Handle session switching if requested
            agent = server._get_agent()
            if agent is None:
                return self._json({"error": "Agent not ready"}, 503)

            # If session_id provided and different from current, try to load
            if session_id and getattr(agent, "session", None) and getattr(agent.session, "id", None) != session_id:
                try:
                    loaded = agent.session_manager.load(session_id)
                    if loaded:
                        agent.session = loaded
                except Exception:
                    pass
            elif session_id and getattr(agent, "session", None) is None:
                try:
                    loaded = agent.session_manager.load(session_id)
                    if loaded:
                        agent.session = loaded
                except Exception:
                    pass

            # Acquire lock to ensure single turn at a time
            if not server._acquire_agent_lock(timeout=5):
                return self._json({"error": "Agent busy, try again shortly"}, 429)

            try:
                if stream:
                    # SSE streaming
                    self._sse_headers()
                    try:
                        for chunk in server._run_agent_stream(prompt):
                            # chunk is LLMChunk or AgentEvent dict
                            if isinstance(chunk, dict):
                                # AgentEvent
                                etype = chunk.get("type")
                                data = chunk.get("data")
                                if etype == "text_delta":
                                    payload = json.dumps({"delta": data, "type": "text"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                elif etype == "reasoning_delta":
                                    payload = json.dumps({"delta": data, "type": "reasoning"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                elif etype == "tool_call_start":
                                    payload = json.dumps({"tool": data, "type": "tool_call_start"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                elif etype == "tool_call_result":
                                    payload = json.dumps({"tool_result": data, "type": "tool_call_result"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                elif etype == "error":
                                    payload = json.dumps({"error": data, "type": "error"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                elif etype == "turn_complete":
                                    pass
                            else:
                                # LLMChunk
                                if getattr(chunk, "delta_text", None):
                                    payload = json.dumps({"delta": chunk.delta_text, "type": "text"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                                if getattr(chunk, "delta_reasoning", None):
                                    payload = json.dumps({"delta": chunk.delta_reasoning, "type": "reasoning"})
                                    self.wfile.write(f"data: {payload}\n\n".encode())
                                    self.wfile.flush()
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception as ex:
                        try:
                            err = json.dumps({"error": str(ex)})
                            self.wfile.write(f"data: {err}\n\n".encode())
                        except Exception:
                            pass
                    return
                else:
                    # Non-streaming: collect full response
                    result = server._run_agent_sync(prompt)
                    return self._json(result, 200)
            finally:
                server._release_agent_lock()

        if path in ("/api/stop", "/stop"):
            try:
                agent = server._get_agent()
                if agent:
                    agent.request_stop()
                return self._json({"ok": True, "stopped": True}, 200)
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)

        return self._json({"error": f"Unknown POST {path}"}, 404)


class MeshServer:
    """Localhost/VPS HTTP API server for a single Harness instance."""

    def __init__(
        self,
        workspace: Optional[str] = None,
        username: Optional[str] = None,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
        agent_ref=None,
        config=None,
        server_token: Optional[str] = None,
    ):
        self.host = host or "127.0.0.1"
        self.workspace = workspace or os.getcwd()
        try:
            self.workspace = str(Path(self.workspace).resolve())
        except Exception:
            self.workspace = os.path.abspath(self.workspace)
        self.username = (username or getpass.getuser() or "harness").strip()
        self.base = compute_base_port(self.workspace, self.username)
        # Port resolution: explicit port >0 ? use it : hash-based
        if port and int(port) > 0:
            self.port = int(port)
            # Verify free, else find next free in block
            from harness.mesh.port import is_port_free
            if not is_port_free(self.port, host=self.host if self.host != "0.0.0.0" else "127.0.0.1"):
                # Try hash-based fallback
                self.port, _ = find_free_port(self.workspace, self.username, host="127.0.0.1" if self.host == "0.0.0.0" else self.host)
        else:
            self.port, _ = find_free_port(self.workspace, self.username, host="127.0.0.1" if self.host == "0.0.0.0" else self.host)
        self._config = config
        self.server_token = server_token or _get_server_token(config)
        self._agent_ref = agent_ref
        self._agent_lock = threading.Lock()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self._started = False
        self._start_time = time.time()

    def _get_agent(self):
        if callable(self._agent_ref):
            try:
                return self._agent_ref()
            except Exception:
                return self._agent_ref
        return self._agent_ref

    def _acquire_agent_lock(self, timeout: float = 5) -> bool:
        return self._agent_lock.acquire(timeout=timeout)

    def _release_agent_lock(self):
        try:
            self._agent_lock.release()
        except Exception:
            pass

    def _run_agent_sync(self, prompt: str) -> Dict[str, Any]:
        agent = self._get_agent()
        if agent is None:
            return {"error": "Agent not available"}
        # Collect response
        text_parts = []
        tool_calls = []
        reasoning_parts = []
        session_id = getattr(getattr(agent, "session", None), "id", None)
        try:
            for ev in agent.step(prompt):
                etype = getattr(ev, "type", None) or ev.get("type") if isinstance(ev, dict) else None
                data = getattr(ev, "data", None) if hasattr(ev, "data") else ev.get("data") if isinstance(ev, dict) else None
                if etype == "text_delta" and data:
                    text_parts.append(str(data))
                elif etype == "reasoning_delta" and data:
                    reasoning_parts.append(str(data))
                elif etype == "tool_call_start" and data:
                    tool_calls.append(data)
                elif etype == "turn_complete":
                    session_id = getattr(getattr(agent, "session", None), "id", session_id)
            # Also try to get last assistant text if no deltas
            if not text_parts:
                try:
                    last = agent._last_assistant_text() if hasattr(agent, "_last_assistant_text") else ""
                    if last:
                        text_parts.append(last)
                except Exception:
                    pass
        except Exception as ex:
            return {"error": str(ex), "session_id": session_id}
        full_text = "".join(text_parts).strip()
        return {
            "response": full_text,
            "text": full_text,
            "reasoning": "".join(reasoning_parts)[:2000],
            "tool_calls": tool_calls[:10],
            "session_id": session_id,
            "model": getattr(agent.config, "model", None) if hasattr(agent, "config") else None,
            "provider": getattr(agent.config, "provider", None) if hasattr(agent, "config") else None,
            "workspace": self.workspace,
            "port": self.port,
        }

    def _run_agent_stream(self, prompt: str):
        agent = self._get_agent()
        if agent is None:
            yield {"type": "error", "data": "Agent not available"}
            return
        for ev in agent.step(prompt):
            yield {"type": ev.type, "data": ev.data} if hasattr(ev, "type") else ev

    def get_status(self) -> Dict[str, Any]:
        provider = ""
        model = ""
        is_busy = False
        session_id = None
        todo_summary = ""
        try:
            agent = self._get_agent()
            if agent is not None:
                provider = getattr(agent.config, "provider", "") or ""
                model = getattr(agent.session, "model", None) or getattr(agent.config, "model", "") or ""
                is_busy = bool(getattr(agent, "is_running", False))
                try:
                    todo_summary = agent.todo_manager.summary() if hasattr(agent, "todo_manager") else ""
                except Exception:
                    todo_summary = ""
                try:
                    session_id = getattr(agent.session, "id", None) if getattr(agent, "session", None) else None
                except Exception:
                    session_id = None
        except Exception:
            pass
        return {
            "status": "ok",
            "port": self.port,
            "base": self.base,
            "workspace": self.workspace,
            "username": self.username,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": self._start_time,
            "uptime": round(time.time() - self._start_time, 1),
            "provider": provider,
            "model": model,
            "is_busy": is_busy,
            "todos": todo_summary,
            "session_id": session_id,
            "host": self.host,
            "token_required": bool(self.server_token or _get_server_token(self._config)),
        }

    def start(self) -> "MeshServer":
        if self._started:
            return self
        try:
            # For 0.0.0.0, bind to all interfaces, but is_port_free checks need special handling
            bind_host = self.host
            self.httpd = ThreadingHTTPServer((bind_host, self.port), MeshHandler)
            self.httpd.mesh_server = self  # type: ignore
            self.httpd.daemon_threads = True
            self.httpd.allow_reuse_address = True
        except OSError as ex:
            raise RuntimeError(f"API server failed to bind {self.host}:{self.port}: {ex}") from ex

        # Register for port tracking (not for peer discovery anymore)
        try:
            register_instance(self.port, self.workspace, self.username)
        except Exception:
            pass

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name=f"harness-api-{self.port}")
        self.thread.start()
        self._started = True
        return self

    def stop(self):
        if not self._started:
            return
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        try:
            deregister_instance(self.port)
        except Exception:
            pass
        self._started = False


def get_mesh() -> Optional["MeshServer"]:
    # Alias for backward compat
    return get_server()

def get_server() -> Optional["MeshServer"]:
    with _mesh_lock:
        return _mesh_singleton

def start_mesh(workspace: Optional[str] = None, username: Optional[str] = None, host: str = "127.0.0.1", on_message=None, agent_ref=None, enable: bool = True, port: Optional[int] = None, config=None) -> Optional["MeshServer"]:
    return start_server(workspace=workspace, username=username, host=host, port=port, agent_ref=agent_ref, config=config, enable=enable)

def start_server(workspace: Optional[str] = None, username: Optional[str] = None, host: str = "127.0.0.1", port: Optional[int] = None, agent_ref=None, config=None, enable: bool = True, server_token: Optional[str] = None) -> Optional["MeshServer"]:
    """Start the singleton API server. Returns None if disabled or failed."""
    global _mesh_singleton
    if not enable:
        return None
    with _mesh_lock:
        if _mesh_singleton is not None and _mesh_singleton._started:
            # If same workspace/host/port requested, reuse; else keep existing
            return _mesh_singleton
        try:
            # Determine host/port from config if not explicitly passed
            cfg_host = host
            cfg_port = port
            if config is not None:
                cfg_host = getattr(config, "server_host", None) or getattr(config, "mesh_host", None) or host
                cfg_port = getattr(config, "server_port", None) or port
                # server_enabled alias
                if not getattr(config, "server_enabled", getattr(config, "mesh_enabled", True)):
                    return None
            srv = MeshServer(workspace=workspace, username=username, host=cfg_host, port=cfg_port, agent_ref=agent_ref, config=config, server_token=server_token)
            srv.start()
            _mesh_singleton = srv
            return srv
        except Exception as ex:
            try:
                print(f"[api] Failed to start ({ex}) — continuing without API server.", flush=True)
            except Exception:
                pass
            return None

def stop_mesh():
    return stop_server()

def stop_server():
    global _mesh_singleton
    with _mesh_lock:
        if _mesh_singleton is not None:
            try:
                _mesh_singleton.stop()
            except Exception:
                pass
            _mesh_singleton = None
