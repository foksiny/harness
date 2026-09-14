"""
Zero-dependency XTEST input backend for Harness computer use.

Uses ctypes to bind libX11 + libXtst and synthesize mouse/keyboard/typing
events through the X11 XTEST extension. This works without installing xdotool
or any other binary and is the primary input path on X11 / XWayland.

If libX11 or libXtst is unavailable (e.g. a Wayland-only compositor without
XWayland), the backend degrades to CLI hints (xdotool / ydotool / wmctrl /
xclip) reported by the controller, and `is_available()` returns False so
callers can switch to a CLI driver instead.
"""
import ctypes
import ctypes.util
from typing import Dict, List, Optional, Tuple

# Classic X11 keycodes for the common US-layout keysyms (queried at runtime
# when possible; fallback table below is the standard evdev/X11 code set).
FALLBACK_KEYCODES: Dict[str, int] = {
    # QWERTY letters (a-z -> 38..63), digits (1-0 -> 10..19),
    "a": 38, "b": 56, "c": 54, "d": 40, "e": 26,
    "f": 41, "g": 42, "h": 43, "i": 31, "j": 44,
    "k": 45, "l": 46, "m": 58, "n": 57, "o": 32,
    "p": 33, "q": 24, "r": 27, "s": 39, "t": 28,
    "u": 30, "v": 55, "w": 25, "x": 53, "y": 29, "z": 52,
    "1": 10, "2": 11, "3": 12, "4": 13, "5": 14,
    "6": 15, "7": 16, "8": 17, "9": 18, "0": 19,
    " ": 65, "-": 20, "=": 21, "[": 34, "]": 35,
    "\\": 51, ";": 47, "'": 48, "`": 49, ",": 59,
    ".": 60, "/": 61, "enter": 36, "return": 36, "\n": 36,
    "backspace": 22, "tab": 23, "shift": 50, "ctrl": 37,
    "alt": 64, "super": 133, "meta": 64,
    "left": 113, "right": 114, "up": 111, "down": 116,
    "home": 110, "end": 115, "pageup": 112, "pagedown": 117,
    "escape": 9, "esc": 9, "delet": 119, "delete": 119,
    "insert": 118, "caps": 66,
}

# Buttons: (press_code, release_code) via XTestFakeButtonEvent.
MOUSE_BUTTONS = {
    "left": 1,
    "middle": 2,
    "right": 3,
    "wheel_up": 4,
    "wheel_down": 5,
    "wheel_left": 6,
    "wheel_right": 7,
}

# Symbols that need Shift in a plain US layout (unshifted + shifted).
_SHIFT_PAIRS: Dict[str, str] = {
    "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0", "_": "-",
    "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'",
    "<": ",", ">": ".", "?": "/",
}


class XTestSession:
    """Live binding to libX11 + libXtst for XTEST input."""

    def __init__(self, display: str) -> None:
        self.display = display
        libx11_name = ctypes.util.find_library("X11") or "libX11.so.6"
        libxtst_name = ctypes.util.find_library("Xtst") or "libXtst.so.6"
        self.libx11 = ctypes.CDLL(libx11_name)
        self.libxtst = ctypes.CDLL(libxtst_name)

        self.libx11.XOpenDisplay.restype = ctypes.c_void_p
        self.libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.dpy = self.libx11.XOpenDisplay(self.display.encode("utf-8"))
        if not self.dpy:
            raise RuntimeError(f"cannot open X display '{display}'")

        self.libx11.XDisplayString.restype = ctypes.c_char_p
        self.libx11.XDisplayString.argtypes = [ctypes.c_void_p]
        self.libx11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.libx11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.libx11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.libx11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.libx11.XStringToKeysym.restype = ctypes.c_ulong
        self.libx11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.libx11.XFlush.argtypes = [ctypes.c_void_p]

        self.libxtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self.libxtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.libxtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self._shift_down = False
        self._ctrl_down = False
        self._alt_down = False
        self._super_down = False

    def _keycode(self, token: str) -> int:
        token = token.lower()
        if token in FALLBACK_KEYCODES:
            return FALLBACK_KEYCODES[token]
        sym = self.libx11.XStringToKeysym(token.encode("utf-8"))
        if not sym:
            raise ValueError(f"unknown keysym '{token}'")
        kc = self.libx11.XKeysymToKeycode(self.dpy, sym)
        if not kc:
            raise ValueError(f"no keycode for keysym '{token}'")
        return int(kc)

    def _mod_state(self) -> None:
        # No persistent modifier concept in XTEST; we track state ourselves.
        pass

    def close(self) -> None:
        if self.dpy:
            try:
                self.libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]
                self.libx11.XCloseDisplay(self.dpy)
            except Exception:
                pass
            self.dpy = None

    def __enter__(self) -> "XTestSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- mouse ----
    def move(self, x: int, y: int) -> None:
        self.libxtst.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
        self.libx11.XFlush(self.dpy)

    def button(self, name: str, down: bool = True) -> None:
        btn = MOUSE_BUTTONS.get(name.lower())
        if btn is None:
            raise ValueError(f"unknown mouse button '{name}'")
        self.libxtst.XTestFakeButtonEvent(self.dpy, btn, 1 if down else 0, 0)
        self.libx11.XFlush(self.dpy)

    def click(self, name: str = "left") -> None:
        self.button(name, True)
        self.button(name, False)

    def double_click(self, name: str = "left") -> None:
        self.click(name)
        self.click(name)

    def scroll(self, x_steps: int = 0, y_steps: int = 0) -> None:
        for _ in range(abs(x_steps or 0)):
            self.button("wheel_left" if x_steps < 0 else "wheel_right")
        for _ in range(abs(y_steps or 0)):
            self.button("wheel_down" if y_steps < 0 else "wheel_up")

    # ---- keyboard ----
    def _set_mod(self, name: str, down: bool) -> None:
        mapping = {
            "shift": "shift", "ctrl": "ctrl", "control": "ctrl",
            "alt": "alt", "super": "super", "meta": "alt",
        }
        token = mapping.get(name.lower(), name.lower())
        self.libxtst.XTestFakeKeyEvent(self.dpy, self._keycode(token), 1 if down else 0, 0)
        self.libx11.XFlush(self.dpy)

    def key(self, name: str, down: bool = True) -> None:
        """Press/release a single key or modifier token."""
        if name.lower() in ("shift", "ctrl", "control", "alt", "super", "meta"):
            self._set_mod(name, down)
            return
        self.libxtst.XTestFakeKeyEvent(self.dpy, self._keycode(name), 1 if down else 0, 0)
        self.libx11.XFlush(self.dpy)

    def combo(self, keys: List[str]) -> None:
        """Press a chord like ["ctrl", "c"] then release in reverse order."""
        held = []
        for k in keys:
            self.key(k, True)
            held.append(k)
        for k in reversed(held):
            self.key(k, False)

    def type_text(self, text: str) -> None:
        """Type UTF-safe text, managing Shift for symbols/uppercase."""
        for ch in text:
            if ch == "\n":
                self.key("enter")
                continue
            lower = ch.lower()
            unshifted = _SHIFT_PAIRS.get(ch, None)
            base = unshifted if unshifted is not None else lower
            needs_shift = unshifted is not None or ch != lower
            if needs_shift:
                self.key("shift", True)
            self.libxtst.XTestFakeKeyEvent(self.dpy, self._keycode(base), 1, 0)
            self.libxtst.XTestFakeKeyEvent(self.dpy, self._keycode(base), 0, 0)
            self.libx11.XFlush(self.dpy)
            if needs_shift:
                self.key("shift", False)


def is_available(display: Optional[str] = None) -> bool:
    """Cheap availability probe: can we open a display and bind both libs?"""
    import os
    display = display or os.environ.get("DISPLAY", "").strip()
    if not display:
        return False
    try:
        with XTestSession(display):
            return True
    except Exception:
        return False
