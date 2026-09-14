"""
Wayland / CLI driver helpers for Harness computer use.

On pure Wayland (no XWayland, no XTEST reachable) the harness still needs a
path to capture the screen and drive input. This module provides thin, never-
raising CLI shims over the standard Linux toolchain so the computer controller
can degrade gracefully:

* `detect_session_type()` -> "wayland" | "x11" | "xwayland" | "headless"
* `capture_hints()` / `input_hints()` -> short "how to fix this" strings
* `is_wayland()` -> bool
* `cli_capture(...)`  -> dict: screen/region capture via grim/scrot/import
* `cli_input(action)` -> dict: single input action via xdotool/ydotool/wmctrl
* `cli_window(...)`   -> dict: window list/focus/active via wmctrl/xdotool
* `cli_clipboard(...)` -> dict: clipboard read/write via xclip/xsel

Every function is structured-output (never raises). If a needed binary is
absent it returns `{"ok": False, "error": ..., "hints": [...]}` so callers can
tell the model exactly what to install.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def detect_session_type() -> str:
    """Detect the active graphics session: wayland / x11 / xwayland / headless."""
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
    return bool((os.environ.get("WAYLAND_DISPLAY") or "").strip())


def capture_hints() -> List[str]:
    out: List[str] = []
    if is_wayland():
        out.append("install grim (Wayland screenshot)")
        if not (_which("grim") or _which("grimblast")):
            out.append("grim -- or grimblast for a one-liner")
    else:
        out.append("install scrot (X11 screenshot)")
        out.append("or xdotool or ImageMagick import")
    return out


def input_hints() -> List[str]:
    out: List[str] = []
    if is_wayland():
        out.append("install ydotool (Wayland input); needs uinput root or udev rules")
    out.append("install xdotool (X11 input; also works over XWayland)")
    out.append("install wmctrl (window focus/list; X11)")
    return out


def _run(cmd: List[str], timeout: int = 10) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        return {"rc": -1, "stdout": "", "stderr": f"timed out: {exc}"}


def cli_capture(
    region: Optional[Dict[str, Any]] = None,
    monitor_index: int = 0,
    out_dir: Optional[str] = None,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture a screen/region through CLI tools.

    Returns a dict with `ok`, `path`, `backend`, and on failure `error` +
    `hints`. Never raises.
    """
    out_dir = out_dir or os.path.expanduser("~/.harness/screenshots")
    os.makedirs(out_dir, exist_ok=True)
    name = f"screen-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.png"
    path = out_path or os.path.join(out_dir, name)

    if region is not None and region:
        geom = (
            f"{int(region.get('width', 0))}x{int(region.get('height', 0))}"
            f"+{int(region.get('x', 0))}+{int(region.get('y', 0))}"
        )

    if _which("grim"):
        args = ["grim", "-g", geom, path] if region and region.get("width") else ["grim", path]
        res = _run(args)
        if res and res["rc"] == 0:
            return {"ok": True, "path": path, "backend": "grim"}
        err = res["stderr"].strip() if res else "grim not found"
        return {"ok": False, "error": f"grim failed: {err}", "hints": capture_hints()}

    if _which("scrot"):
        args = [path]
        if region and region.get("width"):
            error_format = "capture_secret"
            args = ["-a", geom, "-o", path]
        res = _run(["scrot", *args])
        if res and res["rc"] == 0:
            return {"ok": True, "path": path, "backend": "scrot"}
        err = res["stderr"].strip() if res else "scrot not found"
        return {"ok": False, "error": f"scrot failed: {err}", "hints": capture_hints()}

    if _which("import"):
        res = _run(["import", "-window", "root", path])
        if res and res["rc"] == 0:
            return {"ok": True, "path": path, "backend": "import"}
        err = res["stderr"].strip() if res else "import not found"
        return {"ok": False, "error": f"import (ImageMagick) failed: {err}", "hints": capture_hints()}

    return {
        "ok": False,
        "error": "no screenshot CLI available (grim/scrot/import)",
        "hints": capture_hints(),
    }


def _ydotool_button(name: str) -> int:
    return {"left": 1, "middle": 2, "right": 3}.get(name.lower(), 1)


def cli_input(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single input action through CLI drivers (xdotool/ydotool/wmctrl)."""
    name = (action.get("action") or "").lower()
    if not name:
        return {"ok": False, "error": "missing 'action' field"}

    if _which("xdotool"):
        backend = "xdotool"
        if name == "move":
            cmd = ["xdotool", "mousemove", str(action.get("x", 0)), str(action.get("y", 0))]
        elif name == "click":
            cmd = ["xdotool", "click", str(action.get("button", "left"))]
        elif name == "double_click":
            cmd = ["xdotool", "click", "--repeat", "2", str(action.get("button", "left"))]
        elif name == "scroll":
            button = "5" if action.get("direction") == "down" else "4"
            steps = int(action.get("amount", 1))
            cmd = []
            for _ in range(max(1, steps)):
                cmd += ["xdotool", "click", button]
        elif name == "type":
            cmd = ["xdotool", "type", "--delay", "12", str(action.get("text", ""))]
        elif name == "key":
            cmd = ["xdotool", "key", str(action.get("key", ""))]
        elif name == "combo":
            keys = action.get("keys") or []
            cmd = ["xdotool", "key", *[str(k) for k in keys]]
        else:
            return {"ok": False, "error": f"unsupported xdotool action '{name}'"}
        res = _run(cmd)
        if res and res["rc"] == 0:
            return {"ok": True, "backend": backend}
        return {"ok": False, "error": f"xdotool {name} failed: "
                                        f"{(res and res['stderr'] or '').strip()}"}

    if _which("ydotool"):
        backend = "ydotool"
        if name == "click":
            cmd = ["ydotool", "click", str(_ydotool_button(action.get("button", "left")))]
        elif name == "type":
            cmd = ["ydotool", "type", str(action.get("text", ""))]
        elif name == "key":
            cmd = ["ydotool", "key", str(action.get("key", ""))]
        elif name == "combo":
            keys = action.get("keys") or []
            cmd = ["ydotool", "key", *[str(k) for k in keys]]
        else:
            return {"ok": False, "error": f"unsupported ydotool action '{name}'"}
        res = _run(cmd)
        if res and res["rc"] == 0:
            return {"ok": True, "backend": backend}
        return {"ok": False, "error": f"ydotool {name} failed: "
                                        f"{(res and res['stderr'] or '').strip()}"}

    return {"ok": False, "error": "no input CLI (xdotool/ydotool) available",
            "hints": input_hints()}


def cli_window(op: str, **kwargs: Any) -> Dict[str, Any]:
    """Window introspection/focus via wmctrl + xdotool."""
    if op == "list":
        if _which("wmctrl"):
            res = _run(["wmctrl", "-l"])
            if res and res["rc"] == 0:
                windows = []
                for line in res["stdout"].splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        windows.append({"id": parts[0], "desktop": parts[1],
                                        "title": parts[3]})
                return {"ok": True, "windows": windows, "backend": "wmctrl"}
        return {"ok": False, "error": "wmctrl unavailable for window list",
                "hints": ["install wmctrl"]}
    if op == "focus":
        wid = kwargs.get("window_id")
        if not wid:
            return {"ok": False, "error": "focus requires window_id", "hints": ["list windows first"]}
        if _which("wmctrl"):
            res = _run(["wmctrl", "-ia", str(wid)])
            if res and res["rc"] == 0:
                return {"ok": True, "backend": "wmctrl"}
        if _which("xdotool"):
            res = _run(["xdotool", "windowactivate", str(wid)])
            if res and res["rc"] == 0:
                return {"ok": True, "backend": "xdotool"}
        return {"ok": False, "error": "could not focus window; wmctrl/xdotool unavailable",
                "hints": ["install wmctrl"]}
    if op == "active":
        if _which("xdotool"):
            res = _run(["xdotool", "getactivewindow"])
            if res and res["rc"] == 0:
                return {"ok": True, "window_id": res["stdout"].strip(), "backend": "xdotool"}
        return {"ok": False, "error": "cannot get active window; xdotool unavailable",
                "hints": ["install xdotool"]}
    return {"ok": False, "error": f"unknown window op '{op}'"}


def cli_clipboard(op: str, text: Optional[str] = None) -> Dict[str, Any]:
    """Clipboard read/write through xclip/xsel (X11) or wl-clipboard (Wayland)."""
    if op == "read":
        if _which("xclip"):
            res = _run(["xclip", "-o", "-selection", "clipboard"])
            if res and res["rc"] == 0:
                return {"ok": True, "text": res["stdout"], "backend": "xclip"}
        if _which("wl-paste"):
            res = _run(["wl-paste"])
            if res and res["rc"] == 0:
                return {"ok": True, "text": res["stdout"], "backend": "wl-paste"}
        if _which("xsel"):
            res = _run(["xsel", "--clipboard", "--output"])
            if res and res["rc"] == 0:
                return {"ok": True, "text": res["stdout"], "backend": "xsel"}
        return {"ok": False, "error": "no clipboard reader (xclip/wl-paste/xsel)",
                "hints": ["install xclip (X11) or wl-clipboard (Wayland)"]}
    if op == "write":
        if _which("xclip"):
            res = _run(["xclip", "-i", "-selection", "clipboard"], timeout=15)
            if res and res["rc"] == 0:
                return {"ok": True, "backend": "xclip"}
        if _which("wl-copy"):
            res = _run(["wl-copy"], timeout=15)
            if res and res["rc"] == 0:
                return {"ok": True, "backend": "wl-copy"}
        return {"ok": False, "error": "no clipboard writer (xclip/wl-copy)",
                "hints": ["install xclip (X11) or wl-clipboard (Wayland)"]}
    return {"ok": False, "error": f"unknown clipboard op '{op}'"}
