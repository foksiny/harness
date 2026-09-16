"""
CLI ↔ Discord bidirectional sync for Harness.

Two layers:

- **In-process**: :class:`MessageRelay` callbacks, used when the TUI hosts the
  bot in a background thread (``discord_auto_start``).
- **Cross-process**: a tiny append-only JSONL event bus
  (``~/.harness/sync_bus.jsonl``) so a standalone ``harness discord`` process
  stays in sync with an interactive TUI — messages, agent output, state changes
  (``/mode``, ``/provider``, ``/session``, …) and stop requests.

Events are dictionaries with at least ``id``, ``ts``, ``kind`` and ``origin``.
Each consumer keeps its own :class:`SyncCursor` so multiple pollers in the same
process never steal each other's events. All file I/O is wrapped so a broken
disk can never crash the caller.
"""
import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional

# Events older than this are considered stale (dead publisher) and dropped.
SYNC_EVENT_TTL = 900.0
# Rotate the bus file once it grows past this size…
_BUS_ROTATE_BYTES = 1_000_000
# …keeping the most recent tail of this size.
_BUS_KEEP_BYTES = 64_000

MESSAGE = "message"   # user-typed text (prompt / command line)
OUTPUT = "output"     # agent-produced text mirrored for display
STATE = "state"       # config/agent state change (mode, provider, session, …)
STOP = "stop"         # interrupt request


def default_bus_path() -> Path:
    env = os.environ.get("HARNESS_SYNC_BUS")
    if env:
        return Path(env)
    return Path.home() / ".harness" / "sync_bus.jsonl"


class SyncCursor:
    """Per-consumer read position + de-duplication state for a :class:`SyncBus`."""

    __slots__ = ("offset", "seen", "start_ts")

    def __init__(self, offset: int = 0, start_ts: Optional[float] = None):
        self.offset = offset
        self.seen: Deque[str] = deque(maxlen=2048)
        self.start_ts = start_ts if start_ts is not None else time.time()


class SyncBus:
    """Append-only JSONL event bus shared by the CLI and the Discord bot.

    ``publish()`` appends one event per line. ``poll()`` returns events a given
    cursor has not consumed yet, skipping stale (TTL-expired) and pre-start
    events. The file is rotated (atomic replace) when it grows too large; cursors
    self-heal after rotation and the cursor's ``seen`` set prevents replays.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_bus_path()
        self._write_lock = threading.Lock()

    def new_cursor(self) -> SyncCursor:
        """A cursor positioned at the current end of the bus (no backlog replay)."""
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return SyncCursor(offset=size)

    def publish(self, event: Dict) -> None:
        """Append one event. Never raises."""
        try:
            event = dict(event)
            event.setdefault("id", uuid.uuid4().hex)
            event.setdefault("ts", time.time())
            event.setdefault("ttl", SYNC_EVENT_TTL)
            with self._write_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, separators=(",", ":")) + "\n")
                self._maybe_rotate()
        except Exception:
            pass

    def _maybe_rotate(self) -> None:
        try:
            if self.path.stat().st_size < _BUS_ROTATE_BYTES:
                return
            data = self.path.read_bytes()
            tail = data[-_BUS_KEEP_BYTES:]
            # Keep only complete lines from the tail.
            nl = tail.find(b"\n")
            if nl >= 0:
                tail = tail[nl + 1:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(tail)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def poll(self, cursor: SyncCursor, limit: int = 64) -> List[Dict]:
        """Return unconsumed events for this cursor and advance it. Never raises."""
        events: List[Dict] = []
        try:
            if not self.path.exists():
                return events
            size = self.path.stat().st_size
            if cursor.offset > size:
                # File was rotated/truncated under us — restart from the top;
                # the cursor's ``seen`` set de-duplicates replayed tail events.
                cursor.offset = 0
            if cursor.offset == size:
                return events
            with open(self.path, "r", encoding="utf-8") as f:
                f.seek(cursor.offset)
                chunk = f.read(_BUS_ROTATE_BYTES)
                cursor.offset = f.tell()
            now = time.time()
            seen = cursor.seen
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                eid = ev.get("id")
                if not eid or eid in seen:
                    continue
                ts = float(ev.get("ts", 0) or 0)
                ttl = float(ev.get("ttl", SYNC_EVENT_TTL) or SYNC_EVENT_TTL)
                if ts < cursor.start_ts or (now - ts) > ttl:
                    continue
                seen.append(eid)
                events.append(ev)
                if len(events) >= limit:
                    break
        except Exception:
            pass
        return events


class MessageRelay:
    """Thread-safe bidirectional relay between CLI and Discord.

    Keeps the historical in-process callback API (``register_cli`` /
    ``register_discord`` / ``relay_from_cli`` / ``relay_from_discord``) and adds
    state synchronization (``publish_state`` / ``register_state_listener``) plus
    a cross-process :class:`SyncBus` so separate CLI and bot processes mirror
    each other. All bus operations are best-effort and never raise.
    """

    def __init__(self, bus_path: Optional[Path] = None):
        self._lock = threading.Lock()
        self._cli_callback: Optional[Callable[[str], None]] = None
        self._discord_callback: Optional[Callable[[str, Optional[int]], None]] = None
        self._state_listeners: List[Callable[[Dict, str], None]] = []
        self._history: List[dict] = []
        self.bus = SyncBus(bus_path) if bus_path is not None else SyncBus()

    # ── Callback registration ───────────────────────────────────────────

    def register_cli(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._cli_callback = callback

    def register_discord(self, callback: Callable[[str, Optional[int]], None]) -> None:
        with self._lock:
            self._discord_callback = callback

    def register_state_listener(self, callback: Callable[[Dict, str], None]) -> None:
        """Register ``callback(state_dict, origin)`` for state sync events."""
        with self._lock:
            if callback not in self._state_listeners:
                self._state_listeners.append(callback)

    # ── Message relay (in-process callbacks + bus mirror) ────────────────

    def relay_from_cli(self, text: str, meta: Optional[Dict] = None) -> None:
        """CLI received user input; relay to Discord (callbacks + bus)."""
        with self._lock:
            self._history.append({"source": "cli", "text": text})
            cb = self._discord_callback
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": text, **(meta or {})})
        if cb:
            try:
                cb(text, None)
            except Exception:
                pass

    def relay_from_discord(self, text: str, channel_id: Optional[int] = None, meta: Optional[Dict] = None) -> None:
        """Discord received user input; relay to CLI (callbacks + bus)."""
        with self._lock:
            self._history.append({"source": "discord", "text": text, "channel_id": channel_id})
            cb = self._cli_callback
        self.bus.publish({
            "kind": MESSAGE, "origin": "discord", "text": text, "channel_id": channel_id, **(meta or {}),
        })
        if cb:
            try:
                cb(text)
            except Exception:
                pass

    def relay_output(self, text: str, origin: str, channel_id: Optional[int] = None) -> None:
        """Agent output text, mirrored to the *other* side for display only."""
        self.bus.publish({
            "kind": OUTPUT, "origin": origin, "text": text, "channel_id": channel_id,
        })

    # ── State synchronization ──────────────────────────────────────────

    def publish_state(self, payload: Dict, origin: str) -> None:
        """Broadcast a state change (mode, provider, model, session, …)."""
        with self._lock:
            self._history.append({"source": origin, "kind": "state", "payload": dict(payload)})
            listeners = list(self._state_listeners)
        self.bus.publish({"kind": STATE, "origin": origin, "payload": dict(payload)})
        for cb in listeners:
            try:
                cb(dict(payload), origin)
            except Exception:
                pass

    def publish_stop(self, origin: str, channel_id: Optional[int] = None) -> None:
        """Broadcast an interrupt request for any running agent turn."""
        self.bus.publish({"kind": STOP, "origin": origin, "channel_id": channel_id})

    def get_history(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return list(self._history[-limit:])


_global_relay: Optional[MessageRelay] = None
_relay_lock = threading.Lock()


def get_relay() -> MessageRelay:
    global _global_relay
    if _global_relay is None:
        with _relay_lock:
            if _global_relay is None:
                _global_relay = MessageRelay()
    return _global_relay
