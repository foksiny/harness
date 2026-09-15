"""
Browser controller via Chrome DevTools Protocol (CDP).
Works with any Chromium-based browser (Chrome, Edge, Brave, Zen, Opera, Vivaldi)
and Firefox (via its remote debugging protocol).
"""
import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Any, List
from urllib.parse import urlparse

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from harness.browser.overlay import js_show_overlay, js_hide_overlay

DEFAULT_CDP_PORT = 9222
DEFAULT_USER_DATA_DIR = Path(tempfile.mkdtemp(prefix="harness_browser_"))

CHROME_PATHS = [
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/usr/bin/brave-browser", "/usr/bin/brave-browser-stable",
    "/snap/bin/brave", "/snap/bin/chromium",
    "/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
    "/usr/bin/zen-browser", "/usr/bin/zen",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Zen Browser.app/Contents/MacOS/Zen Browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

FIREFOX_PATHS = [
    "/usr/bin/firefox", "/usr/bin/firefox-esr",
    "/snap/bin/firefox",
    "/Applications/Firefox.app/Contents/MacOS/firefox",
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
]


def _find_browser() -> Optional[str]:
    for p in CHROME_PATHS + FIREFOX_PATHS:
        if os.path.isfile(p):
            return p
    from shutil import which
    for name in ("google-chrome", "chromium", "brave-browser", "microsoft-edge",
                 "zen-browser", "zen", "firefox", "firefox-esr"):
        found = which(name)
        if found:
            return found
    return None


class BrowserController:
    """CDP-based browser controller with visual overlay.

    Usage::

        ctrl = BrowserController()
        ctrl.launch()
        ctrl.navigate("https://example.com")
        ctrl.click("#btn")
        ctrl.type_text("input#q", "hello")
        img = ctrl.screenshot()
        ctrl.close()
    """

    def __init__(self, port: int = DEFAULT_CDP_PORT, headless: bool = False):
        if not HAS_WEBSOCKETS:
            raise ImportError("pip install websockets  # required for browser control")
        self.port = port
        self.headless = headless
        self._proc: Optional[subprocess.Popen] = None
        self._ws = None
        self._cmd_id = 0
        self._target_id: Optional[str] = None
        self._connected = False
        self._page_loaded = False

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def launch(self, browser_path: Optional[str] = None, url: str = "about:blank") -> str:
        exe = browser_path or _find_browser()
        if not exe:
            raise RuntimeError("No browser found. Install Chrome/Edge/Brave/Firefox or pass browser_path.")
        is_firefox = "firefox" in exe.lower()

        # If CDP is already available on this port (e.g. user's existing browser),
        # connect to it instead of launching a new instance.
        if self._check_cdp():
            if self.connect():
                return f"Connected to existing browser on port {self.port} ({Path(exe).name})"
            # CDP responded but connect failed — the port is occupied by something
            # that isn't a usable browser. Don't try to launch a new one on the same port.
            raise RuntimeError(
                f"Port {self.port} is in use but CDP connect failed. "
                f"Close the process using port {self.port} or use a different port."
            )
        args = [exe, f"--remote-debugging-port={self.port}"]
        if is_firefox:
            args += [f"--remote-allow-hosts=*", "--no-remote", "--new-instance"]
        if self.headless:
            args.append("--headless" if is_firefox else "--headless=new")
        args += [
            f"--user-data-dir={DEFAULT_USER_DATA_DIR}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync",
            "--disable-extensions", url,
        ]
        launch_kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            launch_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            launch_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(args, **launch_kwargs)
        # Firefox CDP can take a few seconds to become available; retry up to 20s.
        for _ in range(80):
            time.sleep(0.25)
            if self._check_cdp():
                if self.connect():
                    return f"Browser launched ({Path(exe).name})"
        self.close()
        raise RuntimeError(f"Browser launched but CDP not available on port {self.port}")

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
        self._inject_overlay()
        self._show_overlay("ready")
        self._hide_overlay(2000)
        return True

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

    # ── CDP transport ───────────────────────────────────────────────────────

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
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

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

    def _inject_overlay(self):
        if not self._connected:
            return
        from harness.browser.overlay import OVERLAY_JS
        try:
            self._run(self._eval(OVERLAY_JS, timeout=5))
        except Exception:
            pass

    # ── Navigation ──────────────────────────────────────────────────────────

    def navigate(self, url: str) -> str:
        self._show_overlay("navigate", f"Loading {urlparse(url).netloc or url}...")
        self._run(self._send("Page.navigate", {"url": url}))
        time.sleep(1)
        title = self._run(self._eval("document.title")) or ""
        self._hide_overlay(1500)
        return str(title)

    def back(self) -> str:
        self._show_overlay("back")
        self._run(self._eval("history.back()"))
        time.sleep(0.5)
        title = self._run(self._eval("document.title")) or ""
        self._hide_overlay(1500)
        return str(title)

    def forward(self) -> str:
        self._show_overlay("forward")
        self._run(self._eval("history.forward()"))
        time.sleep(0.5)
        title = self._run(self._eval("document.title")) or ""
        self._hide_overlay(1500)
        return str(title)

    def reload(self) -> str:
        self._show_overlay("reload")
        self._run(self._send("Page.reload", {"ignoreCache": False}))
        time.sleep(1)
        title = self._run(self._eval("document.title")) or ""
        self._hide_overlay(1500)
        return str(title)

    # ── Interaction ─────────────────────────────────────────────────────────

    def click(self, selector: str) -> bool:
        self._show_overlay("click", f"Clicking {selector}")
        js = f"""(function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.scrollIntoView({{block: 'center'}});
            el.click();
            return true;
        }})()"""
        result = self._run(self._eval(js))
        self._hide_overlay(1200)
        return bool(result)

    def click_at(self, x: int, y: int) -> None:
        self._show_overlay("click", f"Clicking ({x}, {y})")
        self._run(self._send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1
        }))
        self._run(self._send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1
        }))
        self._hide_overlay(1200)

    def type_text(self, selector: str, text: str) -> bool:
        self._show_overlay("type", f"Typing into {selector}")
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
        self._show_overlay("type", "Typing...")
        self._run(self._send("Input.insertText", {"text": text}))
        self._hide_overlay(1200)

    def press_key(self, key: str) -> None:
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

    def scroll(self, direction: str = "down", amount: int = 500) -> None:
        self._show_overlay("scroll", f"Scrolling {direction}")
        dx, dy = 0, 0
        if direction == "down": dy = amount
        elif direction == "up": dy = -amount
        elif direction == "right": dx = amount
        elif direction == "left": dx = -amount
        self._run(self._send("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": 400, "y": 300, "deltaX": dx, "deltaY": dy
        }))
        self._hide_overlay(1200)

    def hover(self, selector: str) -> bool:
        self._show_overlay("hover", f"Hovering {selector}")
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
        self._show_overlay("evaluate", "Running JavaScript...")
        result = self._run(self._eval(expression))
        self._hide_overlay(1500)
        return result

    # ── Tabs ────────────────────────────────────────────────────────────────

    def list_tabs(self) -> List[dict]:
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
        self._show_overlay("tab_new")
        import requests
        try:
            result = requests.get(f"http://127.0.0.1:{self.port}/json/new?{url}", timeout=5).json()
            tab_id = result.get("id", "")
        except Exception:
            tab_id = ""
        self._hide_overlay(1500)
        return tab_id

    def close_tab(self, tab_id: str) -> bool:
        self._show_overlay("tab_close")
        import requests
        try:
            requests.get(f"http://127.0.0.1:{self.port}/json/close/{tab_id}", timeout=5)
            return True
        except Exception:
            return False

    def focus_tab(self, tab_id: str) -> bool:
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
                        self._inject_overlay()
                        self._hide_overlay(1500)
                        return True
        except Exception:
            pass
        self._hide_overlay(1500)
        return False

    # ── Screenshot ──────────────────────────────────────────────────────────

    def screenshot(self, full_page: bool = False) -> str:
        self._show_overlay("screenshot")
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
        self._hide_overlay(1500)
        return result.get("data", "")

    def screenshot_to_file(self, path: str, full_page: bool = False) -> str:
        data = self.screenshot(full_page)
        if not data:
            return ""
        img_bytes = base64.b64decode(data)
        with open(path, "wb") as f:
            f.write(img_bytes)
        return path

    # ── Page info ───────────────────────────────────────────────────────────

    def get_url(self) -> str:
        return str(self._run(self._eval("window.location.href")) or "")

    def get_title(self) -> str:
        return str(self._run(self._eval("document.title")) or "")

    def get_page_text(self, max_chars: int = 8000) -> str:
        self._show_overlay("evaluate", "Reading page content...")
        js = f"""(function() {{
            var text = document.body ? document.body.innerText : '';
            return text.substring(0, {max_chars});
        }})()"""
        result = str(self._run(self._eval(js)) or "")
        self._hide_overlay(1500)
        return result

    def wait_for(self, selector: str, timeout_ms: int = 5000) -> bool:
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
