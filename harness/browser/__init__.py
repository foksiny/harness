"""Browser automation via Chrome DevTools Protocol (CDP)."""
from harness.browser.controller import BrowserController
from harness.browser.overlay import (
    OVERLAY_JS, ACTION_MESSAGES,
    js_show_overlay, js_hide_overlay, get_action_message,
)

__all__ = [
    "BrowserController",
    "OVERLAY_JS",
    "ACTION_MESSAGES",
    "js_show_overlay",
    "js_hide_overlay",
    "get_action_message",
]
