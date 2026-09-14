---
name: computer_use
description: Desktop interaction, GUI automation, browser control, screenshot analysis, visual debugging, and screen-based task execution.
triggers: [screen, screenshot, capture, click, mouse, computer, gui, desktop, windows, webdev, website, ui, drag, scroll, browser, web development, frontend, visual, layout, css, html, inspect, observe, app, application, window, tab, navigate, popup, dialog, menu, toolbar, taskbar, clipboard, copy, paste, hotkey, shortcut, keyboard, type, input, render, pixel, mockup, wireframe, prototype, design, styling, alignment, spacing, responsive, flexbox, grid]
---
# Computer Use for Harness

Expert discipline for interacting with desktops, GUIs, browsers, and visual interfaces through structured screen capture, vision analysis, and batched input control.

## Core Principles
1. **Always Look First**:
   - Use `screen_capture` or `screen_analyze` BEFORE any click/type/scroll action.
   - Understand what is on screen before interacting with it.
   - Verify results by capturing again AFTER actions complete.
2. **Batch Actions for Efficiency**:
   - Group related mouse/keyboard actions into a single `computer_control` call.
   - Order actions deliberately: move before click, focus before type, scroll before read.
3. **Safety & Permissions**:
   - `screen_capture` and `screen_analyze` are read-only and always safe.
   - `computer_control` mutates the desktop and requires permission approval.
   - Use the smallest number of input actions needed to accomplish the goal.

## Workflow Pattern
1. **Observe**: `screen_capture` to get current visual state.
2. **Understand**: `screen_analyze` to identify elements, text, layout, errors.
3. **Act**: `computer_control` with batched actions (move, click, type, keys, scroll).
4. **Verify**: `screen_capture` again to confirm the action had the intended effect.
5. **Iterate**: Repeat observe-understand-act-verify until the task is complete.

## Web Development & Visual Creation
When building or debugging visual interfaces:
- Capture screenshots of the running app at each significant change.
- Use `screen_analyze` to identify layout bugs, alignment issues, color problems.
- For browser-based apps: use `computer_control` to navigate, inspect elements, test interactions.
- Compare visual output against design requirements using `screen_analyze` with a targeted question.
- Document visual changes by saving annotated screenshots to `~/.harness/screenshots/`.

## Tool Reference
| Tool | Purpose | Mutates? | Approval? |
|------|---------|----------|-----------|
| `screen_capture` | Take screenshot, return path + geometry | No | Auto |
| `screen_analyze` | Capture + describe via vision model | No | Auto |
| `screen_describe_via_vfb` | Describe an existing image file | No | Auto |
| `computer_control` | Batched mouse/keyboard/window actions | Yes | Per-action |
| `computer_clipboard` | Read or write system clipboard | Yes | Per-action |
