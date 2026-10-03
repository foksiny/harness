"""
End-of-turn sound effects for Harness.

Plays a short, synthesized chime the moment the agent finishes a task (or a
distinct lower tone when a turn ends in an error), so the user gets an audible
cue to look back at the terminal.

Zero external dependencies: the tones are generated as raw PCM with the stdlib
(``array`` + ``wave``) into a temporary WAV, then handed to whatever player the
platform already ships:

- macOS:   ``afplay``
- Linux:   ``paplay`` (PulseAudio) → ``pw-play`` (PipeWire) → ``aplay`` (ALSA)
- Windows: PowerShell ``System.Media.SoundPlayer``
- Fallback: terminal bell (BEL byte) when no player is available

Everything is best-effort and asynchronous (playback runs detached via
``Popen``) so a sound problem can never block, crash, or delay the agent.
"""
import atexit
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from typing import List, Optional, Sequence, Tuple

APP_NAME = "Harness"

_RATE = 44100          # CD sample rate; universal support, plenty for a chime
_GAIN = 0.30           # headroom so the cue is noticeable but never startling
_TEMP_DIR: Optional[str] = None
_CACHE: dict = {}      # tone name -> path to generated WAV


def _in_test_sandbox() -> bool:
    """True while the hermetic test sandbox is active — never play during tests."""
    try:
        from harness.testing import is_installed
        return is_installed()
    except Exception:
        return False


# ── Tone recipes ──────────────────────────────────────────────────────────
# Each note is (start_seconds, frequency_hz, duration_seconds).

_COMPLETE: Sequence[Tuple[float, float, float]] = (
    (0.00, 1046.50, 0.42),   # C6
    (0.09, 1318.51, 0.42),   # E6
    (0.18, 1567.98, 0.55),   # G6 — resolves on the octave-up major triad
)

_ERROR: Sequence[Tuple[float, float, float]] = (
    (0.00, 622.25, 0.26),    # D#5
    (0.16, 415.30, 0.42),    # G#4 — a deliberately flat, minor fall
)

TONES = {"complete": _COMPLETE, "error": _ERROR}


# ── Synthesis ─────────────────────────────────────────────────────────────

def _note_samples(rate: int, freq: float, duration: float) -> List[float]:
    """Render one bell-like note into float samples in [-1.0, 1.0].

    Uses a fundamental plus two decaying harmonics and a fast attack/exp-decay
    envelope so it reads as a soft chime instead of a harsh beep.
    """
    total = max(1, int(rate * duration))
    attack = max(1, int(rate * 0.004))
    out: List[float] = []
    for i in range(total):
        t = i / rate
        wave_mix = (
            math.sin(2.0 * math.pi * freq * t)
            + 0.28 * math.sin(4.0 * math.pi * freq * t)
            + 0.10 * math.sin(6.0 * math.pi * freq * t)
        ) / 1.38
        # Exponential decay with a click-free attack ramp.
        env = math.exp(-4.2 * (i / total))
        if i < attack:
            env *= i / attack
        out.append(wave_mix * env)
    return out


def synthesize(notes: Sequence[Tuple[float, float, float]], rate: int = _RATE) -> array:
    """Mix overlapping notes into a single 16-bit mono PCM buffer."""
    span = max(start + dur for start, _f, dur in notes) if notes else 0.0
    total = max(1, int(rate * span))
    buf = [0.0] * total
    for start, freq, dur in notes:
        offset = int(start * rate)
        samples = _note_samples(rate, freq, dur)
        for i, value in enumerate(samples):
            if offset + i >= total:
                break
            buf[offset + i] += value
    pcm = array("h", (int(max(-1.0, min(1.0, v * _GAIN)) * 32767) for v in buf))
    return pcm


def _temp_dir() -> Optional[str]:
    """Lazily create (and auto-clean) the scratch dir holding generated WAVs."""
    global _TEMP_DIR
    if _TEMP_DIR and os.path.isdir(_TEMP_DIR):
        return _TEMP_DIR
    try:
        _TEMP_DIR = tempfile.mkdtemp(prefix="harness-sounds-")

        def _cleanup() -> None:
            shutil.rmtree(_TEMP_DIR, ignore_errors=True)

        atexit.register(_cleanup)
        return _TEMP_DIR
    except Exception:
        return None


def tone_path(name: str = "complete") -> Optional[str]:
    """Return a cached WAV path for a tone, generating it on first use."""
    if name not in TONES:
        name = "complete"
    if name in _CACHE and os.path.exists(_CACHE[name]):
        return _CACHE[name]
    directory = _temp_dir()
    if not directory:
        return None
    path = os.path.join(directory, f"{APP_NAME}-{name}.wav".lower())
    try:
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(_RATE)
            handle.writeframes(synthesize(TONES[name], _RATE).tobytes())
    except Exception:
        return None
    _CACHE[name] = path
    return path


# ── Playback ──────────────────────────────────────────────────────────────

def _player_argv() -> Optional[List[str]]:
    """First available system audio player on this platform, as argv prefix."""
    system = sys.platform
    if system == "darwin":
        if shutil.which("afplay"):
            return ["afplay"]
        return None
    if system.startswith("win"):
        for exe in ("powershell", "powershell.exe"):
            if shutil.which(exe):
                return [exe, "-NoProfile", "-NonInteractive", "-Command"]
        return None
    for exe in ("paplay", "pw-play", "aplay", "play"):  # PulseAudio, PipeWire, ALSA, SoX
        if shutil.which(exe):
            return [exe]
    return None


def _windows_play_script(path: str) -> str:
    return (
        "Add-Type -AssemblyName System.Windows.Forms;"
        f"$p = New-Object System.Media.SoundPlayer '{path}';"
        "$p.Play();$p.Dispose()"
    )


def _bell() -> bool:
    """Terminal-bell fallback. Only fires when stderr is an interactive TTY."""
    try:
        if sys.stderr is not None and sys.stderr.isatty():
            sys.stderr.write("\a")
            sys.stderr.flush()
            return True
    except Exception:
        pass
    return False


def _spawn(args: List[str]) -> bool:
    """Fire-and-forget a player process so the agent never blocks on audio."""
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def play(name: str = "complete", enabled: bool = True) -> bool:
    """Play a named tone ('complete' or 'error'). Returns True when a cue fired.

    Never raises and never blocks: a missing player degrades to the terminal
    bell, and nothing happens at all inside the hermetic test sandbox.
    """
    if not enabled or _in_test_sandbox():
        return False
    try:
        path = tone_path(name)
        prefix = _player_argv()
        if path and prefix:
            if sys.platform.startswith("win"):
                return _spawn(prefix + [_windows_play_script(path)])
            return _spawn(prefix + [path])
        return _bell()
    except Exception:
        return _bell()


def play_task_complete(enabled: bool = True) -> bool:
    """Sweet ascending chime: the agent finished its task."""
    return play("complete", enabled=enabled)


def play_task_failed(enabled: bool = True) -> bool:
    """Flat two-note fall: the agent stopped because of an error."""
    return play("error", enabled=enabled)