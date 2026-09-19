---
name: browser_automation
description: Chrome browser automation via CDP for web development, testing, scraping, and interactive debugging. Launch, navigate, click, type, screenshot, evaluate JS, and manage tabs.
triggers: [browser, chrome, navigate, click, screenshot, scrape, test, debug, web, url, page, dom, selenium, playwright, puppeteer, cdp]
---
# Browser Automation for Harness

Expert guidance for controlling Google Chrome/Chromium via the Chrome DevTools Protocol (CDP). All browser tools share a single persistent session per agent.

## When to Use Browser Tools
- **Web development**: Verify frontend changes, inspect rendered output, debug UI issues
- **Testing**: End-to-end flows, form submission, authentication, user journeys
- **Scraping**: Extract data from JS-heavy sites, SPAs, or authenticated pages
- **Debugging**: Inspect network, console, DOM state; reproduce bugs interactively
- **Documentation**: Capture screenshots of UI for specs, PRs, or bug reports
- **Research**: Navigate docs, search, interact with web apps

## Core Workflow
```
browser_launch        # Start Chrome (visible by default) or connect to existing
browser_navigate      # Go to URL
...interact...        # click, type, scroll, evaluate, tabs, navigation
browser_screenshot    # Capture visual state (auto-saved, shown to you)
browser_get_page_info # Read text/URL/title without screenshot
browser_close         # Clean up when done
```

## Tool Reference

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `browser_launch` | Start Chrome session | `headless` (bool, default false), `port` (int, default 9222), `browser_path` (optional) |
| `browser_navigate` | Load a URL | `url` (string) |
| `browser_click` | Click element | `selector` (CSS) OR `x`+`y` (coordinates) |
| `browser_type` | Type into input | `selector` (CSS), `text` (string) — omit selector for raw keystrokes |
| `browser_press_key` | Press key | `key` (Enter, Tab, Escape, ArrowUp, etc.) |
| `browser_scroll` | Scroll page | `direction` (up/down/left/right), `amount` (pixels, default 500) |
| `browser_screenshot` | Capture page | `full_page` (bool), `save_path` (optional) |
| `browser_evaluate` | Run JS | `expression` (string) |
| `browser_get_page_info` | Read page | — |
| `browser_tab` | Manage tabs | `action` (list/new/close/focus), `tab_id`, `url` |
| `browser_navigation` | History | `action` (back/forward/reload) |
| `browser_close` | End session | — |

## Best Practices

### 1. Launch Once, Reuse Session
```python
# Good: Launch once at start of task
browser_launch(headless=False)
browser_navigate("https://example.com")
browser_click("#login")
browser_type("#password", "secret")
browser_screenshot()

# Bad: Launching repeatedly loses state
browser_launch()
browser_navigate(...)
browser_launch()  # Creates new session, loses login
```

### 2. Prefer Selectors Over Coordinates
- CSS selectors are stable across viewport sizes
- Use `browser_evaluate` to find selectors: `document.querySelectorAll('button')`
- Overlay shows agent cursor — coordinates are for edge cases only

### 3. Wait for Dynamic Content
```python
# Wait for element to appear (up to 5s)
browser_evaluate("""new Promise(r => {
  const el = document.querySelector('.loaded');
  if (el) return r(true);
  new MutationObserver(() => document.querySelector('.loaded') && r(true))
    .observe(document.body, {childList: true, subtree: true});
})""")
```

### 4. Screenshots Are Your Eyes
- `browser_screenshot` saves PNG to `~/.harness/screenshots/` and embeds `[harness:image:path]`
- Vision models see the image directly
- Non-vision models get a text description via Vision Fallback (VFB) if configured
- Use `full_page: true` for long pages

### 5. JavaScript for Complex Operations
```python
# Extract all links
browser_evaluate("Array.from(document.querySelectorAll('a')).map(a => a.href)")

# Fill and submit form
browser_evaluate("""
  document.querySelector('#email').value = 'test@example.com';
  document.querySelector('form').submit();
""")

# Scroll to bottom
browser_evaluate("window.scrollTo(0, document.body.scrollHeight)")
```

### 6. Tab Management for Multi-Page Flows
```python
browser_tab(action="new", url="https://admin.site.com")
# ... work in new tab ...
browser_tab(action="focus", tab_id="original-tab-id")
```

### 7. Headless vs Visible
- **Visible (default)**: See the browser, debug visually, Xvfb auto-starts on headless Linux
- **Headless**: Background CI/CD, faster, no display needed
- Set `headless: true` in `browser_launch` for servers

### 8. Clean Up
Always call `browser_close()` when the task is complete to free the Chrome process and CDP port.

## Common Patterns

### Login Flow
```python
browser_launch()
browser_navigate("https://app.example.com/login")
browser_type("#email", "user@example.com")
browser_type("#password", "password123")
browser_click("button[type=submit]")
browser_screenshot()  # Verify dashboard loaded
```

### Form Testing
```python
browser_navigate("https://app.example.com/form")
browser_type("#name", "Test User")
browser_select_option("#country", "US")  # via evaluate
browser_click("#submit")
browser_wait_for(".success-message")  # via evaluate
browser_screenshot()
```

### Data Extraction
```python
browser_navigate("https://example.com/data")
data = browser_evaluate("""
  Array.from(document.querySelectorAll('.item')).map(el => ({
    title: el.querySelector('h3').innerText,
    price: el.querySelector('.price').innerText,
    link: el.querySelector('a').href
  }))
""")
```

### Visual Regression
```python
browser_navigate("https://staging.example.com")
browser_screenshot(save_path="staging_home.png")
# Compare with production screenshot
```

## Anti-Patterns to Avoid
- ❌ Launching browser for every action — session is persistent
- ❌ Using coordinates when selector works — brittle
- ❌ Not waiting for async content — race conditions
- ❌ Forgetting `browser_close()` — orphaned Chrome processes
- ❌ Assuming page is ready after `navigate` — use `wait_for` pattern
- ❌ Using browser for static HTML — use `web_search` or `fetch` instead

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Browser not launched" | Call `browser_launch` first |
| "Element not found" | Check selector in `browser_evaluate("document.querySelector('...')")`, wait for dynamic content |
| "CDP port in use" | Change `port` in `browser_launch` or close existing Chrome |
| Screenshot empty | Page not ready — wait or scroll first |
| "No display" on Linux | Xvfb auto-starts; set `headless: true` if unavailable |
| Session lost | Each agent gets own `BrowserManager`; subagents need their own launch |

## Vision Fallback (VFB) for Non-Vision Models
When you call `browser_screenshot` and the model lacks vision:
1. Screenshot is saved to disk
2. If `vfb_provider` + `vfb_model` configured in Harness config, the VFB model describes the image
3. Description is injected into the conversation as text
4. You can ask follow-up questions about the screenshot content

Configure in `~/.harness/config.json` or env:
```json
{
  "vfb_provider": "anthropic",
  "vfb_model": "claude-3-5-sonnet-20241022"
}
```

## Security Notes
- Browser tools require `browser` permission (default: ask)
- Cookies/localStorage persist in the throwaway profile per launch
- No network interception or request modification — read/write only
- Stealth scripts applied to reduce bot detection