"""
Hermetic tests for Chrome web control: tools, manager, markers, permissions.

Browser tools ARE auto-registered in ToolRegistry by default (behind a shared
BrowserManager). Never touches a real browser — mock controllers via injected
seams; live-Chrome verification lives in /tmp/opencode/*_smoke.py scripts.
"""
import os
import base64
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from harness.tools import ToolRegistry
from harness.core.modes import Mode
from harness.core.permissions import PermissionManager, PermissionLevel
from harness.core.attachments import split_tool_images
from harness.tools.browser import (
    BrowserLaunchTool, BrowserNavigateTool, BrowserClickTool,
    BrowserTypeTool, BrowserPressKeyTool, BrowserScrollTool,
    BrowserScreenshotTool, BrowserEvaluateTool, BrowserGetPageInfoTool,
    BrowserTabTool, BrowserNavigationTool, BrowserCloseTool,
    register_browser_tools, ALL_BROWSER_TOOLS,
)
from harness.browser.manager import BrowserManager, resolve_headless, screenshots_dir
from harness.browser.controller import BrowserController, find_chrome
from harness.browser.stealth import STEALTH_JS, STEALTH_LAUNCH_FLAGS
from harness.browser.overlay import (
    OVERLAY_JS, ACTION_MESSAGES, js_show_overlay, js_hide_overlay, get_action_message,
    js_mouse_move, js_mouse_click, js_flash_element, js_element_center, js_mouse_hide,
)

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwAD"
    "hgGAWjR9awAAAABJRU5ErkJggg=="
)


def _make_mock_controller(png_path=None):
    ctrl = MagicMock()
    ctrl.launch.return_value = "Chrome launched (google-chrome)"
    ctrl.navigate.return_value = "Example Domain"
    ctrl.click.return_value = True
    ctrl.type_text.return_value = True
    ctrl.screenshot.return_value = TINY_PNG_B64
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
    if png_path:
        def _write(path, full_page=False):
            with open(path, "wb") as f:
                f.write(base64.b64decode(TINY_PNG_B64))
            return path
        ctrl.screenshot_to_file.side_effect = _write
    else:
        ctrl.screenshot_to_file.return_value = "/tmp/shot.png"
    return ctrl


class TestDefaultRegistration(unittest.TestCase):

    def test_default_registry_has_12_browser_tools(self):
        r = ToolRegistry()
        browser = sorted(t.name for t in r.list_tools() if t.name.startswith("browser"))
        self.assertEqual(len(browser), 12)
        self.assertIn("browser_launch", browser)
        self.assertIn("browser_screenshot", browser)
        self.assertIn("browser_close", browser)

    def test_browser_manager_shared(self):
        r = ToolRegistry()
        self.assertIsNotNone(r.browser_manager)
        factory = r.tools["browser_launch"]._factory
        self.assertIs(getattr(factory, "__self__", None), r.browser_manager)

    def test_browser_disabled_registry_has_none(self):
        r = ToolRegistry(browser_enabled=False)
        browser = [t.name for t in r.list_tools() if t.name.startswith("browser")]
        self.assertEqual(browser, [])
        self.assertIsNone(r.browser_manager)

    def test_all_tool_classes_exist(self):
        self.assertEqual(len(ALL_BROWSER_TOOLS), 12)

    def test_action_types_and_read_only_declared(self):
        for tool_cls in ALL_BROWSER_TOOLS:
            tool = tool_cls(controller_factory=lambda **kw: None)
            self.assertIn(tool.action_type, ("browser", "browser_read"), tool.name)
        read_only = {t.name for t in ALL_BROWSER_TOOLS
                     if t(controller_factory=lambda **kw: None).is_read_only}
        self.assertEqual(read_only, {"browser_screenshot", "browser_get_page_info"})
        readers = {t.name for t in ALL_BROWSER_TOOLS
                   if t(controller_factory=lambda **kw: None).action_type == "browser_read"}
        self.assertEqual(readers, {"browser_screenshot", "browser_get_page_info"})


class TestBrowserToolsHermetic(unittest.TestCase):
    """Tools work through the injected factory seam with a mock controller."""

    def setUp(self):
        self.ctrl = _make_mock_controller()
        self.factory = lambda **kw: self.ctrl
        self.registry = ToolRegistry(browser_enabled=False)
        register_browser_tools(
            self.registry, controller_factory=self.factory,
            permission_manager=PermissionManager(PermissionLevel.FULL),
        )

    def test_browser_launch(self):
        result = self.registry.execute("browser_launch", {"headless": True})
        self.assertIn("ready", result)
        self.ctrl.launch.assert_called_once_with(browser_path=None)

    def test_browser_launch_opens_visible_window_by_default(self):
        seen = {}

        def _recording_factory(**kw):
            seen.update(kw)
            return self.ctrl

        r = ToolRegistry(browser_enabled=False)
        register_browser_tools(r, controller_factory=_recording_factory,
                               permission_manager=PermissionManager(PermissionLevel.FULL))
        result = r.execute("browser_launch", {})
        self.assertIn("ready", result)
        self.assertIs(seen.get("headless"), False)
        tool = r.get("browser_launch")
        self.assertIs(tool.parameters["properties"]["headless"]["default"], False)

    def test_browser_launch_chrome_only_copy(self):
        tool = self.registry.get("browser_launch")
        self.assertIn("Chrome", tool.description)
        self.assertNotIn("Firefox", tool.description)

    def test_browser_launch_surfaces_manager_note(self):
        self.ctrl.manager_note = "no display detected — running headless"
        result = self.registry.execute("browser_launch", {})
        self.assertIn("running headless", result)

    def test_browser_navigate(self):
        result = self.registry.execute("browser_navigate", {"url": "https://example.com"})
        self.assertIn("Example Domain", result)
        self.ctrl.navigate.assert_called_once_with("https://example.com")

    def test_browser_click(self):
        result = self.registry.execute("browser_click", {"selector": "#btn"})
        self.assertIn("Clicked", result)
        self.ctrl.click.assert_called_once_with("#btn", None)

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
        self.ctrl.scroll.assert_called_once_with("down", 300, None, None)

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

    def test_browser_close(self):
        result = self.registry.execute("browser_close", {})
        self.assertIn("closed", result.lower())
        self.ctrl.close.assert_called_once()

    def test_no_controller_launch_error(self):
        r = ToolRegistry(browser_enabled=False)
        register_browser_tools(r, controller_factory=None,
                               permission_manager=PermissionManager(PermissionLevel.FULL))
        self.assertIn("No browser controller", r.execute("browser_launch", {}))

    def test_not_launched_error(self):
        r = ToolRegistry(browser_enabled=False)
        register_browser_tools(r, controller_factory=lambda **kw: None,
                               permission_manager=PermissionManager(PermissionLevel.FULL))
        self.assertIn("browser_launch first", r.execute("browser_navigate", {"url": "https://x.com"}))


class TestBrowserScreenshotMarker(unittest.TestCase):

    def test_screenshot_embeds_marker_and_context(self):
        with tempfile.TemporaryDirectory() as td:
            png = os.path.join(td, "shot.png")
            ctrl = _make_mock_controller(png_path=png)
            tool = BrowserScreenshotTool(
                controller_factory=lambda **kw: ctrl,
                permission_manager=PermissionManager(PermissionLevel.FULL),
            )
            out = tool.execute(save_path=png)
            self.assertIn(f"Screenshot saved to: {png}", out)
            self.assertIn("URL: https://example.com", out)
            self.assertIn("Title: Example Domain", out)
            self.assertIn(f"[harness:image:{png}]", out)
            self.assertTrue(os.path.isfile(png))
            clean, paths = split_tool_images(out)
            self.assertEqual(paths, [png])
            self.assertNotIn("[harness:image:", clean)

    def test_screenshot_auto_path_under_harness_dir(self):
        with tempfile.TemporaryDirectory() as td:
            real_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                ctrl = MagicMock()
                written = {}

                def _write(path, full_page=False):
                    with open(path, "wb") as f:
                        f.write(base64.b64decode(TINY_PNG_B64))
                    written["path"] = path
                    return path

                ctrl.screenshot_to_file.side_effect = _write
                ctrl.get_url.return_value = "https://example.com"
                ctrl.get_title.return_value = "T"
                tool = BrowserScreenshotTool(
                    controller_factory=lambda **kw: ctrl,
                    permission_manager=PermissionManager(PermissionLevel.FULL),
                )
                out = tool.execute()
                self.assertTrue(written["path"].startswith(os.path.join(td, ".harness", "screenshots")))
                self.assertTrue(written["path"].endswith(".png"))
                self.assertIn(f"[harness:image:{written['path']}]", out)
            finally:
                if real_home is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = real_home


class TestSplitToolImages(unittest.TestCase):

    def test_no_markers(self):
        clean, paths = split_tool_images("plain text")
        self.assertEqual((clean, paths), ("plain text", []))

    def test_missing_file_dropped(self):
        clean, paths = split_tool_images("a\n[harness:image:/no/such/file.png]\nb")
        self.assertEqual(paths, [])
        self.assertNotIn("harness:image", clean)

    def test_multiple_markers(self):
        with tempfile.TemporaryDirectory() as td:
            p1 = os.path.join(td, "a.png")
            p2 = os.path.join(td, "b.png")
            for p in (p1, p2):
                with open(p, "wb") as f:
                    f.write(b"x")
            text = f"Shot 1\n[harness:image:{p1}]\nShot 2\n[harness:image:{p2}]"
            clean, paths = split_tool_images(text)
            self.assertEqual(paths, [p1, p2])
            self.assertIn("Shot 1", clean)
            self.assertIn("Shot 2", clean)


class TestBrowserPermissions(unittest.TestCase):

    def _registry(self, level, approver=None):
        pm = PermissionManager(level, approver_callback=approver)
        r = ToolRegistry(browser_enabled=False)
        register_browser_tools(r, controller_factory=lambda **kw: _make_mock_controller(),
                               permission_manager=pm)
        return r, pm

    def test_secure_prompts_for_navigation(self):
        calls = []
        r, _ = self._registry(PermissionLevel.SECURE,
                              approver=lambda m, d: (calls.append(d), False)[1])
        out = r.execute("browser_navigate", {"url": "https://example.com"})
        self.assertIn("rejected", out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "browser_navigate")

    def test_secure_allows_prompted_navigation(self):
        r, _ = self._registry(PermissionLevel.SECURE, approver=lambda m, d: True)
        out = r.execute("browser_navigate", {"url": "https://example.com"})
        self.assertIn("Example Domain", out)

    def test_secure_auto_approves_screenshot_without_prompt(self):
        calls = []
        r, _ = self._registry(PermissionLevel.SECURE,
                              approver=lambda m, d: (calls.append(d), True)[1])
        out = r.execute("browser_screenshot", {})
        self.assertIn("Screenshot saved to", out)
        self.assertEqual(calls, [])

    def test_default_allows_browser_without_prompt(self):
        calls = []
        r, _ = self._registry(PermissionLevel.DEFAULT,
                              approver=lambda m, d: (calls.append(d), True)[1])
        out = r.execute("browser_click", {"selector": "#a"})
        self.assertIn("Clicked", out)
        self.assertEqual(calls, [])

    def test_plan_mode_blocks_mutations_but_allows_observation(self):
        ctrl = _make_mock_controller()
        r = ToolRegistry(browser_enabled=False)
        register_browser_tools(r, controller_factory=lambda **kw: ctrl,
                               permission_manager=PermissionManager(PermissionLevel.FULL))
        for name, args in [
            ("browser_launch", {}),
            ("browser_click", {"selector": "#a"}),
            ("browser_type", {"text": "hi"}),
            ("browser_press_key", {"key": "Enter"}),
            ("browser_scroll", {}),
            ("browser_evaluate", {"expression": "1"}),
            ("browser_tab", {"action": "list"}),
            ("browser_navigation", {"action": "back"}),
            ("browser_close", {}),
        ]:
            out = r.execute(name, args, Mode.PLAN)
            self.assertIn("forbidden in PLAN", out, name)
        # Observation stays available in PLAN.
        self.assertIn("Example Domain",
                      r.execute("browser_navigate", {"url": "https://example.com"}, Mode.PLAN))
        self.assertIn("https://example.com",
                      r.execute("browser_get_page_info", {}, Mode.PLAN))
        self.assertIn("Screenshot saved to",
                      r.execute("browser_screenshot", {}, Mode.PLAN))


class TestBrowserManager(unittest.TestCase):

    def test_factory_none_when_not_launched(self):
        m = BrowserManager()
        self.assertIsNone(m.controller_factory())
        self.assertFalse(m.is_live())

    def test_factory_creates_controller_with_params(self):
        m = BrowserManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            c = m.controller_factory(port=9399, headless=True)
        self.assertIsInstance(c, BrowserController)
        self.assertEqual(c.port, 9399)
        self.assertTrue(c.headless)
        # Not launched => usage factory still returns None.
        self.assertIsNone(m.controller_factory())

    def test_factory_reuses_live_controller(self):
        m = BrowserManager()
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        ctrl.port = 9222
        m._controller = ctrl
        self.assertIs(m.controller_factory(), ctrl)
        self.assertTrue(m.is_live())

    def test_factory_replaces_dead_controller(self):
        m = BrowserManager()
        dead = MagicMock()
        dead.is_connected.return_value = False
        m._controller = dead
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            c = m.controller_factory(port=9222, headless=True)
        self.assertIsNot(dead, c)
        dead.close.assert_called_once()

    def test_resolve_headless_explicit_true(self):
        with patch.dict(os.environ, {"DISPLAY": ":1"}):
            self.assertEqual(resolve_headless(True), (True, ""))

    def test_resolve_headless_visible_with_display(self):
        with patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
            self.assertEqual(resolve_headless(False), (False, ""))
            self.assertEqual(resolve_headless(None), (False, ""))

    def test_resolve_headless_no_display_falls_back(self):
        with patch.dict(os.environ, {}, clear=True):
            headless, note = resolve_headless(False)
            self.assertTrue(headless)
            self.assertIn("headless", note)
            headless, _ = resolve_headless(None)
            self.assertTrue(headless)

    def test_screenshots_dir_under_home(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HOME": td}):
                d = screenshots_dir()
                self.assertTrue(d.startswith(td))
                self.assertTrue(os.path.isdir(d))

    def test_find_chrome_none_when_absent(self):
        with patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=None):
            self.assertIsNone(find_chrome())


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
        self.assertEqual(icon, "🤖")

    def test_overlay_js_defines_mouse_indicator(self):
        self.assertIn("__harness_cursor", OVERLAY_JS)
        self.assertIn("__harness_mouse_move", OVERLAY_JS)
        self.assertIn("__harness_mouse_click", OVERLAY_JS)
        self.assertIn("__harness_mouse_hide", OVERLAY_JS)
        self.assertIn("__harness_flash", OVERLAY_JS)
        self.assertIn("__harness_ripple", OVERLAY_JS)  # click ripple keyframes
        # The cursor element is only appended when document.body exists.
        self.assertIn("document.body", OVERLAY_JS)
        # The ring fades out after a quiet period instead of lingering.
        self.assertIn("2500", OVERLAY_JS)
        # Registered via Page.addScriptToEvaluateOnNewDocument: runs at
        # document creation, so it must skip iframes and wait for <body>.
        self.assertIn("window !== window.top", OVERLAY_JS)
        self.assertIn("setTimeout(__harness_boot", OVERLAY_JS)

    def test_js_mouse_builders(self):
        self.assertEqual(js_mouse_move(10, 20), "window.__harness_mouse_move(10, 20);")
        self.assertEqual(js_mouse_click(5, 7), "window.__harness_mouse_click(5, 7);")
        self.assertEqual(js_flash_element("#q", 500), 'window.__harness_flash("#q", 500);')
        self.assertEqual(js_mouse_hide(), "window.__harness_mouse_hide();")
        center = js_element_center("input#q")
        self.assertIn("getBoundingClientRect", center)
        self.assertIn('"input#q"', center)


class TestStealth(unittest.TestCase):
    """Anti-bot hardening: shim JS content and where the controller applies it."""

    def test_shim_hides_webdriver(self):
        self.assertIn("navigator", STEALTH_JS)
        self.assertIn("'webdriver'", STEALTH_JS)
        self.assertIn("undefined", STEALTH_JS)
        self.assertNotIn("return true", STEALTH_JS)

    def test_shim_patches_chrome_object_and_plugins(self):
        self.assertIn("window.chrome", STEALTH_JS)
        self.assertIn("plugins", STEALTH_JS)
        self.assertIn("languages", STEALTH_JS)

    def test_launch_flags_disable_automation_controlled(self):
        self.assertIn("--disable-blink-features=AutomationControlled", STEALTH_LAUNCH_FLAGS)

    def test_controller_launch_appends_stealth_flags(self):
        c = BrowserController(port=0, headless=True)
        captured = {}

        def _fake_popen(args, **kwargs):
            captured["args"] = args
            m = MagicMock()
            m.poll.return_value = 0
            return m

        with patch.object(BrowserController, "_check_cdp",
                          side_effect=[False, True]), \
             patch("subprocess.Popen", side_effect=_fake_popen), \
             patch.object(BrowserController, "connect", side_effect=RuntimeError("stop here")), \
             patch("time.sleep"):
            try:
                c.launch()
            except RuntimeError as e:
                self.assertIn("stop here", str(e))
            args = captured["args"]
            self.assertIn("--disable-blink-features=AutomationControlled", args)
            self.assertIn("--headless=new", args)

    def _stubbed_controller(self):
        """A connected-enough controller for send/eval assertions."""
        c = BrowserController(port=0, headless=True)
        c._connected = True
        c._ws = object()  # _send only checks truthiness
        sent, evals = [], []

        async def _send(method, params, timeout=30):
            sent.append((method, params))
            return {}

        async def _eval(expression, timeout=10):
            evals.append(expression)
            if "document.readyState" in expression:
                return "complete"
            return True

        c._send = _send
        c._eval = _eval
        return c, sent, evals

    def test_apply_stealth_registers_new_document_script(self):
        c, sent, _ = self._stubbed_controller()
        c._apply_stealth()
        methods = [m for m, _ in sent]
        # Both the anti-bot shim AND the overlay (with its mouse indicator)
        # must ride along on every new document — a post-navigation manual
        # inject alone loses the race with the document commit.
        sources = [p.get("source", "") for m, p in sent
                   if m == "Page.addScriptToEvaluateOnNewDocument"]
        self.assertEqual(sources, [STEALTH_JS, OVERLAY_JS])
        self.assertIn("Emulation.setUserAgentOverride", methods)

    def test_click_shows_cursor_ripple(self):
        c, sent, evals = self._stubbed_controller()
        center = None

        async def _center_eval(expression, timeout=10):
            if "__harness_mouse_click" in expression:
                evals.append(("click_marker", expression))
                return True
            # Check for the main click function first (contains querySelectorAll)
            if "querySelectorAll" in expression:
                return {"found": True, "clickedIndex": 0, "totalMatches": 1, "clickableCount": 1}
            # js_element_center uses getBoundingClientRect
            if "getBoundingClientRect" in expression:
                return [40, 60]
            evals.append(("misc", expression))
            # Other document.querySelector uses
            if "document.querySelector" in expression:
                return True
            return True

        async def _center_send(method, params, timeout=30):
            sent.append((method, params))
            return {}

        c._eval = _center_eval
        c._send = _center_send
        ok = c.click("#btn")
        self.assertTrue(ok)
        self.assertIn(("click_marker", "window.__harness_mouse_click(40, 60);"), evals)

    def test_click_at_shows_cursor_ripple(self):
        c, sent, evals = self._stubbed_controller()
        with patch("time.sleep"):
            c.click_at(100, 200)
        markers = [e for e in evals if "__harness_mouse_click" in str(e)]
        self.assertTrue(any("100, 200" in m for m in markers))

    def test_type_text_flashes_target(self):
        c, sent, evals = self._stubbed_controller()

        async def _type_eval(expression, timeout=10):
            evals.append(expression)
            if "getBoundingClientRect" in expression:
                return None  # element center unavailable — flash still fires
            return True

        c._eval = _type_eval
        with patch("time.sleep"):
            ok = c.type_text("#q", "hello")
        self.assertTrue(ok)
        self.assertTrue(any("__harness_flash" in e and '"#q"' in e for e in evals))

    def test_navigate_waits_for_ready_state_and_reinjects_overlay(self):
        c, sent, evals = self._stubbed_controller()

        async def _nav_eval(expression, timeout=10):
            evals.append(expression)
            if "document.readyState" in expression:
                return "complete"
            if "document.title" in expression:
                return "T"
            return True

        async def _nav_send(method, params, timeout=30):
            sent.append((method, params))
            return {}

        c._eval = _nav_eval
        c._send = _nav_send
        title = c.navigate("https://example.com")
        self.assertEqual(title, "T")
        self.assertIn(("Page.navigate", {"url": "https://example.com"}), sent)
        self.assertTrue(any("document.readyState" in e for e in evals))
        # Overlay JS must be re-injected after navigation wipes page state.
        self.assertTrue(any("__harness_show_overlay" in e for e in evals))
        self.assertTrue(any("document.getElementById('__harness_overlay')" in e or
                            "__harness_overlay_loaded" in e for e in evals))

    def test_screenshot_hides_overlay_and_cursor(self):
        c, sent, evals = self._stubbed_controller()
        shot_evals = []

        async def _shot_eval(expression, timeout=10):
            shot_evals.append(expression)
            return {"data": "aGVsbG8="}

        async def _shot_send(method, params, timeout=30):
            sent.append((method, params))
            return {"data": "aGVsbG8="}

        c._eval = _shot_eval
        c._send = _shot_send
        with patch("time.sleep"):
            data = c.screenshot()
        self.assertEqual(data, "aGVsbG8=")
        # The pre-capture cleanup must hide both the banner and the cursor ring.
        self.assertTrue(any("__harness_mouse_hide" in e for e in shot_evals))
        self.assertTrue(any("__harness_overlay" in e for e in shot_evals))
        self.assertIn(("Page.captureScreenshot", {"format": "png"}), sent)


if __name__ == "__main__":
    unittest.main()
