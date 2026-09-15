"""
PyAutoGUI backend for Harness computer use.

Provides a unified, cross-platform backend for screen capture and input
using pyautogui, which handles both X11 and Wayland (via XWayland) automatically.
Falls back gracefully when pyautogui is not available.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy imports to avoid hard dependency
_pyautogui = None
_pil_image = None
_mss = None
_imports_done = False


def _ensure_imports():
    """Lazy-load pyautogui and friends.

    pyautogui transitively imports mouseinfo → Xlib at module load time, which
    raises X11/Xauth errors (NOT ImportError) on headless machines. Catch every
    failure mode so the backend simply reports "unavailable" instead of crashing.
    One-shot: the first result is cached, avoiding repeated crash attempts.

    On Wayland with XWayland (rootless mode), ~/.Xauthority may not exist even
    though X11 auth is not required. We create an empty file so python-xlib
    doesn't crash during import.
    """
    global _pyautogui, _pil_image, _mss, _imports_done
    if _imports_done:
        return
    _imports_done = True

    # XWayland rootless mode doesn't use Xauthority but python-xlib crashes
    # if ~/.Xauthority is missing. Create an empty file so the import works.
    xauth = Path.home() / ".Xauthority"
    if not xauth.exists():
        try:
            xauth.touch(mode=0o600)
        except Exception:
            pass

    try:
        import pyautogui
        _pyautogui = pyautogui
        # Disable pyautogui failsafe (moving mouse to corner throws)
        pyautogui.FAILSAFE = False
        # Set a reasonable pause between actions
        pyautogui.PAUSE = 0.05
    except Exception:
        _pyautogui = None
    try:
        from PIL import Image
        _pil_image = Image
    except Exception:
        _pil_image = None
    try:
        import mss
        _mss = mss
    except Exception:
        _mss = None


def is_available() -> bool:
    """Check if pyautogui is available and a display exists."""
    _ensure_imports()
    if _pyautogui is None:
        return False
    try:
        # Quick probe: if we can get screen size, we're good
        _pyautogui.size()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _is_all_black(path: Optional[str]) -> bool:
    """Check if a PNG screenshot is entirely or nearly all-black.

    Samples a grid of pixels across the image. If the average brightness of
    the sample is below 5 (out of 255), the image is considered all-black,
    which typically means the display was off or the capture was blocked.
    """
    if not path or not os.path.exists(path):
        return False
    if _pil_image is None:
        return False
    try:
        img = _pil_image.open(path)
        w, h = img.size
        if w == 0 or h == 0:
            return True
        pixels = []
        step_x = max(1, w // 10)
        step_y = max(1, h // 10)
        for y in range(0, h, step_y):
            for x in range(0, w, step_x):
                px = img.getpixel((x, y))
                if isinstance(px, int):
                    pixels.append(px)
                else:
                    pixels.append(sum(px[:3]) // 3)
        if not pixels:
            return True
        avg = sum(pixels) // len(pixels)
        return avg < 5
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def capture_screen(
    region: Optional[Dict[str, Any]] = None,
    monitor_index: int = 0,
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture screen using pyautogui (PIL screenshot) or mss as primary.
    
    Returns structured dict with ok, path, geometry, backend info.
    Validates the capture is not all-black (which indicates a locked/display-off
    state or a compositor permission issue).
    """
    _ensure_imports()
    out_dir = out_dir or os.path.expanduser("~/.harness/screenshots")
    os.makedirs(out_dir, exist_ok=True)
    
    # Try mss first (faster, supports multi-monitor)
    if _mss is not None:
        try:
            result = _capture_mss(region, monitor_index, out_dir)
            if result.get("ok") and _is_all_black(result.get("path")):
                result["warning"] = "capture appears all-black (screen may be locked or display off)"
            return result
        except Exception:
            pass
    
    # Try pyautogui screenshot
    if _pyautogui is not None and _pil_image is not None:
        try:
            result = _capture_pyautogui(region, out_dir)
            if result.get("ok") and _is_all_black(result.get("path")):
                result["warning"] = "capture appears all-black (screen may be locked or display off)"
            return result
        except Exception:
            pass
    
    return {"ok": False, "error": "no capture backend available", 
            "hints": ["install pyautogui + Pillow: pip install pyautogui Pillow",
                      "or install mss: pip install mss"]}


def _capture_mss(
    region: Optional[Dict[str, Any]],
    monitor_index: int,
    out_dir: str,
) -> Dict[str, Any]:
    """Capture using mss (fastest).

    When monitor_index=0 (the entire virtual screen), mss may return a black
    framebuffer on Wayland or multi-monitor setups. In that case we retry with
    monitor_index=1 (the first physical monitor) which typically works.
    """
    with _mss.mss() as sct:
        monitors = sct.monitors
        if not monitors:
            return {"ok": False, "error": "no monitors detected"}

        idx = monitor_index
        if idx == 0 and len(monitors) > 1:
            idx = 1

        mon = monitors[min(idx, len(monitors) - 1)]

        if region:
            grab_region = {
                "left": region.get("x", mon["left"]),
                "top": region.get("y", mon["top"]),
                "width": region.get("width", mon["width"]),
                "height": region.get("height", mon["height"]),
            }
        else:
            grab_region = mon

        shot = sct.grab(grab_region)
        width, height = shot.width, shot.height

        png_path = os.path.join(out_dir, f"screen-{int(time.time()*1000)}.png")
        _pil_image.frombytes("RGB", (width, height), shot.rgb).save(png_path)

        return {
            "ok": True,
            "path": png_path,
            "image_path": png_path,
            "width": width,
            "height": height,
            "geometry": {"x": grab_region.get("left", 0), "y": grab_region.get("top", 0),
                         "width": width, "height": height},
            "monitors": monitors,
            "monitor_index": idx,
            "backend": "mss",
        }


def _capture_pyautogui(
    region: Optional[Dict[str, Any]],
    out_dir: str,
) -> Dict[str, Any]:
    """Capture using pyautogui screenshot."""
    if region:
        bbox = (region.get("x", 0), region.get("y", 0),
                region.get("x", 0) + region.get("width", 100),
                region.get("y", 0) + region.get("height", 100))
        screenshot = _pyautogui.screenshot(region=bbox)
    else:
        screenshot = _pyautogui.screenshot()
        bbox = None
    
    png_path = os.path.join(out_dir, f"screen-{int(time.time()*1000)}.png")
    screenshot.save(png_path)
    
    w, h = screenshot.size
    geom = {"x": bbox[0] if bbox else 0, "y": bbox[1] if bbox else 0,
            "width": w, "height": h}
    
    return {
        "ok": True,
        "path": png_path,
        "image_path": png_path,
        "width": w,
        "height": h,
        "geometry": geom,
        "monitors": [{"width": w, "height": h}],
        "monitor_index": 0,
        "backend": "pyautogui",
    }


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def execute_input(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single input action via pyautogui.
    
    Supports: move, click, double, right, middle, drag, scroll,
              type, key, combo/hotkey, keydown, keyup, sleep.
    """
    _ensure_imports()
    if _pyautogui is None:
        return {"ok": False, "error": "pyautogui not available",
                "hints": ["install pyautogui: pip install pyautogui"]}
    
    name = (action.get("action") or "").lower()
    
    try:
        if name == "move":
            x, y = int(action.get("x", 0)), int(action.get("y", 0))
            _pyautogui.moveTo(x, y, duration=action.get("duration", 0))
            return {"ok": True, "action": "move", "x": x, "y": y}
        
        if name == "click":
            x = action.get("x")
            y = action.get("y")
            btn = action.get("button", "left")
            if x is not None and y is not None:
                _pyautogui.click(int(x), int(y), button=btn)
            else:
                _pyautogui.click(button=btn)
            return {"ok": True, "action": "click", "button": btn}
        
        if name == "double":
            x = action.get("x")
            y = action.get("y")
            btn = action.get("button", "left")
            if x is not None and y is not None:
                _pyautogui.doubleClick(int(x), int(y), button=btn)
            else:
                _pyautogui.doubleClick(button=btn)
            return {"ok": True, "action": "double", "button": btn}
        
        if name in ("right", "right_click"):
            x = action.get("x")
            y = action.get("y")
            if x is not None and y is not None:
                _pyautogui.rightClick(int(x), int(y))
            else:
                _pyautogui.rightClick()
            return {"ok": True, "action": "right_click"}
        
        if name in ("middle", "middle_click"):
            x = action.get("x")
            y = action.get("y")
            if x is not None and y is not None:
                _pyautogui.middleClick(int(x), int(y))
            else:
                _pyautogui.middleClick()
            return {"ok": True, "action": "middle_click"}
        
        if name == "drag":
            x1, y1 = int(action.get("x1", 0)), int(action.get("y1", 0))
            x2, y2 = int(action.get("x2", 0)), int(action.get("y2", 0))
            btn = action.get("button", "left")
            duration = action.get("duration", 0.5)
            _pyautogui.moveTo(x1, y1)
            _pyautogui.drag(x2 - x1, y2 - y1, duration=duration, button=btn)
            return {"ok": True, "action": "drag"}
        
        if name == "scroll":
            x = action.get("x")
            y = action.get("y")
            clicks = action.get("clicks", action.get("amount", 3))
            if x is not None and y is not None:
                _pyautogui.scroll(int(clicks), int(x), int(y))
            else:
                _pyautogui.scroll(int(clicks))
            return {"ok": True, "action": "scroll", "clicks": clicks}
        
        if name == "hscroll":
            clicks = action.get("clicks", action.get("amount", 3))
            _pyautogui.hscroll(int(clicks))
            return {"ok": True, "action": "hscroll", "clicks": clicks}
        
        if name == "type":
            text = action.get("text", "")
            interval = action.get("interval", 0.02)
            _pyautogui.typewrite(text, interval=interval) if text.isascii() else _pyautogui.write(text)
            return {"ok": True, "action": "type", "length": len(text)}
        
        if name in ("key", "press"):
            key = action.get("key", "")
            _pyautogui.press(key)
            return {"ok": True, "action": "press", "key": key}
        
        if name in ("keydown",):
            key = action.get("key", "")
            _pyautogui.keyDown(key)
            return {"ok": True, "action": "keydown", "key": key}
        
        if name in ("keyup",):
            key = action.get("key", "")
            _pyautogui.keyUp(key)
            return {"ok": True, "action": "keyup", "key": key}
        
        if name in ("combo", "hotkey", "chord", "keys"):
            keys = action.get("keys", [])
            if keys:
                _pyautogui.hotkey(*keys)
            return {"ok": True, "action": "hotkey", "keys": keys}
        
        if name == "sleep":
            ms = action.get("ms", 100)
            time.sleep(ms / 1000.0)
            return {"ok": True, "action": "sleep", "ms": ms}
        
        return {"ok": False, "error": f"unknown action: {name}"}
    
    except Exception as exc:
        return {"ok": False, "error": f"{name} failed: {exc}"}


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def clipboard_read() -> Dict[str, Any]:
    """Read system clipboard via pyperclip or xclip/wl-paste."""
    # Try pyperclip first
    try:
        import pyperclip
        return {"ok": True, "text": pyperclip.paste()}
    except ImportError:
        pass
    
    # Try CLI tools
    import subprocess
    try:
        result = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return {"ok": True, "text": result.stdout}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    try:
        result = subprocess.run(["wl-paste"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return {"ok": True, "text": result.stdout}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return {"ok": False, "error": "no clipboard backend available",
            "hints": ["install xclip (X11) or wl-clipboard (Wayland)"]}


def clipboard_write(text: str) -> Dict[str, Any]:
    """Write to system clipboard."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"ok": True}
    except ImportError:
        pass
    
    import subprocess
    try:
        result = subprocess.run(["xclip", "-selection", "clipboard"],
                                input=text.encode(), timeout=5)
        if result.returncode == 0:
            return {"ok": True}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    try:
        result = subprocess.run(["wl-copy"], input=text.encode(), timeout=5)
        if result.returncode == 0:
            return {"ok": True}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return {"ok": False, "error": "no clipboard backend available"}


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------

def list_windows() -> Dict[str, Any]:
    """List visible windows."""
    import subprocess
    try:
        # Try wmctrl
        result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            windows = []
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        windows.append({
                            "id": parts[0],
                            "desktop": parts[1],
                            "pid": parts[2],
                            "title": parts[3],
                        })
            return {"ok": True, "windows": windows}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return {"ok": False, "error": "no window manager backend",
            "hints": ["install wmctrl"]}


def focus_window(window_id: str) -> Dict[str, Any]:
    """Focus a window by ID."""
    import subprocess
    try:
        result = subprocess.run(["wmctrl", "-i", "-a", window_id],
                                capture_output=True, timeout=5)
        return {"ok": result.returncode == 0}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "wmctrl not available"}
