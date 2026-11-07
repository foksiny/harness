# Implementation Progress — IMPROVEMENT_PLAN.md

Goal: implement everything in docs/IMPROVEMENT_PLAN.md (P0→P2; P3 where feasible without violating lightweight constraint).

## Steps
1. P0 core gates: finish contract, todo gate, verify gate, prompt Directive#9, modes PLAN, context head+tail+ceiling, config budgets/temp. Check: pytest test_finish/test_context_budget/test_modes/test_ultra — DONE (93 passed)
2. Delegation/swarm lifecycle: subagents A1-A12, B1-B7 (run ids, cancel/timeout/caps, DAG, handoff, verifier, presets), todo graph. Check: pytest test_swarm/test_subagents — DONE (24 passed)
3. Computer+web: computer tools, browser snapshot, web_fetch, search synthesis, grounding. Check: pytest test_browser/test_computer/test_web_fetch — DONE
4. Security/tools/MCP: PLAN shell, perms routing, run_command confine, sandbox fail-closed, MCP lazy, skill trust, missing tools. Check: pytest test_tools/test_mcp/test_modes — DONE
5. Context/learning/evals: compaction schema, learning match, evals suite, bench gate, telemetry. Check: full suite — DONE (707 passed)
6. Final: full test suite + bench, update plan status, critique round. — DONE

## Evidence
- Full suite: 707 passed (was 694), 1 warning
- Evals: 19 passed (`python3 -m evals.run`)
- Bench: cold 95ms, RSS 33MB, 65 tools (was 46)
- New tests: tests/test_computer.py, tests/test_web_fetch.py, tests/test_evals_gate.py
- New modules: harness/computer/grounding.py, harness/tools/computer.py, harness/core/swarm_presets.py, harness/core/ultra_phases.py, harness/core/turn_executor.py, evals/run.py

## Critique round
- Missing? P3 heavy items (VLM grounding, remote search API, cross-instance mesh) are documented stubs/opt-ins — gated on evals per plan.
- Assumptions checked: finish gate allows 2 rejections so trivial turns still finish; PLAN checks command words; computer tools degrade gracefully headless.
- Simpler path? Gates piggyback existing transcript inspection (no extra model calls) — cheapest mechanism.

## Notes on deliberate deviations
- Search synthesis: structured chain kept; full rerank/synthesis pass deferred to eval-gated P3 (free-chain parsers pinned by fixture tests in tests/test_web_fetch.py pattern).
- Sandbox: restricted backend kept behind test/opt-in (config sandbox_backend) rather than hard fail-closed for local dev, but now uses temp HOME/cwd and never silent.
- Mesh stubs: hidden from registry/schemas (H9) rather than deleted — backward-compat import path preserved.

## Computer-use reliability pass (cross-DE / cross-platform)

Goal: desktop control must act instead of observing forever, on GNOME/COSMIC/KDE/XFCE/wlroots (Wayland and X11), macOS, and Windows.

### What changed
- **Perception**: every screenshot now carries tesseract line-level **Set-of-Marks** (`[N] 'text' nx ny`); `computer_click(mark=N)` is the primary grounding path. No bundled VLM, so this stays lightweight. `parse_ocr_tsv` reads page dimensions from tesseract's own level-1 row, so marks no longer require Pillow. `marks=false` skips OCR (~3s) when only pixels are needed.
- **Action**: `computer_act(steps=[click mark, type, key Return])` runs a batch server-side, stops at the first failure, and returns one fresh screenshot + fresh marks. Type steps go through the guarded path below; a failed delivery stops the batch before the Return step.
- **Typing safety (wrong-app incident)**: `computer_type(submit=true)` is no longer fire-and-forget. It captures a fresh screenshot first and refuses to type when an overview/app-search/launcher is open or the screen is stale; after typing it captures again and requires the text to appear near the input that was clicked. Only then is Return sent. Results carry `delivered` / `verified` / `submitted`; `delivered=false` means the text did NOT reach the intended input and was NOT sent. A failed delivery also locks non-Escape keys, so a later manual `computer_key(Return)` cannot submit the stray text; Escape or a verified retype clears the lock. `computer_type(expect='Discord')` additionally proves the intended window is in front. `verify=false` still refuses Return unless `allow_unverified=true`. The finish gate rejects a success claim once when the turn ended with `delivered=false`.
- **Guards implementation**: `computer/guards.py` holds the platform-neutral checks (`typing_preflight`, `locate_typed_text`, `check_landed`, `keyboard_allowed`); `ComputerController.type_text` is the only typing path, and `execute_batch` routes `type`/`text` steps through it. Overview detection uses OCR markers ("Type to search"/"Digite para pesquisar" plus Activities + Show Applications), so it works on GNOME, COSMIC, KDE, X11, macOS, and Windows without window introspection.
- **Loop breaker**: dedicated computer watchdog in `core/agent.py` — 3 observation-only steps (screenshot/OCR/window list/`ps`) trigger a "stop observing, act now" nudge; 6 end the turn. Repeating the same GUI action twice nudges a different approach; four ends the turn. Generic exploration watchdog unchanged.
- **Wayland reality**: empty `computer_windows` lists are labelled as expected (Wayland exposes no generic window list) and never retried; the result carries running `apps`, a screenshot fallback, and a "do not call again" note. Same note is only injected into the transcript on Wayland sessions.
- **Goal anchor**: every computer tool result is tagged with the current turn goal, so the model stops drifting into app internals (tokens, caches) when asked to operate a GUI.
- **Ambiguity**: the prompt and watchdog direct the model to `ask_user` once when the target (which friend/chat/app) is ambiguous instead of guessing or inspecting app databases.
- **Platform drivers** (`computer/wayland_utils.py`, despite the legacy name): Linux GNOME (`gnome-screenshot`, gdbus), COSMIC (`cosmic-screenshot`), KDE (`spectacle`), wlroots (`grim`), XFCE, Cinnamon/MATE/LXQt, X11 (`scrot`/`maim`/`import`), macOS (`screencapture`, `pbcopy`, AppleScript, optional `cliclick`), Windows (PowerShell `System.Drawing`, `WScript.SendKeys`, `Get/Set-Clipboard`). On Wayland, compositor capture runs before mss (which returns black frames); `ydotool` is preferred for native-window input. On X11/macOS/Windows, mss/pyautogui remains the fast path.
- **Key-name safety**: ydotool 0.1.8 resolves key names through libevdevPlus and silently falls back to the *first character* of unknown names — `Return` pressed R, `Page_Down` pressed P, `space` pressed S. All backends now normalize `Return`/`enter`, `Page_Down`/`pagedown`, `space`/`spacebar`, `cmd`/`super`, `option`/`alt`, etc. to one canonical vocabulary and translate it per platform. Unsupported names (F13+, Menu, shifted characters) fail with an actionable message instead of pressing a random key. Scroll fallbacks use `PAGEDOWN`/`PAGEUP` directly, and Windows no longer silently maps Super to Ctrl.
- **Import resilience**: `ComputerController` no longer imports mss/Pillow at module load; PNG/BMP dimensions are read from file headers. Missing optional libraries no longer silently unregister the whole computer toolset.
- **Prompt**: `COMPUTER_PROTOCOL` is injected only when computer tools are registered.

### Evidence
- Full suite: **791 passed**, 1 warning, 2 subtests (`python3 -m pytest -q`)
- Evals: **19 passed** (`python3 -m evals.run`)
- Computer tests: **60 passed** (`tests/test_computer.py`), including overview blocks typing, missing `expect` blocks, text absent/wrong-place never presses Return, manual Return blocked after failed delivery, and Escape clears the lock. `tests/test_finish.py` covers the finish rejection after `delivered=false`.
- Live GNOME Wayland (this host): `detect_de=gnome`, `session=xwayland`, capture backend `gnome-screenshot` at 1920x1200, 46 OCR line marks; `screen_state()` reports `has_screenshot=true`, `overview=false`, `typing_blocked=false`, and Return/Escape both allowed on a normal screen.
- Incident reproduction covered by tests: a string is typed, OCR finds it far from the clicked input (overview search bar), and no Return is sent.
- Import smoke test with `mss`/`PIL` forcibly unavailable: controller imports and `_image_size` still reads PNG headers.

### Pointer placement fix (cursor stuck in screen corner)

- **Symptom**: `computer_move`/`computer_click`/`computer_scroll` left the pointer in a screen corner instead of the requested coordinates.
- **Root causes**: (1) `_apply_xtest` clicked at the *current* position — `click`/`double` ignored `x`/`y`; (2) on this host the session is `xwayland` (`is_wayland()` is true), so `_default_input_exec` preferred `ydotool 0.1.8`, whose `mousemove <x> <y>` is a *relative* move (warp to the origin, then apply a delta — confirmed in the vendored `v0.1.8` source), stranding the cursor when compositing/acceleration intervenes; (3) `computer_move`/`computer_scroll` defaulted a missing target to `x=0, y=0` (top-left corner).
- **Fix**: pointer actions (`move`, `click`, `double`/`double_click`, `right`/`middle` clicks, `drag`, `scroll`) are now routed through XTEST first on X11/XWayland — it takes real absolute coordinates. `_apply_xtest` always calls `move(x, y)` before a click/double/scroll (and `drag` presses, moves, releases); `XTestSession.move`/`button` use `XSync` so the button event lands at the new point. On XWayland the button itself is injected with ydotool (uinput) so native Wayland windows also receive the click, while XTEST still did the positioning (`backend: xtest+ydotool`). Keyboard/text still go via ydotool on Wayland (XTEST only reaches XWayland clients). `ydotool mousemove --absolute --xpos/--ypos` (1.x) is detected via `--help` and used when present; the 0.1.8 relative syntax now returns an explicit `warning`. `XTestSession` defaults to `$DISPLAY` and gained `drag`; `computer_move` requires a target and `computer_scroll` only moves the pointer when `x`/`y` were actually given.
- **Evidence**: `python3 -m pytest tests/test_computer.py -q` → **74 passed** (14 new `TestPointerPositioning` cases: move-before-click/double/drag/scroll, hybrid XTEST+ydotool clicks, XTEST-first routing on XWayland, keyboard stays on CLI, ydotool absolute vs legacy syntax + warning, no-`0,0` defaults); full suite **805 passed**, 1 warning, 2 subtests; evals **19 passed**. Live on this host: `move` to `(500,400)`, `(1200,800)`, `(100,100)`, `(800,600)` → all `ok: true`, `backend: xtest`, and `XQueryPointer` reports exactly those coordinates (pre-fix the same `move` went through ydotool and the pointer never left the previous corner).

### Unicode typing (beyond ASCII)

- **Problem**: every direct key-synthesis path only types characters bound in the current keymap. `XTestSession.type_text` resolved each character through `FALLBACK_KEYCODES`/`XStringToKeysym`, `ydotool 0.1.8 type` has a 128-entry ASCII table (bytes ≥ 128 index out of bounds), and pyautogui `write == typewrite` both synthesize keycodes — so accents, CJK, and emoji were silently dropped or mangled.
- **Fix**: ASCII text keeps the fast direct paths; any non-ASCII text is delivered by clipboard + paste (`clipboard_paste` in `wayland_utils.py`: save the user's clipboard best-effort, write via `wl-copy`/`xclip`/`xsel` or the new zero-dependency `computer/x11clipboard.py` ctypes X11 selection holder, send `Ctrl+V`/`Cmd+V`, then restore), on every OS. `cli_input` and `_default_input_exec` route Unicode there automatically, `_windows_input` does too, macOS keeps native AppleScript unicode keystrokes, and the lower layers (`XTestSession.type_text`, `_ydotool_input`, `pyautogui_backend`) now fail loudly for non-ASCII instead of mangling it. Short-string verification boundaries in `locate_typed_text` are Unicode-aware (`\w`, so "olá" no longer matches inside "Solá").
- **Evidence**: `python3 -m pytest tests/test_computer.py -q` → **87 passed** (13 new `TestUnicodeTyping` cases: ASCII detection, paste chord per platform, write→paste→restore order, audible failure without a clipboard, `cli_input`/default routing, XTEST/ydotool/pyautogui rejection, Unicode token boundaries); full suite **818 passed**, 1 warning, 2 subtests; evals **19 passed**. Live on this host (no `wl-copy`/`xclip` installed): `cli_clipboard write("olá mundo 🎉 中文 café ñ — test")` → `x11clipboard`, and `read` returns it byte-identical; `clipboard_paste` reports `clipboard(x11clipboard)+paste` with the clipboard restored; `ydotool type` and `XTestSession.type_text` refuse Unicode with no CLI calls made.

### Company mode (/company)

- **What it does**: `/company` starts an always-on companion (`harness/company/controller.py`, daemon thread, one per agent): every 25 s it runs one agent beat — screenshot first, then the model chooses to speak in the terminal, side-chat through the focused app via the guarded computer tools, help with the game, or stay silent (quiet is the default; it only interrupts when the screen shows something worth it). `/company <role>` sets the role (e.g. "side-chat me on Discord", "watch my game and help me win"); bare `/company` uses a general-company role; `/company stop|status|help` manage it. Voice-out-loud is opt-in via role text ("speak out loud"/"TTS") using `spd-say`/`espeak-ng`. Whole turns are serialized through `HarnessAgent._turn_lock` so the worker, mesh, and company never interleave `step()` calls.
- **Stopping (four paths)**: `/stop` and `/company stop` (+ `agent.request_stop()` for the in-flight beat); Ctrl+C (mapped to stop-company first, only quitting on the second press); in-terminal Shift+Esc (prompt-toolkit `ShiftEscape` + raw-mode sequence); kernel-level Super+Shift+Esc via raw evdev where `/dev/input` is readable, falling back to the user-bindable stop-file `~/.harness/company.stop` (GNOME Settings → Keyboard → Shortcuts → Custom: `touch ~/.harness/company.stop`). Super is consumed by GNOME before it reaches the terminal, so it inherently cannot be captured in-process without those out-of-band paths.
- **Evidence**: `python3 -m pytest tests/test_company.py -q` → **15 passed** (tick-prompt quiet defaults, evdev chord parsing, loop beat/stop lifecycle, duplicate-start rejection, stop-file trigger, quiet-streak speech reset, registry role/status/stop routing including "stop chatting" as a role, input-handler listing, turn-lock serialization); full suite **833 passed**, 1 warning, 2 subtests; evals **19 passed**. On this host `/company` is registered, the stop path resolves to `~/.harness/company.stop`, and evdev is correctly reported unreadable (user is not in the `input` group) — the other stop paths still apply.

### Still deferred (P3)
- Bundled/local VLM grounding (OCR marks + normalized coords shipped instead; VLM remains opt-in).
- Full cross-instance mesh and remote search synthesis (unchanged from earlier deferral list).
