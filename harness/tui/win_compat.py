"""
Windows terminal compatibility helpers.

Enables ANSI escape code support (VT processing) and UTF-8 output on
Windows cmd.exe / PowerShell / Windows Terminal so that colors, braille
spinner, and kaomoji render correctly.
"""
import os
import sys


def enable_windows_vt_processing():
    """Enable VT100 / ANSI escape code processing on Windows terminals.

    On Windows 10 1511+ this makes ``\\033[...`` sequences work in
    cmd.exe, PowerShell, and Windows Terminal.  On older Windows or
    non-terminal environments this is a silent no-op.
    """
    if os.name != "nt":
        return

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

        for handle_id in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(handle_id)
            if handle is None or handle == -1:
                continue
            mode = ctypes.wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, new_mode)

        # Also enable VT input for ANSI key sequences
        stdin_handle = kernel32.GetStdHandle(-10)
        if stdin_handle and stdin_handle != -1:
            mode = ctypes.wintypes.DWORD()
            if kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT
                kernel32.SetConsoleMode(stdin_handle, new_mode)
    except Exception:
        pass


def ensure_utf8_stdout():
    """Reconfigure stdout/stderr to UTF-8 on Windows if possible.

    On Python 3.7+ with ``PYTHONIOENCODING=utf-8`` or when the console
    supports UTF-8, this ensures braille, box-drawing, and kaomoji
    characters display instead of ``?`` or mojibake.
    """
    if os.name != "nt":
        return

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        # If already UTF-8, nothing to do
        enc = getattr(stream, "encoding", "") or ""
        if enc.lower().replace("-", "") in ("utf8", "utf_8"):
            continue
        # Try to reconfigure (Python 3.7+)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Python <3.7 or already configured — try wrapping
            try:
                import io
                wrapper = io.TextIOWrapper(
                    stream.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=stream.line_buffering,
                )
                setattr(sys, stream_name, wrapper)
            except Exception:
                pass
