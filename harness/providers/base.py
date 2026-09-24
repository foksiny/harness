"""
Base Provider interface for Harness.
Defines unified streaming chunk protocol, model specification resolution,
and parameter normalization for reasoning/thinking effort.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Iterator
import threading
from harness.providers.detector import inspect_model, ModelSpec

@dataclass
class ToolCallDelta:
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""

@dataclass
class LLMChunk:
    delta_text: str = ""
    delta_reasoning: str = ""
    tool_calls: List[ToolCallDelta] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


# Errors that retrying cannot fix (auth, bad request, missing model, context
# overflow). The agent consults this before spending a retry on a dead request.
_FATAL_PROVIDER_ERROR_MARKERS = (
    "no api key", "api key", "authentication", "unauthorized", "forbidden",
    "permission denied", "invalid api key", "invalid request",
    "unsupported parameter", "unsupported", "not found", "does not exist",
    "invalid model", "model_not_found", "context length", "too long",
    "quota", "billing", "deactivated", "input image", "moderation",
)

# The endpoint returned HTTP 200 but the SSE stream carried zero usable data.
# Usually an upstream/LB silently dropping the request. Retrying once is
# reasonable; hammering it with exponential backoff just burns the user's time.
_EMPTY_STREAM_ERROR_MARKERS = (
    "stream ended without a response",
)


def is_fatal_provider_error(error_text: str) -> bool:
    """True when a provider error is permanent — retrying is pointless."""
    low = (error_text or "").lower()
    return any(m in low for m in _FATAL_PROVIDER_ERROR_MARKERS)


def is_empty_stream_error(error_text: str) -> bool:
    """True when the endpoint returned an empty 200 stream (no SSE payload)."""
    low = (error_text or "").lower()
    return any(m in low for m in _EMPTY_STREAM_ERROR_MARKERS)

class BaseProvider(ABC):
    """Abstract interface for all LLM providers."""

    name: str
    display_name: str
    default_model: str

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0, stream_timeout: Optional[float] = None):
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.1, float(base_delay))
        # SSE read timeout (seconds): how long the socket may go without
        # receiving any bytes mid-stream. Generous by default so long
        # reasoning stalls don't surface as "read operation timed out".
        self.stream_timeout = resolve_stream_timeout(stream_timeout)
        # In-flight SSE streams of THIS provider instance, so a stop request
        # from another thread can hard-abort a stalled HTTP read immediately
        # instead of waiting out the (potentially 300s) read timeout.
        self._active_sse_lock = threading.Lock()
        self._active_sse: list = []
        # Bumped every time abort_active_streams() runs. Each stream call
        # captures the value at start; retry loops bail out the moment it
        # changes, so an abandoned (stopped) stream never opens a NEW
        # connection in a zombie pump thread.
        self._abort_gen = 0

    def _tracked_sse_stream(self, endpoint: str, headers: Optional[Dict[str, str]], body: Dict[str, Any]):
        """Context manager wrapping sse_post_stream with abort registration.

        While the stream is open it is registered with this provider instance;
        ``abort_active_streams()`` (called by the agent on /stop) can then tear
        the socket down from another thread and unblock the reader.
        """
        return _TrackedSsePostStream(self, endpoint, headers, body, self.stream_timeout)

    def _register_sse(self, sse: "SsePostStream") -> None:
        with self._active_sse_lock:
            self._active_sse.append(sse)

    def _unregister_sse(self, sse: "SsePostStream") -> None:
        with self._active_sse_lock:
            try:
                self._active_sse.remove(sse)
            except ValueError:
                pass

    def abort_active_streams(self) -> None:
        """Hard-abort every in-flight SSE stream of this provider (thread-safe).

        Best-effort: used to unblock stalled reads immediately when the user
        requests a stop. Failures are swallowed — the reader thread's own
        timeout is the safety net. Also bumps the abort generation so any
        zombie retry loop from the abandoned call gives up instead of
        opening a fresh connection.
        """
        self._abort_gen += 1
        with self._active_sse_lock:
            streams = list(self._active_sse)
            self._active_sse.clear()
        for s in streams:
            try:
                s.abort()
            except Exception:
                pass

    def _is_retryable_http_code(self, code: int) -> bool:
        # Retry on rate limit and transient server errors
        return code in (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529)

    def _retry_delay(self, attempt: int) -> float:
        # Exponential backoff: base * 2^attempt  (attempt 0 => 5s, 1=>10s, 2=>20s)
        return self.base_delay * (2 ** attempt)

    def get_model_spec(self, model_name: Optional[str] = None) -> ModelSpec:
        """Resolve model specifications using model-specific dynamic detection and discovery."""
        m = model_name or self.default_model
        try:
            from harness.providers.discovery import resolve_model_spec_dynamic
            return resolve_model_spec_dynamic(m, self.name, self.api_key, self.base_url)
        except Exception:
            return inspect_model(m, self.name)

    def normalize_thinking_effort(self, model_spec: ModelSpec, effort_setting: str) -> Dict[str, Any]:
        """
        Convert user effort setting (off, low, medium, high, or integer)
        into the model's exact parameter format (per-provider thinking dialect).
        """
        if not model_spec.supports_thinking:
            return {}

        ttype = model_spec.thinking_type or "reasoning_effort"
        # NVIDIA NIM does not support the Together-style reasoning toggle.
        # For models wrongly marked toggle but hosted on NVIDIA, silently disable
        # reasoning so we never send {"reasoning": {"enabled": true}} to NIM.
        if ttype == "reasoning_toggle" and getattr(self, "name", "") == "nvidia":
            return {}
        eff = effort_setting.lower().strip()
        off = eff in ("off", "none", "false", "disabled", "no", "0", "disable", "stop", "cancel")

        if ttype == "budget_tokens":
            # Anthropic style
            if off:
                return {}
            tokens = max(1024, self._level_tokens(eff, 2048, 8192, 16384))
            return {"thinking": {"type": "enabled", "budget_tokens": tokens}}

        if ttype == "thinking_budget":
            # Gemini style. includeThoughts=true surfaces the thinking blocks.
            if off:
                return {}
            budget = self._level_tokens(eff, 2048, 8192, 16384)
            return {"thinking_config": {"thinking_budget": budget, "include_thoughts": True}}

        if ttype == "thinking_token_budget":
            # Cohere style
            if off:
                return {"thinking": {"type": "disabled"}}
            budget = max(1024, self._level_tokens(eff, 2048, 8192, 16384))
            return {"thinking": {"type": "enabled", "token_budget": budget}}

        if ttype == "reasoning_object":
            # OpenRouter style
            if off:
                return {"reasoning": {"enabled": False}}
            return {"reasoning": {"effort": self._level_effort(eff)}}

        if ttype == "reasoning_toggle":
            # Together hybrid models
            if off:
                return {"reasoning": {"enabled": False}}
            return {"reasoning": {"enabled": True}}

        if ttype == "chat_template_kwargs":
            # NVIDIA NIM (DeepSeek-V4 etc.) — reasoning_effort none|high|max.
            if off:
                return {"chat_template_kwargs": {"thinking": False}}
            effort = "high"
            if eff in ("max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
                effort = "max"
            elif eff in ("low", "minimal", "light", "brief", "shallow"):
                effort = "high"
            elif eff.isdigit():
                n = int(eff)
                effort = "max" if n >= 12000 else "high"
            return {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": effort}

        # reasoning_effort / default
        if off:
            return {}
        return {"reasoning_effort": self._level_effort(eff)}

    @staticmethod
    def _level_tokens(eff: str, low_t: int, med_t: int, high_t: int) -> int:
        if eff in ("low", "minimal", "light", "brief", "shallow"):
            return low_t
        if eff in ("high", "max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
            return high_t
        if eff.isdigit():
            return int(eff)
        return med_t

    @staticmethod
    def _level_effort(eff: str) -> str:
        if eff in ("low", "minimal", "light", "brief", "shallow"):
            return "low"
        if eff in ("high", "max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
            return "high"
        if eff in ("medium", "med", "moderate", "normal", "default", "standard"):
            return "medium"
        if eff.isdigit():
            n = int(eff)
            return "low" if n < 4000 else ("medium" if n < 12000 else "high")
        return "medium"

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        """Stream response chunks from provider."""
        pass



# Default streaming timeouts (seconds). Connect stays short so dead endpoints
# fail fast; read is generous because reasoning models (e.g. DeepSeek on
# NVIDIA NIM) can stall for minutes between tokens during long thinking.
# The old urllib single-timeout of 120s fired constantly on those stalls
# ("The read operation timed out"); httpx lets us split connect vs read the
# way opencode / pi-agent do.
DEFAULT_STREAM_CONNECT_TIMEOUT = 15.0
DEFAULT_STREAM_READ_TIMEOUT = 300.0
DEFAULT_STREAM_WRITE_TIMEOUT = 30.0
DEFAULT_STREAM_POOL_TIMEOUT = 15.0


def resolve_stream_timeout(config_or_value=None) -> float:
    """Resolve the SSE read timeout from config, env override, or default."""
    import os as _os
    if isinstance(config_or_value, (int, float)):
        return max(30.0, float(config_or_value))
    if config_or_value is not None:
        try:
            v = float(getattr(config_or_value, "provider_stream_timeout", 0) or 0)
            if v > 0:
                return max(30.0, v)
        except Exception:
            pass
    try:
        env = _os.environ.get("HARNESS_STREAM_TIMEOUT", "").strip()
        if env:
            return max(30.0, float(env))
    except Exception:
        pass
    return DEFAULT_STREAM_READ_TIMEOUT


class StreamHTTPError(Exception):
    """Non-2xx response from a streaming POST (carries body + headers)."""

    def __init__(self, status_code: int, body: str, headers=None):
        super().__init__(f"HTTP {status_code}: {(body or '')[:200]}")
        self.status_code = status_code
        self.body = body or ""
        self.headers = headers

    def retry_after(self) -> Optional[float]:
        try:
            raw = None
            if self.headers is not None:
                getter = getattr(self.headers, "get", None)
                raw = getter("retry-after") if getter else None
            if raw:
                return max(0.0, float(raw))
        except Exception:
            pass
        return None


class SsePostStream:
    """POST JSON and stream SSE lines as they arrive via httpx.

    Replaces the old ``urllib.request.urlopen`` pattern: httpx decodes and
    yields each line the moment its bytes arrive (no stdlib buffering
    delays), splits connect vs read timeouts, and surfaces ``: ping``
    keep-alives promptly so long reasoning stalls don't kill the socket.
    """

    def __init__(
        self,
        endpoint: str,
        headers: Optional[Dict[str, str]],
        body: Dict[str, Any],
        stream_timeout: Optional[float] = None,
    ):
        self.endpoint = endpoint
        self.headers = headers or {}
        self.body = body
        self.stream_timeout = resolve_stream_timeout(stream_timeout)
        self._client = None
        self._context = None
        self._resp = None

    def __enter__(self) -> "SsePostStream":
        import httpx
        timeout = httpx.Timeout(
            connect=DEFAULT_STREAM_CONNECT_TIMEOUT,
            read=self.stream_timeout,
            write=DEFAULT_STREAM_WRITE_TIMEOUT,
            pool=DEFAULT_STREAM_POOL_TIMEOUT,
        )
        self._client = httpx.Client(timeout=timeout)
        self._context = self._client.stream(
            "POST", self.endpoint, headers=self.headers, json=self.body
        )
        self._resp = self._context.__enter__()
        if self._resp.status_code >= 400:
            try:
                raw = self._resp.read()
                body = raw.decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            headers = self._resp.headers
            self.__exit__(None, None, None)
            raise StreamHTTPError(self._resp.status_code, body, headers)
        return self

    def __iter__(self):
        # iter_lines() yields each line as soon as its newline arrives.
        for line in self._resp.iter_lines():
            yield line

    def abort(self) -> None:
        """Hard-abort the in-flight HTTP stream (thread-safe, best-effort).

        Called from OTHER threads (e.g. the agent loop when the user hits /stop)
        to unblock a reader stuck in ``iter_lines`` waiting on a stalled socket.

        Plain ``client.close()`` is NOT enough: the connection is checked out
        to the response, and closing the pool does not interrupt a concurrent
        blocked read. So this walks the httpx object graph (defensively — these
        are httpcore internals) for the raw socket and shuts it down, which
        makes the blocked ``recv()`` raise immediately instead of lingering
        for the (potentially 300s) read timeout.
        """
        resp = self._resp
        # 1. HARD: find and tear down every raw socket in the response graph.
        #    Must run BEFORE any graceful close — resp.close() rewrites the
        #    stream chain and the socket becomes unreachable for the walk.
        try:
            import socket as _socket
            seen = set()
            def _find_socks(obj, depth):
                if depth > 8 or id(obj) in seen:
                    return
                seen.add(id(obj))
                d = getattr(obj, "__dict__", None)
                if not isinstance(d, dict):
                    return
                for val in d.values():
                    if isinstance(val, _socket.socket):
                        try:
                            val.shutdown(_socket.SHUT_RDWR)
                        except OSError:
                            pass
                        try:
                            val.close()
                        except OSError:
                            pass
                    elif hasattr(val, "__dict__"):
                        _find_socks(val, depth + 1)
            _find_socks(resp, 0)
        except Exception:
            pass
        # 2. Graceful: close the response (releases the connection).
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        # 3. Pool cleanup so the client doesn't linger either.
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._context is not None:
                self._context.__exit__(exc_type, exc, tb)
        except Exception:
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        return False


def sse_post_stream(
    endpoint: str,
    headers: Optional[Dict[str, str]],
    body: Dict[str, Any],
    stream_timeout: Optional[float] = None,
) -> SsePostStream:
    """Convenience constructor for ``with sse_post_stream(...) as stream:``."""
    return SsePostStream(endpoint, headers, body, stream_timeout)


class _TrackedSsePostStream:
    """``sse_post_stream`` wrapper that registers itself with a provider.

    Enters the inner ``SsePostStream`` and registers it on the provider so
    ``abort_active_streams()`` can tear the socket down mid-read from another
    thread (user stop / Ctrl-C). Unregisters on exit so aborted, dead streams
    don't linger in the registry.
    """

    def __init__(self, provider: "BaseProvider", endpoint: str,
                 headers: Optional[Dict[str, str]], body: Dict[str, Any],
                 stream_timeout: Optional[float]):
        self._provider = provider
        self._inner = SsePostStream(endpoint, headers, body, stream_timeout)

    def __enter__(self) -> "SsePostStream":
        stream = self._inner.__enter__()
        self._provider._register_sse(self._inner)
        return stream

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._provider._unregister_sse(self._inner)
        return self._inner.__exit__(exc_type, exc, tb)


def is_transient_transport_error(ex: Exception) -> bool:
    """True for connection-level failures a fresh request can fix.

    Covers httpx timeout/transport errors plus stdlib socket timeouts.
    Auth/validation errors are NOT transient.
    """
    try:
        import httpx as _httpx
        if isinstance(ex, _httpx.TimeoutException):
            return True
        if isinstance(ex, _httpx.TransportError):
            return True
    except Exception:
        pass
    if isinstance(ex, (TimeoutError, ConnectionError)):
        return True
    low = str(ex).lower()
    return any(t in low for t in (
        "timed out", "timeout", "connection reset", "connection aborted",
        "connection refused", "temporarily unavailable", "connection error",
        "remote protocol error", "incomplete read",
    ))


def probe_effort_options(dialect: Optional[str]) -> List[str]:
    """Probe which effort names a dialect actually accepts.

    Returns a list of canonical effort level names that produce distinct
    results for the given dialect. Also indicates if numeric token counts
    are accepted (shown as '<n>' in the list).
    """
    if not dialect:
        return []

    def _run(v):
        eff = v.lower().strip()
        off = eff in ("off", "none", "false", "disabled", "no", "0", "disable", "stop", "cancel")
        if dialect == "budget_tokens":
            if off: return {}
            return {"thinking": {"type": "enabled", "budget_tokens": max(1024, BaseProvider._level_tokens(eff, 2048, 8192, 16384))}}
        if dialect == "thinking_budget":
            if off: return {}
            return {"thinking_config": {"thinking_budget": BaseProvider._level_tokens(eff, 2048, 8192, 16384), "include_thoughts": True}}
        if dialect == "thinking_token_budget":
            if off: return {"thinking": {"type": "disabled"}}
            return {"thinking": {"type": "enabled", "token_budget": max(1024, BaseProvider._level_tokens(eff, 2048, 8192, 16384))}}
        if dialect == "reasoning_object":
            if off: return {"reasoning": {"enabled": False}}
            return {"reasoning": {"effort": BaseProvider._level_effort(eff)}}
        if dialect == "reasoning_toggle":
            if off: return {"reasoning": {"enabled": False}}
            return {"reasoning": {"enabled": True}}
        if dialect == "chat_template_kwargs":
            if off: return {"chat_template_kwargs": {"thinking": False}}
            effort = "max" if eff in ("max", "maximum", "full", "deep") or (eff.isdigit() and int(eff) >= 12000) else "high"
            return {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": effort}
        # reasoning_effort / default
        if off: return {}
        return {"reasoning_effort": BaseProvider._level_effort(eff)}

    # Test canonical levels and check which produce distinct results
    levels = [("off", "off"), ("low", "low"), ("medium", "medium"), ("high", "high")]
    results = {}
    for label, probe_val in levels:
        r = repr(_run(probe_val))
        if r not in results:
            results[r] = label

    canonical = list(results.values())

    # For toggle/nim dialects, prefer natural names over level names
    if dialect == "reasoning_toggle" and len(canonical) == 2:
        # off + one other → show as "off, on"
        canonical = ["off", "on"]
    elif dialect == "chat_template_kwargs" and len(canonical) == 2:
        # off + one other → show as "none, high, max"
        canonical = ["none", "high", "max"]

    # Check if numeric values are preserved as actual token counts (not just mapped to levels)
    high_r = _run("high")
    num_r = _run("8000")
    if num_r != high_r and num_r != _run("off"):
        # Check if the numeric value is actually in the result (token count preserved)
        num_str = repr(num_r)
        if "8000" in num_str:
            canonical.append("<n>")

    return canonical

