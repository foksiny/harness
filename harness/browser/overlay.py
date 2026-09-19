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
/* Agent mouse cursor: a glowing ring + dot that glides to wherever the
   model is pointing, so a human watcher can follow along. */
#__harness_cursor {
  position: fixed;
  left: 0;
  top: 0;
  width: 26px;
  height: 26px;
  margin: -13px 0 0 -13px;
  border: 2.5px solid #22d3ee;
  border-radius: 50%;
  box-shadow: 0 0 12px rgba(34, 211, 238, 0.9), inset 0 0 6px rgba(34, 211, 238, 0.6);
  z-index: 2147483646;
  pointer-events: none;
  opacity: 0;
  transition: transform 0.18s ease-out, opacity 0.2s ease;
  will-change: transform;
}
#__harness_cursor::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 5px;
  height: 5px;
  margin: -2.5px 0 0 -2.5px;
  border-radius: 50%;
  background: #22d3ee;
  box-shadow: 0 0 8px rgba(34, 211, 238, 1);
}
#__harness_cursor.__harness_on { opacity: 1; }
/* Click ripple: expanding ring that bursts out of the cursor and fades. */
#__harness_cursor.__harness_click { animation: __harness_ripple 0.55s ease-out; }
@keyframes __harness_ripple {
  0%   { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0.9), 0 0 12px rgba(34, 211, 238, 0.9); }
  100% { box-shadow: 0 0 0 26px rgba(34, 211, 238, 0), 0 0 12px rgba(34, 211, 238, 0); }
}
/* Element flash: brief neon outline around the field/link being used. */
.__harness_flash {
  outline: 3px solid #22d3ee !important;
  outline-offset: 2px !important;
  box-shadow: 0 0 14px rgba(34, 211, 238, 0.8) !important;
  transition: outline-color 0.3s ease, box-shadow 0.3s ease !important;
}
"""

OVERLAY_JS = r"""
(function() {
  // Only the top frame gets the banner/cursor — never duplicate inside iframes.
  if (window !== window.top) return;
  if (window.__harness_overlay_loaded) return;

  // May run at document creation (Page.addScriptToEvaluateOnNewDocument)
  // before <body> exists — poll until it does, then boot exactly once.
  function __harness_boot() {
    if (window.__harness_overlay_loaded) return;
    if (!document.body || !document.head) {
      setTimeout(__harness_boot, 30);
      return;
    }
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

    // ── Agent mouse indicator ──────────────────────────────────────
    var cursor = document.createElement('div');
    cursor.id = '__harness_cursor';
    document.body.appendChild(cursor);
    var cursorFade = null;

    window.__harness_mouse_move = function(x, y) {
      if (!cursor) return false;
      cursor.classList.add('__harness_on');
      cursor.style.transform = 'translate(' + x + 'px,' + y + 'px)';
      // Fade out after a quiet period so the ring never lingers over content.
      if (cursorFade) clearTimeout(cursorFade);
      cursorFade = setTimeout(function() { cursor.classList.remove('__harness_on'); }, 2500);
      return true;
    };

    window.__harness_mouse_click = function(x, y) {
      if (!window.__harness_mouse_move(x, y)) return false;
      cursor.classList.remove('__harness_click');
      // Force reflow so the ripple animation restarts on rapid clicks.
      void cursor.offsetWidth;
      cursor.classList.add('__harness_click');
      return true;
    };

    // Hide the cursor immediately (called before screenshots so the model
    // receives a clean page — the human watching the live window still sees it).
    window.__harness_mouse_hide = function() {
      if (!cursor) return false;
      if (cursorFade) clearTimeout(cursorFade);
      cursor.classList.remove('__harness_on', '__harness_click');
      return true;
    };

    // Briefly outline an element (input being typed into, link hovered, ...).
    window.__harness_flash = function(selector, ms) {
      try {
        var target = document.querySelector(selector);
        if (!target) return false;
        target.classList.add('__harness_flash');
        setTimeout(function() { target.classList.remove('__harness_flash'); }, ms || 900);
        return true;
      } catch (e) { return false; }
    };
  }
  __harness_boot();
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


def js_mouse_move(x: int, y: int) -> str:
    """Return JS that glides the agent cursor ring to (x, y)."""
    return f"window.__harness_mouse_move({int(x)}, {int(y)});"


def js_mouse_click(x: int, y: int) -> str:
    """Return JS that moves the cursor ring to (x, y) and bursts a ripple."""
    return f"window.__harness_mouse_click({int(x)}, {int(y)});"


def js_mouse_hide() -> str:
    """Return JS that hides the agent cursor (used before screenshots)."""
    return "window.__harness_mouse_hide();"


def js_flash_element(selector: str, ms: int = 900) -> str:
    """Return JS that briefly outlines the element matching selector."""
    return f"window.__harness_flash({json.dumps(selector)}, {int(ms)});"


def js_element_center(selector: str) -> str:
    """Return a JS expression evaluating to [cx, cy] viewport center of the
    element (or null when missing)."""
    return f"""(function() {{
        var el = document.querySelector({json.dumps(selector)});
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return [r.x + r.width / 2, r.y + r.height / 2];
    }})()"""
