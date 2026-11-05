"""
Browser-assisted login for the web-session providers (openaiauth / claudeauth).

Instead of asking the user to dig the session cookie out of DevTools by hand,
``/login openaiauth`` / ``/login claudeauth`` can now pop a real Chrome
window on the provider's website. The user signs in there (Google / email /
whatever the site offers — including free accounts), and once the site sets
its session cookie we read it back through CDP and store it in the secure
key store, exactly as if it had been pasted.

Flow (``capture_session_token``):

  1. Launch a *visible* Chrome via the same BrowserController the agent's
     browser_* tools use (fresh throwaway profile, stealth flags, CDP on a
     scratch port). Falls back to headless+Xvfb on display-less boxes, which
     is useless for interactive login — in that case we say so and let the
     caller fall back to the manual paste prompt.
  2. Navigate to the site and show a banner telling the user to log in.
  3. Poll ``document.cookie`` every ~1.5s. Cookies that matter are HttpOnly,
     so ``document.cookie`` can't see them — we read them through the CDP
     ``Network.getCookies`` domain instead, which sees everything the browser
     holds for that domain.
  4. The moment the target cookie appears (login complete), close the
     browser and return the raw cookie value.

Why a scratch CDP port: the shared agent browser lives on the default port
9222 and may be busy or headless; login wants its own visible window that
we can tear down cleanly afterwards without touching the agent's session.
"""
import time
from typing import Callable, Optional

# provider id -> (site to open, cookie name to capture)
LOGIN_TARGETS = {
    "openaiauth": ("https://chatgpt.com", "__Secure-next-auth.session-token"),
    "claudeauth": ("https://claude.ai", "sessionKey"),
}

# How long to wait for the user to finish logging in before giving up.
_LOGIN_TIMEOUT_SECONDS = 600
_POLL_INTERVAL = 1.5

# Scratch CDP port for the login window (default 9222 belongs to the agent's
# shared browser session — never fight over it).
_LOGIN_CDP_PORT = 9333


class LoginCancelled(Exception):
    """The user aborted the browser login flow."""


def _cdp_read_cookies(port: int, urls: list) -> dict:
    """Read all cookies the browser holds for ``urls`` via the DevTools HTTP API.

    Uses ``Storage.getCookies`` over a websocket-free one-shot: the DevTools
    HTTP endpoint only offers discovery, so we open a minimal websocket to
    the page target and call the CDP domain once. Returns {name: value}.
    """
    import json
    import requests
    import websockets

    # Find a page target for the site.
    targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=3).json()
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        return {}
    ws_url = None
    for p in pages:
        if any(u in (p.get("url") or "") for u in urls):
            ws_url = p.get("webSocketDebuggerUrl")
            break
    if ws_url is None:
        ws_url = pages[0].get("webSocketDebuggerUrl")

    import asyncio

    async def _fetch():
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "id": 1,
                "method": "Storage.getCookies",
                "params": {"urls": urls},
            }))
            # Read frames until our reply arrives (skipping events).
            for _ in range(50):
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    cookies = msg.get("result", {}).get("cookies", [])
                    return {c.get("name"): c.get("value") for c in cookies if c.get("name")}
            return {}

    return asyncio.run(_fetch())


def _notify(status: str, detail: str = "", sink: Optional[Callable] = None) -> None:
    """Best-effort progress note to the user (TUI print or stderr)."""
    line = f"  {status}" + (f" — {detail}" if detail else "")
    if sink is not None:
        try:
            sink(line)
        except Exception:
            pass
    else:
        try:
            import sys
            print(line, file=sys.stderr, flush=True)
        except Exception:
            pass


def capture_session_token(
    provider: str,
    progress: Optional[Callable] = None,
    timeout: int = _LOGIN_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Open the provider's site in a visible Chrome and capture the session cookie.

    Args:
        provider: "openaiauth" or "claudeauth".
        progress: optional callback receiving short status lines.
        timeout: max seconds to wait for the login to complete.

    Returns the raw cookie value, or None when the browser flow is
    unavailable (no Chrome, no display) — the caller should then fall back
    to the manual paste prompt.

    Raises LoginCancelled when the user interrupts the wait.
    """
    if provider not in LOGIN_TARGETS:
        raise ValueError(f"Unknown web-session provider: {provider}")
    site_url, cookie_name = LOGIN_TARGETS[provider]

    from harness.browser.manager import display_available, resolve_headless
    from harness.browser.controller import BrowserController, find_chrome

    if not find_chrome():
        _notify("no Chrome installation found", "fall back to manual paste", progress)
        return None
    if not display_available():
        # Headless can't do interactive login — no point pretending.
        _notify("no graphical display", "fall back to manual paste", progress)
        return None

    _notify("opening browser", site_url, progress)
    controller = BrowserController(port=_LOGIN_CDP_PORT, headless=False)
    launched = controller.launch(url=site_url)
    _notify("browser ready", launched, progress)

    cookie_value: Optional[str] = None
    try:
        # Tell the user what to do inside the window.
        try:
            controller.evaluate(
                f"(function(){{document.title='Log in to finish Harness /login ({provider}) — this window closes itself';}})()"
            )
        except Exception:
            pass

        _notify("waiting for login", "log in inside the browser window", progress)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                cookies = {}
                try:
                    cookies = _cdp_read_cookies(_LOGIN_CDP_PORT, [site_url])
                except Exception:
                    pass
                val = cookies.get(cookie_name)
                if val:
                    cookie_value = val
                    break
                time.sleep(_POLL_INTERVAL)
            except KeyboardInterrupt:
                raise LoginCancelled("interrupted while waiting for login")
    finally:
        try:
            controller.close()
        except Exception:
            pass

    if cookie_value:
        _notify("login captured", "session cookie stored", progress)
    else:
        _notify("timed out", f"no {cookie_name} cookie after {timeout}s", progress)
    return cookie_value


def verify_session_token(provider: str, token: str) -> bool:
    """Cheap liveness check for a stored session credential.

    Hits the provider's lightweight authenticated endpoint:
      * openaiauth -> /backend-api/me (session info for the logged-in user)
      * claudeauth -> /api/organizations (org list requires a valid session)

    Returns True when the endpoint answers 200 with the cookie attached.
    Never raises — a network failure just means "unverified".
    """
    if provider == "openaiauth":
        url = "https://chatgpt.com/backend-api/me"
    elif provider == "claudeauth":
        url = "https://claude.ai/api/organizations"
    else:
        return False
    try:
        from harness.providers.web_session_provider import _WebSessionHttp
        http = _WebSessionHttp(token)
        code, _ = http.get_json(url)
        return code == 200
    except Exception:
        return False
