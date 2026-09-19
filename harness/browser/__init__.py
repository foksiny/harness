"""Browser automation via Chrome DevTools Protocol (CDP). Chrome-only."""
from harness.browser.controller import BrowserController, find_chrome, DEFAULT_CDP_PORT
from harness.browser.manager import BrowserManager, resolve_headless, screenshots_dir
from harness.browser.stealth import STEALTH_JS, STEALTH_LAUNCH_FLAGS
from harness.browser.overlay import (
    OVERLAY_JS, ACTION_MESSAGES,
    js_show_overlay, js_hide_overlay, get_action_message,
    js_mouse_move, js_mouse_click, js_flash_element, js_element_center, js_mouse_hide,
)

__all__ = [
    "BrowserController",
    "BrowserManager",
    "find_chrome",
    "DEFAULT_CDP_PORT",
    "resolve_headless",
    "screenshots_dir",
    "STEALTH_JS",
    "STEALTH_LAUNCH_FLAGS",
    "OVERLAY_JS",
    "ACTION_MESSAGES",
    "js_show_overlay",
    "js_hide_overlay",
    "get_action_message",
    "js_mouse_move",
    "js_mouse_click",
    "js_mouse_hide",
    "js_flash_element",
    "js_element_center",
]
