"""
ComputerController — the hermetic, injectable seam for Harness computer use.

This is the ONLY place that knows about screens, windows, the mouse, the
keyboard, and the clipboard. Everything above it (the tools) treats it as a
black box that returns structured dicts: capture with geometry/monitor info,
single batched input actions, window listing/focus, and clipboard read/write.

It is intentionally **hermetic and injectable**:

* `capture_backend` — a callable `(region, monitor_index, out_dir) -> dict`.
  Defaults to the real path (mss → PIL PNG, BMP fallback; then CLI drivers
  `grim`/`scrot`/`import`/`gnome-screenshot`). Tests inject a fake that never
  touches a display.
* `input_exec` — a callable `(action: dict) -> dict`. Defaults to the real
  path (ctypes XTEST via `harness.computer.xtest`, then CLI drivers
  `xdotool`/`ydotool`/`wmctrl`). Tests inject a fake that records actions.
* `window_backend` and `clipboard_driver` — same pattern for windows+clipboard.

Never raises for environment failures: missing display, missing libs, or a
headless box all produce a structured `{"ok": False, "error": ..., "hints": [...]}`
so the caller can degrade gracefully (and tell the model exactly what to
install). This keeps the computer tools safe to call in hermetic tests and in
PLAN mode (read-only inspection still works when a backend exists).
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from harness.computer.xtest import is_available as xtest_available
from harness.computer.wayland_utils import (
    detect_session_type,
    capture_hints,
    input_hints,
    is_wayland,
    cli_capture,
    cli_input,
)

# Try pyautogui backend
try:
    from harness.computer.pyautogui_backend import (
        is_available as pyautogui_available,
        capture_screen as pyautogui_capture,
        execute_input as pyautogui_input,
    )
except ImportError:
    pyautogui_available = lambda: False
    pyautogui_capture = None
    pyautogui_input = None

# ---------------------------------------------------------------------------
# Environment probe
# ---------------------------------------------------------------------------

def _probe_env() -> Dict[str, Any]:
    env = dict(os.environ)
    display = env.get("DISPLAY", "")
    wayland = env.get("WAYLAND_DISPLAY", "")
    session = detect_session_type()
    return {
        "session": session,
        "display": display,
        "wayland_display": wayland,
        "xtest": bool(xtest_available()),
        "wayland": is_wayland(),
        "cli": {
            "grim": bool(shutil.which("grim")),
            "scrot": bool(shutil.which("scrot")),
            "import": bool(shutil.which("import")),
            "gnome_screenshot": bool(shutil.which("gnome-screenshot")),
            "xdotool": bool(shutil.which("xdotool")),
            "ydotool": bool(shutil.which("ydotool")),
            "wmctrl": bool(shutil.which("wmctrl")),
            "xclip": bool(shutil.which("xclip")),
            "wl_paste": bool(shutil.which("wl-paste")),
        },
    }


DEFAULT_CAPTURE_BACKEND = None  # default resolved lazily in controller.__init__
DEFAULT_INPUT_EXEC = None


import mss
from PIL import Image
from io import BytesIO


def _default_capture_backend(region: Any, monitor_index: int, out_dir: str) -> Dict[str, Any]:
    """Real capture path. Tries pyautogui first (cross-platform), then mss, then CLI drivers."""
    # Try pyautogui backend first (simplest, most portable)
    if pyautogui_capture is not None and pyautogui_available():
        try:
            return pyautogui_capture(region=region, monitor_index=monitor_index, out_dir=out_dir)
        except Exception:
            pass
    
    # Try mss
    try:
        import mss as _mss_mod
    except ImportError:
        _mss_mod = None

    if _mss_mod is not None:
        try:
            # Newer mss versions expose MSS as the primary constructor.
            factory = getattr(_mss_mod, "MSS", None) or _mss_mod.mss
            with factory() as sct:
                monitors = sct.monitors or []
                if not monitors:
                    return default_result("capture", "no monitors detected by mss")
                # When monitor_index=0 (virtual screen), prefer monitor 1 on
                # multi-monitor setups to avoid black frames on Wayland.
                idx = monitor_index
                if idx == 0 and len(monitors) > 1:
                    idx = 1
                mon = monitors[idx] if 0 <= idx < len(monitors) else monitors[0]
                shot = sct.grab(mon)
                width, height = shot.width, shot.height
                try:
                    from PIL import Image
                    import os
                    os.makedirs(out_dir, exist_ok=True)
                    png_path = os.path.join(out_dir, _stamp_name("png"))
                    Image.frombytes("RGB", (width, height), shot.rgb).save(png_path)
                    image_path = png_path
                    format_ = "png"
                except Exception:
                    try:
                        bmp_path = os.path.join(out_dir, _stamp_name("bmp"))
                        Image.frombytes("RGB", (width, height), shot.rgb).save(bmp_path)
                        image_path = bmp_path
                        format_ = "bmp"
                    except Exception as exc:
                        return default_result("capture", f"could not persist screenshot: {exc}")
        except Exception as exc:
            mss_err = str(exc)
        else:
            return {
                "ok": True,
                "image_path": image_path,
                "format": format_,
                "width": width,
                "height": height,
                "monitors": monitors,
                "monitor_index": monitor_index,
                "region": region,
                "backend": "mss",
            }
    else:
        mss_err = "mss unavailable"

    # CLI fallback path (grim / scrot / import / gnome-screenshot).
    cli_res = cli_capture(region=region, monitor_index=monitor_index, out_dir=out_dir)
    return cli_res or default_result(
        "capture",
        f"no capture backend available ({mss_err}); "
        "install pyautogui (pip install pyautogui) or grim/scrot/ImageMagick import",
    )


def _stamp_name(ext: str) -> str:
    return f"screen-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.{ext}"


def _default_input_exec(action: Dict[str, Any]) -> Dict[str, Any]:
    """Real input path. Tries pyautogui first (cross-platform), then XTEST, then CLI drivers."""
    # Try pyautogui backend first (simplest, most portable)
    if pyautogui_input is not None and pyautogui_available():
        try:
            return pyautogui_input(action)
        except Exception:
            pass
    
    from harness.computer.xtest import XTestSession

    action_type = action.get("action", "")
    name = action_type.lower()

    # Try XTEST first for the actions it supports natively.
    if name in _XTEST_ACTIONS:
        try:
            with XTestSession() as session:
                return _apply_xtest(session, action)
        except Exception as exc:
            xtest_err = str(exc)
    else:
        xtest_err = ""

    cli_res = cli_input(action)
    if cli_res:
        return cli_res
    return default_result(
        "input",
        f"no input backend available for '{name}' ({xtest_err}); "
        "install pyautogui (pip install pyautogui) or xdotool/ydotool",
    )


_XTEST_ACTIONS = {"move", "click", "drag", "scroll", "type", "key", "keys", "combo", "keyup", "keydown", "button"}


def _apply_xtest(session: Any, action: Dict[str, Any]) -> Dict[str, Any]:
    action_type = (action.get("action") or "").lower()
    if action_type == "move":
        session.move(int(action.get("x", 0)), int(action.get("y", 0)))
        return {"ok": True, "action": action_type, "x": action.get("x"), "y": action.get("y")}
    if action_type == "click":
        btn = action.get("button", "left")
        session.click(btn)
        return {"ok": True, "action": action_type, "button": btn}
    if action_type == "double":
        session.double_click(action.get("button", "left"))
        return {"ok": True, "action": action_type}
    if action_type == "drag":
        session.drag(
            action.get("x1", 0), action.get("y1", 0),
            action.get("x2", 0), action.get("y2", 0),
            action.get("button", "left"),
        )
        return {"ok": True, "action": action_type}
    if action_type == "scroll":
        session.scroll(action.get("x", 0), action.get("y", 0))
        return {"ok": True, "action": action_type}
    if action_type == "type":
        session.type_text(action.get("text", ""))
        return {"ok": True, "action": action_type}
    if action_type == "key":
        token = action.get("key", "")
        session.key(token, down=True)
        session.key(token, down=False)
        return {"ok": True, "action": action_type, "key": token}
    if action_type in ("combo", "chord", "keys"):
        keys = action.get("keys") or []
        for k in keys:
            session.combo_press(k)
        for k in reversed(keys):
            session.combo_release(k)
        return {"ok": True, "action": action_type, "keys": keys}
    if action_type in ("keydown", "press"):
        session.key(action.get("key", ""), down=True)
        return {"ok": True, "action": action_type}
    if action_type in ("keyup", "release"):
        session.key(action.get("key", ""), down=False)
        return {"ok": True, "action": action_type}
    return default_result("input", f"unsupported XTEST action '{action_type}'")


def default_result(kind: str, error: str) -> Dict[str, Any]:
    return {"ok": False, "kind": kind, "error": error, "hints": (_hints_for(kind))}


def _hints_for(kind: str) -> List[str]:
    env = _probe_env()
    if kind == "capture":
        if env["wayland"]:
            return ["install grim to screenshot on Wayland", "or run under X11/XWayland and install scrot"]
        return ["install scrot, ImageMagick import, or gnome-screenshot on X11"]
    if kind == "input":
        if env["wayland"]:
            return ["install ydotool and grant CAP_DAC_OVERRIDE", "or run under X11/XWayland for xdotool/XTEST"]
        return ["install xdotool on X11", "or ensure libXtst/libX11 are present for ctypes XTEST"]
    return []


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class ComputerController:
    """Hermetic computer controller. Every rendered input path underneath is
    injectable so unit tests never touch a real display."""

    def __init__(
        self,
        capture_backend: Optional[Callable] = None,
        input_exec: Optional[Callable] = None,
        window_backend: Optional[Callable] = None,
        clipboard_driver: Optional[Callable] = None,
        screenshot_dir: Optional[str] = None,
        allow_read_only: bool = True,
    ):
        self.capture_backend = capture_backend or _default_capture_backend
        self.input_exec = input_exec or _default_input_exec
        self.window_backend = window_backend or self._default_window_backend
        self.clipboard_driver = clipboard_driver or self._default_clipboard_driver
        self.screenshot_dir = screenshot_dir or os.path.expanduser("~/.harness/screenshots")
        self.allow_read_only = allow_read_only
        self._latest_capture_path: Optional[str] = None

    # -- env / probe ------------------------------------------------
    def probe(self) -> Dict[str, Any]:
        env = _probe_env()
        return {
            "ok": True,
            **env,
            "screenshot_dir": self.screenshot_dir,
        }

    # -- capture ----------------------------------------------------
    def capture(
        self,
        region: Any = None,
        monitor_index: int = 0,
        out_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        out_dir = out_dir or self.screenshot_dir
        result = self.capture_backend(region, monitor_index, out_dir)
        if result.get("ok"):
            self._latest_capture_path = result.get("image_path") or result.get("path")
        return result

    def latest_capture(self) -> Optional[str]:
        """Return the path of the most recent successful screenshot, or None."""
        return self._latest_capture_path

    # -- input ------------------------------------------------------
    def execute_input(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return self.input_exec(action)

    def execute_batch(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for action in actions or []:
            res = self.execute_input(action)
            results.append(res)
            if not res.get("ok"):
                break
        return results

    # -- windows -----------------------------------------------------
    def _default_window_backend(self, op: str, **kwargs: Any) -> Dict[str, Any]:
        from harness.computer.wayland_utils import cli_window
        return cli_window(op, **kwargs)

    def list_windows(self) -> Dict[str, Any]:
        return self.window_backend("list")

    def focus_window(self, window_id: str) -> Dict[str, Any]:
        return self.window_backend("focus", window_id=window_id)

    def active_window(self) -> Dict[str, Any]:
        return self.window_backend("active")

    # -- clipboard -----------------------------------------------------
    def _default_clipboard_driver(self, op: str, text: Optional[str] = None) -> Dict[str, Any]:
        from harness.computer.wayland_utils import cli_clipboard
        return cli_clipboard(op, text=text)

    def clipboard_read(self) -> Dict[str, Any]:
        return self.clipboard_driver("read")

    def clipboard_write(self, text: str) -> Dict[str, Any]:
        return self.clipboard_driver("write", text=text)
