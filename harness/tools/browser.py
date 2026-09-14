"""
Browser automation tools — hermetic, injectable seams, zero real browser in tests.
"""
from harness.tools.base import Tool


class BrowserLaunchTool(Tool):
    name = "browser_launch"
    description = "Launch or connect to a browser. Returns browser info. Supports Chrome, Edge, Brave, Zen, Firefox."
    parameters = {
        "type": "object",
        "properties": {
            "browser_path": {"type": "string", "description": "Path to browser executable (auto-detected if omitted)"},
            "headless": {"type": "boolean", "description": "Run without GUI", "default": False},
            "port": {"type": "integer", "description": "CDP debugging port", "default": 9222},
        },
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, browser_path=None, headless=False, port=9222, **kwargs):
        ctrl = self._factory(port=port, headless=headless) if self._factory else None
        if ctrl is None:
            return "Error: No browser controller available"
        try:
            info = ctrl.launch(browser_path=browser_path)
            return f"Browser ready: {info} (CDP port {port})"
        except Exception as e:
            return f"Error launching browser: {e}"


class BrowserNavigateTool(Tool):
    name = "browser_navigate"
    description = "Navigate the browser to a URL. Returns the page title."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to navigate to"},
        },
        "required": ["url"],
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, url: str, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            title = ctrl.navigate(url)
            return f"Navigated to {url}\nTitle: {title}"
        except Exception as e:
            return f"Error navigating: {e}"


class BrowserClickTool(Tool):
    name = "browser_click"
    description = "Click an element on the page by CSS selector or at (x, y) coordinates."
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector to click"},
            "x": {"type": "integer", "description": "X coordinate (if no selector)"},
            "y": {"type": "integer", "description": "Y coordinate (if no selector)"},
        },
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, selector=None, x=None, y=None, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            if selector:
                ok = ctrl.click(selector)
                return f"Clicked {selector}" if ok else f"Element not found: {selector}"
            elif x is not None and y is not None:
                ctrl.click_at(x, y)
                return f"Clicked at ({x}, {y})"
            else:
                return "Error: Provide selector or x/y coordinates"
        except Exception as e:
            return f"Error clicking: {e}"


class BrowserTypeTool(Tool):
    name = "browser_type"
    description = "Type text into an input field, or type raw keystrokes."
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of input/textarea (omit for raw typing)"},
            "text": {"type": "string", "description": "Text to type"},
        },
        "required": ["text"],
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, text: str, selector=None, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            if selector:
                ok = ctrl.type_text(selector, text)
                return f"Typed into {selector}" if ok else f"Element not found: {selector}"
            else:
                ctrl.type_keys(text)
                return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"Error typing: {e}"


class BrowserPressKeyTool(Tool):
    name = "browser_press_key"
    description = "Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Key name: Enter, Tab, Escape, Backspace, Delete, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Home, End, PageUp, PageDown, Space"},
        },
        "required": ["key"],
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, key: str, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            ctrl.press_key(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Error pressing key: {e}"


class BrowserScrollTool(Tool):
    name = "browser_scroll"
    description = "Scroll the page in a direction by a pixel amount."
    parameters = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
            "amount": {"type": "integer", "description": "Pixels to scroll", "default": 500},
        },
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, direction="down", amount=500, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            ctrl.scroll(direction, amount)
            return f"Scrolled {direction} {amount}px"
        except Exception as e:
            return f"Error scrolling: {e}"


class BrowserScreenshotTool(Tool):
    name = "browser_screenshot"
    description = "Take a screenshot of the current browser page. Returns base64 PNG data."
    parameters = {
        "type": "object",
        "properties": {
            "full_page": {"type": "boolean", "description": "Capture full scrollable page", "default": False},
            "save_path": {"type": "string", "description": "Optional file path to save the screenshot"},
        },
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, full_page=False, save_path=None, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            if save_path:
                ctrl.screenshot_to_file(save_path, full_page)
                return f"Screenshot saved to {save_path}"
            data = ctrl.screenshot(full_page)
            return f"Screenshot captured ({len(data)} bytes base64). Use browser_screenshot with save_path to save to disk."
        except Exception as e:
            return f"Error taking screenshot: {e}"


class BrowserEvaluateTool(Tool):
    name = "browser_evaluate"
    description = "Execute JavaScript in the browser page and return the result."
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
        },
        "required": ["expression"],
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, expression: str, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            result = ctrl.evaluate(expression)
            return str(result) if result is not None else "undefined"
        except Exception as e:
            return f"Error evaluating JS: {e}"


class BrowserGetPageInfoTool(Tool):
    name = "browser_get_page_info"
    description = "Get current page URL, title, and visible text content."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
        try:
            url = ctrl.get_url()
            title = ctrl.get_title()
            text = ctrl.get_page_text(max_chars=4000)
            return f"URL: {url}\nTitle: {title}\n\nText:\n{text}"
        except Exception as e:
            return f"Error getting page info: {e}"


class BrowserTabTool(Tool):
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

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, action: str, tab_id=None, url="about:blank", **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
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


class BrowserNavigationTool(Tool):
    name = "browser_navigation"
    description = "Browser navigation: back, forward, reload."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["back", "forward", "reload"], "description": "Navigation action"},
        },
        "required": ["action"],
    }

    def __init__(self, controller_factory=None):
        self._factory = controller_factory

    def execute(self, action: str, **kwargs):
        ctrl = self._factory() if self._factory else None
        if ctrl is None:
            return "Error: Browser not connected"
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
]


def register_browser_tools(registry, controller_factory=None):
    """Register all browser tools into a ToolRegistry."""
    for tool_cls in ALL_BROWSER_TOOLS:
        tool = tool_cls(controller_factory=controller_factory)
        registry.register(tool)
