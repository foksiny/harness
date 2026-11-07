"""X11 CLIPBOARD via ctypes libX11 — zero external tools.

Pure key-event typing (XTEST, ydotool ``type``, pyautogui ``typewrite``) can
only synthesize characters bound in the current keymap. Arbitrary Unicode
(CJK, emoji, accented text) needs a different mechanism: put the text in the
CLIPBOARD selection and send the paste shortcut. The usual providers
(``wl-copy`` / ``xclip`` / ``xsel``) are separate binaries, which are missing
on minimal installs. This module provides the same selection ownership
directly through libX11 — the only native dependency the computer backends
already need for XTEST input — so Unicode paste works on X11 and XWayland
out of the box, with no extra packages.

:class:`write` spawns a daemon thread that owns the CLIPBOARD selection and
answers SelectionRequest events until another app takes ownership (or a
120 s timeout). ``wl-copy``/``xclip`` do the same in a subprocess; a thread
is equivalent here because the agent process stays alive while it pastes.

:class:`read` requests the current CLIPBOARD contents through the same
channel (used to preserve the user's clipboard across a paste). It never
raises: any failure is ``None``/``False`` so callers degrade to the CLI
drivers or a clear error.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
from typing import Optional

_HOLDER_SECONDS = 120.0
_INITS = False
_INITS_LOCK = threading.Lock()


def _lib():
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    lib = ctypes.CDLL(name)
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XSetSelectionOwner.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    lib.XGetSelectionOwner.restype = ctypes.c_ulong
    lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XPending.argtypes = [ctypes.c_void_p]
    lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.XChangeProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.XSendEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
        ctypes.c_void_p]
    lib.XConvertSelection.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_ulong]
    lib.XGetWindowProperty.restype = ctypes.c_int
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
        ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    lib.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XInitThreads = getattr(lib, "XInitThreads", None)
    if lib.XInitThreads is not None:
        lib.XInitThreads.argtypes = []
        lib.XInitThreads.restype = ctypes.c_int
    return lib


def _ensure_threads(lib) -> None:
    global _INITS
    if _INITS or getattr(lib, "XInitThreads", None) is None:
        return
    with _INITS_LOCK:
        if not _INITS:
            try:
                lib.XInitThreads()
            except Exception:
                pass
            _INITS = True


def _display():
    import os as _os
    display = _os.environ.get("DISPLAY", "").strip()
    if not display:
        return None
    lib = _lib()
    _ensure_threads(lib)
    dpy = lib.XOpenDisplay(display.encode("utf-8"))
    return (lib, dpy) if dpy else None


def is_available() -> bool:
    """Can we reach an X display through libX11?"""
    try:
        got = _display()
        if not got:
            return False
        lib, dpy = got
        lib.XCloseDisplay(dpy)
        return True
    except Exception:
        return False


class _XEvent(ctypes.Structure):
    _fields_ = [("buf", ctypes.c_ubyte * 192)]


def _atom(lib, dpy, name: str) -> int:
    return int(lib.XInternAtom(dpy, name.encode("utf-8"), 0))


def _serve_selection(lib, dpy, root, text: str, ready: threading.Event,
                     result: dict, duration: float = _HOLDER_SECONDS) -> None:
    """Become CLIPBOARD owner and answer requests on this thread."""
    try:
        clipboard = _atom(lib, dpy, "CLIPBOARD")
        utf8 = _atom(lib, dpy, "UTF8_STRING")
        string = _atom(lib, dpy, "STRING")
        targets = _atom(lib, dpy, "TARGETS")
        lib.XSetSelectionOwner(dpy, clipboard, root, 0)
        lib.XFlush(dpy)
        result["ok"] = int(lib.XGetSelectionOwner(dpy, clipboard) or 0) == int(root)
        ready.set()
        if not result["ok"]:
            lib.XCloseDisplay(dpy)
            return

        utf8_bytes = text.encode("utf-8")
        try:
            latin_bytes = text.encode("latin-1")
            latin_ok = True
        except UnicodeEncodeError:
            latin_bytes = utf8_bytes
            latin_ok = False
        utf8_buf = (ctypes.c_ubyte * len(utf8_bytes)).from_buffer_copy(utf8_bytes)
        latin_buf = (ctypes.c_ubyte * len(latin_bytes)).from_buffer_copy(latin_bytes)
        targets_buf = (ctypes.c_ulong * 3)(targets, utf8, string)

        evt = _XEvent()
        deadline = time.time() + duration
        while time.time() < deadline:
            while lib.XPending(dpy):
                lib.XNextEvent(dpy, ctypes.byref(evt))
                buf = bytes(evt.buf[:192])
                etype = int.from_bytes(buf[0:4], "little")
                if etype == 29:  # SelectionClear: someone else owns it now.
                    return
                if etype != 30:  # SelectionRequest
                    continue

                def u64(off: int) -> int:
                    return int.from_bytes(buf[off:off + 8], "little")

                requestor = u64(40)
                selection = u64(48)
                target = u64(56)
                prop = u64(64)
                req_time = u64(72)
                if target == targets:
                    lib.XChangeProperty(
                        dpy, requestor, prop, targets, 32, 0,
                        targets_buf, 3)
                elif target == utf8:
                    lib.XChangeProperty(
                        dpy, requestor, prop, utf8, 8, 0,
                        utf8_buf, len(utf8_bytes))
                elif target == string:
                    lib.XChangeProperty(
                        dpy, requestor, prop, string, 8, 0,
                        latin_buf, len(latin_bytes))
                else:
                    prop = 0
                # SelectionNotify reply
                notify = _XEvent()
                out = bytearray(192)
                out[0:4] = int(31).to_bytes(4, "little")
                out[24:32] = int(dpy).to_bytes(8, "little")
                out[32:40] = int(requestor).to_bytes(8, "little")
                out[40:48] = int(selection).to_bytes(8, "little")
                out[48:56] = int(target).to_bytes(8, "little")
                out[56:64] = int(prop).to_bytes(8, "little")
                out[64:72] = int(req_time).to_bytes(8, "little")
                ctypes.memmove(ctypes.byref(notify), bytes(out), 192)
                lib.XSendEvent(dpy, requestor, 0, 0, ctypes.byref(notify))
                lib.XFlush(dpy)
            time.sleep(0.02)
    except Exception as exc:  # never raise from a clipboard backend
        result.setdefault("error", str(exc))
        ready.set()
    finally:
        try:
            lib.XCloseDisplay(dpy)
        except Exception:
            pass
        ready.set()


def write(text: str, duration: float = _HOLDER_SECONDS) -> bool:
    """Put ``text`` on the X11 CLIPBOARD. Returns True once we own it."""
    try:
        got = _display()
        if not got:
            return False
        # The serving thread owns this Display after handoff (connections
        # are not safely shared), so it just becomes the selection owner.
        lib, dpy = got
        root = int(lib.XDefaultRootWindow(dpy) or 0)
        if not root:
            lib.XCloseDisplay(dpy)
            return False
        ready = threading.Event()
        result: dict = {}
        threading.Thread(
            target=_serve_selection,
            args=(lib, dpy, root, str(text), ready, result, duration),
            daemon=True,
        ).start()
        if not ready.wait(timeout=4.0):
            return False
        return bool(result.get("ok"))
    except Exception:
        return False


def read(timeout: float = 3.0) -> Optional[str]:
    """Request the current CLIPBOARD contents. None when unavailable/empty."""
    try:
        got = _display()
        if not got:
            return None
        lib, dpy = got
        try:
            root = int(lib.XDefaultRootWindow(dpy) or 0)
            clipboard = _atom(lib, dpy, "CLIPBOARD")
            utf8 = _atom(lib, dpy, "UTF8_STRING")
            if not int(lib.XGetSelectionOwner(dpy, clipboard) or 0):
                return None
            lib.XConvertSelection(dpy, clipboard, utf8, clipboard, root, 0)
            lib.XFlush(dpy)
            deadline = time.time() + max(0.2, timeout)
            while time.time() < deadline:
                time.sleep(0.05)
                actual_type = ctypes.c_ulong()
                actual_format = ctypes.c_int()
                nitems = ctypes.c_ulong()
                bytes_after = ctypes.c_ulong()
                prop_data = ctypes.POINTER(ctypes.c_ubyte)()
                ret = lib.XGetWindowProperty(
                    dpy, root, clipboard, 0, 1_000_000, 0, 0,
                    ctypes.byref(actual_type), ctypes.byref(actual_format),
                    ctypes.byref(nitems), ctypes.byref(bytes_after),
                    ctypes.byref(prop_data))
                if ret == 0 and prop_data and nitems.value > 0:
                    raw = bytes(
                        bytearray(prop_data[i] for i in range(nitems.value)))
                    lib.XDeleteProperty(dpy, root, clipboard)
                    lib.XFlush(dpy)
                    return raw.decode("utf-8", errors="replace")
                if prop_data:
                    lib.XFree(prop_data)
            return None
        finally:
            lib.XCloseDisplay(dpy)
    except Exception:
        return None
