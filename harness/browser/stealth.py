"""
Anti-bot hardening for the harness-owned Chrome instance.

Sites (Google News, etc.) flag automation via well-known signals, the biggest
being ``navigator.webdriver === true`` (set whenever Chrome is driven over
CDP) and the ``AutomationControlled`` blink feature. This module:

* adds ``--disable-blink-features=AutomationControlled`` to the Chrome launch
  flags (``STEALTH_LAUNCH_FLAGS``), and
* injects ``STEALTH_JS`` via ``Page.addScriptToEvaluateOnNewDocument`` so every
  new document (navigations included) patches the giveaways before any page
  script runs.

The shims are deliberately conservative — they restore the values a normal
desktop Chrome exposes (``webdriver`` undefined, a ``window.chrome`` object,
a populated plugin list, real languages) without touching anything pages rely
on for rendering. Nothing here bypasses logins, paywalls, or CAPTCHAs; it only
stops the browser from *self-identifying* as automated.

No mitigation is perfect (IP reputation / TLS fingerprinting / behavior also
count), but this removes the #1 programmatic trigger.
"""

# Extra flags appended to the Chrome command line at launch.
STEALTH_LAUNCH_FLAGS = [
    "--disable-blink-features=AutomationControlled",
]

# Runs in the page before any site script. Keep it small, synchronous, and
# exception-free — a throw here would break the document.
STEALTH_JS = r"""
(function() {
  try {
    // The big one: CDP-driven Chrome sets navigator.webdriver = true.
    Object.defineProperty(navigator, 'webdriver', { get: function() { return undefined; } });

    // Normal desktop Chrome exposes window.chrome.runtime.
    if (!window.chrome) { window.chrome = {}; }
    if (!window.chrome.runtime) { window.chrome.runtime = {}; }

    // Headless/automated builds often report zero plugins; fake a normal list.
    // (Only the length/index access is shimmed — checks almost always test
    // `navigator.plugins.length > 0`.)
    try {
      Object.defineProperty(navigator, 'plugins', {
        get: function() { return [1, 2, 3, 4, 5]; }
      });
    } catch (e) {}

    // Ensure a believable language list.
    try {
      if (!navigator.languages || navigator.languages.length === 0) {
        Object.defineProperty(navigator, 'languages', {
          get: function() { return ['en-US', 'en']; }
        });
      }
    } catch (e) {}

    // Permissions API fingerprinting: real users usually have notifications
    // decided already; report a decided state instead of 'prompt'.
    try {
      var origQuery = window.Notification && window.Notification.permission;
      if (origQuery === 'default') {
        Object.defineProperty(window.Notification, 'permission', { get: function() { return 'denied'; } });
      }
    } catch (e) {}
  } catch (e) {}
})();
"""
