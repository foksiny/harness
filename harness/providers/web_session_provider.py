"""
Web-session providers for Harness: ``openaiauth`` and ``claudeauth``.

These providers let a Harness user drive the SAME backend their ChatGPT /
Claude web subscription talks to — chatgpt.com and claude.ai — instead of
the pay-per-token developer APIs. Any plan works, including the free tier,
because requests authenticate with the session cookie the browser itself
uses.

Why separate provider ids instead of replacing ``openai`` / ``claude``:
the normal API-key providers stay untouched for users with developer
accounts; ``openaiauth`` / ``claudeauth`` are additional selectable
providers.

⚠️ ToS note: driving the web UI's private endpoints from a CLI is not an
officially supported use of either product and could be restricted or be
considered a violation of their terms of service. Use responsibly.

Protocol notes (reverse-engineered; degrades gracefully when it drifts):

ChatGPT web (chatgpt.com/backend-api):
  1. POST /backend-api/sentinel/chat-answers with a random token, then
     POST /backend-api/sentinel/chat-requirements which replies with a
     requirements token + proof-of-work parameters.
  2. The PoW answer goes into the ``openai-sentinel-proof-token`` header
     and the requirements token into
     ``openai-sentinel-chat-requirements-token`` of the conversation POST.
  3. POST /backend-api/conversation with a device id, the model slug and
     a user message. The response is SSE-ish: full JSON lines with no
     ``data:`` prefix. ``message.content.parts[]`` carry the text,
     ``content_type == "reasoning"`` marks thinking deltas, and
     ``message.status == "finished_successfully"`` ends the turn.

Claude web (claude.ai):
  1. Organization UUID: GET https://claude.ai/api/organizations with the
     cookie-authenticated session — the first org's ``uuid`` is used.
  2. One POST /api/append_message per completion with a locally generated
     ``conversation_uuid`` and ``request_id`` (UUIDv4). The endpoint takes
     flat ``completion.prompt`` / ``text`` fields, not the Messages API
     schema.
  3. Response events arrive as SSE ``data:`` frames: ``completion`` (text
     delta), ``ping``, ``error`` (fatal), and a trailing ``DONE``/empty
     frame which we treat as turn end.

Both providers reuse the unified ``LLMChunk`` streaming protocol and the
SSE timeout / abort plumbing from BaseProvider, so /stop, retries and
token accounting keep working unchanged. Model specs resolve through
``detector.inspect_model`` so unknown future models get sane defaults.

Message handling: the web backends have no concept of the canonical
system/assistant/tool message tree, so the history is flattened into a
transcript prompt and each call is a brand-new web conversation. This is
deliberate: it keeps the harness conversation authoritative and means a
login refresh never strands stale server-side chat state.
"""
import json
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from harness.providers.base import (
    BaseProvider,
    LLMChunk,
    StreamHTTPError,
    is_transient_transport_error,
)

# The web backends reject very large bodies much harder than the official
# APIs; keep the flattened transcript conservative.
_MAX_WEB_PROMPT_CHARS = 180_000

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _trim_to_limit(prompt: str, limit: int = _MAX_WEB_PROMPT_CHARS) -> str:
    """Keep the head of an oversized transcript (with a visible marker)."""
    if len(prompt) <= limit:
        return prompt
    return prompt[:limit] + "\n\n[context trimmed for web-session length limit]"


def _content_text(content: Any) -> str:
    """Flatten canonical content (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


def _clean_model_slug(model: Optional[str], fallback: str) -> str:
    """Normalize a user-typed model id to a web endpoint slug (empty => default)."""
    return (model or "").strip() or fallback


class _WebSessionHttp:
    """Minimal JSON/GET/stream helper with browser-like headers and cookie auth."""

    def __init__(self, session_token: str, base_headers: Optional[Dict[str, str]] = None, timeout: float = 30.0):
        import httpx
        self._httpx = httpx
        self.token = session_token or ""
        self.timeout = timeout
        self.extra = dict(base_headers or {})

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "User-Agent": _UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            **self.extra,
        }
        if self.token:
            # Accept either a bare token or a full "name=value" cookie header
            # fragment; pass both through verbatim.
            h["Cookie"] = self.token
        if extra:
            h.update(extra)
        return h

    def post_json(self, url: str, payload: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
        with self._httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, headers=self._headers(extra_headers), json=payload)
            return r.status_code, r.text

    def get_json(self, url: str) -> Tuple[int, str]:
        with self._httpx.Client(timeout=self.timeout) as c:
            r = c.get(url, headers=self._headers())
            return r.status_code, r.text

    def post_stream(self, url: str, payload: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None, read_timeout: Optional[float] = None) -> Iterator[str]:
        """POST and yield raw text lines as they arrive (SSE or line-delimited JSON)."""
        httpx = self._httpx
        to = httpx.Timeout(connect=15.0, read=read_timeout or self.timeout, write=30.0, pool=15.0)
        with httpx.Client(timeout=to) as c:
            with c.stream("POST", url, headers=self._headers(extra_headers), json=payload) as resp:
                if resp.status_code >= 400:
                    try:
                        body = resp.read().decode("utf-8", errors="ignore")
                    except Exception:
                        body = ""
                    raise StreamHTTPError(resp.status_code, body, resp.headers)
                for line in resp.iter_lines():
                    if line is not None:
                        yield line


class OpenAIWebProvider(BaseProvider):
    """ChatGPT web-session provider (chatgpt.com/backend-api). Any plan, including free."""
    name = "openaiauth"
    display_name = "OpenAI (ChatGPT Web Session)"
    default_model = "gpt-5.2"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0, stream_timeout: Optional[float] = None):
        # `api_key` here carries the session credential:
        # "__Secure-next-auth.session-token=<token>" (value set via /login).
        super().__init__(api_key=api_key, base_url=base_url or "https://chatgpt.com", max_retries=max_retries, base_delay=base_delay, stream_timeout=stream_timeout)
        self.device_id = str(uuid.uuid4())
        self.http = _WebSessionHttp(
            api_key or "",
            base_headers={
                "Referer": "https://chatgpt.com/",
                "Origin": "https://chatgpt.com",
                "App-Name": "browser",
            },
        )
        # Requirements/proof-of-work state (per provider instance; refreshed lazily)
        self._requirements: Dict[str, str] = {}

    # ── proof-of-work ("sentinel") handling ──────────────────────────────
    @staticmethod
    def _solve_pow(difficulty: str) -> str:
        """Answer the chat-requirements proof-of-work challenge.

        Same core loop the ChatGPT web client runs: find a base64 candidate
        whose SHA3-512 hex digest has the required leading zero count.
        """
        import base64
        import hashlib
        import secrets
        diff_map = {"easy": 0, "medium": 1, "hard": 2}
        n = diff_map.get(str(difficulty).lower(), 1)
        prefix = secrets.token_bytes(12)
        for i in range(200_000):
            candidate = base64.b64encode(prefix + i.to_bytes(4, "big")).decode()
            digest = hashlib.sha3_512(candidate.encode()).hexdigest()
            if digest.startswith("0" * n):
                return "gAAAAAC" + candidate
        # Fallback: never block the stream forever on a hard challenge
        return "gAAAAAB" + base64.b64encode(secrets.token_bytes(16)).decode()

    def _chat_requirements(self) -> Dict[str, Any]:
        """Fetch (and solve) the sentinel chat-requirements for this request.

        Returns a dict with the ``token`` (requirements token) and ``pow``
        (solved proof) ready to drop into the request headers. Empty dict on
        any failure — the caller then simply omits the headers, which works
        on accounts/regions where the challenge is not enforced.
        """
        import base64
        import secrets
        try:
            code, text = self.http.post_json(
                f"{self.base_url.rstrip('/')}/backend-api/sentinel/chat-requirements",
                {"p": base64.b64encode(secrets.token_bytes(42)).decode()},
                extra_headers={"OAI-Device-Id": self.device_id},
            )
        except Exception:
            return {}
        if code != 200:
            return {}
        try:
            req = json.loads(text)
        except Exception:
            return {}
        if not isinstance(req, dict):
            return {}
        pow_ans = ""
        pow_cfg = req.get("proof_of_work")
        if isinstance(pow_cfg, dict) and pow_cfg.get("required"):
            pow_ans = self._solve_pow(pow_cfg.get("difficulty"))
        return {"token": req.get("token") or "", "pow": pow_ans}

    def _requirements_headers(self) -> Dict[str, str]:
        """Build the sentinel headers; cached briefly to avoid re-solving PoW per chunk."""
        now = time.time()
        if not self._requirements or (now - self._requirements.get("_ts", 0)) > 60:
            try:
                self._requirements = self._chat_requirements()
                self._requirements["_ts"] = now
            except Exception:
                self._requirements = {}
        headers: Dict[str, str] = {"OAI-Device-Id": self.device_id}
        if self._requirements.get("token"):
            headers["Openai-Sentinel-Chat-Requirements-Token"] = self._requirements["token"]
        if self._requirements.get("pow"):
            headers["Openai-Sentinel-Proof-Token"] = self._requirements["pow"]
        return headers

    # ── transcript flattening ───────────────────────────────────────────
    @staticmethod
    def _flatten_transcript(messages: List[Dict[str, Any]], system_prompt: Optional[str], tools: Optional[List[Dict[str, Any]]]) -> str:
        """Flatten the canonical history into a labeled transcript the web chat understands.

        The ChatGPT web conversation API takes a single new user message per
        call (it has no client-supplied history field), so prior turns travel
        inside the prompt. Tool calls/results are rendered as readable text
        because the web backend has no native tool-calling surface.
        """
        parts: List[str] = []
        if system_prompt:
            parts.append(f"[System instructions]\n{system_prompt}")
        if tools:
            names = ", ".join(
                (t.get("function", {}) or {}).get("name") or t.get("name") or "?"
                for t in tools
            )
            parts.append(f"[Available tools — request them in text]: {names}")
        for m in messages:
            role = m.get("role")
            text = _content_text(m.get("content"))
            if role == "system":
                if text:
                    parts.append(f"[System instructions]\n{text}")
            elif role == "assistant":
                call_txt = ""
                tcs = m.get("tool_calls") or []
                if tcs:
                    rendered = []
                    for tc in tcs:
                        fn = tc.get("function", {}) or {}
                        rendered.append(f"{fn.get('name', '?')}({fn.get('arguments', '')})")
                    call_txt = "\n[Tool calls: " + "; ".join(rendered) + "]"
                parts.append(f"ChatGPT: {text}{call_txt}" if text or call_txt else "")
            elif role == "tool":
                parts.append(f"[Tool result for {m.get('name') or m.get('tool_call_id', '?')}]\n{text}")
            else:
                parts.append(f"User: {text}")
        return _trim_to_limit("\n\n".join(p for p in parts if p))

    # ── main stream ─────────────────────────────────────────────────────
    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        active_model = _clean_model_slug(model, self.default_model)
        self.get_model_spec(active_model)  # resolve & cache spec (detector tier chain)

        if not self.api_key:
            raise RuntimeError(
                "No ChatGPT session token for openaiauth. Run:  /login openaiauth"
            )

        prompt = self._flatten_transcript(messages, system_prompt, tools)

        # Each stream_chat() is a brand-new web conversation (multi-turn state
        # lives in the flattened prompt), so server-side chats never dangle.
        parent_id = str(uuid.uuid4())
        body: Dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": parent_id,
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                    "metadata": {},
                }
            ],
            "moderation_params": {},
            "model": active_model,
            "timezone_offset_min": 0,
            "history_and_training_disabled": True,
            "conversation_mode": {"kind": "primary_assistant"},
            "force_paragen": False,
            "force_nulligen": False,
            "force_rate_limit": False,
            "websocket_request_id": str(uuid.uuid4()),
            "supported_encodings": ["v1_compatible"],
        }

        headers = self._requirements_headers()
        saw_payload = False
        # Track how much of the assistant text we've already streamed, because
        # the backend sends full `parts` snapshots, not incremental deltas.
        sent_len = 0
        abort_gen = getattr(self, "_abort_gen", 0)

        for attempt in range(self.max_retries + 1):
            if getattr(self, "_abort_gen", 0) != abort_gen:
                return
            try:
                for line in self.http.post_stream(
                    f"{self.base_url.rstrip('/')}/backend-api/conversation",
                    body,
                    extra_headers=headers,
                    read_timeout=self.stream_timeout,
                ):
                    line = (line or "").strip()
                    if not line:
                        continue
                    # The backend streams full JSON objects, one per line
                    # (no "data:" prefix), SSE-style.
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line or line in ("[DONE]", "DONE"):
                        if saw_payload:
                            yield LLMChunk(finish_reason="stop")
                            return
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    # The backend also wraps errors inside a 200 stream
                    if ev.get("error"):
                        raise RuntimeError(str(ev.get("error")))

                    msg = ev.get("message") or {}
                    if not msg:
                        continue
                    author = (msg.get("author") or {}).get("role") == "assistant"
                    if not author:
                        continue
                    content = msg.get("content") or {}
                    ctype = content.get("content_type")
                    parts = content.get("parts") or []
                    received = "".join(p for p in parts if isinstance(p, str))

                    # Snapshot-based streaming: only yield the new suffix of
                    # the growing assistant text (or restart if it shrank,
                    # e.g. the model re-answered).
                    if received:
                        if len(received) > sent_len:
                            delta = received[sent_len:]
                            sent_len = len(received)
                            saw_payload = True
                            if ctype == "reasoning":
                                yield LLMChunk(delta_reasoning=delta, native=ev)
                            else:
                                yield LLMChunk(delta_text=delta, native=ev)
                        elif len(received) < sent_len:
                            sent_len = len(received)
                    elif ctype == "text" and msg.get("status") == "finished_successfully":
                        saw_payload = True
                        yield LLMChunk(finish_reason="stop", native=ev)
                        return
                # Stream closed without an explicit completion frame.
                if saw_payload:
                    yield LLMChunk(finish_reason="stop")
                    return
                raise RuntimeError("stream ended without a response")
            except StreamHTTPError as he:
                # 401/403 usually means dead session token or a fresh PoW
                # challenge. Refresh requirements once per attempt, then fall
                # through to normal retry handling.
                if he.status_code in (401, 403) and not saw_payload:
                    # 401 means the session token itself is dead — refreshing
                    # PoW won't help. Tell the user to re-login.
                    if he.status_code == 401:
                        yield LLMChunk(delta_text=f"\n[ChatGPT session token rejected (HTTP 401). Run /login openaiauth with a fresh token.]\n", finish_reason="error")
                        return
                    self._requirements = {}  # force PoW re-solve on next header build
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(attempt))
                        continue
                if not he.status_code or not self._is_retryable_http_code(he.status_code) or attempt >= self.max_retries:
                    yield LLMChunk(delta_text=f"\n[HTTP {he.status_code} from ChatGPT web: {he.body[:300]}]\n", finish_reason="error")
                    return
                time.sleep(self._retry_delay(attempt))
                continue
            except Exception as ex:
                if is_transient_transport_error(ex) and not saw_payload and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt))
                    continue
                yield LLMChunk(delta_text=f"\n[ChatGPT web session error: {str(ex)}]\n", finish_reason="error")
                return
        yield LLMChunk(delta_text="\n[ChatGPT web session: retries exhausted]\n", finish_reason="error")


class ClaudeWebProvider(BaseProvider):
    """Claude web-session provider (claude.ai /api/append_message). Any plan, including free."""
    name = "claudeauth"
    display_name = "Claude (claude.ai Web Session)"
    default_model = "claude-sonnet-4-5"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0, stream_timeout: Optional[float] = None):
        # `api_key` here carries the session credential:
        # "sessionKey=<key>" (value set via /login).
        super().__init__(api_key=api_key, base_url=base_url or "https://claude.ai", max_retries=max_retries, base_delay=base_delay, stream_timeout=stream_timeout)
        self.http = _WebSessionHttp(
            api_key or "",
            base_headers={
                "Referer": "https://claude.ai/",
                "Origin": "https://claude.ai",
            },
        )
        self._org_id: Optional[str] = None

    # ── org resolution ─────────────────────────────────────────────────
    def _ensure_org_id(self) -> Optional[str]:
        """Resolve (and cache) the account's organization UUID via /api/organizations."""
        if self._org_id:
            return self._org_id
        try:
            code, text = self.http.get_json(f"{self.base_url.rstrip('/')}/api/organizations")
            if code == 200 and text:
                data = json.loads(text)
                orgs = data.get("orgs") if isinstance(data, dict) else data
                if isinstance(orgs, dict):
                    orgs = list(orgs.values())
                if isinstance(orgs, list) and orgs:
                    first = orgs[0] or {}
                    uuid_field = first.get("uuid") or first.get("id")
                    if uuid_field:
                        self._org_id = str(uuid_field)
                        return self._org_id
        except Exception:
            pass
        return None

    # ── transcript flattening ───────────────────────────────────────────
    @staticmethod
    def _flatten_transcript(messages: List[Dict[str, Any]], system_prompt: Optional[str], tools: Optional[List[Dict[str, Any]]]) -> str:
        """Flatten canonical history to a single prompt in Claude web chat format.

        The append_message endpoint takes one flat ``completion.prompt``
        string, not a message array, so the history is rendered as a labeled
        transcript. Tool calls/results render as readable text since the web
        backend has no native tool-calling surface.
        """
        parts: List[str] = []
        if system_prompt:
            parts.append(f"[System instructions]\n{system_prompt}")
        if tools:
            names = ", ".join(
                (t.get("function", {}) or {}).get("name") or t.get("name") or "?"
                for t in tools
            )
            parts.append(f"[Available tools — request them in text]: {names}")
        for m in messages:
            role = m.get("role")
            text = _content_text(m.get("content"))
            if role == "system":
                if text:
                    parts.append(f"[System instructions]\n{text}")
            elif role == "assistant":
                call_txt = ""
                tcs = m.get("tool_calls") or []
                if tcs:
                    rendered = []
                    for tc in tcs:
                        fn = tc.get("function", {}) or {}
                        rendered.append(f"{fn.get('name', '?')}({fn.get('arguments', '')})")
                    call_txt = "\n[Tool calls: " + "; ".join(rendered) + "]"
                parts.append(f"Assistant: {text}{call_txt}" if text or call_txt else "")
            elif role == "tool":
                parts.append(f"[Tool result for {m.get('name') or m.get('tool_call_id', '?')}]\n{text}")
            else:
                parts.append(f"Human: {text}")
        return _trim_to_limit("\n\n".join(p for p in parts if p))

    # ── main stream ─────────────────────────────────────────────────────
    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        active_model = _clean_model_slug(model, self.default_model)
        self.get_model_spec(active_model)  # resolve & cache spec (detector tier chain)

        if not self.api_key:
            raise RuntimeError(
                "No claude.ai session key for claudeauth. Run:  /login claudeauth"
            )

        prompt = self._flatten_transcript(messages, system_prompt, tools)

        org_id = self._ensure_org_id()
        if not org_id:
            yield LLMChunk(
                delta_text="\n[claude.ai: could not resolve your organization id — the sessionKey may be invalid or expired. Run /login claudeauth with a fresh key.]\n",
                finish_reason="error",
            )
            return

        body: Dict[str, Any] = {
            "completion": {
                "prompt": prompt,
                "timezone_offset_min": 0,
                "model": active_model,
            },
            "org_uuid": org_id,
            "conversation_uuid": str(uuid.uuid4()),
            "text": prompt,
            "request_id": str(uuid.uuid4()),
            "api_key": None,
        }

        headers = {"Accept": "text/event-stream"}
        saw_payload = False
        abort_gen = getattr(self, "_abort_gen", 0)

        for attempt in range(self.max_retries + 1):
            if getattr(self, "_abort_gen", 0) != abort_gen:
                return
            try:
                for line in self.http.post_stream(
                    f"{self.base_url.rstrip('/')}/api/append_message",
                    body,
                    extra_headers=headers,
                    read_timeout=self.stream_timeout,
                ):
                    line = (line or "").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line:
                        continue
                    if line in ("DONE", "[DONE]"):
                        yield LLMChunk(finish_reason="stop")
                        return
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("completion"):
                        saw_payload = True
                        yield LLMChunk(delta_text=str(ev.get("completion")), native=ev)
                    elif ev.get("error"):
                        raise RuntimeError(str(ev.get("error")))
                if saw_payload:
                    yield LLMChunk(finish_reason="stop")
                    return
                raise RuntimeError("stream ended without a response")
            except StreamHTTPError as he:
                if he.status_code in (401, 403):
                    # Dead/invalid session key — fatal, no point retrying auth.
                    yield LLMChunk(
                        delta_text=f"\n[claude.ai rejected the session key (HTTP {he.status_code}). Run /login claudeauth with a fresh sessionKey.]\n",
                        finish_reason="error",
                    )
                    return
                if not he.status_code or not self._is_retryable_http_code(he.status_code) or attempt >= self.max_retries:
                    yield LLMChunk(delta_text=f"\n[HTTP {he.status_code} from claude.ai: {he.body[:300]}]\n", finish_reason="error")
                    return
                time.sleep(self._retry_delay(attempt))
                continue
            except Exception as ex:
                if is_transient_transport_error(ex) and not saw_payload and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt))
                    continue
                yield LLMChunk(delta_text=f"\n[claude.ai web session error: {str(ex)}]\n", finish_reason="error")
                return
        yield LLMChunk(delta_text="\n[claude.ai web session: retries exhausted]\n", finish_reason="error")
