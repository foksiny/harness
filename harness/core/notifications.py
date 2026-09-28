"""
Cross-platform desktop notifications for Harness.

Fires a native OS notification when the agent finishes a task or a turn
ends in an error, so the user can context-switch back the moment the run
is over. Zero external dependencies — each platform uses its built-in
mechanism:

- macOS:    ``osascript`` display notification (with sound)
- Linux:    ``notify-send`` (libnotify, shipped by every desktop env)
- Windows:  PowerShell balloon tip (rendered as a toast on Win10+) + sound
- Fallback: terminal bell (BEL byte) when no OS backend is available

Everything here is best-effort: a notification must NEVER crash or delay
the agent. Every call is wrapped, time-boxed, and silently degrades to
the bell (or a no-op when stderr is not a TTY).
"""
import shutil
import subprocess
import sys
from typing import List

_TIMEOUT = 8  # seconds; generous enough for a cold-starting PowerShell

APP_NAME = "Harness"
_MAX_MESSAGE = 220  # keep toasts to a single readable banner


def _in_test_sandbox() -> bool:
    """True while the hermetic test sandbox is active — never notify during tests."""
    try:
        from harness.testing import is_installed
        return is_installed()
    except Exception:
        return False


def _escape_applescript(text: str) -> str:
    """Escape backslashes and double quotes for an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_powershell(text: str) -> str:
    """Escape single quotes for a PowerShell single-quoted string literal."""
    return text.replace("'", "''")


def _macos_args(title: str, message: str, urgent: bool) -> List[str]:
    sound = "Basso" if urgent else "Glass"
    script = (
        f'display notification "{_escape_applescript(message)}" '
        f'with title "{_escape_applescript(title)}" sound name "{sound}"'
    )
    return ["osascript", "-e", script]


def _linux_args(title: str, message: str, urgent: bool) -> List[str]:
    return [
        "notify-send",
        "--app-name", APP_NAME,
        "--urgency", "critical" if urgent else "normal",
        title,
        message,
    ]


def _windows_args(title: str, message: str, urgent: bool) -> List[str]:
    icon = "Error" if urgent else "Information"
    tip_icon = "Error" if urgent else "Info"
    sound = "Exclamation" if urgent else "Asterisk"
    script = ";".join([
        "Add-Type -AssemblyName System.Windows.Forms",
        "Add-Type -AssemblyName System.Drawing",
        f"$n = New-Object System.Windows.Forms.NotifyIcon",
        f"$n.Icon = [System.Drawing.SystemIcons]::{icon}",
        "$n.Visible = $true",
        f"$n.BalloonTipTitle = '{_escape_powershell(title)}'",
        f"$n.BalloonTipText = '{_escape_powershell(message)}'",
        f"$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{tip_icon}",
        "$n.ShowBalloonTip(6000)",
        f"[System.Media.SystemSounds]::{sound}.Play()",
        "Start-Sleep -Milliseconds 1200",
        "$n.Dispose()",
    ])
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def _run_quiet(args: List[str], timeout: float = _TIMEOUT) -> bool:
    """Run a notification command, discarding all output. True on success."""
    try:
        proc = subprocess.run(
            args,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except Exception:
        return False


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


def _notify_macos(title: str, message: str, urgent: bool) -> bool:
    return _run_quiet(_macos_args(title, message, urgent))


def _notify_linux(title: str, message: str, urgent: bool) -> bool:
    if not shutil.which("notify-send"):
        return False
    return _run_quiet(_linux_args(title, message, urgent))


def _notify_windows(title: str, message: str, urgent: bool) -> bool:
    if not shutil.which("powershell") and not shutil.which("powershell.exe"):
        return False
    return _run_quiet(_windows_args(title, message, urgent), timeout=_TIMEOUT + 4)


def send_notification(title: str, message: str, urgent: bool = False) -> bool:
    """Send a native desktop notification on any platform. Never raises.

    Returns True when a backend (OS notification or bell) reported success.
    """
    if _in_test_sandbox():
        return False
    try:
        # Collapse all whitespace so titles/messages are always a single line.
        title = " ".join(str(title or "").split())
        message = " ".join(str(message or "").split())
        if not title and not message:
            return False

        system = sys.platform
        if system == "darwin":
            ok = _notify_macos(title, message, urgent)
        elif system.startswith("win"):
            ok = _notify_windows(title, message, urgent)
        else:
            # Linux, *BSD, and anything else desktop-ish goes through libnotify.
            ok = _notify_linux(title, message, urgent)
        return ok or _bell()
    except Exception:
        return _bell()


def _truncate(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > _MAX_MESSAGE:
        text = text[:_MAX_MESSAGE - 3].rstrip() + "..."
    return text


def notify_task_complete(summary: str = "", enabled: bool = True) -> bool:
    """Notify the user that the agent finished its task. No-op when disabled."""
    if not enabled:
        return False
    return send_notification(
        f"{APP_NAME}: Task Complete",
        _truncate(summary) or "The agent finished its task.",
        urgent=False,
    )


def notify_task_failed(error: str = "", enabled: bool = True) -> bool:
    """Notify the user that the agent stopped because of an error."""
    if not enabled:
        return False
    return send_notification(
        f"{APP_NAME}: Task Failed",
        _truncate(error) or "The agent stopped because of an error.",
        urgent=True,
    )
