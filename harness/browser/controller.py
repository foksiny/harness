"""
Browser controller via Chrome DevTools Protocol (CDP).

Chrome-only. Drives Google Chrome (or Chromium) over a websocket using the
``websockets`` package — no Playwright, Selenium, or other heavy dependency.

Usage::

    ctrl = BrowserController()
    ctrl.launch()                    # visible window by default; headless=...
                                     # set on the constructor for background use
    ctrl.navigate("https://example.com")
    ctrl.click("#btn")
    ctrl.type_text("input#q", "hello")
    img_b64 = ctrl.screenshot()
    ctrl.close()
"""
import asyncio
import base64
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Any, List, Dict
from urllib.parse import urlparse

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from harness.browser.overlay import (
    js_show_overlay, js_hide_overlay,
    js_mouse_move, js_mouse_click, js_flash_element, js_element_center, js_mouse_hide,
)
from harness.browser.stealth import STEALTH_JS, STEALTH_LAUNCH_FLAGS

DEFAULT_CDP_PORT = 9222

CHROME_PATHS = [
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CHROME_BIN_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def find_chrome() -> Optional[str]:
    """Return the path to a Chrome/Chromium executable, or None."""
    for p in CHROME_PATHS:
        if os.path.isfile(p):
            return p
    from shutil import which
    for name in CHROME_BIN_NAMES:
        found = which(name)
        if found:
            return found
    return None


class BrowserController:
    """CDP-based Chrome controller with a visual status overlay.

    A single persistent asyncio event loop is owned by each controller instance
    (all CDP traffic for one browser session must share a loop — creating a new
    loop per command breaks the websocket binding in modern ``websockets``).
    A controller is therefore bound to the thread that first uses it; every
    agent (main, subagent, worker) gets its own controller via BrowserManager.
    """

    def __init__(self, port: int = DEFAULT_CDP_PORT, headless: bool = False,
                 env_extra: Optional[Dict[str, str]] = None):
        if not HAS_WEBSOCKETS:
            raise ImportError("pip install websockets  # required for browser control")
        self.port = port
        self.headless = headless
        self._env_extra = dict(env_extra or {})
        self._proc: Optional[subprocess.Popen] = None
        self._ws = None
        self._cmd_id = 0
        self._target_id: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_owner: Optional[int] = None
        self._profile_dir: Optional[str] = None

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def launch(
        self,
        browser_path: Optional[str] = None,
        url: str = "about:blank",
        env_extra: Optional[Dict[str, str]] = None,
    ) -> str:
        exe = browser_path or find_chrome()
        if not exe:
            raise RuntimeError(
                "No Chrome installation found. Install Google Chrome (or Chromium) "
                "or pass browser_path to the exact chrome executable."
            )

        # If CDP is already available on this port (e.g. a previous harness
        # session or the user's own Chrome with --remote-debugging-port),
        # attach to it instead of launching a new instance.
        if self._check_cdp():
            if self.connect():
                return f"Connected to existing Chrome on port {self.port} ({Path(exe).name})"
            # CDP responded but connect failed — the port is occupied by
            # something that isn't a usable browser. Don't try to launch a new
            # one on the same port.
            raise RuntimeError(
                f"Port {self.port} is in use but CDP connect failed. "
                f"Close the process using port {self.port} or use a different port."
            )

        # Fresh throwaway profile per launch: sharing one profile dir between
        # concurrent Chrome instances breaks on the singleton profile lock.
        self._profile_dir = tempfile.mkdtemp(prefix="harness_chrome_")
        args = [exe, f"--remote-debugging-port={self.port}"]
        if self.headless:
            args.append("--headless=new")
        args += list(STEALTH_LAUNCH_FLAGS)
        args += [
            f"--user-data-dir={self._profile_dir}",
            "--window-size=1280,800",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync",
            "--disable-extensions", "--disable-default-apps",
            url,
        ]
        env = dict(os.environ)
        if self._env_extra:
            env.update(self._env_extra)
        if env_extra:
            env.update(env_extra)
        launch_kwargs: Dict[str, Any] = dict(
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        if os.name == "nt":
            launch_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            launch_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(args, **launch_kwargs)
        for _ in range(80):
            time.sleep(0.25)
            if self._check_cdp():
                if self.connect():
                    return f"Chrome launched ({Path(exe).name})"
        self.close()
        raise RuntimeError(f"Chrome launched but CDP did not become available on port {self.port}")

    def connect(self, port: Optional[int] = None) -> bool:
        import requests
        p = port or self.port
        try:
            info = requests.get(f"http://127.0.0.1:{p}/json/version", timeout=3).json()
            ws_url = info.get("webSocketDebuggerUrl", "")
            if not ws_url:
                return False
        except Exception:
            return False
        try:
            targets = requests.get(f"http://127.0.0.1:{p}/json", timeout=3).json()
            pages = [t for t in targets if t.get("type") == "page"]
            if not pages:
                return False
            self._target_id = pages[0]["id"]
            target_ws = pages[0].get("webSocketDebuggerUrl", ws_url)
        except Exception:
            target_ws = ws_url
        self._ws = self._run(websockets.connect(target_ws, max_size=50 * 1024 * 1024))
        self._connected = True
        self._run(self._send("Page.enable", {}))
        self._run(self._send("Runtime.enable", {}))
        self._apply_stealth()
        self._inject_overlay()
        self._show_overlay("ready")
        self._hide_overlay(2000)
        return True

    def is_connected(self) -> bool:
        """True when a live browser session is attached."""
        return bool(self._connected and self._ws is not None)

    def _check_cdp(self) -> bool:
        import requests
        try:
            return requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=2).status_code == 200
        except Exception:
            return False

    def close(self):
        if self._ws:
            try:
                self._run(self._ws.close())
            except Exception:
                pass
            self._ws = None
        self._connected = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        # Drop the owned event loop so a later launch starts clean.
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._loop_owner = None

    # ── CDP transport ───────────────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """One persistent loop per controller, created on first use.

        The websockets connection binds background tasks to the loop that
        created it, so every command for this session must run on the same
        loop (a fresh loop per call raises "Future attached to a different
        loop" on the second command).
        """
        owner = threading.get_ident()
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._loop_owner = owner
        elif self._loop_owner != owner:
            raise RuntimeError(
                "BrowserController used from a different thread than the one "
                "that owns its connection. Give each thread its own controller."
            )
        return self._loop

    async def _send(self, method: str, params: dict, timeout: float = 30) -> dict:
        if not self._ws:
            raise RuntimeError("Not connected to browser")
        self._cmd_id += 1
        msg_id = self._cmd_id
        await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=min(timeout, 5))
                data = json.loads(raw)
                if data.get("id") == msg_id:
                    if "error" in data:
                        raise RuntimeError(f"CDP error: {data['error']}")
                    return data.get("result", {})
                # Unrelated frames (page events like Page.loadEventFired)
                # carry no id — skip them and keep waiting for our reply.
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                raise RuntimeError("Browser disconnected")
        raise TimeoutError(f"CDP command {method} timed out")

    async def _eval(self, expression: str, timeout: float = 10) -> Any:
        result = await self._send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        remote = result.get("result", {})
        if remote.get("type") == "undefined":
            return None
        if "value" in remote:
            return remote["value"]
        if remote.get("subtype") == "error":
            raise RuntimeError(f"JS error: {remote.get('description', 'unknown')}")
        return remote.get("description", remote)

    def _run(self, coro):
        return self._get_loop().run_until_complete(coro)

    # ── Overlay ─────────────────────────────────────────────────────────────

    def _show_overlay(self, action: str, custom_msg: str = None):
        if not self._connected:
            return
        try:
            self._run(self._eval(js_show_overlay(action, custom_msg), timeout=3))
        except Exception:
            pass

    def _hide_overlay(self, delay_ms: int = 2000):
        if not self._connected:
            return
        try:
            self._run(self._eval(js_hide_overlay(delay_ms), timeout=3))
        except Exception:
            pass

    def _hide_overlay_now(self):
        """Hide the overlay banner and the agent cursor immediately (used
        right before screenshots so neither pollutes the captured image)."""
        if not self._connected:
            return
        try:
            self._run(self._eval(
                "(function(){var el=document.getElementById('__harness_overlay');"
                "if(el){el.className='__harness_hidden';}"
                "if(window.__harness_mouse_hide){window.__harness_mouse_hide();}"
                "return true;})()",
                timeout=3,
            ))
        except Exception:
            pass

    def _inject_overlay(self):
        if not self._connected:
            return
        from harness.browser.overlay import OVERLAY_JS
        try:
            self._run(self._eval(OVERLAY_JS, timeout=5))
        except Exception:
            pass

    def _apply_stealth(self):
        """Register the anti-bot + overlay document scripts for every NEW
        document in the attached target (navigations included) plus a language
        override. Registering the overlay here removes the post-navigation
        injection race: Page.navigate returns before the new document commits,
        so an immediate Runtime.evaluate could run on the dying old document.
        The overlay script boots itself as soon as <body> exists and only in
        the top frame. Best-effort — never raises.
        """
        if not self._connected:
            return
        try:
            self._run(self._send("Page.addScriptToEvaluateOnNewDocument", {
                "source": STEALTH_JS,
            }, timeout=5))
        except Exception:
            pass
        try:
            from harness.browser.overlay import OVERLAY_JS
            self._run(self._send("Page.addScriptToEvaluateOnNewDocument", {
                "source": OVERLAY_JS,
            }, timeout=5))
        except Exception:
            pass
        try:
            self._run(self._send("Emulation.setUserAgentOverride", {
                "acceptLanguage": "en-US,en;q=0.9",
            }, timeout=5))
        except Exception:
            pass

    def _wait_page_ready(self, timeout: float = 15) -> None:
        """Poll document.readyState until 'complete' (best effort).

        Page.navigate returns as soon as the navigation *starts* — the very
        first poll can still hit the OLD document (which reports 'complete'),
        so settle briefly first; without this the agent reads half-rendered
        pages on JS-heavy sites.
        """
        if not self._connected:
            return
        time.sleep(0.4)  # let the new document commit before probing it
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                try:
                    state = self._run(self._eval("document.readyState", timeout=5))
                except Exception:
                    break
                if state == "complete":
                    break
                time.sleep(0.5)
        except Exception:
            pass

    def _after_navigation(self, overlay_action: str = "navigate", custom_msg: str = None):
        """Shared tail for navigation ops: settle, re-inject overlay (page JS
        is wiped on every navigation), show a status blip."""
        self._wait_page_ready()
        self._inject_overlay()
        self._show_overlay(overlay_action, custom_msg)
        title = ""
        try:
            title = self._run(self._eval("document.title")) or ""
        except Exception:
            pass
        self._hide_overlay(1500)
        return str(title)

    # ── Agent mouse indicator ─────────────────────────────────────────

    def _mouse_move(self, x: int, y: int):
        if not self._connected:
            return
        try:
            self._run(self._eval(js_mouse_move(x, y), timeout=3))
        except Exception:
            pass

    def _mouse_click(self, x: int, y: int, pause: float = 0.3):
        """Show the click ripple at (x, y) and pause so a human watcher (and
        the next screenshot) can actually see it."""
        if not self._connected:
            return
        try:
            self._run(self._eval(js_mouse_click(x, y), timeout=3))
        except Exception:
            pass
        if pause > 0:
            time.sleep(pause)

    def _flash_element(self, selector: str):
        if not self._connected:
            return
        try:
            self._run(self._eval(js_flash_element(selector), timeout=3))
        except Exception:
            pass

    def _element_center(self, selector: str):
        """Viewport center of the element, or None."""
        if not self._connected:
            return None
        try:
            pt = self._run(self._eval(js_element_center(selector), timeout=5))
            if isinstance(pt, (list, tuple)) and len(pt) == 2:
                return int(pt[0]), int(pt[1])
        except Exception:
            pass
        return None

    def _require_connected(self):
        if not self.is_connected():
            raise RuntimeError("Browser not launched — call browser_launch first")

    # ── Navigation ──────────────────────────────────────────────────────────

    def navigate(self, url: str) -> str:
        self._require_connected()
        self._show_overlay("navigate", f"Loading {urlparse(url).netloc or url}...")
        self._run(self._send("Page.navigate", {"url": url}))
        return self._after_navigation("navigate", f"Loading {urlparse(url).netloc or url}...")

    def back(self) -> str:
        self._require_connected()
        self._show_overlay("back")
        self._run(self._eval("history.back()"))
        return self._after_navigation("back")

    def forward(self) -> str:
        self._require_connected()
        self._show_overlay("forward")
        self._run(self._eval("history.forward()"))
        return self._after_navigation("forward")

    def reload(self) -> str:
        self._require_connected()
        self._show_overlay("reload")
        self._run(self._send("Page.reload", {"ignoreCache": False}))
        return self._after_navigation("reload")

    # ── Interaction ─────────────────────────────────────────────────────────

    def click(self, selector: str, index: Optional[int] = None) -> bool:
        """Click an element by CSS selector.
        
        If index is provided, clicks the Nth matching element (0-based).
        Otherwise, finds the first visible, clickable element among all matches.
        """
        self._require_connected()
        self._show_overlay("click", f"Clicking {selector}")
        
        # Build JS to find clickable element(s)
        js = f"""(function() {{
            var selector = {json.dumps(selector)};
            var index = {json.dumps(index)};
            
            function isClickable(el) {{
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                var rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                // Check if element is in viewport (at least partially)
                if (rect.right < 0 || rect.bottom < 0 || 
                    rect.left > window.innerWidth || rect.top > window.innerHeight) return false;
                // Check if element or ancestors have pointer-events: none
                var current = el;
                while (current) {{
                    var s = window.getComputedStyle(current);
                    if (s.pointerEvents === 'none') return false;
                    current = current.parentElement;
                }}
                return true;
            }}
            
            var allMatches = Array.from(document.querySelectorAll(selector));
            if (allMatches.length === 0) {{
                return {{ found: false, reason: 'no_matches', count: 0 }};
            }}
            
            var clickableMatches = allMatches.filter(isClickable);
            
            if (index !== null && index !== undefined) {{
                if (index >= 0 && index < allMatches.length) {{
                    var target = allMatches[index];
                    if (!isClickable(target)) {{
                        return {{ found: false, reason: 'index_not_clickable', count: allMatches.length, clickableCount: clickableMatches.length }};
                    }}
                    target.scrollIntoView({{block: 'center'}});
                    target.click();
                    return {{ found: true, clickedIndex: index, totalMatches: allMatches.length }};
                }}
                return {{ found: false, reason: 'index_out_of_bounds', count: allMatches.length }};
            }}
            
            // No index: click first clickable match
            if (clickableMatches.length === 0) {{
                return {{ found: false, reason: 'none_clickable', count: allMatches.length, clickableCount: 0 }};
            }}
            
            var target = clickableMatches[0];
            target.scrollIntoView({{block: 'center'}});
            target.click();
            return {{ found: true, clickedIndex: allMatches.indexOf(target), totalMatches: allMatches.length, clickableCount: clickableMatches.length }};
        }})()"""
        result = self._run(self._eval(js))
        self._hide_overlay(1200)
        
        if isinstance(result, dict) and result.get('found'):
            if pt := self._element_center(selector):
                self._mouse_click(*pt)
            return True
        return False

    def click_at(self, x: int, y: int) -> None:
        self._require_connected()
        self._show_overlay("click", f"Clicking ({x}, {y})")
        self._mouse_click(x, y)
        self._run(self._send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1
        }))
        self._run(self._send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1
        }))
        self._hide_overlay(1200)

    def type_text(self, selector: str, text: str) -> bool:
        self._require_connected()
        self._show_overlay("type", f"Typing into {selector}")
        self._flash_element(selector)
        focus_js = f"""(function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.focus(); el.value = '';
            return true;
        }})()"""
        if not self._run(self._eval(focus_js)):
            self._hide_overlay(1200)
            return False
        self._run(self._send("Input.insertText", {"text": text}))
        self._run(self._eval(f"""(function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (el) {{
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        }})()"""))
        self._hide_overlay(1200)
        return True

    def type_keys(self, text: str) -> None:
        self._require_connected()
        self._show_overlay("type", "Typing...")
        self._run(self._send("Input.insertText", {"text": text}))
        self._hide_overlay(1200)

    def press_key(self, key: str) -> None:
        self._require_connected()
        self._show_overlay("press", f"Pressing {key}")
        KEY_MAP = {
            "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
            "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9},
            "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27},
            "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8},
            "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46, "nativeVirtualKeyCode": 46},
            "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38, "nativeVirtualKeyCode": 38},
            "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40},
            "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37, "nativeVirtualKeyCode": 37},
            "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39, "nativeVirtualKeyCode": 39},
            "Home": {"key": "Home", "code": "Home", "windowsVirtualKeyCode": 36, "nativeVirtualKeyCode": 36},
            "End": {"key": "End", "code": "End", "windowsVirtualKeyCode": 35, "nativeVirtualKeyCode": 35},
            "PageUp": {"key": "PageUp", "code": "PageUp", "windowsVirtualKeyCode": 33, "nativeVirtualKeyCode": 33},
            "PageDown": {"key": "PageDown", "code": "PageDown", "windowsVirtualKeyCode": 34, "nativeVirtualKeyCode": 34},
            "Space": {"key": " ", "code": "Space", "windowsVirtualKeyCode": 32, "nativeVirtualKeyCode": 32},
        }
        params = KEY_MAP.get(key, {"key": key, "code": key})
        self._run(self._send("Input.dispatchKeyEvent", {"type": "keyDown", **params}))
        self._run(self._send("Input.dispatchKeyEvent", {"type": "keyUp", **params}))
        self._hide_overlay(1200)

    def scroll(self, direction: str = "down", amount: int = 500, to: Optional[str] = None, selector: Optional[str] = None) -> None:
        """Scroll the page.
        
        Args:
            direction: "up", "down", "left", "right" (used with amount)
            amount: Pixels to scroll, or string like "50%" for viewport percentage
            to: "top", "bottom", or a CSS selector to scroll into view
            selector: CSS selector of element to scroll into view (alias for to)
        """
        self._require_connected()
        
        # Scroll to element
        target_selector = selector or (to if to and to not in ("top", "bottom") else None)
        if target_selector:
            self._show_overlay("scroll", f"Scrolling to {target_selector}")
            js = f"""(function() {{
                var el = document.querySelector({json.dumps(target_selector)});
                if (!el) return false;
                el.scrollIntoView({{block: 'center', behavior: 'smooth'}});
                return true;
            }})()"""
            result = self._run(self._eval(js))
            if not result:
                raise RuntimeError(f"Element not found: {target_selector}")
            self._hide_overlay(1200)
            return
        
        # Scroll to top/bottom
        if to == "top":
            self._show_overlay("scroll", "Scrolling to top")
            self._run(self._eval("window.scrollTo({top: 0, behavior: 'smooth'})"))
            self._hide_overlay(1200)
            return
        if to == "bottom":
            self._show_overlay("scroll", "Scrolling to bottom")
            self._run(self._eval("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"))
            self._hide_overlay(1200)
            return
        
        # Scroll by amount (pixels or percentage)
        self._show_overlay("scroll", f"Scrolling {direction}")
        
        # Parse amount - can be int (pixels) or string like "50%"
        pixels = 0
        if isinstance(amount, str) and amount.endswith("%"):
            try:
                pct = float(amount.rstrip("%")) / 100
                js = f"window.innerHeight * {pct}"
                pixels = int(self._run(self._eval(js)))
            except Exception:
                pixels = 500
        else:
            pixels = int(amount)
        
        dx, dy = 0, 0
        if direction == "down": dy = pixels
        elif direction == "up": dy = -pixels
        elif direction == "right": dx = pixels
        elif direction == "left": dx = -pixels
        
        self._run(self._send("Input.dispatchMouseEvent", {{
            "type": "mouseWheel", "x": 400, "y": 300, "deltaX": dx, "deltaY": dy
        }}))
        self._hide_overlay(1200)

    def hover(self, selector: str) -> bool:
        self._require_connected()
        self._show_overlay("hover", f"Hovering {selector}")
        pt = self._element_center(selector)
        if pt is not None:
            self._mouse_move(*pt)
        js = f"""(function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.scrollIntoView({{block: 'center'}});
            var rect = el.getBoundingClientRect();
            var ev = new MouseEvent('mouseover', {{clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2, bubbles: true}});
            el.dispatchEvent(ev);
            return true;
        }})()"""
        result = self._run(self._eval(js))
        self._hide_overlay(1200)
        return bool(result)

    def select_option(self, selector: str, value: str) -> bool:
        self._require_connected()
        self._show_overlay("select", f"Selecting in {selector}")
        js = f"""(function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el || el.tagName !== 'SELECT') return false;
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
        }})()"""
        result = self._run(self._eval(js))
        self._hide_overlay(1200)
        return bool(result)

    # ── JavaScript ──────────────────────────────────────────────────────────

    def evaluate(self, expression: str) -> Any:
        self._require_connected()
        self._show_overlay("evaluate", "Running JavaScript...")
        result = self._run(self._eval(expression))
        self._hide_overlay(1500)
        return result

    # ── Tabs ────────────────────────────────────────────────────────────────

    def list_tabs(self) -> List[dict]:
        self._require_connected()
        self._show_overlay("tab_list")
        import requests
        try:
            targets = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=3).json()
            pages = [{"id": t["id"], "title": t.get("title", ""), "url": t.get("url", "")}
                     for t in targets if t.get("type") == "page"]
        except Exception:
            pages = []
        self._hide_overlay(1500)
        return pages

    def new_tab(self, url: str = "about:blank") -> str:
        self._require_connected()
        self._show_overlay("tab_new")
        import requests
        try:
            # The DevTools HTTP API requires PUT for tab creation (GET is 405).
            result = requests.put(f"http://127.0.0.1:{self.port}/json/new?{url}", timeout=5).json()
            tab_id = result.get("id", "")
        except Exception:
            tab_id = ""
        self._hide_overlay(1500)
        return tab_id

    def close_tab(self, tab_id: str) -> bool:
        self._require_connected()
        self._show_overlay("tab_close")
        import requests
        try:
            # PUT as well — GET returns 405 on modern Chrome.
            resp = requests.put(f"http://127.0.0.1:{self.port}/json/close/{tab_id}", timeout=5)
            return resp.status_code < 400
        except Exception:
            return False

    def focus_tab(self, tab_id: str) -> bool:
        self._require_connected()
        self._show_overlay("focus")
        import requests
        try:
            targets = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=3).json()
            for t in targets:
                if t["id"] == tab_id and t.get("type") == "page":
                    ws_url = t.get("webSocketDebuggerUrl", "")
                    if ws_url:
                        if self._ws:
                            self._run(self._ws.close())
                        self._ws = self._run(websockets.connect(ws_url, max_size=50*1024*1024))
                        self._target_id = tab_id
                        self._run(self._send("Page.enable", {}))
                        self._run(self._send("Runtime.enable", {}))
                        self._apply_stealth()
                        self._inject_overlay()
                        self._hide_overlay(1500)
                        return True
        except Exception:
            pass
        self._hide_overlay(1500)
        return False

    # ── Screenshot ──────────────────────────────────────────────────────────

    def screenshot(self, full_page: bool = False) -> str:
        self._require_connected()
        # Hide the overlay *before* capturing so the status banner never
        # pollutes the image the model sees; wait out the CSS fade first.
        self._hide_overlay_now()
        time.sleep(0.35)
        if full_page:
            metrics = self._run(self._send("Page.getLayoutMetrics", {}))
            content = metrics.get("cssContentSize", metrics.get("contentSize", {"width": 1280, "height": 800}))
            self._run(self._send("Emulation.setDeviceMetricsOverride", {
                "width": int(content["width"]),
                "height": int(content["height"]),
                "deviceScaleFactor": 1,
                "mobile": False,
            }))
            result = self._run(self._send("Page.captureScreenshot", {"format": "png"}))
            self._run(self._send("Emulation.clearDeviceMetricsOverride", {}))
        else:
            result = self._run(self._send("Page.captureScreenshot", {"format": "png"}))
        return result.get("data", "")

    def screenshot_to_file(self, path: str, full_page: bool = False) -> str:
        data = self.screenshot(full_page)
        if not data:
            return ""
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        img_bytes = base64.b64decode(data)
        with open(path, "wb") as f:
            f.write(img_bytes)
        return os.path.abspath(path)

    # ── Page info ───────────────────────────────────────────────────────────

    def get_url(self) -> str:
        self._require_connected()
        return str(self._run(self._eval("window.location.href")) or "")

    def get_title(self) -> str:
        self._require_connected()
        return str(self._run(self._eval("document.title")) or "")

    def get_page_text(self, max_chars: int = 8000) -> str:
        self._require_connected()
        self._show_overlay("evaluate", "Reading page content...")
        js = f"""(function() {{
            var text = document.body ? document.body.innerText : '';
            return text.substring(0, {max_chars});
        }})()"""
        result = str(self._run(self._eval(js)) or "")
        self._hide_overlay(1500)
        return result

    def wait_for(self, selector: str, timeout_ms: int = 5000) -> bool:
        self._require_connected()
        js = f"""new Promise(function(resolve) {{
            var el = document.querySelector({json.dumps(selector)});
            if (el) {{ resolve(true); return; }}
            var obs = new MutationObserver(function() {{
                if (document.querySelector({json.dumps(selector)})) {{
                    obs.disconnect();
                    resolve(true);
                }}
            }});
            obs.observe(document.body || document, {{childList: true, subtree: true}});
            setTimeout(function() {{ obs.disconnect(); resolve(false); }}, {timeout_ms});
        }})"""
        return bool(self._run(self._eval(js, timeout=timeout_ms / 1000 + 2)))
