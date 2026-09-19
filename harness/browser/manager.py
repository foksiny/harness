"""
BrowserManager — the shared, hermetic seam between the browser_* tools and
the real Chrome controller.

Every agent (main, subagent, worker) owns one BrowserManager, which owns at
most one BrowserController. Because the factory hands the *same* controller
to every tool, state (launched browser, active tab) persists across tool
calls: ``browser_launch`` once, then ``browser_navigate`` / ``browser_click``
/ … all drive the same session. A call without a live controller returns
``None`` so the tool can report "browser not launched" instead of crashing.

Headless policy (``resolve_headless``):
  * ``headless=True``  — always headless (background, no window).
  * ``headless=False`` — visible Chrome window (the default). Needs a display;
    on a headless Linux box an Xvfb server is started automatically when the
    ``Xvfb`` binary is available, otherwise we fall back to headless and say so.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple


def display_available() -> bool:
    """True when a graphical display is (probably) present."""
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _free_display(start: int = 99, tries: int = 10) -> Optional[str]:
    """Find an unused X display number by probing the X11 socket dir."""
    sock_dir = Path("/tmp/.X11-unix")
    for n in range(start, start + tries):
        if sock_dir.exists() and (sock_dir / f"X{n}").exists():
            continue
        # Also probe TCP 6000+n in case Xvfb listens there.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", 6000 + n))
                continue  # something answers — taken
            except OSError:
                pass
        return f":{n}"
    return None


def ensure_xvfb(width: int = 1280, height: int = 800) -> Tuple[Optional[str], Optional[subprocess.Popen]]:
    """Start an Xvfb server if the binary exists. Returns (DISPLAY, proc)."""
    if display_available():
        return os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), None
    xvfb = shutil.which("Xvfb") or shutil.which("xvfb-run")
    if not xvfb or os.path.basename(xvfb) == "xvfb-run":
        # xvfb-run is a wrapper, not the server — only Xvfb works here.
        if not shutil.which("Xvfb"):
            return None, None
        xvfb = shutil.which("Xvfb")
    disp = _free_display()
    if not disp:
        return None, None
    try:
        proc = subprocess.Popen(
            [xvfb, disp, "-screen", "0", f"{width}x{height}x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return None, None
    # Wait for the socket to appear.
    sock = Path(f"/tmp/.X11-unix/X{disp[1:]}")
    for _ in range(40):
        if sock.exists():
            return disp, proc
        if proc.poll() is not None:
            return None, None
        time.sleep(0.25)
    try:
        proc.terminate()
    except Exception:
        pass
    return None, None


def resolve_headless(requested: Optional[bool]) -> Tuple[bool, str]:
    """Resolve the effective headless flag.

    Returns (headless, note) where note explains any automatic decision
    ("" when the request was honored verbatim).
    """
    if requested is True:
        return True, ""
    if display_available():
        return False, ""
    if requested is False:
        return True, "no display detected and Xvfb is unavailable — fell back to headless mode"
    return True, "no display detected — running headless"


def screenshots_dir() -> str:
    """Directory for auto-saved browser screenshots."""
    d = Path.home() / ".harness" / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


class BrowserManager:
    """Holds one shared Chrome session for all browser_* tools of an agent."""

    def __init__(self):
        self._controller = None
        self._xvfb_proc: Optional[subprocess.Popen] = None
        self._xvfb_display: Optional[str] = None
        self.last_note: str = ""

    # ── Tool seam ───────────────────────────────────────────────────────

    def controller_factory(self, port: Optional[int] = None, headless: Optional[bool] = None):
        """Factory injected into the browser tools.

        * Called with ``port`` (from ``browser_launch``): create (or reuse)
          the shared controller and return it (unlaunched).
        * Called without args (every other tool): return the live controller,
          or None when no browser session is attached.
        """
        from harness.browser.controller import BrowserController, DEFAULT_CDP_PORT
        if port is None:
            c = self._controller
            if c is not None and c.is_connected():
                return c
            return None
        port = int(port or DEFAULT_CDP_PORT)
        if self._controller is not None and self._controller.is_connected() \
                and getattr(self._controller, "port", None) == port:
            return self._controller
        # Stale or wrong-port controller: retire it before creating a new one.
        if self._controller is not None:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None
        resolved, note = resolve_headless(headless)
        env_extra = None
        if not resolved and not display_available():
            # Visible Chrome requested on a headless box: try Xvfb so the
            # launch still succeeds instead of dying on a missing display.
            disp, proc = ensure_xvfb()
            if disp and proc is not None:
                self._xvfb_proc = proc
                self._xvfb_display = disp
                env_extra = {"DISPLAY": disp}
                note = f"no display detected — started Xvfb on {disp} for a visible browser"
            else:
                resolved, note = True, "no display detected and Xvfb is unavailable — fell back to headless mode"
        self.last_note = note
        self._controller = BrowserController(port=port, headless=resolved, env_extra=env_extra)
        # Surfaced by browser_launch so the agent sees e.g. the Xvfb fallback.
        self._controller.manager_note = note
        return self._controller

    def launch_env_extra(self) -> Optional[dict]:
        if self._xvfb_display:
            return {"DISPLAY": self._xvfb_display}
        return None

    @property
    def xvfb_display(self) -> Optional[str]:
        return self._xvfb_display

    def is_live(self) -> bool:
        return self._controller is not None and self._controller.is_connected()

    def close(self):
        if self._controller is not None:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None
        if self._xvfb_proc is not None:
            try:
                self._xvfb_proc.terminate()
                self._xvfb_proc.wait(timeout=5)
            except Exception:
                try:
                    self._xvfb_proc.kill()
                except Exception:
                    pass
            self._xvfb_proc = None
            self._xvfb_display = None
