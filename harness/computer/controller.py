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

GUI guards: the controller tracks capture/input timestamps so tools can
refuse to type into a stale screenshot, a background window, or a
GNOME/COSMIC overview-search (where typing+Return launches the top search
hit — the Claude-instead-of-Discord failure). See
``screen_state()`` and ``harness.computer.grounding.detect_overview``.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from harness.computer.xtest import is_available as xtest_available, XTestSession
from harness.computer.wayland_utils import (
    detect_platform,
    detect_session_type,
    capture_hints,
    input_hints,
    is_wayland,
    cli_capture,
    cli_input,
    clipboard_paste,
    is_ascii_text,
    preferred_capture_backend,
    preferred_input_backend,
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
    platform = detect_platform()
    try:
        from harness.computer.wayland_utils import detect_de
        de = detect_de()
    except Exception:
        de = "unknown"
    return {
        "platform": platform,
        "session": session,
        "de": de,
        "display": display,
        "wayland_display": wayland,
        "xtest": bool(xtest_available()),
        "wayland": is_wayland(),
        "preferred_capture": preferred_capture_backend(),
        "preferred_input": preferred_input_backend(),
        "cli": {
            "grim": bool(shutil.which("grim")),
            "scrot": bool(shutil.which("scrot")),
            "import": bool(shutil.which("import")),
            "maim": bool(shutil.which("maim")),
            "gnome_screenshot": bool(shutil.which("gnome-screenshot")),
            "gdbus": bool(shutil.which("gdbus")),
            "spectacle": bool(shutil.which("spectacle")),
            "xfce4_screenshooter": bool(shutil.which("xfce4-screenshooter")),
            "cosmic_screenshot": bool(shutil.which("cosmic-screenshot")),
            "xdotool": bool(shutil.which("xdotool")),
            "ydotool": bool(shutil.which("ydotool")),
            "wmctrl": bool(shutil.which("wmctrl")),
            "swaymsg": bool(shutil.which("swaymsg")),
            "hyprctl": bool(shutil.which("hyprctl")),
            "tesseract": bool(shutil.which("tesseract")),
            "xclip": bool(shutil.which("xclip")),
            "wl_paste": bool(shutil.which("wl-paste")),
            "screencapture": bool(shutil.which("screencapture")),
            "powershell": bool(shutil.which("powershell") or shutil.which("pwsh")),
            "osascript": bool(shutil.which("osascript")),
            "cliclick": bool(shutil.which("cliclick")),
        },
    }


DEFAULT_CAPTURE_BACKEND = None  # default resolved lazily in controller.__init__
DEFAULT_INPUT_EXEC = None


def _image_size(path: str) -> Tuple[int, int]:
    """Read PNG/BMP dimensions without requiring Pillow.

    The capture layer must stay importable on machines where mss/Pillow are
    missing (the CLI screenshot drivers still work there), so geometry is read
    straight from the file header. Unknown formats fall back to Pillow when it
    is installed, else (0, 0).
    """
    try:
        import struct
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
                width, height = struct.unpack(">II", head[16:24])
                return int(width), int(height)
            if head[:2] == b"BM" and len(head) >= 26:
                width, height = struct.unpack("<ii", head[18:26])
                return int(width), abs(int(height))
    except Exception:
        pass
    try:
        from PIL import Image  # optional
        with Image.open(path) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return 0, 0


def _default_capture_backend(region: Any, monitor_index: int, out_dir: str) -> Dict[str, Any]:
    """Real capture path.

    On Linux Wayland, compositor-native CLI capture (gnome-screenshot /
    cosmic-screenshot / spectacle / grim) is both faster and the only way to
    see native windows. mss/pyautogui often return black frames there, so try
    the CLI first. On X11/macOS/Windows, mss/pyautogui is the fast path.
    """
    platform = detect_platform()
    if platform == "linux" and is_wayland():
        cli_res = cli_capture(region=region, monitor_index=monitor_index, out_dir=out_dir)
        if cli_res.get("ok"):
            return _enrich_capture(cli_res)

    # Try pyautogui backend first (cross-platform)
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

    # CLI fallback path (platform-native drivers).
    cli_res = cli_capture(region=region, monitor_index=monitor_index, out_dir=out_dir)
    if cli_res and cli_res.get("ok"):
        return _enrich_capture(cli_res)
    return cli_res or default_result(
        "capture",
        f"no capture backend available ({mss_err}); "
        "install pyautogui/mss or a platform screenshot tool",
    )


def _enrich_capture(result: Dict[str, Any]) -> Dict[str, Any]:
    """Fill width/height/geometry for CLI captures that only return a path."""
    path = result.get("image_path") or result.get("path")
    if not path:
        return result
    result.setdefault("image_path", path)
    if result.get("width") and result.get("height"):
        result.setdefault("geometry", {
            "x": 0, "y": 0,
            "width": int(result["width"]), "height": int(result["height"]),
        })
        return result
    try:
        width, height = _image_size(path)
        if width > 0 and height > 0:
            result["width"] = width
            result["height"] = height
            result["geometry"] = {"x": 0, "y": 0, "width": width, "height": height}
        else:
            result.setdefault("width", 0)
            result.setdefault("height", 0)
    except Exception:
        result.setdefault("width", 0)
        result.setdefault("height", 0)
    return result


def _stamp_name(ext: str) -> str:
    return f"screen-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}-{os.getpid()}.{ext}"


def _default_input_exec(action: Dict[str, Any]) -> Dict[str, Any]:
    """Real input path.

    Pointer actions need *absolute* screen coordinates. On X11 and XWayland
    XTEST provides that; ``ydotool`` 0.1.8's ``mousemove`` is actually a
    relative move (reset to the origin, then delta), which is why the cursor
    used to end up in a screen corner instead of the requested point. So on
    Linux, route pointer actions through XTEST first whenever an X display is
    reachable.

    Keyboard/text actions still go through ``ydotool`` first on Wayland,
    because XTEST only reaches XWayland clients. On XWayland, clicks are
    positioned by XTEST and then injected with ydotool (when available) so
    native Wayland windows receive them too. Everywhere else pyautogui is the
    fast, cross-platform path.

    Text is the exception: direct key synthesis (XTEST, ydotool ``type``,
    pyautogui ``typewrite``) only types characters bound in the current
    keymap. Any non-ASCII text (accents, CJK, emoji — the full Unicode
    range) is delivered by clipboard + paste instead, on every OS.
    """
    platform = detect_platform()
    session_type = detect_session_type()
    action_type = action.get("action", "")
    name = action_type.lower()
    pointer = name in _POINTER_ACTIONS

    # Unicode typing goes via clipboard paste. ASCII typing keeps the fast
    # direct-key paths below (no clipboard clobber, natural typing feel).
    if name == "type":
        body = str(action.get("text", ""))
        if body and not is_ascii_text(body):
            return clipboard_paste(body)

    # Absolute pointer placement (X11 / XWayland).
    if platform == "linux" and pointer and session_type in ("x11", "xwayland"):
        try:
            with XTestSession() as session:
                res = _apply_xtest(
                    session, action,
                    hybrid_clicks=(session_type == "xwayland"),
                )
                if res.get("ok"):
                    return res
                xtest_err = str(res.get("error") or "")
        except Exception as exc:
            xtest_err = str(exc)
    else:
        xtest_err = ""

    cli_first = platform == "linux" and is_wayland()

    if cli_first:
        cli_res = cli_input(action)
        if cli_res and cli_res.get("ok"):
            return cli_res
        if pyautogui_input is not None and pyautogui_available():
            try:
                res = pyautogui_input(action)
                if res.get("ok"):
                    res["warning"] = (
                        "Applied via pyautogui/XTEST, which only reaches XWayland "
                        "windows on Wayland. If this was a native Wayland window, "
                        "install/start ydotool and retry."
                    )
                    return res
            except Exception:
                pass
        return cli_res or default_result("input", "no Wayland input backend")

    # Cross-platform fast path (X11, macOS, Windows).
    if pyautogui_input is not None and pyautogui_available():
        try:
            return pyautogui_input(action)
        except Exception:
            pass

    # Try XTEST for the actions it supports natively (X11).
    if platform == "linux" and name in _XTEST_ACTIONS:
        try:
            with XTestSession() as session:
                return _apply_xtest(session, action)
        except Exception as exc:
            xtest_err = str(exc)

    cli_res = cli_input(action)
    if cli_res:
        return cli_res
    return default_result(
        "input",
        f"no input backend available for '{name}' ({xtest_err}); "
        "install pyautogui or the platform input tool",
    )


_XTEST_ACTIONS = {"move", "click", "double", "double_click", "right", "right_click", "middle", "middle_click", "drag", "scroll", "type", "key", "keys", "combo", "keyup", "keydown", "button"}
_POINTER_ACTIONS = {"move", "click", "double", "double_click", "right", "right_click", "middle", "middle_click", "drag", "scroll", "hscroll", "button"}
_SCROLL_DIRECTIONS = {"down", "up", "left", "right"}


def _apply_xtest(
    session: Any,
    action: Dict[str, Any],
    hybrid_clicks: bool = False,
) -> Dict[str, Any]:
    action_type = (action.get("action") or "").lower()
    x, y = action.get("x"), action.get("y")

    def positioned() -> bool:
        if x is None or y is None:
            return False
        session.move(int(x), int(y))
        return True

    def press(button: str, double: bool = False) -> Tuple[str, str]:
        """Click at the (already positioned) pointer.

        On XWayland XTEST clicks only reach X11 clients. ydotool injects the
        button through uinput at the compositor's current pointer, so it also
        reaches native Wayland windows. XTEST still did the positioning.
        """
        if hybrid_clicks:
            clicks = 2 if double else 1
            delivered = 0
            for i in range(clicks):
                res = cli_input({"action": "click", "button": button})
                if not res or not res.get("ok"):
                    break
                delivered += 1
                if i == 0 and clicks == 2:
                    time.sleep(0.12)
            if delivered == clicks:
                return "xtest+ydotool", ""
            if delivered:
                return "xtest+ydotool", (
                    "Only the first half of the double-click was delivered; "
                    "check the screen before retrying."
                )
        if double:
            session.double_click(button)
        else:
            session.click(button)
        return "xtest", ""

    if action_type == "move":
        if not positioned():
            return {"ok": False, "error": "move needs x and y"}
        return {"ok": True, "action": action_type, "x": x, "y": y,
                "backend": "xtest"}
    if action_type in ("click", "double", "double_click", "right",
                       "right_click", "middle", "middle_click"):
        positioned()
        button = action.get("button", "left")
        if action_type in ("right", "right_click"):
            button = "right"
        elif action_type in ("middle", "middle_click"):
            button = "middle"
        double = action_type in ("double", "double_click")
        backend, warning = press(str(button), double)
        result = {"ok": True, "action": action_type, "button": button,
                  "backend": backend}
        if warning:
            result["warning"] = warning
        return result
    if action_type == "drag":
        session.drag(
            int(action.get("x1", 0) or 0), int(action.get("y1", 0) or 0),
            int(action.get("x2", 0) or 0), int(action.get("y2", 0) or 0),
            action.get("button", "left"),
        )
        return {"ok": True, "action": action_type, "backend": "xtest"}
    if action_type == "scroll":
        positioned()
        direction = str(action.get("direction", "down")).lower()
        amount = max(1, min(int(action.get("amount", 3) or 3), 20))
        if direction == "down":
            session.scroll(0, -amount)
        elif direction == "up":
            session.scroll(0, amount)
        elif direction == "left":
            session.scroll(-amount, 0)
        else:
            session.scroll(amount, 0)
        return {"ok": True, "action": action_type, "direction": direction,
                "amount": amount, "backend": "xtest"}
    if action_type == "type":
        body = str(action.get("text", ""))
        if body and not is_ascii_text(body):
            return {
                "ok": False,
                "error": (
                    "XTEST cannot type non-ASCII text via keycodes; use "
                    "clipboard paste (the default input path routes Unicode "
                    "there automatically)."
                ),
                "hints": input_hints(),
            }
        session.type_text(body)
        return {"ok": True, "action": action_type}
    if action_type == "key":
        token = action.get("key", "")
        session.key(token, down=True)
        session.key(token, down=False)
        return {"ok": True, "action": action_type, "key": token}
    if action_type in ("combo", "chord", "keys"):
        keys = action.get("keys") or []
        session.combo([str(k) for k in keys])
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
    if kind == "capture":
        return capture_hints()
    if kind == "input":
        return input_hints()
    return []


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class ComputerController:
    """Hermetic computer controller. Every rendered input path underneath is
    injectable so unit tests never touch a real display."""

    MARKS_TTL_SECONDS = 90.0

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
        self._latest_geometry: Dict[str, int] = {}
        self._latest_marks: List[Dict[str, Any]] = []
        self._latest_marks_path: Optional[str] = None
        self._latest_marks_ts: float = 0.0
        self._latest_capture_ts: float = 0.0
        self._last_input_ts: float = 0.0
        self._last_input_action: str = ""
        # Last click that landed on an input-capable surface. Typing verification
        # uses it as an anchor: the typed text must appear near this point, or it
        # was captured by another surface (overview search, another window).
        self._last_click: Dict[str, Any] = {}
        # When typing could not be verified, manual non-Escape keys (especially
        # Return) are blocked until Escape or a verified retype clears this.
        self._typing_blocked: str = ""

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
        with_marks: bool = False,
    ) -> Dict[str, Any]:
        out_dir = out_dir or self.screenshot_dir
        result = self.capture_backend(region, monitor_index, out_dir)
        if result.get("ok"):
            self._latest_capture_path = result.get("image_path") or result.get("path")
            # D2: normalize to one shape {ok, image_path, width, height, monitors, backend}
            result["image_path"] = self._latest_capture_path
            if result.get("path") and not result.get("image_path"):
                result["image_path"] = result["path"]
            result.setdefault("width", result.get("width") or 0)
            result.setdefault("height", result.get("height") or 0)
            result.setdefault("monitors", result.get("monitors") or [])
            result.setdefault("backend", result.get("backend") or "default")
            self._latest_geometry = {
                "x": int(result.get("x", 0) or 0),
                "y": int(result.get("y", 0) or 0),
                "width": int(result.get("width", 0) or 0),
                "height": int(result.get("height", 0) or 0),
            }
            self._latest_capture_ts = time.time()
            if with_marks:
                marks = self.compute_marks()
                result["marks"] = marks
                if marks:
                    from harness.computer.grounding import render_marks_text, detect_overview
                    result["marks_text"] = render_marks_text(marks)
                    ov = detect_overview(marks)
                    if ov.get("overview"):
                        result["overview"] = True
                        result["overview_reason"] = ov.get("reason", "")
                        result["warning"] = (
                            "OVERVIEW/LAUNCHER OPEN: typing+Return here searches "
                            "and launches the top hit, it does not reach the app. "
                            "Press Escape or click the real window, then re-screenshot. "
                            "Do NOT type now."
                        )
                self._latest_marks_ts = time.time()
        return result

    def screen_state(self) -> Dict[str, Any]:
        """Latest screenshot freshness + overview flag for tool preflight."""
        try:
            from harness.computer.grounding import detect_overview
            ov = detect_overview(list(self._latest_marks)) if self._latest_marks else {"overview": False, "reason": ""}
        except Exception:
            ov = {"overview": False, "reason": ""}
        now = time.time()
        capture_age = (now - self._latest_capture_ts) if self._latest_capture_ts else None
        marks_age = (now - self._latest_marks_ts) if self._latest_marks_ts else None
        return {
            "has_screenshot": bool(self._latest_capture_path),
            "image_path": self._latest_capture_path,
            "marks_count": len(self._latest_marks),
            "capture_age_seconds": round(capture_age, 1) if capture_age is not None else None,
            "marks_age_seconds": round(marks_age, 1) if marks_age is not None else None,
            "marks_stale": bool(marks_age is not None and marks_age > self.MARKS_TTL_SECONDS),
            "overview": bool(ov.get("overview")),
            "overview_reason": ov.get("reason", ""),
            "needs_fresh_screenshot": bool(
                self._last_input_ts
                and self._latest_capture_ts < self._last_input_ts
            ),
            "last_input_action": self._last_input_action,
            "last_input_age_seconds": round(now - self._last_input_ts, 1) if self._last_input_ts else None,
            "last_click": dict(self._last_click) if self._last_click else None,
            "typing_blocked": bool(self._typing_blocked),
            "typing_blocked_reason": self._typing_blocked,
        }

    def latest_capture(self) -> Optional[str]:
        """Return the path of the most recent successful screenshot, or None."""
        return self._latest_capture_path

    def latest_geometry(self) -> Dict[str, int]:
        return dict(self._latest_geometry)

    def latest_marks(self) -> List[Dict[str, Any]]:
        return list(self._latest_marks)

    def compute_marks(
        self,
        image_path: Optional[str] = None,
        max_marks: int = 70,
    ) -> List[Dict[str, Any]]:
        """OCR the capture into line-level marks and cache them.

        Marks are cached per screenshot path so repeated calls in one turn
        don't spam tesseract, but a new capture always refreshes them.
        """
        path = image_path or self._latest_capture_path
        if not path:
            return []
        if path == self._latest_marks_path and self._latest_marks:
            return list(self._latest_marks)
        try:
            from harness.computer.grounding import ocr_marks
            marks = ocr_marks(path, max_marks=max_marks)
        except Exception:
            marks = []
        self._latest_marks = marks
        self._latest_marks_path = path
        self._latest_marks_ts = time.time()
        return list(marks)

    def resolve_mark(self, number: Any) -> Optional[Dict[str, int]]:
        """Resolve a mark number to absolute pixel coordinates."""
        from harness.computer.grounding import find_mark
        mark = find_mark(self._latest_marks, number)
        if not mark:
            return None
        return {"x": int(mark.get("x", 0)), "y": int(mark.get("y", 0))}

    # -- input ------------------------------------------------------
    def _resolve_action_coords(self, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Resolve mark / normalized coords to absolute pixels in-place.

        Returns an error dict when the model used normalized coords before any
        screenshot established the geometry (the classic "click 0,0" bug),
        or when marks are stale (the screen may have changed since).
        """
        if action.get("mark") is not None and action.get("mark") != "":
            if self._latest_marks and self._latest_marks_ts and (time.time() - self._latest_marks_ts) > self.MARKS_TTL_SECONDS:
                return {
                    "ok": False,
                    "error": (
                        "Marks are stale (screenshot >90s old). Re-screenshot "
                        "before clicking — the screen may have changed."
                    ),
                    "next": "Call computer_screenshot, then click by mark=N.",
                }
            pos = self.resolve_mark(action.get("mark"))
            if not pos:
                available = ", ".join(str(m.get("mark")) for m in self._latest_marks[:20]) or "none"
                return {
                    "ok": False,
                    "error": (
                        f"Unknown mark {action.get('mark')}. Call computer_screenshot "
                        f"first; available marks: {available}."
                    ),
                }
            action["x"], action["y"] = pos["x"], pos["y"]
            action.pop("nx", None)
            action.pop("ny", None)
            return None

        if action.get("nx") is not None and action.get("ny") is not None:
            geom = self._latest_geometry or {}
            width = int(geom.get("width", 0) or 0)
            height = int(geom.get("height", 0) or 0)
            if width <= 0 or height <= 0:
                return {
                    "ok": False,
                    "error": (
                        "Normalized coordinates need a screenshot first. "
                        "Call computer_screenshot (or click by mark=N)."
                    ),
                }
            if self._latest_marks and self._latest_marks_ts and (time.time() - self._latest_marks_ts) > self.MARKS_TTL_SECONDS:
                return {
                    "ok": False,
                    "error": (
                        "Screenshot is stale (>90s old). Re-screenshot before "
                        "acting with nx/ny — coordinates may point at another window."
                    ),
                    "next": "Call computer_screenshot, then click by mark=N.",
                }
            from harness.computer.grounding import normalize_coords
            pos = normalize_coords(float(action["nx"]), float(action["ny"]), width, height)
            action["x"] = pos["x"] + int(geom.get("x", 0) or 0)
            action["y"] = pos["y"] + int(geom.get("y", 0) or 0)
            action.pop("nx", None)
            action.pop("ny", None)
        return None

    def _record_input(self, action_name: str, action: Optional[Dict[str, Any]] = None) -> None:
        self._last_input_ts = time.time()
        self._last_input_action = action_name
        if not action:
            return
        name = str(action.get("action") or "").lower()
        if name in ("click", "double", "double_click", "drag"):
            x, y = action.get("x"), action.get("y")
            if x is not None and y is not None:
                self._last_click = {
                    "x": int(x), "y": int(y), "ts": time.time(),
                    "action": action_name, "mark": action.get("mark"),
                }
        if name in ("key", "keys", "combo", "chord", "keydown", "keyup"):
            from harness.computer.grounding import is_escape_combo, is_escape_key
            if (is_escape_key(action.get("key"))
                    or is_escape_combo(action.get("keys"))):
                # Escape is the bail-out: it clears the overview and the
                # unverified-typing lock.
                self._typing_blocked = ""

    def _keyboard_prescreen(self, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Block non-Escape keys on an overview/launcher or before any screenshot."""
        name = str(action.get("action") or "").lower()
        if name not in ("key", "keys", "combo", "chord", "keydown", "keyup"):
            return None
        from harness.computer.guards import keyboard_allowed
        verdict = keyboard_allowed(
            key=action.get("key"),
            keys=action.get("keys") if isinstance(action.get("keys"), list) else None,
            state=self.screen_state(),
        )
        if verdict.get("allow"):
            return None
        return {
            "ok": False,
            "error": verdict.get("reason", "keyboard input blocked"),
            "kind": verdict.get("kind"),
            "next": verdict.get("next", ""),
        }

    def execute_input(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action = dict(action or {})
        cord_used = "mark" if action.get("mark") is not None else ("nxny" if action.get("nx") is not None else None)
        err = self._resolve_action_coords(action)
        if err:
            return err
        blocked = self._keyboard_prescreen(action)
        if blocked:
            return blocked
        res = self.input_exec(action)
        if isinstance(res, dict) and res.get("ok"):
            self._record_input(str(action.get("action") or ""), action)
            if cord_used == "nxny" and self._latest_marks:
                res = dict(res)
                res["warning"] = (
                    "Used raw 0-1000 nx/ny while OCR marks are available. "
                    "Prefer computer_click(mark=N) — nx/ny easily hits another window."
                )
        return res

    # -- guarded typing ---------------------------------------------
    def typing_preflight(self, expect: Optional[str] = None) -> Dict[str, Any]:
        """Capture fresh pixels, then decide whether a keystroke is safe.

        Always takes a new screenshot: the screen the model last saw may be
        stale, and this is the only cross-platform way to prove what is in
        front of the user before typing.
        """
        from harness.computer.guards import typing_preflight
        shot = self.capture(with_marks=True)
        if not shot.get("ok"):
            return {
                "allow": False, "ok": False, "kind": "capture_failed",
                "reason": f"Could not capture the screen before typing: {shot.get('error')}",
                "next": "Fix the screenshot backend, then retry. Do not type blind.",
            }
        verdict = typing_preflight(
            marks=self._latest_marks,
            state=self.screen_state(),
            expect=expect,
        )
        verdict["screenshot"] = self._latest_capture_path
        return verdict

    def type_text(
        self,
        text: str,
        submit: bool = False,
        expect: Optional[str] = None,
        verify: bool = True,
        allow_unverified: bool = False,
    ) -> Dict[str, Any]:
        """Type text, prove it landed near the intended field, then submit.

        ``submit=True`` presses Return only after the text is visible in the
        focused window. If verification fails the call returns
        ``delivered=False`` and no Return is sent — a stray message can no
        longer be launched into whatever surface happens to own focus.
        """
        from harness.computer.guards import check_landed

        pre: Dict[str, Any] = {"allow": True, "ok": True, "verified": False}
        if verify:
            pre = self.typing_preflight(expect=expect)
            if not pre.get("allow"):
                self._typing_blocked = str(
                    pre.get("reason") or pre.get("kind") or "typing blocked")
                return {
                    "ok": False,
                    "delivered": False,
                    "verified": False,
                    "submitted": False,
                    "kind": pre.get("kind"),
                    "error": pre.get("reason", "typing blocked"),
                    "next": pre.get("next", ""),
                    "screenshot": pre.get("screenshot"),
                }

        typed = self.input_exec({"action": "type", "text": text})
        if not isinstance(typed, dict) or not typed.get("ok"):
            self._typing_blocked = "the typing backend failed; delivery unknown"
            return {
                "ok": False, "delivered": False, "verified": False,
                "submitted": False, "kind": "input_failed",
                "error": str((typed or {}).get("error") or "typing backend failed"),
                "next": "Check the input backend (ydotool/pyautogui) and retry.",
            }
        self._record_input("type")

        if not verify:
            if submit and not allow_unverified:
                self._typing_blocked = (
                    "verification was disabled, so the typed text may be in the "
                    "wrong surface")
                return {
                    "ok": False, "delivered": None, "verified": False,
                    "submitted": False, "kind": "submit_unverified",
                    "error": (
                        "Verification was disabled; refusing to press Return blind. "
                        "An unverified Enter can send/launch in the wrong surface."
                    ),
                    "next": (
                        "Use verify=true (recommended), or pass allow_unverified=true "
                        "only if you visually confirmed the text is in the right input."
                    ),
                }
            submitted = False
            if submit:
                enter = self.input_exec({"action": "key", "key": "Return"})
                submitted = bool(isinstance(enter, dict) and enter.get("ok"))
                if submitted:
                    self._record_input("key")
            if not submit:
                # Typed but unproven: block a later manual Return until Escape
                # or a verified retype.
                self._typing_blocked = (
                    "typing was unverified; delivery to the intended input is unknown")
            else:
                self._typing_blocked = ""
            return {
                "ok": True, "delivered": None, "verified": False,
                "submitted": submitted,
                "warning": (
                    "Verification was disabled. The text may have gone to another "
                    "window; confirm visually before claiming it was sent."
                ),
            }

        post = self.capture(with_marks=True)
        if not post.get("ok"):
            self._typing_blocked = "typed text could not be verified (capture failed)"
            return {
                "ok": False, "delivered": None, "verified": False,
                "submitted": False, "kind": "verify_capture_failed",
                "error": (
                    "Typed, but could not capture the screen to verify delivery. "
                    "DO NOT press Return; re-screenshot and check manually."
                ),
                "next": "Call computer_screenshot, inspect the input, then retry.",
            }

        geom = self._latest_geometry or {}
        landed = check_landed(
            self._latest_marks, text,
            anchor=self._last_click or None,
            screen_height=int(geom.get("height", 0) or 0),
        )
        if not landed.get("ok"):
            self._typing_blocked = str(
                landed.get("reason") or "typed text was not verified")
            return {
                "ok": False,
                "delivered": landed.get("delivered"),
                "verified": bool(landed.get("verified")),
                "submitted": False,
                "kind": landed.get("kind"),
                "error": landed.get("reason", "typed text not verified"),
                "next": landed.get("next", ""),
                "anchor_checked": landed.get("anchor_checked"),
                "screenshot": self._latest_capture_path,
            }

        if submit and not landed.get("verified") and not allow_unverified:
            self._typing_blocked = "typed text could not be verified (no OCR)"
            return {
                "ok": False, "delivered": None, "verified": False,
                "submitted": False, "kind": "submit_unverified",
                "error": (
                    "Text was typed but cannot be verified on screen (no OCR). "
                    "Refusing to press Return, because an unverified Enter can "
                    "launch/submit in the wrong surface."
                ),
                "next": (
                    "Install tesseract-ocr, or pass allow_unverified=true only if "
                    "you visually confirmed the text is in the right input."
                ),
                "screenshot": self._latest_capture_path,
            }

        # Verified delivery (or explicit opt-in): a later Return is now safe.
        self._typing_blocked = ""

        if submit:
            enter = self.input_exec({"action": "key", "key": "Return"})
            if not isinstance(enter, dict) or not enter.get("ok"):
                return {
                    "ok": False, "delivered": True, "verified": True,
                    "submitted": False, "kind": "submit_failed",
                    "error": str((enter or {}).get("error") or "Return key failed"),
                    "next": "Text is in the input; press Return manually or retry computer_key.",
                }
            self._record_input("key")

        return {
            "ok": True,
            "delivered": True,
            "verified": bool(landed.get("verified")),
            "submitted": bool(submit),
            "anchor_checked": bool(landed.get("anchor_checked")),
            "screenshot": self._latest_capture_path,
        }

    def execute_batch(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run a macro. Supports focus/wait/screenshot control steps.

        ``focus`` steps take ``window_id`` or ``app``; ``wait`` sleeps
        (capped at 2s); ``type`` steps go through :meth:`type_text` so a
        macro cannot type into the wrong surface; every other step is a
        normal input action.
        """
        results: List[Dict[str, Any]] = []
        for action in actions or []:
            action = dict(action or {})
            name = str(action.get("action") or "").lower()
            if name == "focus":
                res = self.focus_window(
                    str(action.get("window_id") or ""),
                    app=str(action.get("app") or ""),
                )
            elif name in ("wait", "sleep"):
                ms = max(0, min(int(action.get("ms", 250) or 250), 2000))
                time.sleep(ms / 1000.0)
                res = {"ok": True, "action": "wait", "ms": ms}
            elif name == "screenshot":
                shot = self.capture(with_marks=False)
                res = {"ok": bool(shot.get("ok")),
                       "image_path": shot.get("image_path"),
                       "error": shot.get("error")}
            elif name in ("type", "text"):
                res = self.type_text(
                    str(action.get("text") or ""),
                    submit=bool(action.get("submit")),
                    expect=(str(action.get("expect") or action.get("target") or "") or None),
                    verify=action.get("verify") is not False,
                    allow_unverified=bool(action.get("allow_unverified")),
                )
            else:
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

    def focus_window(self, window_id: str, app: str = "") -> Dict[str, Any]:
        return self.window_backend("focus", window_id=window_id, app=app)

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
