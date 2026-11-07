"""
Desktop / CLI driver helpers for Harness computer use.

Despite the historical filename, this module is cross-platform:

* Linux  — X11, XWayland, and native Wayland compositors (GNOME, COSMIC,
  KDE, Sway, Hyprland, XFCE, Cinnamon, MATE, ...).
* macOS  — built-in ``screencapture`` / ``pbcopy`` / AppleScript, with
  ``cliclick`` as an optional mouse driver.
* Windows — PowerShell + ``System.Drawing`` capture, WScript.SendKeys input,
  and the built-in clipboard cmdlets.

Exports:
* :func:`detect_platform` -> "linux" | "macos" | "windows"
* :func:`detect_session_type` -> "wayland" | "x11" | "xwayland" | "headless"
  (plus "macos" / "windows")
* :func:`detect_de` -> short desktop name ("gnome", "cosmic", "kde", ...),
  or the OS name on macOS/Windows.
* :func:`capture_hints` / :func:`input_hints` -> actionable fix strings.
* :func:`cli_capture` / :func:`cli_input` / :func:`cli_window` /
  :func:`cli_clipboard` -> structured-output shims that never raise.

Every function returns ``{"ok": bool, ...}`` and includes ``error`` + ``hints``
on failure, so the tools can tell the model exactly what to install or which
fallback to use instead of burning steps guessing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def detect_platform() -> str:
    """Return the host platform in a stable, lowercase form."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform or "unknown"


def detect_session_type() -> str:
    """Detect the active graphics session.

    Linux returns wayland / x11 / xwayland / headless. macOS and Windows
    return their own platform name so callers can branch on one value.
    """
    platform = detect_platform()
    if platform == "windows":
        return "windows"
    if platform == "macos":
        return "macos"
    wayland_display = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    display = (os.environ.get("DISPLAY") or "").strip()
    if not wayland_display and not display:
        return "headless"
    if wayland_display and display:
        return "xwayland"
    if wayland_display:
        return "wayland"
    return "x11"


def is_wayland() -> bool:
    return detect_session_type() in ("wayland", "xwayland")


# XDG_CURRENT_DESKTOP values are messy ("ubuntu:GNOME", "pop:GNOME",
# "KDE", "GNOME-Classic:GNOME", ...). Markers are checked in order.
_DE_MARKERS = (
    ("cosmic", ("cosmic",)),
    ("gnome", ("gnome", "ubuntu", "pop", "unity", "budgie", "pantheon")),
    ("kde", ("kde", "plasma")),
    ("xfce", ("xfce",)),
    ("cinnamon", ("cinnamon",)),
    ("mate", ("mate",)),
    ("lxqt", ("lxqt",)),
    ("lxde", ("lxde",)),
    ("sway", ("sway",)),
    ("hyprland", ("hyprland",)),
    ("niri", ("niri",)),
    ("river", ("river",)),
    ("wayfire", ("wayfire",)),
    ("i3", ("i3",)),
    ("openbox", ("openbox",)),
    ("awesome", ("awesome",)),
    ("dwm", ("dwm",)),
)


def detect_de() -> str:
    """Detect the desktop environment / windowing system.

    Checks XDG_CURRENT_DESKTOP (and XDG_SESSION_DESKTOP), then compositor
    binaries, then distro markers. Returns a lowercase short name. On macOS
    and Windows this returns "macos"/"windows".
    """
    platform = detect_platform()
    if platform == "windows":
        return "windows"
    if platform == "macos":
        return "macos"

    cur = (os.environ.get("XDG_CURRENT_DESKTOP") or "").strip().lower()
    ses = (os.environ.get("XDG_SESSION_DESKTOP") or "").strip().lower()
    for cand in (cur, ses):
        if not cand:
            continue
        for name, markers in _DE_MARKERS:
            if any(marker in cand for marker in markers):
                return name

    # Compositor / desktop binaries are more reliable than an unset env var.
    if _which("cosmic-comp") or _which("cosmic-screenshot"):
        return "cosmic"
    if _which("gnome-shell"):
        return "gnome"
    if _which("plasmashell") or _which("kscreen-doctor"):
        return "kde"
    if _which("xfce4-session"):
        return "xfce"
    if _which("cinnamon"):
        return "cinnamon"
    if _which("mate-session"):
        return "mate"
    if _which("swaymsg"):
        return "sway"
    if _which("hyprctl"):
        return "hyprland"
    if _which("i3-msg"):
        return "i3"

    # Unknown DE: keep the first token so the model still sees something
    # useful instead of an empty string.
    return cur.split(":")[0].split(";")[0] or "unknown"


def capture_hints() -> List[str]:
    out: List[str] = []
    platform = detect_platform()
    if platform == "windows":
        return [
            "Windows: mss/pyautogui capture is built in (pip install pyautogui mss)",
            "fallback: PowerShell System.Drawing capture is built in",
        ]
    if platform == "macos":
        return [
            "macOS: built-in `screencapture` is used automatically",
            "grant Screen Recording permission to your terminal in System Settings if captures are black",
        ]

    de = detect_de()
    if de == "cosmic":
        out.append("COSMIC: cosmic-screenshot is built-in (used automatically)")
        out.append("fallback: install grim for a Wayland screenshot")
    elif de == "gnome":
        out.append("GNOME: gnome-screenshot (used automatically) or gdbus Shell.Screenshot")
        out.append("fallback: install grim only on wlroots, not GNOME")
    elif de == "kde":
        out.append("KDE: spectacle -b -n -o (used automatically)")
        out.append("fallback: install grim on wlroots; scrot/import on X11")
    elif de in ("sway", "hyprland", "niri", "river", "wayfire", "i3"):
        out.append("wlroots: install grim for Wayland screenshots")
        out.append("X11 fallback: scrot or ImageMagick import")
    elif de in ("xfce", "cinnamon", "mate", "lxqt", "lxde"):
        out.append(f"{de}: install scrot, xfce4-screenshooter, or ImageMagick import")
    else:
        out.append("install grim (Wayland) or scrot/import (X11)")
    return out


def input_hints() -> List[str]:
    out: List[str] = []
    platform = detect_platform()
    if platform == "windows":
        return ["Windows: pyautogui input is built in (pip install pyautogui)",
                "fallback: PowerShell SendKeys is built in"]
    if platform == "macos":
        return ["macOS: pyautogui input is built in",
                "optional: brew install cliclick for a CLI mouse driver",
                "grant Accessibility permission to your terminal for send-keys"]

    de = detect_de()
    if is_wayland():
        if de in ("gnome", "kde", "cosmic"):
            out.append(f"{de} Wayland: ydotool (uinput) reaches native windows; xdotool only reaches XWayland")
        out.append("install ydotool + start ydotoold (uinput root or udev rules)")
    if not is_wayland():
        out.append("install xdotool (X11 input) or ensure libXtst/libX11 for ctypes XTEST")
    out.append("wmctrl/xdotool only see X11/XWayland windows — empty on native Wayland apps")
    return out


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: List[str],
    timeout: int = 10,
    input_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
        return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        return {"rc": -1, "stdout": "", "stderr": f"timed out: {exc}"}
    except Exception as exc:  # never raise from a backend probe
        return {"rc": -1, "stdout": "", "stderr": str(exc)}


def _file_ok(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _result_ok(path: str, backend: str) -> Optional[Dict[str, Any]]:
    if _file_ok(path):
        return {"ok": True, "path": path, "backend": backend}
    return None


def _powershell() -> Optional[str]:
    return shutil.which("powershell") or shutil.which("pwsh")


def _powershell_run(script: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    exe = _powershell()
    if not exe:
        return None
    return _run([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


def _osascript(script: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    if not _which("osascript"):
        return None
    return _run(["osascript", "-e", script], timeout=timeout)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _capture_windows(path: str, region: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Capture via PowerShell + System.Drawing (built-in, no extra deps)."""
    if not _powershell():
        return None
    escaped = path.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{escaped}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    _powershell_run(script, timeout=25)
    return _result_ok(path, "powershell")


def _capture_macos(path: str, region: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not _which("screencapture"):
        return None
    args = ["screencapture", "-x", "-t", "png"]
    if region and region.get("width"):
        args += [
            "-R",
            f"{int(region.get('x', 0))},{int(region.get('y', 0))},"
            f"{int(region.get('width', 0))},{int(region.get('height', 0))}",
        ]
    args.append(path)
    _run(args, timeout=20)
    return _result_ok(path, "screencapture")


def _capture_gnome_gdbus(path: str) -> Optional[Dict[str, Any]]:
    if not _which("gdbus"):
        return None
    _run([
        "gdbus", "call", "--session",
        "--dest", "org.gnome.Shell.Screenshot",
        "--object-path", "/org/gnome/Shell/Screenshot",
        "--method", "org.gnome.Shell.Screenshot.Screenshot",
        "false", "false", path,
    ], timeout=12)
    return _result_ok(path, "gdbus-gnome")


def _capture_gnome_cli(path: str) -> Optional[Dict[str, Any]]:
    if not _which("gnome-screenshot"):
        return None
    _run(["gnome-screenshot", "-f", path], timeout=15)
    return _result_ok(path, "gnome-screenshot")


def _capture_cosmic(path: str, out_dir: str) -> Optional[Dict[str, Any]]:
    if not _which("cosmic-screenshot"):
        return None
    out_dir_arg = os.path.dirname(path) or out_dir
    started = time.time()
    _run([
        "cosmic-screenshot", "--interactive=false", "--modal=false",
        "--notify=false", f"--save-dir={out_dir_arg}",
    ], timeout=15)
    if _result_ok(path, "cosmic-screenshot"):
        return {"ok": True, "path": path, "backend": "cosmic-screenshot"}
    # cosmic-screenshot names its own file; adopt the newest Screenshot_*.png
    # produced by THIS invocation (ignore older files already in the folder).
    import glob
    files = sorted(
        glob.glob(os.path.join(out_dir_arg, "Screenshot_*.png")),
        key=os.path.getmtime, reverse=True,
    )
    for candidate in files:
        try:
            if os.path.getmtime(candidate) < started - 1.0:
                continue
            if candidate != path:
                os.replace(candidate, path)
        except OSError:
            path = candidate
        return _result_ok(path, "cosmic-screenshot")
    return None


def _capture_spectacle(path: str) -> Optional[Dict[str, Any]]:
    if not _which("spectacle"):
        return None
    _run(["spectacle", "-b", "-n", "-o", path], timeout=20)
    return _result_ok(path, "spectacle")


def _capture_grim(path: str, region: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not _which("grim"):
        return None
    if region and region.get("width"):
        geom = (
            f"{int(region.get('width', 0))}x{int(region.get('height', 0))}"
            f"+{int(region.get('x', 0))}+{int(region.get('y', 0))}"
        )
        _run(["grim", "-g", geom, path], timeout=15)
    else:
        _run(["grim", path], timeout=15)
    return _result_ok(path, "grim")


def _capture_xfce(path: str, out_dir: str) -> Optional[Dict[str, Any]]:
    if not _which("xfce4-screenshooter"):
        return None
    # xfce4-screenshooter's -s flag takes a DIRECTORY, not a filename.
    save_dir = os.path.dirname(path) or out_dir
    started = time.time()
    _run(["xfce4-screenshooter", "-f", "-s", save_dir], timeout=15)
    if _result_ok(path, "xfce4-screenshooter"):
        return {"ok": True, "path": path, "backend": "xfce4-screenshooter"}
    import glob
    files = sorted(
        (f for f in glob.glob(os.path.join(save_dir, "*"))
         if f.lower().endswith((".png", ".jpg", ".jpeg"))),
        key=os.path.getmtime, reverse=True,
    )
    for candidate in files:
        try:
            if os.path.getmtime(candidate) < started - 1.0:
                continue
            if candidate != path:
                os.replace(candidate, path)
        except OSError:
            path = candidate
        return _result_ok(path, "xfce4-screenshooter")
    return None


def _capture_scrot(path: str, region: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not _which("scrot"):
        return None
    args = ["scrot", path]
    if region and region.get("width"):
        geom = (
            f"{int(region.get('width', 0))}x{int(region.get('height', 0))}"
            f"+{int(region.get('x', 0))}+{int(region.get('y', 0))}"
        )
        args = ["scrot", "-a", geom, "-o", path]
    _run(args, timeout=15)
    return _result_ok(path, "scrot")


def _capture_maim(path: str) -> Optional[Dict[str, Any]]:
    if not _which("maim"):
        return None
    _run(["maim", path], timeout=15)
    return _result_ok(path, "maim")


def _capture_import(path: str) -> Optional[Dict[str, Any]]:
    if not _which("import"):
        return None
    _run(["import", "-window", "root", path], timeout=20)
    return _result_ok(path, "import")


def cli_capture(
    region: Optional[Dict[str, Any]] = None,
    monitor_index: int = 0,
    out_dir: Optional[str] = None,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture the screen through platform-native CLI tools.

    Returns ``{"ok", "path", "backend"}`` or ``{"ok": False, "error", "hints"}``.
    Never raises.
    """
    out_dir = out_dir or os.path.expanduser("~/.harness/screenshots")
    os.makedirs(out_dir, exist_ok=True)
    name = (f"screen-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
            f"{int(time.time() * 1000) % 1000:03d}-{os.getpid()}.png")
    path = out_path or os.path.join(out_dir, name)

    platform = detect_platform()

    if platform == "windows":
        r = _capture_windows(path, region)
        if r:
            return r
        return {
            "ok": False,
            "error": "PowerShell screenshot failed",
            "hints": capture_hints(),
        }

    if platform == "macos":
        r = _capture_macos(path, region)
        if r:
            return r
        return {
            "ok": False,
            "error": "screencapture failed (Screen Recording permission?)",
            "hints": capture_hints(),
        }

    de = detect_de()
    # Ordered candidates per desktop. Keep the list short: each failed backend
    # can cost a second, and the agent is waiting on this call.
    candidate_names: List[str]
    if de == "cosmic":
        candidate_names = ["cosmic", "grim", "gnome_cli", "scrot", "import"]
    elif de == "gnome":
        candidate_names = ["gnome_cli", "gdbus", "grim", "scrot", "import"]
    elif de == "kde":
        candidate_names = ["spectacle", "grim", "scrot", "import"]
    elif de in ("sway", "hyprland", "niri", "river", "wayfire", "i3"):
        candidate_names = ["grim", "scrot", "import"]
    elif de == "xfce":
        candidate_names = ["xfce", "scrot", "import"]
    elif de in ("cinnamon", "mate", "lxqt", "lxde"):
        candidate_names = ["gnome_cli", "scrot", "import"]
    else:
        candidate_names = ["gnome_cli", "gdbus", "cosmic", "spectacle", "grim",
                           "xfce", "scrot", "maim", "import"]

    last_error = ""
    for cand in candidate_names:
        if cand == "cosmic":
            r = _capture_cosmic(path, out_dir)
        elif cand == "gdbus":
            r = _capture_gnome_gdbus(path)
        elif cand == "gnome_cli":
            r = _capture_gnome_cli(path)
        elif cand == "spectacle":
            r = _capture_spectacle(path)
        elif cand == "grim":
            r = _capture_grim(path, region)
        elif cand == "xfce":
            r = _capture_xfce(path, out_dir)
        elif cand == "scrot":
            r = _capture_scrot(path, region)
        elif cand == "maim":
            r = _capture_maim(path)
        elif cand == "import":
            r = _capture_import(path)
        else:
            r = None
        if r and r.get("ok"):
            return r
        last_error = last_error or f"{cand} did not produce a screenshot"

    return {
        "ok": False,
        "error": f"no screenshot CLI produced an image on {de} ({last_error})",
        "hints": capture_hints(),
    }


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _ydotool_button(name: str) -> int:
    return {
        "left": 1, "primary": 1,
        "middle": 3,
        "right": 2, "secondary": 2,
    }.get(str(name).lower(), 1)


# Key names arrive from several vocabularies: xdotool ("Return", "Page_Down"),
# pyautogui ("enter", "pagedown"), Qt/GTK ("Escape", "BackSpace"), and the
# model's own guesses ("cmd", "option"). Normalize them once to xdotool-style
# names, then let each backend translate from that canonical form.
_KEY_ALIASES = {
    "return": "Return", "enter": "Return", "kpenter": "Return",
    "escape": "Escape", "esc": "Escape",
    "backspace": "BackSpace", "backspace2": "BackSpace",
    "delete": "Delete", "del": "Delete", "forwarddelete": "Delete",
    "insert": "Insert", "ins": "Insert",
    "tab": "Tab", "space": "space", "spacebar": "space",
    "pageup": "Page_Up", "pgup": "Page_Up", "prior": "Page_Up",
    "pagedown": "Page_Down", "pgdn": "Page_Down", "next": "Page_Down",
    "home": "Home", "end": "End",
    "up": "Up", "arrowup": "Up", "uparrow": "Up",
    "down": "Down", "arrowdown": "Down", "downarrow": "Down",
    "left": "Left", "arrowleft": "Left", "leftarrow": "Left",
    "right": "Right", "arrowright": "Right", "rightarrow": "Right",
    "capslock": "Caps_Lock", "numlock": "Num_Lock",
    "scrolllock": "Scroll_Lock",
    "print": "Print", "printscreen": "Print", "prtsc": "Print",
    "prtscr": "Print", "sysrq": "Print",
    "pause": "Pause", "break": "Pause",
    "menu": "Menu", "apps": "Menu", "contextmenu": "Menu",
    "ctrl": "ctrl", "control": "ctrl",
    "ctrlr": "ctrl_r", "rightctrl": "ctrl_r", "rctrl": "ctrl_r",
    "shift": "shift", "shiftr": "shift_r", "rightshift": "shift_r",
    "rshift": "shift_r",
    "alt": "alt", "option": "alt",
    "altr": "alt_r", "rightalt": "alt_r", "ralt": "alt_r", "altgr": "alt_r",
    "super": "super", "meta": "super", "win": "super", "windows": "super",
    "cmd": "super", "command": "super", "logo": "super",
    "superr": "super_r", "metar": "super_r", "rightmeta": "super_r",
}
for _i in range(1, 25):
    _KEY_ALIASES[f"f{_i}"] = f"F{_i}"
del _i


def _normalize_key_name(name: str) -> str:
    """Map any common key vocabulary to one canonical xdotool-style name."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    if len(raw) == 1:
        return raw
    low = raw.lower().replace("-", "_").replace(" ", "_")
    return _KEY_ALIASES.get(low.replace("_", "")) or _KEY_ALIASES.get(low) or raw


def _canonical_key_tokens(keys: List[str]) -> List[str]:
    """Flatten ``["ctrl+c"]`` / ``["ctrl", "Page_Down"]`` into canonical keys."""
    tokens: List[str] = []
    for key in keys:
        for part in str(key).split("+"):
            part = part.strip()
            if part:
                tokens.append(_normalize_key_name(part))
    return tokens


# ydotool 0.1.8 resolves key names through libevdevPlus: modifier table, then
# function table, then it falls back to the FIRST CHARACTER (so "Return" would
# press R and "Page_Down" would press P). Everything therefore has to be
# translated to names it knows, and unknown names must fail loudly.
_YDOTOOL_KEYS = {
    "Return": "ENTER", "Escape": "ESC", "Tab": "TAB",
    "BackSpace": "BACKSPACE", "Delete": "DELETE", "Insert": "INSERT",
    "Home": "HOME", "End": "END",
    "Page_Up": "PAGEUP", "Page_Down": "PAGEDOWN",
    "Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT",
    "Caps_Lock": "CAPSLOCK", "Num_Lock": "NUMLOCK",
    "Scroll_Lock": "SCROLLLOCK", "Print": "SYSRQ", "Pause": "PAUSE",
    "ctrl": "CTRL", "ctrl_r": "CTRL_R",
    "shift": "SHIFT", "shift_r": "SHIFT_R",
    "alt": "ALT", "alt_r": "ALT_R",
    "super": "SUPER", "super_r": "SUPER_R",
    "space": " ",
}
for _i in range(1, 13):
    _YDOTOOL_KEYS[f"F{_i}"] = f"F{_i}"
del _i

# Single characters ydotool's fallback table understands without Shift.
_YDOTOOL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .,/\\;-=`'[]"
)


def _ydotool_key_token(token: str) -> str:
    canonical = _normalize_key_name(token)
    if canonical in _YDOTOOL_KEYS:
        return _YDOTOOL_KEYS[canonical]
    if canonical.startswith("F") and canonical[1:].isdigit():
        raise ValueError(
            f"ydotool 0.1.8 supports F1-F12, not {canonical}; "
            "use pyautogui/XTEST on X11/XWayland instead."
        )
    if canonical == "Menu":
        raise ValueError(
            "ydotool 0.1.8 cannot press Menu/Apps; use a screenshot mark "
            "or the app's own shortcut instead."
        )
    if len(canonical) == 1 and canonical in _YDOTOOL_CHARS:
        return canonical
    if len(canonical) == 1:
        raise ValueError(
            f"ydotool cannot press {canonical!r} as a key (it needs Shift); "
            "use computer_type for text characters."
        )
    raise ValueError(f"unknown key name {token!r}")


def _ydotool_key_sequence(keys: List[str]) -> str:
    tokens = _canonical_key_tokens(keys)
    if not tokens:
        raise ValueError("no key given")
    return "+".join(_ydotool_key_token(token) for token in tokens)


def _linux_input(action: Dict[str, Any]) -> Dict[str, Any]:
    name = (action.get("action") or "").lower()
    if not name:
        return {"ok": False, "error": "missing 'action' field"}

    # Native Wayland apps ignore XTEST/xdotool. Prefer ydotool there, then
    # fall back to xdotool for XWayland windows.
    if is_wayland() and _which("ydotool"):
        res = _ydotool_input(action)
        if res.get("ok"):
            return res
        if _which("xdotool"):
            alt = _xdotool_input(action)
            if alt.get("ok"):
                alt["warning"] = (
                    "Reached via XWayland only; if this was a native Wayland "
                    "window, the action may not have applied."
                )
                return alt
        return res

    if _which("xdotool"):
        return _xdotool_input(action)
    if _which("ydotool"):
        return _ydotool_input(action)

    return {
        "ok": False,
        "error": "no input CLI (xdotool/ydotool) available",
        "hints": input_hints(),
    }


def _xdotool_input(action: Dict[str, Any]) -> Dict[str, Any]:
    name = (action.get("action") or "").lower()
    backend = "xdotool"
    x, y = action.get("x"), action.get("y")

    if name == "move":
        cmd = ["xdotool", "mousemove", str(x or 0), str(y or 0)]
    elif name == "click":
        cmd = (["xdotool", "mousemove", str(x), str(y)] if x is not None and y is not None else [])
        cmd += ["xdotool", "click", str(action.get("button", "left"))]
    elif name in ("double_click", "double"):
        cmd = (["xdotool", "mousemove", str(x), str(y)] if x is not None and y is not None else [])
        cmd += ["xdotool", "click", "--repeat", "2", str(action.get("button", "left"))]
    elif name == "drag":
        cmd = [
            "xdotool",
            "mousemove", str(action.get("x1", 0)), str(action.get("y1", 0)),
            "mousedown", str(action.get("button", "left")),
            "mousemove", str(action.get("x2", 0)), str(action.get("y2", 0)),
            "mouseup", str(action.get("button", "left")),
        ]
    elif name == "scroll":
        button = "5" if str(action.get("direction", "down")).lower() == "down" else "4"
        steps = max(1, min(int(action.get("amount", 1) or 1), 20))
        if x is not None and y is not None:
            _run(["xdotool", "mousemove", str(x), str(y)])
        for _ in range(steps):
            res = _run(["xdotool", "click", button])
            if not res or res["rc"] != 0:
                return {"ok": False, "backend": backend,
                        "error": f"xdotool scroll failed: {(res and res['stderr'] or '').strip()}",
                        "hints": input_hints()}
        return {"ok": True, "backend": backend}
    elif name == "type":
        cmd = ["xdotool", "type", "--delay", "12", str(action.get("text", ""))]
    elif name in ("key", "press"):
        keys = _canonical_key_tokens([str(action.get("key", ""))])
        if not keys:
            return {"ok": False, "error": "key is required", "hints": input_hints()}
        cmd = ["xdotool", "key", "+".join(keys)]
    elif name in ("combo", "keys", "chord", "hotkey"):
        keys = _canonical_key_tokens(
            [str(k) for k in (action.get("keys") or []) if str(k)])
        if not keys:
            return {"ok": False, "error": "combo needs at least one key",
                    "hints": input_hints()}
        cmd = ["xdotool", "key", "+".join(keys)]
    elif name in ("keydown",):
        cmd = ["xdotool", "keydown",
               _normalize_key_name(str(action.get("key", "")))]
    elif name in ("keyup",):
        cmd = ["xdotool", "keyup",
               _normalize_key_name(str(action.get("key", "")))]
    else:
        return {"ok": False, "error": f"unsupported xdotool action '{name}'",
                "hints": input_hints()}

    res = _run(cmd)
    if res and res["rc"] == 0:
        return {"ok": True, "backend": backend}
    return {
        "ok": False,
        "backend": backend,
        "error": f"xdotool {name} failed: {(res and res['stderr'] or '').strip()}",
        "hints": input_hints(),
    }


_YDOTOOL_ABS_CACHE: Optional[bool] = None


def _ydotool_supports_absolute(force: bool = False) -> bool:
    """Whether ``ydotool mousemove`` understands absolute coordinates.

    ydotool 0.1.8 (the Debian/Ubuntu package) only has ``mousemove <x> <y>``,
    implemented as a relative move: it first warps to the top-left corner and
    then applies a delta. With pointer acceleration/compositing that often
    strands the cursor in a screen corner. ydotool 1.x added
    ``mousemove --absolute --xpos X --ypos Y``, which is safe.
    """
    global _YDOTOOL_ABS_CACHE
    if _YDOTOOL_ABS_CACHE is not None and not force:
        return _YDOTOOL_ABS_CACHE
    res = _run(["ydotool", "mousemove", "--help"], timeout=5)
    text = ((res or {}).get("stdout") or "") + ((res or {}).get("stderr") or "")
    _YDOTOOL_ABS_CACHE = "--absolute" in text
    return _YDOTOOL_ABS_CACHE


def _ydotool_move_cmd(x: int, y: int) -> List[str]:
    """Build an absolute-move command, falling back to 0.1.8 syntax."""
    if _ydotool_supports_absolute():
        return ["ydotool", "mousemove", "--absolute",
                "--xpos", str(int(x)), "--ypos", str(int(y))]
    return ["ydotool", "mousemove", str(int(x)), str(int(y))]


def _ydotool_input(action: Dict[str, Any]) -> Dict[str, Any]:
    name = (action.get("action") or "").lower()
    backend = "ydotool"
    x, y = action.get("x"), action.get("y")

    def _fail(err: str) -> Dict[str, Any]:
        return {"ok": False, "backend": backend, "error": err, "hints": input_hints()}

    def _with_relative_warning(out: Dict[str, Any]) -> Dict[str, Any]:
        if (name in ("move", "click", "double_click", "double", "scroll")
                and x is not None and y is not None
                and not _ydotool_supports_absolute()):
            out["warning"] = (
                "ydotool 0.1.8 mousemove is relative, so absolute placement is "
                "approximate. XTEST is used instead on X11/XWayland."
            )
        return out

    if name == "move":
        cmd = _ydotool_move_cmd(x or 0, y or 0)
    elif name in ("click", "double_click", "double"):
        if x is not None and y is not None:
            mv = _run(_ydotool_move_cmd(x, y))
            if mv is None or mv.get("rc") != 0:
                return _fail(f"ydotool mousemove failed: {(mv and mv['stderr'] or '').strip()}")
        button = str(_ydotool_button(action.get("button", "left")))
        if name == "click":
            cmd = ["ydotool", "click", button]
        else:
            first = _run(["ydotool", "click", button])
            if first is None or first.get("rc") != 0:
                return _fail(f"ydotool double-click failed: {(first and first['stderr'] or '').strip()}")
            time.sleep(0.12)
            second = _run(["ydotool", "click", button])
            if second is None or second.get("rc") != 0:
                return _fail(f"ydotool double-click failed: {(second and second['stderr'] or '').strip()}")
            return _with_relative_warning(
                {"ok": True, "backend": backend, "action": "double"})
    elif name == "scroll":
        # ydotool has no wheel command; PageUp/PageDown is the reliable
        # keyboard-level fallback for scrollable content. Use the ydotool
        # spelling directly: "Page_Down" would be parsed as the letter P.
        if x is not None and y is not None:
            mv = _run(_ydotool_move_cmd(x, y))
            if mv is None or mv.get("rc") != 0:
                return _fail(f"ydotool mousemove failed: {(mv and mv['stderr'] or '').strip()}")
        key = "PAGEDOWN" if str(action.get("direction", "down")).lower() == "down" else "PAGEUP"
        steps = max(1, min(int(action.get("amount", 1) or 1), 10))
        for _ in range(steps):
            res = _run(["ydotool", "key", key])
            if not res or res.get("rc") != 0:
                return _fail(f"ydotool scroll failed: {(res and res['stderr'] or '').strip()}")
        return _with_relative_warning(
            {"ok": True, "backend": backend, "note": "scrolled via PageUp/PageDown"})
    elif name == "type":
        # ydotool ``type`` only maps ASCII 0-127 (a 128-entry table); bytes
        # >= 128 index out of bounds. Non-ASCII must go via clipboard paste
        # (cli_input routes it there); refuse loudly if called directly.
        if not is_ascii_text(str(action.get("text", ""))):
            return _fail(
                "ydotool type cannot type non-ASCII text (ASCII-only table). "
                "Use clipboard paste via cli_input/configured input instead.")
        # ``--`` keeps text that starts with "-" from being parsed as a flag.
        cmd = ["ydotool", "type", "--", str(action.get("text", ""))]
    elif name in ("key", "press"):
        try:
            seq = _ydotool_key_sequence([str(action.get("key", ""))])
        except ValueError as exc:
            return _fail(str(exc))
        cmd = ["ydotool", "key", seq]
    elif name in ("combo", "keys", "chord", "hotkey"):
        parts = [str(k) for k in (action.get("keys") or []) if str(k)]
        try:
            seq = _ydotool_key_sequence(parts)
        except ValueError as exc:
            return _fail(str(exc))
        cmd = ["ydotool", "key", seq]
    elif name in ("keydown", "keyup"):
        return _fail("ydotool has no standalone keydown/keyup; use computer_combo")
    elif name in ("drag", "hscroll"):
        return _fail(
            f"ydotool cannot {name} on this version — use pyautogui/XTEST on "
            "XWayland, or move + key/type fallbacks."
        )
    else:
        return _fail(f"unsupported ydotool action '{name}'")

    res = _run(cmd)
    if res and res.get("rc") == 0:
        return _with_relative_warning({"ok": True, "backend": backend})
    return _fail(
        f"ydotool {name} failed: {(res and res['stderr'] or '').strip()}. "
        "ydotool needs ydotoold/uinput permissions on Wayland."
    )


_KEY_CODES_MAC = {
    "return": 36, "enter": 36, "tab": 48, "escape": 53, "esc": 53,
    "backspace": 51, "delete": 51, "forwarddelete": 117, "space": 49,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
_MAC_MODIFIERS = {
    "ctrl": "control down",
    "shift": "shift down",
    "alt": "option down",
    "super": "command down",
}


def _mac_key_code(name: str) -> Optional[int]:
    compact = str(name or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if compact in ("forwarddelete", "fndelete"):
        return _KEY_CODES_MAC["forwarddelete"]
    canonical = _normalize_key_name(name)
    return _KEY_CODES_MAC.get(canonical.lower().replace("_", ""))


def _macos_input(action: Dict[str, Any]) -> Dict[str, Any]:
    name = (action.get("action") or "").lower()
    x, y = action.get("x"), action.get("y")
    cliclick = shutil.which("cliclick")

    if name in ("move", "click", "double", "double_click", "drag") and not cliclick:
        return {
            "ok": False,
            "error": f"macOS mouse action '{name}' needs cliclick (brew install cliclick) or pyautogui",
            "hints": input_hints(),
        }

    if name == "move":
        res = _run([cliclick, f"m:{int(x or 0)},{int(y or 0)}"])
        return {"ok": bool(res and res.get("rc") == 0), "backend": "cliclick"}
    if name == "click":
        res = _run([cliclick, f"c:{int(x or 0)},{int(y or 0)}"])
        return {"ok": bool(res and res.get("rc") == 0), "backend": "cliclick"}
    if name in ("double", "double_click"):
        res = _run([cliclick, f"dc:{int(x or 0)},{int(y or 0)}"])
        return {"ok": bool(res and res.get("rc") == 0), "backend": "cliclick"}
    if name == "drag":
        res = _run([
            cliclick,
            f"dd:{int(action.get('x1', 0))},{int(action.get('y1', 0))}",
            f"dm:{int(action.get('x2', 0))},{int(action.get('y2', 0))}",
            f"du:{int(action.get('x2', 0))},{int(action.get('y2', 0))}",
        ])
        return {"ok": bool(res and res.get("rc") == 0), "backend": "cliclick"}
    if name == "type":
        text = str(action.get("text", "")).replace("\\", "\\\\").replace('"', '\\"')
        res = _osascript(f'tell application "System Events" to keystroke "{text}"')
        return {"ok": bool(res and res.get("rc") == 0), "backend": "osascript"}
    if name in ("key", "press"):
        key = str(action.get("key", ""))
        code = _mac_key_code(key)
        if code is not None:
            res = _osascript(f'tell application "System Events" to key code {code}')
        else:
            canonical = _normalize_key_name(key)
            if len(canonical) != 1:
                return {"ok": False, "error": f"unknown macOS key name {key!r}",
                        "hints": input_hints()}
            escaped = canonical.replace("\\", "\\\\").replace('"', '\\"')
            res = _osascript(f'tell application "System Events" to keystroke "{escaped}"')
        return {"ok": bool(res and res.get("rc") == 0), "backend": "osascript"}
    if name in ("combo", "keys", "chord", "hotkey"):
        keys = _canonical_key_tokens(
            [str(k) for k in (action.get("keys") or []) if str(k)])
        mods = []
        plain = []
        for key in keys:
            if key in _MAC_MODIFIERS:
                mods.append(_MAC_MODIFIERS[key])
            elif key.endswith("_r"):
                # Right-hand modifiers are rare; use the left equivalent.
                mods.append(_MAC_MODIFIERS.get(key[:-2], ""))
            else:
                plain.append(key)
        mods = [m for m in mods if m]
        if not plain:
            return {"ok": False, "error": "combo needs at least one non-modifier key"}
        last = plain[-1]
        code = _mac_key_code(last)
        if code is not None:
            script = f'tell application "System Events" to key code {code}'
        elif len(last) == 1:
            escaped = last.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"'
        else:
            return {"ok": False, "error": f"unknown macOS key name {last!r}",
                    "hints": input_hints()}
        if mods:
            script += " using {" + ", ".join(mods) + "}"
        res = _osascript(script)
        return {"ok": bool(res and res.get("rc") == 0), "backend": "osascript"}
    if name == "scroll":
        return {
            "ok": False,
            "error": "macOS CLI scroll needs pyautogui (built-in fallback)",
            "hints": ["pyautogui is installed by default; this path is only used when it fails"],
        }
    return {"ok": False, "error": f"unsupported macOS action '{name}'",
            "hints": input_hints()}


_SENDKEYS_SPECIAL = "+^%~()[]{}"


def _sendkeys_escape(text: str) -> str:
    return "".join(("{" + ch + "}") if ch in _SENDKEYS_SPECIAL else ch for ch in text)


_KEY_CODES_WIN = {
    "return": "{ENTER}", "enter": "{ENTER}", "tab": "{TAB}",
    "escape": "{ESC}", "esc": "{ESC}", "backspace": "{BACKSPACE}",
    "delete": "{DELETE}", "del": "{DELETE}", "insert": "{INSERT}",
    "space": " ", "left": "{LEFT}", "right": "{RIGHT}",
    "up": "{UP}", "down": "{DOWN}", "home": "{HOME}", "end": "{END}",
    "pageup": "{PGUP}", "pagedown": "{PGDN}",
    "print": "{PRTSC}", "pause": "{BREAK}", "menu": "{APPS}",
    "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}", "f5": "{F5}",
    "f6": "{F6}", "f7": "{F7}", "f8": "{F8}", "f9": "{F9}", "f10": "{F10}",
    "f11": "{F11}", "f12": "{F12}", "f13": "{F13}", "f14": "{F14}",
    "f15": "{F15}", "f16": "{F16}",
}


def _win_sendkeys_key(name: str) -> Optional[str]:
    canonical = _normalize_key_name(name)
    lookup = canonical.lower().replace("_", "")
    if lookup in _KEY_CODES_WIN:
        return _KEY_CODES_WIN[lookup]
    if len(canonical) == 1:
        return _sendkeys_escape(canonical)
    return None


def _windows_input(action: Dict[str, Any]) -> Dict[str, Any]:
    name = (action.get("action") or "").lower()
    # SendKeys cannot reliably type arbitrary Unicode; paste it instead.
    if name == "type":
        body = str(action.get("text", ""))
        if body and not is_ascii_text(body):
            return clipboard_paste(body)
    ps = _powershell()
    if not ps:
        return {"ok": False, "error": "PowerShell not found",
                "hints": input_hints()}

    if name in ("type", "key", "press", "combo", "keys", "chord", "hotkey"):
        if name == "type":
            keys = _sendkeys_escape(str(action.get("text", "")))
        elif name == "key":
            keys = _win_sendkeys_key(str(action.get("key", "")))
            if keys is None:
                return {"ok": False,
                        "error": f"unknown Windows key name {action.get('key')!r}",
                        "hints": input_hints()}
        else:
            parts = _canonical_key_tokens(
                [str(k) for k in (action.get("keys") or []) if str(k)])
            mods = ""
            plain = ""
            for key in parts:
                if key in ("ctrl", "ctrl_r"):
                    mods += "^"
                elif key in ("shift", "shift_r"):
                    mods += "+"
                elif key in ("alt", "alt_r"):
                    mods += "%"
                elif key in ("super", "super_r"):
                    # SendKeys has no Windows-key code. Mapping it to ^ (the
                    # old behaviour) silently pressed Ctrl instead, which is
                    # worse than a clear failure; pyautogui handles Win keys.
                    return {"ok": False,
                            "error": "Windows SendKeys cannot press the Windows/Super key; "
                                     "the pyautogui input path is required",
                            "hints": input_hints()}
                else:
                    plain = _win_sendkeys_key(key)
                    if plain is None:
                        return {"ok": False,
                                "error": f"unknown Windows key name {key!r}",
                                "hints": input_hints()}
            if not plain:
                return {"ok": False, "error": "combo needs at least one non-modifier key",
                        "hints": input_hints()}
            keys = f"{mods}{plain}"
        escaped = keys.replace("'", "''")
        script = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"$w.SendKeys('{escaped}')"
        )
        res = _powershell_run(script, timeout=10)
        return {"ok": bool(res and res.get("rc") == 0),
                "backend": "powershell-sendkeys",
                "error": None if (res and res.get("rc") == 0) else
                f"SendKeys failed: {(res and res.get('stderr') or '').strip()}"}

    return {
        "ok": False,
        "error": f"Windows mouse action '{name}' needs pyautogui (built-in fallback)",
        "hints": ["pyautogui is installed by default; this path is only used when it fails"],
    }


def cli_input(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single input action through the platform's CLI drivers."""
    # Direct key synthesis (ydotool/xdotool/SendKeys) cannot type arbitrary
    # Unicode. Route non-ASCII text through clipboard paste on every OS.
    if (str((action or {}).get("action") or "").lower() == "type"
            and action.get("text") not in (None, "")
            and not is_ascii_text(str(action.get("text")))):
        return clipboard_paste(str(action.get("text")))
    platform = detect_platform()
    if platform == "windows":
        return _windows_input(action)
    if platform == "macos":
        return _macos_input(action)
    return _linux_input(action)


# ---------------------------------------------------------------------------
# Windows / apps
# ---------------------------------------------------------------------------

def _running_apps_linux() -> List[str]:
    """Cheap process scan for common GUI apps (Wayland has no window list)."""
    known = {
        "discord", "vesktop", "slack", "telegram", "signal", "element",
        "firefox", "firefox-esr", "chromium", "chromium-browser", "google-chrome",
        "brave", "brave-browser", "vivaldi", "opera", "code", "code-oss",
        "codium", "zed", "jetbrains", "idea", "pycharm", "spotify",
        "nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "cosmic-files",
        "gnome-terminal", "konsole", "kitty", "alacritty", "wezterm", "foot",
        "obsidian", "thunderbird", "libreoffice", "gimp", "steam", "vlc",
        "mpv", "telegram-desktop", "whatsapp", "zoom", "teams", "skype",
    }
    res = _run(["ps", "-eo", "comm="], timeout=4)
    if not res or res.get("rc") != 0:
        return []
    seen: List[str] = []
    for line in (res.get("stdout") or "").splitlines():
        name = os.path.basename(line.strip()).lower()
        if not name or name in seen:
            continue
        if name in known or any(name.startswith(k) for k in known):
            seen.append(name)
    return seen


def _linux_window(op: str, **kwargs: Any) -> Dict[str, Any]:
    de = detect_de()
    session = detect_session_type()
    windows: List[Dict[str, Any]] = []
    backends: List[str] = []

    if _which("wmctrl"):
        res = _run(["wmctrl", "-l"], timeout=5)
        if res and res.get("rc") == 0:
            backends.append("wmctrl")
            for line in (res.get("stdout") or "").splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    windows.append({
                        "id": f"wmctrl:{parts[0]}",
                        "desktop": parts[1],
                        "title": parts[3],
                    })

    if de == "sway" and _which("swaymsg"):
        res = _run(["swaymsg", "-t", "get_tree", "-r"], timeout=5)
        if res and res.get("rc") == 0:
            try:
                tree = json.loads(res.get("stdout") or "{}")
            except Exception:
                tree = {}

            def walk(node: Dict[str, Any]) -> None:
                if node.get("type") in ("con", "floating_con") and node.get("name"):
                    windows.append({
                        "id": f"sway:{node.get('id')}",
                        "title": node.get("name"),
                        "app": node.get("app_id") or node.get("window_properties", {}).get("class"),
                        "focused": bool(node.get("focused")),
                    })
                for child in node.get("nodes", []) + node.get("floating_nodes", []):
                    walk(child)

            walk(tree)
            backends.append("swaymsg")

    if de == "hyprland" and _which("hyprctl"):
        res = _run(["hyprctl", "clients", "-j"], timeout=5)
        if res and res.get("rc") == 0:
            try:
                clients = json.loads(res.get("stdout") or "[]")
            except Exception:
                clients = []
            for c in clients:
                windows.append({
                    "id": f"hypr:{c.get('address')}",
                    "title": c.get("title"),
                    "app": c.get("class"),
                    "focused": bool(c.get("focused")),
                })
            backends.append("hyprctl")

    # GNOME Shell can sometimes list windows via Eval. Recent GNOME versions
    # disable Eval for apps, in which case this returns (false, '').
    if de == "gnome" and _which("gdbus") and not windows:
        res = _run([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell",
            "--object-path", "/org/gnome/Shell",
            "--method", "org.gnome.Shell.Eval",
            "global.get_window_actors().map(a => a.meta_window.get_title()).join('|')",
        ], timeout=5)
        if res and res.get("rc") == 0:
            out = (res.get("stdout") or "").strip()
            if out.startswith("(true,"):
                titles = out.split(",", 1)[1].strip().rstrip(")")
                for i, title in enumerate(t for t in titles.split("|") if t):
                    windows.append({"id": f"gnome:{i}", "title": title})
                backends.append("gnome-shell-eval")

    apps = _running_apps_linux()
    note = None
    fallback = None
    if session in ("wayland", "xwayland") and de in ("gnome", "cosmic", "kde"):
        note = (
            f"{de} Wayland does not expose native windows to wmctrl/xdotool; "
            "an empty window list is normal. Use computer_screenshot + marks/OCR."
        )
        fallback = "computer_screenshot then computer_click(mark=N) or computer_ocr"
    elif de == "cosmic":
        note = "COSMIC Wayland-native windows are not visible to wmctrl; use screenshot + OCR."
        fallback = "computer_screenshot then computer_ocr"

    if op == "list":
        return {
            "ok": True,
            "windows": windows,
            "apps": apps,
            "backend": "+".join(backends) or "none",
            "de": de,
            "session": session,
            "note": note,
            "fallback": fallback,
            "guidance": (
                "Do NOT repeat computer_windows hoping for native windows. "
                "Act on the screenshot marks instead."
            ) if session in ("wayland", "xwayland") else None,
        }
    if op == "focus":
        wid = str(kwargs.get("window_id") or "")
        app = str(kwargs.get("app") or "")
        target = wid or app
        if not target:
            return {"ok": False, "error": "focus requires window_id or app",
                    "hints": ["list windows first, or pass app='Discord'"]}
        if wid.startswith("sway:") and _which("swaymsg"):
            res = _run(["swaymsg", f"[con_id={wid.split(':', 1)[1]}]", "focus"], timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "backend": "swaymsg"}
        if wid.startswith("hypr:") and _which("hyprctl"):
            res = _run(["hyprctl", "dispatch", "focuswindow",
                        f"address:{wid.split(':', 1)[1]}"], timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "backend": "hyprctl"}
        plain_wid = wid.split(":", 1)[1] if ":" in wid else wid
        if _which("wmctrl"):
            res = _run(["wmctrl", "-ia", plain_wid], timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "backend": "wmctrl"}
            if app:
                res = _run(["wmctrl", "-a", app], timeout=5)
                if res and res.get("rc") == 0:
                    return {"ok": True, "backend": "wmctrl"}
        if _which("xdotool"):
            if wid:
                res = _run(["xdotool", "windowactivate", plain_wid], timeout=5)
                if res and res.get("rc") == 0:
                    return {"ok": True, "backend": "xdotool"}
            if app:
                res = _run(["xdotool", "search", "--name", app, "windowactivate"], timeout=5)
                if res and res.get("rc") == 0:
                    return {"ok": True, "backend": "xdotool"}
        return {
            "ok": False,
            "error": f"cannot focus '{target}' on {de} {session}",
            "hints": input_hints(),
            "next": (
                "Wayland has no generic focus-by-id. Take a screenshot and click "
                "the app/window in it: computer_click(mark=N)."
            ),
        }
    if op == "active":
        if _which("xdotool"):
            res = _run(["xdotool", "getactivewindow"], timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "window_id": f"xdotool:{res.get('stdout', '').strip()}",
                        "backend": "xdotool"}
        if de == "sway" and _which("swaymsg"):
            res = _run(["swaymsg", "-t", "get_tree", "-r"], timeout=5)
            if res and res.get("rc") == 0:
                try:
                    tree = json.loads(res.get("stdout") or "{}")
                    stack = [tree]
                    while stack:
                        node = stack.pop()
                        if node.get("focused"):
                            return {"ok": True, "window_id": f"sway:{node.get('id')}",
                                    "title": node.get("name"), "backend": "swaymsg"}
                        stack.extend(node.get("nodes", []) + node.get("floating_nodes", []))
                except Exception:
                    pass
        if de == "hyprland" and _which("hyprctl"):
            res = _run(["hyprctl", "activewindow", "-j"], timeout=5)
            if res and res.get("rc") == 0:
                try:
                    info = json.loads(res.get("stdout") or "{}")
                    return {"ok": True, "window_id": f"hypr:{info.get('address')}",
                            "title": info.get("title"), "app": info.get("class"),
                            "backend": "hyprctl"}
                except Exception:
                    pass
        return {"ok": False, "error": f"cannot get active window on {de} {session}",
                "hints": input_hints(),
                "next": "Use computer_screenshot to see what is focused."}
    return {"ok": False, "error": f"unknown window op '{op}'"}


def _macos_window(op: str, **kwargs: Any) -> Dict[str, Any]:
    if op == "list":
        res = _osascript(
            'tell application "System Events" to get name of every process whose visible is true'
        )
        if res and res.get("rc") == 0:
            names = [n.strip() for n in (res.get("stdout") or "").split(",") if n.strip()]
            return {
                "ok": True,
                "windows": [{"id": f"macapp:{n}", "title": n, "app": n} for n in names],
                "apps": names,
                "backend": "osascript",
                "de": "macos",
                "session": "macos",
                "note": "macOS lists visible applications, not individual windows.",
            }
        return {
            "ok": False,
            "error": (res and res.get("stderr") or "osascript unavailable").strip(),
            "hints": ["grant Accessibility permission to your terminal",
                      "System Settings > Privacy & Security > Accessibility"],
        }
    if op == "focus":
        target = str(kwargs.get("app") or kwargs.get("window_id") or "").replace("macapp:", "")
        if not target:
            return {"ok": False, "error": "focus requires app or window_id"}
        res = _osascript(
            f'tell application "System Events" to set frontmost of process "{target}" to true'
        )
        if res and res.get("rc") == 0:
            return {"ok": True, "backend": "osascript"}
        return {"ok": False, "error": (res and res.get("stderr") or "focus failed").strip(),
                "hints": ["grant Accessibility permission to your terminal"]}
    if op == "active":
        res = _osascript(
            'tell application "System Events" to get name of first process whose frontmost is true'
        )
        if res and res.get("rc") == 0:
            name = (res.get("stdout") or "").strip()
            return {"ok": True, "window_id": f"macapp:{name}", "app": name,
                    "backend": "osascript"}
        return {"ok": False, "error": (res and res.get("stderr") or "cannot get active app").strip()}
    return {"ok": False, "error": f"unknown window op '{op}'"}


def _windows_window(op: str, **kwargs: Any) -> Dict[str, Any]:
    if op == "list":
        script = (
            "Get-Process | Where-Object {$_.MainWindowTitle} | "
            "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"
        )
        res = _powershell_run(script, timeout=15)
        if not res or res.get("rc") != 0:
            return {"ok": False,
                    "error": (res and res.get("stderr") or "PowerShell unavailable").strip(),
                    "hints": input_hints()}
        raw = (res.get("stdout") or "").strip()
        try:
            data = json.loads(raw) if raw else []
        except Exception:
            data = []
        if isinstance(data, dict):
            data = [data]
        windows = [{
            "id": f"win:{item.get('Id')}",
            "title": item.get("MainWindowTitle"),
            "app": item.get("ProcessName"),
        } for item in data if item.get("MainWindowTitle")]
        return {"ok": True, "windows": windows,
                "apps": sorted({w.get("app") for w in windows if w.get("app")}),
                "backend": "powershell", "de": "windows", "session": "windows"}
    if op == "focus":
        target = str(kwargs.get("window_id") or kwargs.get("app") or "")
        if not target:
            return {"ok": False, "error": "focus requires window_id or app"}
        target = target.replace("win:", "")
        escaped = target.replace("'", "''")
        script = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"if(-not $w.AppActivate('{escaped}')){{exit 1}}"
        )
        res = _powershell_run(script, timeout=10)
        if res and res.get("rc") == 0:
            return {"ok": True, "backend": "powershell"}
        return {"ok": False, "error": f"could not focus '{target}'",
                "hints": ["pass the numeric process id from computer_windows"]}
    if op == "active":
        script = (
            "Add-Type @'\n"
            "using System; using System.Runtime.InteropServices;\n"
            "public class W { [DllImport(\"user32.dll\")] public static extern IntPtr "
            "GetForegroundWindow(); }\n"
            "'@; (Get-Process | Where-Object {$_.MainWindowHandle -eq [W]::GetForegroundWindow()} "
            "| Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress)"
        )
        res = _powershell_run(script, timeout=10)
        if res and res.get("rc") == 0:
            try:
                item = json.loads((res.get("stdout") or "").strip() or "{}")
            except Exception:
                item = {}
            if item:
                return {"ok": True, "window_id": f"win:{item.get('Id')}",
                        "app": item.get("ProcessName"),
                        "title": item.get("MainWindowTitle"),
                        "backend": "powershell"}
        return {"ok": False, "error": "cannot get active window"}
    return {"ok": False, "error": f"unknown window op '{op}'"}


def cli_window(op: str, **kwargs: Any) -> Dict[str, Any]:
    """Window introspection/focus, cross-platform.

    On native Wayland sessions (GNOME, COSMIC, KDE, Sway, Hyprland) no generic
    window-list protocol exists. Empty lists are normal; the result carries a
    ``note`` + ``fallback`` so the agent should switch to screenshot marks.
    """
    platform = detect_platform()
    if platform == "windows":
        return _windows_window(op, **kwargs)
    if platform == "macos":
        return _macos_window(op, **kwargs)
    return _linux_window(op, **kwargs)


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def _clipboard_write(cmd: List[str], text: str, timeout: int = 8) -> Optional[Dict[str, Any]]:
    res = _run(cmd, timeout=timeout, input_text=text)
    if res and res.get("rc") == 0:
        return {"ok": True, "backend": cmd[0]}
    return None


def cli_clipboard(op: str, text: Optional[str] = None) -> Dict[str, Any]:
    """Clipboard read/write on Linux, macOS, and Windows."""
    platform = detect_platform()

    if platform == "windows":
        ps = _powershell()
        if not ps:
            return {"ok": False, "error": "PowerShell not found"}
        if op == "read":
            res = _powershell_run("Get-Clipboard -Raw", timeout=10)
            if res and res.get("rc") == 0:
                return {"ok": True, "text": res.get("stdout") or "", "backend": "powershell"}
            return {"ok": False, "error": (res and res.get("stderr") or "Get-Clipboard failed").strip()}
        if op == "write":
            # Set-Clipboard reads the text from stdin: avoids all quoting and
            # length limits of embedding it in the PowerShell command line.
            res = _run([ps, "-NoProfile", "-NonInteractive", "-Command", "$input | Set-Clipboard"],
                       timeout=10, input_text=str(text or ""))
            if res and res.get("rc") == 0:
                return {"ok": True, "backend": "powershell"}
            return {"ok": False, "error": (res and res.get("stderr") or "Set-Clipboard failed").strip()}
        return {"ok": False, "error": f"unknown clipboard op '{op}'"}

    if platform == "macos":
        if op == "read":
            res = _run(["pbpaste"], timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "text": res.get("stdout") or "", "backend": "pbpaste"}
            return {"ok": False, "error": (res and res.get("stderr") or "pbpaste failed").strip()}
        if op == "write":
            r = _clipboard_write(["pbcopy"], str(text or ""))
            if r:
                return r
            return {"ok": False, "error": "pbcopy failed"}
        return {"ok": False, "error": f"unknown clipboard op '{op}'"}

    # Linux: prefer the session-native clipboard, then X11 tools.
    prefer_wayland = is_wayland()
    readers = [("wl-paste", ["wl-paste"]), ("xclip", ["xclip", "-o", "-selection", "clipboard"]),
               ("xsel", ["xsel", "--clipboard", "--output"])]
    writers = [("wl-copy", ["wl-copy"]), ("xclip", ["xclip", "-i", "-selection", "clipboard"]),
               ("xsel", ["xsel", "--clipboard", "--input"])]
    if not prefer_wayland:
        readers.reverse()
        writers.reverse()

    if op == "read":
        for name, cmd in readers:
            if not _which(name):
                continue
            res = _run(cmd, timeout=5)
            if res and res.get("rc") == 0:
                return {"ok": True, "text": res.get("stdout") or "", "backend": name}
        try:
            from harness.computer import x11clipboard
            xtext = x11clipboard.read()
            if xtext is not None:
                return {"ok": True, "text": xtext, "backend": "x11clipboard"}
        except Exception:
            pass
        return {"ok": False, "error": "no clipboard reader (wl-paste/xclip/xsel)",
                "hints": ["install wl-clipboard (Wayland) or xclip (X11)"]}
    if op == "write":
        for name, cmd in writers:
            if not _which(name):
                continue
            r = _clipboard_write(cmd, str(text or ""))
            if r:
                return r
        try:
            from harness.computer import x11clipboard
            if detect_session_type() in ("x11", "xwayland") and x11clipboard.write(str(text or "")):
                return {"ok": True, "backend": "x11clipboard"}
        except Exception:
            pass
        return {"ok": False, "error": "no clipboard writer (wl-copy/xclip/xsel)",
                "hints": ["install wl-clipboard (Wayland) or xclip (X11)"]}
    return {"ok": False, "error": f"unknown clipboard op '{op}'"}


# ---------------------------------------------------------------------------
# Unicode typing: clipboard paste
# ---------------------------------------------------------------------------

def is_ascii_text(text: Any) -> bool:
    """True when a direct key-event path can type ``text`` faithfully."""
    try:
        if text is None:
            return True
        return str(text).isascii()
    except Exception:
        return True


def paste_combo_keys() -> List[str]:
    """Canonical paste chord for this platform."""
    return ["super", "v"] if detect_platform() == "macos" else ["ctrl", "v"]


def clipboard_paste(text: str) -> Dict[str, Any]:
    """Put ``text`` on the clipboard and send the paste chord.

    Direct key synthesis (XTEST, ydotool ``type``, pyautogui ``typewrite``)
    only types characters bound in the current keymap. Anything else —
    accented text, CJK, emoji — is typed by pasting instead, which keeps the
    full Unicode range intact. The user's previous clipboard contents are
    restored afterwards, best-effort.
    """
    body = str(text)
    saved: Optional[str] = None
    try:
        prev = cli_clipboard("read")
        if prev.get("ok") and prev.get("text") not in (None, ""):
            saved = str(prev.get("text"))
    except Exception:
        saved = None
    written = cli_clipboard("write", text=body)
    if not written.get("ok"):
        return {
            "ok": False,
            "error": (
                "Unicode typing needs a clipboard (key synthesis cannot type "
                f"{body[:32]!r}...). {written.get('error') or 'no clipboard writer'}"
            ),
            "hints": list(written.get("hints") or ["install wl-clipboard (Wayland) or xclip (X11)"]),
        }
    pasted = cli_input({"action": "combo", "keys": paste_combo_keys()})
    restored = False
    if saved is not None:
        try:
            # Give the focused app a moment to read the paste before restore.
            time.sleep(0.2)
            restored = bool(cli_clipboard("write", text=saved).get("ok"))
        except Exception:
            pass
    if not pasted.get("ok"):
        return {
            "ok": False,
            "backend": f"clipboard({written.get('backend')})+paste",
            "error": (
                f"Clipboard was set but the paste chord failed: "
                f"{pasted.get('error') or 'unknown error'}"
            ),
            "hints": input_hints(),
        }
    out = {
        "ok": True,
        "backend": f"clipboard({written.get('backend')})+paste",
        "via": "clipboard-paste",
        "length": len(body),
    }
    if restored:
        out["restored_clipboard"] = True
    return out


# ---------------------------------------------------------------------------
# Backend preference (single source of truth for controller + probe)
# ---------------------------------------------------------------------------

def preferred_capture_backend() -> str:
    platform = detect_platform()
    if platform == "windows":
        return "mss/pyautogui (Windows)"
    if platform == "macos":
        return "screencapture (macOS)"
    de = detect_de()
    if de == "cosmic" and _which("cosmic-screenshot"):
        return "cosmic-screenshot"
    if de == "gnome" and _which("gnome-screenshot"):
        return "gnome-screenshot"
    if de == "kde" and _which("spectacle"):
        return "spectacle"
    if de in ("sway", "hyprland", "niri", "river", "wayfire") and _which("grim"):
        return "grim"
    if _which("scrot"):
        return "scrot"
    if _which("import"):
        return "import"
    return "pyautogui/mss"


def preferred_input_backend() -> str:
    platform = detect_platform()
    if platform == "windows":
        return "pyautogui (Windows)"
    if platform == "macos":
        return "pyautogui (macOS)"
    if is_wayland() and _which("ydotool"):
        return "ydotool (Wayland native)"
    if _which("xdotool"):
        return "xdotool (X11/XWayland)"
    if _which("ydotool"):
        return "ydotool"
    return "pyautogui/XTEST"
