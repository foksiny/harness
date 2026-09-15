"""
CLI ↔ Discord bidirectional message sync for Harness.

Provides a thread-safe relay so messages sent from Discord appear in the CLI
and vice versa. Both sides register a callback; when either side receives a
message, the other side's callback is invoked.
"""
import threading
from typing import Callable, List, Optional


class MessageRelay:
    """Thread-safe bidirectional message relay between CLI and Discord."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cli_callback: Optional[Callable[[str], None]] = None
        self._discord_callback: Optional[Callable[[str, Optional[int]], None]] = None
        self._history: List[dict] = []

    def register_cli(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._cli_callback = callback

    def register_discord(self, callback: Callable[[str, Optional[int]], None]) -> None:
        with self._lock:
            self._discord_callback = callback

    def relay_from_cli(self, text: str) -> None:
        """CLI received user input; relay to Discord."""
        with self._lock:
            self._history.append({"source": "cli", "text": text})
            cb = self._discord_callback
        if cb:
            try:
                cb(text, None)
            except Exception:
                pass

    def relay_from_discord(self, text: str, channel_id: Optional[int] = None) -> None:
        """Discord received user input; relay to CLI."""
        with self._lock:
            self._history.append({"source": "discord", "text": text, "channel_id": channel_id})
            cb = self._cli_callback
        if cb:
            try:
                cb(text)
            except Exception:
                pass

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
