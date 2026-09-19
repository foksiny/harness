"""
Browser automation tools — Chrome control for the agent.

Hermetic, injectable seams, zero real browser in tests: every tool takes an
optional ``controller_factory`` (``BrowserManager.controller_factory`` in
production) plus an optional ``permission_manager``.

Screenshot flow: ``browser_screenshot`` always saves a PNG to disk and embeds
a ``[harness:image:path]`` marker in its text result. The agent loop strips
the marker and routes the file — as a real image block for vision models, or
through the vision-fallback (VFB) describer for non-vision models.
"""
import json
import os
import time
from typing import Optional
from harness.tools.base import Tool
from harness.core.permissions import PermissionManager
from harness.core.context_budget import truncate_output

_EVAL_MAX_CHARS = 8000


class _BrowserBase(Tool):
    action_type = "browser"
    is_read_only = False

    def __init__(self, controller_factory=None, permission_manager: Optional[PermissionManager] = None):
        self._factory = controller_factory
        self.permission_manager = permission_manager

    def _controller(self):
        ctrl = self._factory() if self._factory else None
        return ctrl

    def _allowed(self, summary: str) -> Optional[str]:
        """Return an error string when permission is denied, else None."""
        if self.permission_manager is None:
            return None
        ok = self.permission_manager.check_permission(
            self.action_type, {"tool": self.name, "summary": summary}
        )
        if not ok:
            return f"Error: {self.name} rejected by security policy or user: {summary}"
        return None


class BrowserLaunchTool(_BrowserBase):
    name = "browser_launch"
    description = (
        "Launch Chrome in a visible window (or connect to an already-running "
        "Chrome with remote debugging on the given port). Must be called before "
        "any other browser_* tool. Chrome/Chromium only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "browser_path": {"type": "string", "description": "Path to the chrome executable (auto-detected if omitted)"},
            "headless": {"type": "boolean", "description": "Run without a visible window (default false — a real Chrome window opens; pass true to run in the background. On servers without a display, Xvfb is used automatically when available, otherwise it falls back to headless)", "default": False},
            "port": {"type": "integer", "description": "CDP debugging port", "default": 9222},
        },
    }

    def execute(self, browser_path=None, headless=False, port=9222, **kwargs):
        ctrl = self._factory(port=port, headless=headless) if self._factory else None
        if ctrl is None:
            return "Error: No browser controller available"
        denied = self._allowed(f"Launch Chrome (headless={bool(headless)}, port={port})")
        if denied:
            return denied
        try:
            info = ctrl.launch(browser_path=browser_path)
            note = getattr(ctrl, "manager_note", "")
            out = f"Browser ready: {info} (CDP port {port})"
            if isinstance(note, str) and note:
                out += f"\nNote: {note}"
            return out
        except Exception as e:
            return f"Error launching browser: {e}"


class BrowserNavigateTool(_BrowserBase):
    name = "browser_navigate"
    description = "Navigate the browser to a URL. Returns the page title."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to navigate to"},
        },
        "required": ["url"],
    }

    def execute(self, url: str, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Navigate to {url}")
        if denied:
            return denied
        try:
            title = ctrl.navigate(url)
            return f"Navigated to {url}\nTitle: {title}"
        except Exception as e:
            return f"Error navigating: {e}"


class BrowserClickTool(_BrowserBase):
    name = "browser_click"
    description = (
        "Click an element on the page by CSS selector. For generic selectors like 'a' or 'button', "
        "automatically finds the first visible, clickable match. Use 'index' to click a specific match (0-based)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector to click (e.g., 'a', 'button#submit', '.result a')"},
            "index": {"type": "integer", "description": "Optional: click the Nth matching element (0-based). Use when selector matches multiple elements and you want a specific one."},
            "x": {"type": "integer", "description": "X coordinate (if no selector)"},
            "y": {"type": "integer", "description": "Y coordinate (if no selector)"},
        },
    }

    def execute(self, selector=None, index=None, x=None, y=None, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        target = selector or (f"({x}, {y})" if x is not None and y is not None else "?")
        denied = self._allowed(f"Click {target}")
        if denied:
            return denied
        try:
            if selector:
                ok = ctrl.click(selector, index)
                if ok:
                    msg = f"Clicked {selector}"
                    if index is not None:
                        msg += f" [index {index}]"
                    return msg
                # Try to get more details about why it failed
                details = ctrl.evaluate(f"""(function() {{
                    var selector = {json.dumps(selector)};
                    var matches = document.querySelectorAll(selector);
                    return {{ count: matches.length }};
                }})()""")
                count = details.get('count', 0) if isinstance(details, dict) else 0
                if count == 0:
                    return f"Element not found: {selector} (no matches)"
                else:
                    return f"Element not clickable: {selector} ({count} match(es) found, but none are visible/clickable). Try using 'index' parameter to target a specific match, or use a more specific selector."
            elif x is not None and y is not None:
                ctrl.click_at(x, y)
                return f"Clicked at ({x}, {y})"
            else:
                return "Error: Provide selector or x/y coordinates"
        except Exception as e:
            return f"Error clicking: {e}"


class BrowserTypeTool(_BrowserBase):
    name = "browser_type"
    description = "Type text into an input field, or type raw keystrokes at the focused element."
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of input/textarea (omit for raw typing)"},
            "text": {"type": "string", "description": "Text to type"},
        },
        "required": ["text"],
    }

    def execute(self, text: str, selector=None, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Type into {selector or 'focused element'}")
        if denied:
            return denied
        try:
            if selector:
                ok = ctrl.type_text(selector, text)
                return f"Typed into {selector}" if ok else f"Element not found: {selector}"
            else:
                ctrl.type_keys(text)
                return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"Error typing: {e}"


class BrowserPressKeyTool(_BrowserBase):
    name = "browser_press_key"
    description = "Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Key name: Enter, Tab, Escape, Backspace, Delete, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Home, End, PageUp, PageDown, Space"},
        },
        "required": ["key"],
    }

    def execute(self, key: str, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Press key {key}")
        if denied:
            return denied
        try:
            ctrl.press_key(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Error pressing key: {e}"


class BrowserScrollTool(_BrowserBase):
    name = "browser_scroll"
    description = (
        "Scroll the page. Supports multiple modes:\n"
        "  - direction + amount: scroll by pixels (default) or viewport percentage (e.g., '50%')\n"
        "  - to: 'top' or 'bottom' to scroll to page extremes\n"
        "  - selector: CSS selector to scroll element into view (smooth)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down", "description": "Scroll direction (used with amount)"},
            "amount": {"type": ["integer", "string"], "description": "Pixels to scroll, or viewport percentage like '50%'", "default": 500},
            "to": {"type": "string", "description": "Scroll to 'top', 'bottom', or a CSS selector"},
            "selector": {"type": "string", "description": "CSS selector of element to scroll into view (alias for 'to')"},
        },
    }

    def execute(self, direction="down", amount=500, to=None, selector=None, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        
        # Build summary for permission check
        if to:
            summary = f"Scroll to {to}"
        elif selector:
            summary = f"Scroll to element {selector}"
        else:
            summary = f"Scroll {direction} {amount}"
        denied = self._allowed(summary)
        if denied:
            return denied
        try:
            ctrl.scroll(direction, amount, to, selector)
            if to:
                return f"Scrolled to {to}"
            elif selector:
                return f"Scrolled to element {selector}"
            else:
                return f"Scrolled {direction} {amount}"
        except Exception as e:
            return f"Error scrolling: {e}"


class BrowserScreenshotTool(_BrowserBase):
    name = "browser_screenshot"
    action_type = "browser_read"
    is_read_only = True
    description = (
        "Take a screenshot of the current browser page. The image is saved to "
        "disk and shown to you directly (vision models see the image; "
        "non-vision models receive a written description via the vision "
        "fallback model when one is configured)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "full_page": {"type": "boolean", "description": "Capture full scrollable page", "default": False},
            "save_path": {"type": "string", "description": "Optional file path to save the screenshot (auto-chosen under ~/.harness/screenshots if omitted)"},
        },
    }

    def execute(self, full_page=False, save_path=None, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed("Capture page screenshot")
        if denied:
            return denied
        try:
            if save_path:
                path = os.path.abspath(os.path.expanduser(save_path))
            else:
                from harness.browser.manager import screenshots_dir
                stamp = time.strftime("%Y%m%d_%H%M%S")
                path = os.path.join(screenshots_dir(), f"browser_{stamp}.png")
            written = ctrl.screenshot_to_file(path, full_page)
            if not written:
                return "Error: Screenshot came back empty — the page may not have rendered yet"
            lines = [f"Screenshot saved to: {written}"]
            try:
                url = ctrl.get_url()
                title = ctrl.get_title()
            except Exception:
                url, title = "", ""
            if url:
                lines.append(f"URL: {url}")
            if title:
                lines.append(f"Title: {title}")
            lines.append(f"[harness:image:{written}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Error taking screenshot: {e}"


class BrowserEvaluateTool(_BrowserBase):
    name = "browser_evaluate"
    description = "Execute JavaScript in the browser page and return the result (truncated to ~8000 chars)."
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
        },
        "required": ["expression"],
    }

    def execute(self, expression: str, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Evaluate JS: {expression[:120]}")
        if denied:
            return denied
        try:
            result = ctrl.evaluate(expression)
            text = str(result) if result is not None else "undefined"
            if len(text) > _EVAL_MAX_CHARS:
                return truncate_output(text, _EVAL_MAX_CHARS)
            return text
        except Exception as e:
            return f"Error evaluating JS: {e}"


class BrowserGetPageInfoTool(_BrowserBase):
    name = "browser_get_page_info"
    action_type = "browser_read"
    is_read_only = True
    description = "Get current page URL, title, and visible text content."
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed("Read page URL/title/text")
        if denied:
            return denied
        try:
            url = ctrl.get_url()
            title = ctrl.get_title()
            text = ctrl.get_page_text(max_chars=4000)
            return f"URL: {url}\nTitle: {title}\n\nText:\n{text}"
        except Exception as e:
            return f"Error getting page info: {e}"


class BrowserTabTool(_BrowserBase):
    name = "browser_tab"
    description = "Manage browser tabs: list, new, close, focus."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "new", "close", "focus"], "description": "Tab action"},
            "tab_id": {"type": "string", "description": "Tab ID (for close/focus)"},
            "url": {"type": "string", "description": "URL for new tab", "default": "about:blank"},
        },
        "required": ["action"],
    }

    def execute(self, action: str, tab_id=None, url="about:blank", **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Tab {action}")
        if denied:
            return denied
        try:
            if action == "list":
                tabs = ctrl.list_tabs()
                if not tabs:
                    return "No tabs found"
                lines = [f"  {t['id'][:12]}  {t['title'][:60]}  {t['url'][:80]}" for t in tabs]
                return f"Tabs ({len(tabs)}):\n" + "\n".join(lines)
            elif action == "new":
                tid = ctrl.new_tab(url)
                return f"New tab: {tid}"
            elif action == "close":
                if not tab_id:
                    return "Error: tab_id required"
                ok = ctrl.close_tab(tab_id)
                return f"Closed tab {tab_id}" if ok else f"Failed to close tab {tab_id}"
            elif action == "focus":
                if not tab_id:
                    return "Error: tab_id required"
                ok = ctrl.focus_tab(tab_id)
                return f"Focused tab {tab_id}" if ok else f"Failed to focus tab {tab_id}"
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Error managing tabs: {e}"


class BrowserNavigationTool(_BrowserBase):
    name = "browser_navigation"
    description = "Browser navigation: back, forward, reload."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["back", "forward", "reload"], "description": "Navigation action"},
        },
        "required": ["action"],
    }

    def execute(self, action: str, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "Error: Browser not launched — call browser_launch first"
        denied = self._allowed(f"Navigate {action}")
        if denied:
            return denied
        try:
            if action == "back":
                title = ctrl.back()
                return f"Went back. Title: {title}"
            elif action == "forward":
                title = ctrl.forward()
                return f"Went forward. Title: {title}"
            elif action == "reload":
                title = ctrl.reload()
                return f"Reloaded. Title: {title}"
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Error: {e}"


class BrowserCloseTool(_BrowserBase):
    name = "browser_close"
    description = "Close the browser session started by browser_launch. Call when finished browsing."
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        ctrl = self._controller()
        if ctrl is None:
            return "No browser session is running."
        denied = self._allowed("Close browser")
        if denied:
            return denied
        try:
            ctrl.close()
            return "Browser closed."
        except Exception as e:
            return f"Error closing browser: {e}"


ALL_BROWSER_TOOLS = [
    BrowserLaunchTool,
    BrowserNavigateTool,
    BrowserClickTool,
    BrowserTypeTool,
    BrowserPressKeyTool,
    BrowserScrollTool,
    BrowserScreenshotTool,
    BrowserEvaluateTool,
    BrowserGetPageInfoTool,
    BrowserTabTool,
    BrowserNavigationTool,
    BrowserCloseTool,
]


def register_browser_tools(registry, controller_factory=None, permission_manager=None):
    """Register all browser tools into a ToolRegistry."""
    for tool_cls in ALL_BROWSER_TOOLS:
        tool = tool_cls(
            controller_factory=controller_factory,
            permission_manager=permission_manager,
        )
        registry.register(tool)
