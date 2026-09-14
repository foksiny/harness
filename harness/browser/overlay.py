"""
Browser overlay injector — adds a visible status banner inside the page
so the user always knows what the agent is doing.
"""
import json

OVERLAY_CSS = r"""
#__harness_overlay {
  position: fixed;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2147483647;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 20px;
  border-radius: 8px;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 4px 24px rgba(0,0,0,0.25), 0 0 0 1px rgba(255,255,255,0.1);
  pointer-events: none;
  transition: opacity 0.25s ease, transform 0.25s ease, background 0.3s ease;
  opacity: 0;
  max-width: 80vw;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
#__harness_overlay.__harness_visible {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
#__harness_overlay.__harness_hidden {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}
#__harness_overlay .__harness_icon {
  display: inline-block;
  margin-right: 6px;
  font-style: normal;
}
"""

OVERLAY_JS = r"""
(function() {
  if (window.__harness_overlay_loaded) return;
  window.__harness_overlay_loaded = true;

  var style = document.createElement('style');
  style.textContent = %s;
  document.head.appendChild(style);

  var el = document.createElement('div');
  el.id = '__harness_overlay';
  el.className = '__harness_hidden';
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');
  document.body.appendChild(el);

  var hideTimer = null;

  window.__harness_show_overlay = function(message, icon, color) {
    if (!el) return;
    icon = icon || '🤖';
    color = color || '#1a1a2e';
    el.innerHTML = '<span class="__harness_icon">' + icon + '</span> ' + message;
    el.style.background = color;
    el.style.color = '#fff';
    el.className = '__harness_visible';
    if (hideTimer) clearTimeout(hideTimer);
  };

  window.__harness_hide_overlay = function(delay) {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function() {
      if (el) el.className = '__harness_hidden';
    }, delay || 2000);
  };

  window.__harness_pulse_overlay = function(message, icon, color) {
    window.__harness_show_overlay(message, icon, color);
    // Keep alive while pulsing — caller re-invokes to sustain
  };
})();
""" % json.dumps(OVERLAY_CSS)


# Message templates for different actions
ACTION_MESSAGES = {
    "navigate":     ("Navigating…",         "🌐", "#0f3460"),
    "click":        ("Clicking…",           "👆", "#16213e"),
    "type":         ("Typing…",             "⌨️",  "#1a1a2e"),
    "scroll":       ("Scrolling…",          "📜", "#1a1a2e"),
    "screenshot":   ("Taking screenshot…",  "📸", "#0f3460"),
    "evaluate":     ("Running JS…",         "⚙️",  "#1a1a2e"),
    "select":       ("Selecting…",          "📋", "#1a1a2e"),
    "hover":        ("Hovering…",           "🖱️",  "#1a1a2e"),
    "press":        ("Pressing key…",       "⌨️",  "#1a1a2e"),
    "drag":         ("Dragging…",           "🤝", "#1a1a2e"),
    "tab_new":      ("Opening tab…",        "➕", "#0f3460"),
    "tab_close":    ("Closing tab…",        "➖", "#533483"),
    "tab_list":     ("Listing tabs…",       "📑", "#1a1a2e"),
    "back":         ("Going back…",         "◀️",  "#1a1a2e"),
    "forward":      ("Going forward…",      "▶️",  "#1a1a2e"),
    "reload":       ("Reloading…",          "🔄", "#1a1a2e"),
    "focus":        ("Focusing tab…",       "🎯", "#0f3460"),
    "close":        ("Closing browser…",    "🛑", "#533483"),
    "ready":        ("Browser ready",       "✅", "#1b4332"),
    "error":        ("Error",               "❌", "#6a040f"),
    "thinking":     ("Thinking…",           "💭", "#1a1a2e"),
}


def get_action_message(action: str) -> tuple:
    """Return (message, icon, color) for a browser action."""
    return ACTION_MESSAGES.get(action, (action, "🤖", "#1a1a2e"))


def js_show_overlay(action: str, custom_message: str = None) -> str:
    """Return JS that shows the overlay for a given action."""
    msg, icon, color = get_action_message(action)
    if custom_message:
        msg = custom_message
    return f"window.__harness_show_overlay({json.dumps(msg)}, {json.dumps(icon)}, {json.dumps(color)});"


def js_hide_overlay(delay_ms: int = 2000) -> str:
    """Return JS that hides the overlay after a delay."""
    return f"window.__harness_hide_overlay({delay_ms});"
