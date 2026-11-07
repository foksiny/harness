"""Company mode: an always-on companion that watches, chats, and helps.

``/company`` starts a background loop that repeatedly asks the agent to
look at the screen and decide whether to speak up, side-chat through the
focused app, help with a game the user is playing, or stay quiet. It runs
until stopped via ``/stop``, ``/company stop``, Ctrl+C, the in-terminal
``Ctrl+Shift+Esc``, a Super+Shift+Escape watcher (kernel evdev, where
readable), or a stop-file the user can bind a GNOME custom shortcut to —
see :meth:`CompanyController.stop_file_path`.

This module is deliberately hermetic: the capture, step, sleep and
announce functions are injected, so tests never touch a display, network,
or the user's keyboard. The real wiring (``agent.step`` + TUI renderer)
is passed in by :mod:`harness.commands.registry`.
"""
from __future__ import annotations

import os
import shutil
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# Evdev key codes for the Super+Shift+Esc chord.
_EV_SUPER_L, _EV_SUPER_R = 125, 126  # KEY_LEFTMETA, KEY_RIGHTMETA
_EV_SHIFT_L, _EV_SHIFT_R = 42, 54   # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
_EV_ESC = 1                         # KEY_ESC

DEFAULT_TICK_INTERVAL = 25.0  # seconds of quiet between company beats
DEFAULT_ROLE = "general company: hang out, watch the screen, chat when there's something worth interrupting for"

COMPANY_BANNER_ITEMS = (
    "Runs on its own until you stop it: /stop, /company stop, Ctrl+C, or Ctrl+Shift+Esc.",
    "Quiet by default — it only speaks or acts when the screen shows something worth interrupting for.",
    "All typing keeps the usual guards: it never types into an overview/launcher, and never presses Return for text it cannot prove landed in the right input.",
)


def default_stop_path() -> str:
    """User-bindable stop file (e.g. a GNOME shortcut touching this path)."""
    base = os.environ.get("HARNESS_COMPANY_STOP_FILE", "").strip()
    if base:
        return os.path.expanduser(base)
    return os.path.expanduser("~/.harness/company.stop")


def build_tick_prompt(role: str, tick_no: int, last_speech: str = "",
                      quiet_streak: int = 0) -> str:
    """One company beat. The agent decides: screenshot, speak, act, or none."""
    role = (role or DEFAULT_ROLE).strip() or DEFAULT_ROLE
    history = ""
    if last_speech:
        history = f"\nYou last said: {last_speech[:200]!r}. Do not repeat yourself."
    if quiet_streak >= 3:
        history += f"\nYou have stayed quiet for {quiet_streak} beats. A brief hello is welcome if it feels natural — but do not force it."
    return (
        f"[company beat #{tick_no}] Role: {role}.{history}\n"
        "You are the user's company while they do their own thing (playing, "
        "chatting, working). Look at the screen (computer_screenshot) and "
        "decide what a good companion does right now:\n"
        "- Default to silence. Only speak or act when the screen shows "
        "something worth interrupting for: a new message addressed to the "
        "user, an invitation, a screenshot change suggesting a game event, "
        "or a tiny moment of colour after a long quiet stretch.\n"
        "- To chat in another app (e.g. Discord), click its input, then use "
        "computer_type with expect set to a word visible in that app "
        "(channel or app name) so it can never land in the wrong window. "
        "Follow the COMPUTER_PROTOCOL guards strictly: never type into an "
        "overview/launcher, never force Return for unverified text.\n"
        "- To help with a game, act only when the action is clearly useful "
        "and low-risk. Prefer observing over clicking; never fight the user "
        "for focus.\n"
        "- If nothing is worth it, say nothing and finish. Short is better "
        "than chatty. Never restate this brief."
    )


def voice_requested(role: str) -> bool:
    """Out-loud speech is opt-in via the role text (e.g. 'speak out loud')."""
    text = (role or "").lower()
    return any(
        marker in text
        for marker in ("out loud", "speak aloud", "tts", "read aloud"))


def speak(text: str, max_chars: int = 240) -> bool:
    """Best-effort speech via speech-dispatcher or espeak-ng. Never raises."""
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    clean = clean[:max_chars]
    try:
        from harness.testing import is_installed
        if is_installed():
            return False
    except Exception:
        pass
    for cmd in ("spd-say", "espeak-ng", "espeak"):
        exe = shutil.which(cmd)
        if not exe:
            continue
        args = [exe]
        if cmd == "spd-say":
            args += ["--wait"]
        args.append(clean)
        try:
            import subprocess
            subprocess.run(args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            return True
        except Exception:
            continue
    return False


def parse_evdev_events(blob: bytes) -> List[bool]:
    """Decode a run of evdev input_events (struct input_event, Linux).

    Returns a list of ``pressed`` flags for every event that completes the
    Super+Shift+Esc chord (all three modifiers down, Esc going down).
    Empty for short/invalid buffers — never raises.
    """
    hits: List[bool] = []
    try:
        count = len(blob) // 24
        down = set()
        for i in range(count):
            _sec, _usec, etype, ecode, evalue = struct.unpack_from(
                "<qqHHi", blob, i * 24)
            if etype != 0x01:  # EV_KEY
                continue
            if evalue == 1:  # key down
                down.add(ecode)
                if (ecode == _EV_ESC
                        and any(m in down for m in (_EV_SUPER_L, _EV_SUPER_R))
                        and any(m in down for m in (_EV_SHIFT_L, _EV_SHIFT_R))):
                    hits.append(True)
            elif evalue == 0:  # key up
                down.discard(ecode)
    except Exception:
        return []
    return hits


def input_event_files() -> List[str]:
    """Readable /dev/input/event* paths (best-effort, never raises)."""
    out: List[str] = []
    try:
        for name in sorted(os.listdir("/dev/input")):
            if not name.startswith("event") or not name[5:].isdigit():
                continue
            path = os.path.join("/dev/input", name)
            try:
                if os.path.exists(path) and os.access(path, os.R_OK):
                    out.append(path)
            except OSError:
                continue
    except OSError:
        pass
    return out


class CompanyController:
    """One autonomous companion loop (daemon thread).

    ``step_fn`` runs a single agent turn for a beat and returns its
    events; ``render_fn`` sinks those events (TUI renderer); ``announce_fn``
    prints short notices. ``sleep_fn(tick_interval, stop_event)`` waits
    interruptibly between beats (default ``stop_event.wait``). ``watch_fn``
    optionally runs in parallel (e.g. the evdev watcher).
    """

    def __init__(
        self,
        step_fn: Optional[Callable[[str], Any]] = None,
        render_fn: Optional[Callable[[Any], None]] = None,
        announce_fn: Optional[Callable[[str], None]] = None,
        sleep_fn: Optional[Callable[[float, threading.Event], None]] = None,
        speak_fn: Optional[Callable[[str], bool]] = None,
        finish_fn: Optional[Callable[[], None]] = None,
        tick_interval: float = DEFAULT_TICK_INTERVAL,
        stop_path: Optional[str] = None,
    ) -> None:
        self._step_fn = step_fn
        self._render_fn = render_fn
        self._announce_fn = announce_fn
        self._sleep_fn = sleep_fn
        self._finish_fn = finish_fn
        self._speak_fn = speak_fn or (lambda _text: False)
        self.tick_interval = max(1.0, float(tick_interval or DEFAULT_TICK_INTERVAL))
        self.stop_path = stop_path or default_stop_path()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.role = ""
        self.tick_no = 0
        self.last_speech = ""
        self.quiet_streak = 0
        self.started_at = 0.0
        self.stopped_reason = ""
        self._tick_is_running = False

    # -- lifecycle --------------------------------------------------
    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def stop_file_path(self) -> str:
        return self.stop_path

    def start(self, role: str) -> bool:
        """Begin looping. False when already running (no duplicate)."""
        with self._lock:
            if self.is_running:
                return False
            self.role = (role or "").strip() or DEFAULT_ROLE
            self._stop_event = threading.Event()
            self.tick_no = 0
            self.last_speech = ""
            self.quiet_streak = 0
            self.started_at = time.time()
            self.stopped_reason = ""
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="harness-company")
            self._watch_thread = threading.Thread(
                target=self._watch_evdev, daemon=True, name="harness-company-evdev")
            thread = self._thread
            watch = self._watch_thread
        thread.start()
        watch.start()
        return True

    def stop(self, reason: str = "stopped by user") -> None:
        """Request stop. Safe to call repeatedly and from any thread."""
        if self.stopped_reason:
            return
        self.stopped_reason = reason
        self._stop_event.set()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "role": self.role,
            "tick": self.tick_no,
            "quiet_streak": self.quiet_streak,
            "interval": self.tick_interval,
            "stop_file": self.stop_path,
            "evdev_files": input_event_files(),
            "stopped_reason": self.stopped_reason,
        }

    # -- helpers ----------------------------------------------------
    def _announce(self, text: str) -> None:
        if self._announce_fn is None:
            return
        try:
            self._announce_fn(text)
        except Exception:
            pass

    def _check_stop_file(self) -> bool:
        """True (and consume the file) when the watcher file appeared."""
        try:
            if os.path.exists(self.stop_path):
                try:
                    os.remove(self.stop_path)
                except OSError:
                    pass
                return True
        except OSError:
            pass
        return False

    def _sleep_interval(self) -> None:
        """Interruptible quiet period, polling the stop file too."""
        deadline = time.time() + self.tick_interval
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            if self._sleep_fn is not None:
                # Injected sleeps (tests) replace the whole interval.
                self._sleep_fn(remaining, self._stop_event)
                return
            step = min(0.5, remaining)
            if self._stop_event.wait(timeout=step):
                return
            if self._check_stop_file():
                self.stop("stopped via stop-file (Super+Shift+Esc shortcut)")
                return

    def _watch_evdev(self) -> None:
        """Super+Shift+Esc via raw evdev where the user can read it.

        GNOME consumes the chord before it reaches the terminal, but the
        kernel still sees it. This watcher is best-effort: no readable
        device (e.g. the user is not in the ``input`` group) means no
        watcher, and the other stop paths still apply.
        """
        import select
        for path in input_event_files():
            if self._stop_event.is_set():
                return
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                while not self._stop_event.is_set():
                    ready, _, _ = select.select([fd], [], [], 0.5)
                    if not ready:
                        continue
                    try:
                        blob = os.read(fd, 24 * 32)
                    except OSError:
                        break
                    if parse_evdev_events(blob):
                        self.stop("stopped via Super+Shift+Esc")
                        return
            except Exception:
                pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _run_one_tick(self) -> None:
        prompt = build_tick_prompt(
            self.role, self.tick_no, self.last_speech, self.quiet_streak)
        parts: List[str] = []
        self._tick_is_running = True
        try:
            events = self._step_fn(prompt) if self._step_fn else []
            for ev in events or []:
                if self._stop_event.is_set():
                    break
                if self._render_fn is not None:
                    self._render_fn(ev)
                if getattr(ev, "type", "") == "text_delta":
                    data = str(getattr(ev, "data", "") or "")
                    line = data.strip()
                    if line and not line.startswith("[Harness]"):
                        parts.append(data)
            if self._finish_fn is not None:
                try:
                    self._finish_fn()
                except Exception:
                    pass
        finally:
            self._tick_is_running = False
        spoken = "".join(parts).strip()
        if spoken:
            self.last_speech = spoken[-240:]
            self.quiet_streak = 0
            if voice_requested(self.role):
                try:
                    self._speak_fn(spoken)
                except Exception:
                    pass
        else:
            self.quiet_streak += 1

    def _loop(self) -> None:
        self._announce(
            f"🏠 Company started — {self.role} "
            f"(beat every {self.tick_interval:.0f}s, quiet by default)")
        try:
            while not self._stop_event.is_set():
                if self._check_stop_file():
                    self.stop("stopped via stop-file (Super+Shift+Esc shortcut)")
                    break
                self.tick_no += 1
                try:
                    self._run_one_tick()
                except KeyboardInterrupt:
                    self.stop("stopped via Ctrl+C")
                    break
                except Exception as exc:  # keep the companion alive on error
                    self._announce(f"🏠 Company tick failed ({exc}); continuing.")
                if self._stop_event.is_set():
                    break
                self._sleep_interval()
        finally:
            self._announce(
                f"🏠 Company stopped after {self.tick_no} beats "
                f"({self.stopped_reason or 'stopped by user'}).")
            self._stop_event.set()
