"""
Hermetic tests for browser automation tools.
Browser tools are NOT auto-registered in ToolRegistry anymore (removed from
the default toolset). These tests verify the tools still work when registered
manually via register_browser_tools(), and that the overlay module is intact.
Never touches a real browser — uses mock controllers via injected seams.
"""
import unittest
from unittest.mock import MagicMock
from harness.tools import ToolRegistry
from harness.tools.browser import (
    BrowserLaunchTool, BrowserNavigateTool, BrowserClickTool,
    BrowserTypeTool, BrowserPressKeyTool, BrowserScrollTool,
    BrowserScreenshotTool, BrowserEvaluateTool, BrowserGetPageInfoTool,
    BrowserTabTool, BrowserNavigationTool, register_browser_tools,
    ALL_BROWSER_TOOLS,
)
from harness.browser.overlay import (
    OVERLAY_JS, ACTION_MESSAGES, js_show_overlay, js_hide_overlay, get_action_message,
)


def _make_mock_controller():
    ctrl = MagicMock()
    ctrl.launch.return_value = "Browser launched (chromium)"
    ctrl.navigate.return_value = "Example Domain"
    ctrl.click.return_value = True
    ctrl.type_text.return_value = True
    ctrl.screenshot.return_value = "iVBORw0KGgoAAAANSUhEUg=="
    ctrl.evaluate.return_value = "hello"
    ctrl.get_url.return_value = "https://example.com"
    ctrl.get_title.return_value = "Example Domain"
    ctrl.get_page_text.return_value = "This is example text."
    ctrl.list_tabs.return_value = [{"id": "abc123", "title": "Tab 1", "url": "https://example.com"}]
    ctrl.new_tab.return_value = "new-tab-id"
    ctrl.close_tab.return_value = True
    ctrl.focus_tab.return_value = True
    ctrl.back.return_value = "Prev Page"
    ctrl.forward.return_value = "Next Page"
    ctrl.reload.return_value = "Reloaded"
    return ctrl


class TestBrowserToolsHermetic(unittest.TestCase):
    """Browser tools work when manually registered via register_browser_tools()."""

    def setUp(self):
        self.ctrl = _make_mock_controller()
        self.factory = lambda **kw: self.ctrl
        self.registry = ToolRegistry()
        register_browser_tools(self.registry, controller_factory=self.factory)

    def test_bare_registry_has_no_browser_tools(self):
        r = ToolRegistry()
        browser = [t.name for t in r.list_tools() if t.name.startswith("browser")]
        self.assertEqual(browser, [])

    def test_manual_registry_has_11_browser_tools(self):
        browser = [t.name for t in self.registry.list_tools() if t.name.startswith("browser")]
        self.assertEqual(len(browser), 11)

    def test_all_tool_classes_exist(self):
        self.assertEqual(len(ALL_BROWSER_TOOLS), 11)

    def test_browser_launch(self):
        result = self.registry.execute("browser_launch", {"headless": True})
        self.assertIn("ready", result)
        self.ctrl.launch.assert_called_once()

    def test_browser_navigate(self):
        result = self.registry.execute("browser_navigate", {"url": "https://example.com"})
        self.assertIn("Example Domain", result)
        self.ctrl.navigate.assert_called_once_with("https://example.com")

    def test_browser_click(self):
        result = self.registry.execute("browser_click", {"selector": "#btn"})
        self.assertIn("Clicked", result)
        self.ctrl.click.assert_called_once_with("#btn")

    def test_browser_click_coordinates(self):
        result = self.registry.execute("browser_click", {"x": 100, "y": 200})
        self.assertIn("Clicked at", result)
        self.ctrl.click_at.assert_called_once_with(100, 200)

    def test_browser_type(self):
        result = self.registry.execute("browser_type", {"selector": "#q", "text": "hello"})
        self.assertIn("Typed into", result)
        self.ctrl.type_text.assert_called_once_with("#q", "hello")

    def test_browser_type_raw(self):
        result = self.registry.execute("browser_type", {"text": "raw keystrokes"})
        self.assertIn("Typed:", result)
        self.ctrl.type_keys.assert_called_once_with("raw keystrokes")

    def test_browser_press_key(self):
        result = self.registry.execute("browser_press_key", {"key": "Enter"})
        self.assertIn("Pressed", result)
        self.ctrl.press_key.assert_called_once_with("Enter")

    def test_browser_scroll(self):
        result = self.registry.execute("browser_scroll", {"direction": "down", "amount": 300})
        self.assertIn("Scrolled", result)
        self.ctrl.scroll.assert_called_once_with("down", 300)

    def test_browser_screenshot(self):
        result = self.registry.execute("browser_screenshot", {})
        self.assertIn("Screenshot captured", result)
        self.ctrl.screenshot.assert_called_once()

    def test_browser_screenshot_save(self):
        result = self.registry.execute("browser_screenshot", {"save_path": "/tmp/test.png"})
        self.assertIn("saved", result)
        self.ctrl.screenshot_to_file.assert_called_once()

    def test_browser_evaluate(self):
        result = self.registry.execute("browser_evaluate", {"expression": "1+1"})
        self.assertEqual(result, "hello")
        self.ctrl.evaluate.assert_called_once_with("1+1")

    def test_browser_get_page_info(self):
        result = self.registry.execute("browser_get_page_info", {})
        self.assertIn("https://example.com", result)
        self.assertIn("Example Domain", result)

    def test_browser_tab_list(self):
        result = self.registry.execute("browser_tab", {"action": "list"})
        self.assertIn("abc123", result)

    def test_browser_tab_new(self):
        result = self.registry.execute("browser_tab", {"action": "new"})
        self.assertIn("new-tab-id", result)

    def test_browser_tab_close(self):
        result = self.registry.execute("browser_tab", {"action": "close", "tab_id": "abc123"})
        self.assertIn("Closed", result)

    def test_browser_tab_focus(self):
        result = self.registry.execute("browser_tab", {"action": "focus", "tab_id": "abc123"})
        self.assertIn("Focused", result)

    def test_browser_back(self):
        result = self.registry.execute("browser_navigation", {"action": "back"})
        self.assertIn("Prev Page", result)

    def test_browser_forward(self):
        result = self.registry.execute("browser_navigation", {"action": "forward"})
        self.assertIn("Next Page", result)

    def test_browser_reload(self):
        result = self.registry.execute("browser_navigation", {"action": "reload"})
        self.assertIn("Reloaded", result)

    def test_no_controller_returns_error(self):
        r = ToolRegistry()
        result = r.execute("browser_navigate", {"url": "https://x.com"}, mode="build")
        self.assertIn("not found", result)


class TestBrowserOverlay(unittest.TestCase):
    """Overlay JS/CSS is valid and messages map correctly."""

    def test_overlay_js_is_valid(self):
        self.assertIn("__harness_show_overlay", OVERLAY_JS)
        self.assertIn("__harness_hide_overlay", OVERLAY_JS)
        self.assertIn("position: fixed", OVERLAY_JS)

    def test_action_messages_cover_all_actions(self):
        expected_actions = {"navigate", "click", "type", "scroll", "screenshot", "evaluate",
                            "select", "hover", "press", "drag", "tab_new", "tab_close",
                            "tab_list", "back", "forward", "reload", "focus", "close",
                            "ready", "error", "thinking"}
        self.assertTrue(expected_actions.issubset(set(ACTION_MESSAGES.keys())))

    def test_js_show_overlay_returns_js(self):
        js = js_show_overlay("click", "Clicking #btn")
        self.assertIn("__harness_show_overlay", js)
        self.assertIn("Clicking #btn", js)

    def test_js_hide_overlay_returns_js(self):
        js = js_hide_overlay(1500)
        self.assertIn("__harness_hide_overlay", js)
        self.assertIn("1500", js)

    def test_get_action_message_default(self):
        msg, icon, color = get_action_message("unknown_action")
        self.assertEqual(icon, "\U0001f916")


if __name__ == "__main__":
    unittest.main()
