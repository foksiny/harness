"""
Interactive REPL loop for Harness.
Orchestrates prompt inputs, slash command dispatches, streaming output, live HUD updates,
and full CLI ↔ Discord synchronization (state, activity mirroring, and stop requests).
"""
import os
import time
import threading
import queue as pyqueue
from typing import Optional
from harness import __version__
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.tui.input_handler import (
    InputHandler,
    SENTINEL_OPEN_AGENTS,
    SENTINEL_BACK,
    VIEW_AGENTS,
    VIEW_PARENT,
    is_command_input,
    no_echo_stdin,
)
from harness.tui.queue import ExecutionQueue, QueuedItem
from harness.core.compaction import calculate_history_tokens


def _apply_remote_state(agent: HarnessAgent, payload: dict, origin: str) -> None:
    """Apply a state change published by the other side (usually Discord).

    Best-effort: any error is swallowed — a malformed remote event must never
    break the local loop.
    """
    try:
        if origin == "cli":  # our own echo via the in-process bus
            return
        if "mode" in payload:
            try:
                agent.set_mode(Mode.from_string(payload["mode"]))
            except Exception:
                pass
        if "permission" in payload:
            try:
                agent.set_permission(PermissionLevel.from_string(payload["permission"]))
            except Exception:
                pass
        if "provider" in payload:
            try:
                agent.set_provider(
                    payload["provider"],
                    model_name=payload.get("model") or None,
                )
                agent.config.provider = payload["provider"]
                if payload.get("model"):
                    agent.config.model = payload["model"]
            except Exception:
                pass
        elif "model" in payload:
            try:
                from harness.providers.detector import inspect_model
                new_model = payload["model"]
                if agent.session is not None:
                    agent.session.model = new_model
                agent.config.model = new_model
                spec = agent.provider.get_model_spec(new_model)
                agent.compactor.context_window = spec.context_window
            except Exception:
                pass
        if "thinking_effort" in payload:
            agent.config.thinking_effort = payload["thinking_effort"]
        session_action = payload.get("session_action")
        if session_action in ("create", "resume", "fork"):
            sid = payload.get("session_id")
            if sid:
                loaded = agent.session_manager.load(sid)
                if loaded is not None:
                    agent.session = loaded
        # delete / rename of *other* sessions don't touch the active one.
    except Exception:
        pass


def _mirror_prompt(agent: HarnessAgent, text: str) -> None:
    """Mirror locally typed prompts to the Discord side. Best-effort."""
    try:
        from harness.discord.sync import get_relay
        get_relay().relay_from_cli(text)
    except Exception:
        pass


def _drain_discord_activity(agent: HarnessAgent, renderer: TerminalRenderer, queue: "list") -> None:
    """Render any messages produced by Discord-driven runs since the last poll."""
    while queue:
        text, channel_id = queue.pop(0)
        if text and text.strip():
            renderer.print_info(f"[Discord] {text.strip()}")


def _poll_sync_bus(
    agent: HarnessAgent,
    renderer: TerminalRenderer,
    cursor,
    activity_queue: "list",
    queue: Optional[ExecutionQueue] = None,
    worker_wake: Optional[threading.Event] = None,
) -> None:
    """Pull new events from the cross-process bus (standalone ``harness discord``)."""
    try:
        from harness.discord.sync import get_relay, STATE, STOP, MESSAGE, OUTPUT
        relay = get_relay()
        for ev in relay.bus.poll(cursor, limit=32):
            kind = ev.get("kind")
            origin = ev.get("origin", "")
            if origin == "cli":
                continue
            if kind == STATE:
                payload = ev.get("payload", {})
                # Queue management from Discord
                q_action = payload.get("queue_action")
                if q_action and queue is not None:
                    if q_action == "clear":
                        cleared = queue.clear()
                        renderer.print_info(f"[Discord] Queue cleared ({cleared} tasks).")
                    elif q_action == "pause":
                        queue.pause()
                        renderer.print_warning("[Discord] Queue paused.")
                    elif q_action == "resume":
                        queue.resume()
                        renderer.print_success("[Discord] Queue resumed.")
                        if worker_wake:
                            worker_wake.set()
                _apply_remote_state(agent, payload, origin)
                if payload.get("turn_capsule"):
                    capsule = f"▣ {str(payload.get('mode', 'build')).upper()} · {payload.get('model', '')} · {payload.get('duration', 0)}s"
                    if payload.get("tokens"):
                        capsule += f" · {payload.get('tokens')}t"
                    activity_queue.append((capsule, None))
                elif not q_action:
                    renderer.print_info("[Discord] State synchronized.")
            elif kind == STOP:
                if agent.is_running:
                    agent.request_stop()
                    renderer.print_warning("[Discord] Stop requested — interrupting execution…")
            elif kind == MESSAGE:
                text = str(ev.get("text", "")).strip()
                channel_id = ev.get("channel_id")
                if text:
                    activity_queue.append((text, channel_id))
                    # Auto-enqueue Discord prompt into ExecutionQueue if not an activity echo
                    if queue is not None and not text.startswith("⌨️"):
                        item = queue.enqueue(text)
                        renderer.print_info(f"[Discord] Enqueued prompt #{item.id} from channel {channel_id or 'main'}: {text[:60]}")
                        if worker_wake is not None:
                            worker_wake.set()
            elif kind == OUTPUT:
                text = str(ev.get("text", "")).strip()
                channel_id = ev.get("channel_id")
                if text:
                    activity_queue.append((text, channel_id))
    except Exception:
        pass


def _input_with_echo_visible(prompt: str) -> str:
    """Read input with terminal echo forcibly restored.

    The TUI wraps the entire agent turn in `no_echo_stdin()` which disables
    ECHO via termios to avoid Ctrl-C/ESC leak. While that context is active
    a plain `input()` would be invisible. This helper briefly restores echo,
    reads, prints an explicit  ↳ You typed:  line, and lets the outer
    no_echo re-apply.
    """
    import sys
    restored = False
    old_attrs = None
    fd = None
    try:
        import termios
        try:
            fd = sys.stdin.fileno()
            if sys.stdin.isatty():
                old_attrs = termios.tcgetattr(fd)
                import termios as _t
                if not (old_attrs[3] & _t.ECHO):
                    new_attrs = termios.tcgetattr(fd)
                    new_attrs[3] |= _t.ECHO | _t.ECHOCTL | _t.ECHOE | _t.ECHOK
                    termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
                    restored = True
        except Exception:
            pass
    except ImportError:
        pass
    try:
        raw = input(prompt)
        # Explicit echo line (input() already echoed, this is an extra confirmation)
        try:
            from rich.console import Console
            Console().print(f"[dim]↳ You typed:[/dim] [bold cyan]{raw}[/bold cyan]")
        except Exception:
            print(f"↳ You typed: {raw}")
        return raw
    finally:
        if restored and old_attrs is not None and fd is not None:
            try:
                import termios
                termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
            except Exception:
                pass


def _make_tui_ask_handler(renderer: TerminalRenderer, input_handler: InputHandler):
    """Create an ask_user handler that shows rich details and echo-visible input."""
    def handler(question: str, options: list, allow_custom: bool, recommended: str | None) -> str:
        import time as _time
        opts = options or []
        # Rich detailed panel
        try:
            from rich.panel import Panel
            from rich.table import Table
            meta = [
                f"[bold white]{question}[/bold white]",
                "",
                f"[dim]Time: {_time.strftime('%Y-%m-%d %H:%M:%S')}  |  Options: {len(opts)}  |  Custom: {'yes' if allow_custom else 'no'}[/dim]",
            ]
            if recommended:
                meta.append(f"[yellow]★ Recommended: {recommended}[/yellow]")
            renderer.console.print(Panel("\n".join(meta), title="🤖 HARNESS — Question", border_style="cyan", padding=(1, 2)))
            if opts:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("No.", style="bold cyan", width=4)
                table.add_column("Option", style="white")
                table.add_column("Badge", style="yellow")
                for idx, opt in enumerate(opts, 1):
                    badge = "★ RECOMMENDED" if (recommended and recommended.lower() in opt.lower()) else ""
                    table.add_row(f"[{idx}]", opt, badge)
                if allow_custom:
                    table.add_row("[0]", "[dim]Type a custom write-in response[/dim]", "")
                renderer.console.print(table)
                renderer.console.print("[dim]Tip: type number, or type your answer directly. Your typing will be shown as  ↳ You typed: ...[/dim]\n")
            else:
                renderer.console.print("[dim]Your typing will be echoed visibly.[/dim]\n")
        except Exception:
            # Fallback to plain prints
            print("\n" + "=" * 60)
            print(f"🤖 [HARNESS IS ASKING FOR YOUR INPUT]\n❓ {question}\n")
            if opts:
                for idx, opt in enumerate(opts, 1):
                    print(f"  [{idx}] {opt}")
                if allow_custom:
                    print(f"  [0] Type a custom write-in response")

        # Read with echo-visible input
        try:
            prompt = "Your selection (number or custom answer): " if opts else "Your answer: "
            raw = _input_with_echo_visible(prompt).strip()
            if opts and raw.isdigit():
                val = int(raw)
                if 1 <= val <= len(opts):
                    ans = opts[val - 1]
                    try:
                        from rich.panel import Panel
                        renderer.console.print(Panel(f"[bold green]✔ Selected option [{val}]:[/bold green]\n[white]{ans}[/white]", border_style="green"))
                    except Exception:
                        print(f"✔ Selected option [{val}]: {ans}")
                    return f"User selected option [{val}]: {ans}"
                elif val == 0 and allow_custom:
                    # Custom write-in
                    try:
                        renderer.console.print("[dim]Custom write-in — please provide your answer:[/dim]")
                    except Exception:
                        print("Custom write-in — please provide your answer:")
                    custom = _input_with_echo_visible("Enter your custom answer: ").strip()
                    try:
                        from rich.panel import Panel
                        renderer.console.print(Panel(f"[bold green]✔ Custom response:[/bold green]\n[white]{custom}[/white]", border_style="green"))
                    except Exception:
                        print(f"✔ Custom response: {custom}")
                    return f"User wrote custom response: {custom}"
            if raw:
                # Check exact match to an option
                for idx, opt in enumerate(opts, 1):
                    if raw.lower() == opt.lower():
                        try:
                            from rich.panel import Panel
                            renderer.console.print(Panel(f"[bold green]✔ Matched option [{idx}]:[/bold green]\n[white]{opt}[/white]", border_style="green"))
                        except Exception:
                            print(f"✔ Matched option [{idx}]: {opt}")
                        return f"User selected option [{idx}]: {opt}"
                try:
                    from rich.panel import Panel
                    renderer.console.print(Panel(f"[bold green]✔ You replied:[/bold green]\n[white]{raw}[/white]", border_style="green"))
                except Exception:
                    print(f"✔ You replied: {raw}")
                return f"User replied: {raw}"
            # Fallback
            def_ans = recommended or (opts[0] if opts else "")
            if def_ans:
                try:
                    from rich.panel import Panel
                    renderer.console.print(Panel(f"[yellow]⚠ No input — defaulting to:[/yellow]\n[white]{def_ans}[/white]", border_style="yellow"))
                except Exception:
                    print(f"No input — defaulting to: {def_ans}")
                return f"User defaulted to: {def_ans}"
            return "User provided no answer."
        except (EOFError, KeyboardInterrupt):
            try:
                renderer.console.print("\n[dim]Skipped / cancelled.[/dim]")
            except Exception:
                print("\nSkipped / cancelled.")
            return "User skipped / cancelled question prompt."
    return handler


def _make_tui_approver(renderer: TerminalRenderer):
    """Create a permission approver that shows rich details and echo-visible input."""
    def approver(message: str, details: dict) -> bool:
        import time as _time
        try:
            from rich.panel import Panel
            action = details.get("action_type") or details.get("type") or details.get("tool") or "action"
            summary = details.get("summary") or details.get("command") or details.get("path") or str(details)[:120]
            risk = details.get("risk") or ""
            body_lines = [
                f"[bold white]{message}[/bold white]",
                "",
                f"[dim]Time: {_time.strftime('%Y-%m-%d %H:%M:%S')}  |  Action: {action}  |  Risk: {risk or 'unknown'}[/dim]",
                f"[dim]Details:[/dim] [white]{summary}[/white]",
            ]
            if len(str(details)) < 400 and details:
                try:
                    import json as _json
                    pretty = _json.dumps(details, indent=2)[:500]
                    if pretty and pretty != "{}":
                        body_lines.append("")
                        body_lines.append(f"[dim]Full details:[/dim]\n[dim]{pretty}[/dim]")
                except Exception:
                    pass
            renderer.console.print(Panel("\n".join(body_lines), title="⚠️  PERMISSION REQUIRED", border_style="yellow", padding=(1, 2)))
            renderer.console.print("[dim]Your typing will be shown as  ↳ You typed: ...  |  [y]es / [n]o  (default N)[/dim]")
            raw = _input_with_echo_visible("Allow this action? [y/N]: ").strip().lower()
            allowed = raw in ("y", "yes")
            if allowed:
                renderer.console.print(Panel(f"[bold green]✔ Approved[/bold green]\n[white]{message[:120]}[/white]", border_style="green"))
            else:
                renderer.console.print(Panel(f"[bold red]✖ Denied[/bold red]\n[white]{message[:120]}[/white]", border_style="red"))
            return allowed
        except (EOFError, KeyboardInterrupt):
            try:
                renderer.console.print("\n[dim]Denied (cancelled).[/dim]")
            except Exception:
                print("\nDenied (cancelled).")
            return False
        except Exception:
            # Fallback
            print(f"\n⚠️  [PERMISSION REQUIRED]: {message}")
            try:
                raw = _input_with_echo_visible("Allow this action? [y/N]: ").strip().lower()
                return raw in ("y", "yes")
            except Exception:
                return False
    return approver


def _resolve_ask_response(raw: str, req: dict, renderer: TerminalRenderer) -> str:
    opts = req.get("options") or []
    allow_custom = req.get("allow_custom", True)
    recommended = req.get("recommended")
    raw = raw.strip()
    if opts and raw.isdigit():
        val = int(raw)
        if 1 <= val <= len(opts):
            ans = opts[val - 1]
            renderer.print_success(f"Selected option [{val}]: {ans}")
            return f"User selected option [{val}]: {ans}"
        elif val == 0 and allow_custom:
            renderer.print_info("Custom write-in selected.")
            return "User requested custom write-in"
    if raw:
        for idx, opt in enumerate(opts, 1):
            if raw.lower() == opt.lower():
                renderer.print_success(f"Matched option [{idx}]: {opt}")
                return f"User selected option [{idx}]: {opt}"
        renderer.print_success(f"Replied: {raw}")
        return f"User replied: {raw}"
    def_ans = recommended or (opts[0] if opts else "")
    if def_ans:
        renderer.print_warning(f"No input — defaulting to: {def_ans}")
        return f"User defaulted to: {def_ans}"
    return "User provided no answer."


def run_interactive(agent: HarnessAgent):
    """Run interactive terminal session with concurrent prompt & queue support."""
    renderer = TerminalRenderer(agent.config.theme)
    commands = CommandRegistry()
    input_handler = InputHandler()
    queue = ExecutionQueue()

    worker_stop = threading.Event()
    worker_wake = threading.Event()
    pending_interactive_lock = threading.Lock()
    pending_interactive = None

    def _ask_handler(question: str, options: list, allow_custom: bool, recommended: str | None) -> str:
        nonlocal pending_interactive
        resp_q = pyqueue.Queue()
        with pending_interactive_lock:
            pending_interactive = {
                "type": "ask",
                "question": question,
                "options": options or [],
                "allow_custom": allow_custom,
                "recommended": recommended,
                "resp_q": resp_q,
            }
        opts = options or []
        try:
            from rich.panel import Panel
            from rich.table import Table
            meta = [
                f"[bold white]{question}[/bold white]",
                "",
                f"[dim]Options: {len(opts)}  |  Custom: {'yes' if allow_custom else 'no'}[/dim]",
            ]
            if recommended:
                meta.append(f"[yellow]★ Recommended: {recommended}[/yellow]")
            renderer.console.print(Panel("\n".join(meta), title="🤖 HARNESS — Question", border_style="cyan", padding=(1, 2)))
            if opts:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("No.", style="bold cyan", width=4)
                table.add_column("Option", style="white")
                table.add_column("Badge", style="yellow")
                for idx, opt in enumerate(opts, 1):
                    badge = "★ RECOMMENDED" if (recommended and recommended.lower() in opt.lower()) else ""
                    table.add_row(f"[{idx}]", opt, badge)
                if allow_custom:
                    table.add_row("[0]", "[dim]Type a custom write-in response[/dim]", "")
                renderer.console.print(table)
                renderer.console.print("[dim]Type your answer directly in the prompt below...[/dim]\n")
        except Exception:
            print(f"\n❓ Question: {question}")

        ans = resp_q.get()
        with pending_interactive_lock:
            pending_interactive = None
        return ans

    def _approver_handler(message: str, details: dict) -> bool:
        nonlocal pending_interactive
        resp_q = pyqueue.Queue()
        with pending_interactive_lock:
            pending_interactive = {
                "type": "perm",
                "message": message,
                "details": details,
                "resp_q": resp_q,
            }
        try:
            from rich.panel import Panel
            action = details.get("action_type") or details.get("type") or details.get("tool") or "action"
            summary = details.get("summary") or details.get("command") or details.get("path") or str(details)[:120]
            risk = details.get("risk") or ""
            body_lines = [
                f"[bold white]{message}[/bold white]",
                "",
                f"[dim]Action: {action}  |  Risk: {risk or 'unknown'}[/dim]",
                f"[dim]Details:[/dim] [white]{summary}[/white]",
            ]
            renderer.console.print(Panel("\n".join(body_lines), title="⚠️  PERMISSION REQUIRED", border_style="yellow", padding=(1, 2)))
            renderer.console.print("[dim]Type [y]es or [n]o in the prompt below to approve/deny (default: no)...[/dim]\n")
        except Exception:
            print(f"\n⚠️  Permission required: {message}")

        allowed = resp_q.get()
        with pending_interactive_lock:
            pending_interactive = None
        return allowed

    # Wire TUI-rich handlers for ask_user and permission prompts
    try:
        ask_tool = agent.tool_registry.get("ask_user")
        if ask_tool is not None:
            ask_tool.interactive_handler = _ask_handler
    except Exception:
        pass
    try:
        agent.permission_manager.approver_callback = _approver_handler
    except Exception:
        pass

    # Initial display
    renderer.print_welcome_splash(agent, queue=queue)

    view = VIEW_PARENT

    # ── Background Worker Loop ───────────────────────────────────────
    def _worker_loop():
        while not worker_stop.is_set():
            if queue.is_paused:
                worker_wake.wait(timeout=0.2)
                worker_wake.clear()
                continue

            item = queue.dequeue()
            if item is None:
                worker_wake.wait(timeout=0.2)
                worker_wake.clear()
                continue

            t_start = time.time()
            renderer.render_user_prompt(item.prompt)
            _mirror_prompt(agent, item.prompt)

            if item.mode:
                try:
                    agent.set_mode(Mode.from_string(item.mode))
                except Exception:
                    pass

            try:
                agent.is_running = True
                # Accumulate response for Discord mirroring
                response_parts: list[str] = []
                tokens_est = 0
                for ev in agent.step(item.prompt):
                    renderer.render_agent_event(ev)
                    # Mirror incremental output to Discord side (best-effort)
                    try:
                        etype = getattr(ev, "type", "")
                        data = getattr(ev, "data", None)
                        if etype in ("text_delta", "reasoning_delta"):
                            txt = str(data) if data else ""
                            if txt:
                                response_parts.append(txt)
                            # Also stream incremental to bus for live Discord view
                            if etype == "text_delta" and txt:
                                from harness.discord.sync import get_relay
                                get_relay().relay_output(txt, origin="cli")
                        elif etype == "tool_call_start":
                            from harness.discord.sync import get_relay
                            tname = (data or {}).get("name", "tool")
                            get_relay().relay_output(f"🔧 Using `{tname}`", origin="cli")
                        elif etype == "tool_call_result":
                            from harness.discord.sync import get_relay
                            res = str((data or {}).get("result", ""))[:200]
                            if res:
                                get_relay().relay_output(f"✔ {res.split(chr(10))[0]}", origin="cli")
                    except Exception:
                        pass
                renderer.finish_markdown()
                renderer.finish_thinking()
                turn_dur = time.time() - t_start
                renderer.render_turn_capsule(
                    agent.mode.value,
                    agent.session.model if agent.session is not None else agent.config.model,
                    turn_dur,
                )
                # Estimate tokens and context pct for enriched capsule
                try:
                    from harness.core.compaction import calculate_history_tokens
                    tokens_est = calculate_history_tokens(agent.session.messages) if agent.session else 0
                    c_win = agent.compactor.context_window
                    pct = round((tokens_est / max(1, c_win)) * 100, 1)
                except Exception:
                    pct = 0
                # Relay final response back to Discord (mirrored output)
                try:
                    from harness.discord.sync import get_relay
                    from harness.discord.renderer import format_capsule_card
                    final_text = "".join(response_parts).strip()
                    if final_text:
                        get_relay().relay_output(final_text[:4000], origin="cli")
                    # Enriched turn capsule with tokens/context
                    capsule_text = format_capsule_card(
                        agent.mode.value,
                        agent.session.model if agent.session is not None else agent.config.model,
                        turn_dur,
                        tokens=tokens_est,
                    )
                    if pct:
                        capsule_text += f" · `{pct}% ctx`"
                    get_relay().relay_output(capsule_text, origin="cli")
                    get_relay().publish_state({
                        "turn_capsule": True,
                        "mode": agent.mode.value,
                        "model": agent.session.model if agent.session is not None else agent.config.model,
                        "duration": round(turn_dur, 2),
                        "tokens": tokens_est,
                        "context_pct": pct,
                    }, origin="cli")
                except Exception:
                    pass
                queue.finish_current(status="completed")
            except KeyboardInterrupt:
                renderer.finish_markdown()
                renderer.finish_thinking()
                renderer.print_warning("\nExecution interrupted by user.")
                agent.is_running = False
                agent.clear_stop()
                queue.finish_current(status="cancelled")
            except Exception as ex:
                renderer.finish_markdown()
                renderer.finish_thinking()
                renderer.print_error(f"\nExecution error: {str(ex)}")
                agent.is_running = False
                queue.finish_current(status="failed")
            finally:
                agent.is_running = False

    worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="harness-tui-worker")
    worker_thread.start()

    # ── API server info ──────────────────────────────────────────────
    api_server = None
    try:
        from harness.mesh.server import get_server, get_mesh
        api_server = get_server() or get_mesh()
        if api_server is None and getattr(agent.config, "server_enabled", getattr(agent.config, "mesh_enabled", True)):
            from harness.mesh.server import start_server
            api_server = start_server(workspace=__import__("os").getcwd(), agent_ref=lambda: agent, config=agent.config, enable=True)
        if api_server is not None:
            renderer.print_info(f"[api] API server at http://{api_server.host}:{api_server.port}  (POST /api/prompt, GET /health)")
    except Exception:
        api_server = None

    # ── Discord sync wiring ──────────────────────────────────────────
    activity_queue: list = []
    sync_cursor = None
    try:
        from harness.discord.sync import get_relay
        relay = get_relay()
        relay.attach_queue(queue)
        relay.set_connected(True)

        def _cli_callback(text: str) -> None:
            activity_queue.append((text, None))

        relay.register_cli(_cli_callback)
        sync_cursor = relay.bus.new_cursor()
        relay.register_state_listener(lambda payload, origin: _apply_remote_state(agent, payload, origin))
    except Exception:
        sync_cursor = None

    # ── Main Foreground Prompt Loop ──────────────────────────────────
    while True:
        _drain_discord_activity(agent, renderer, activity_queue)
        if sync_cursor is not None:
            _poll_sync_bus(agent, renderer, sync_cursor, activity_queue, queue=queue, worker_wake=worker_wake)
            _drain_discord_activity(agent, renderer, activity_queue)

        # Check API server external prompts
        if api_server is not None:
            try:
                pending_api = api_server.drain_prompts(limit=10)
                for p_item in pending_api:
                    ext_p = str(p_item.get("prompt", "")).strip()
                    if ext_p:
                        renderer.print_info(f"[api] External prompt received → {ext_p[:120]}")
                        queue.enqueue(ext_p)
                        worker_wake.set()
            except Exception:
                pass

        from prompt_toolkit.formatted_text import HTML

        placeholder = None
        if view == VIEW_AGENTS:
            plain_prompt = HTML("<ansicyan><b>Agents │</b></ansicyan> ")
            placeholder = HTML('<style color="#666666">/agent &lt;id&gt; to inspect | ESC to return</style>')
        else:
            with pending_interactive_lock:
                req = pending_interactive

            if req is not None:
                if req["type"] == "perm":
                    plain_prompt = HTML("<ansiyellow><b>[Approve? y/N] │</b></ansiyellow> ")
                else:
                    plain_prompt = HTML("<ansiyellow><b>[Answer] │</b></ansiyellow> ")
                placeholder = HTML('<style color="#666666">Type answer or choice above...</style>')
            else:
                plain_prompt = HTML("<ansicyan><b>│</b></ansicyan> ")
                placeholder = HTML('<style color="#666666">Ask anything.. "What is the tech stack of this project?"</style>')

        def get_bottom_toolbar():
            toks = calculate_history_tokens(agent.session.messages) if agent.session is not None else 0
            c_win = agent.compactor.context_window
            pct = round((toks / max(1, c_win)) * 100, 1)
            toks_k = f"{toks / 1000:.1f}k" if toks >= 1000 else str(toks)
            c_win_k = f"{c_win / 1000:.0f}k" if c_win >= 1000 else str(c_win)
            cwd_str = os.getcwd()
            short_cwd = "~" if cwd_str == os.path.expanduser("~") else os.path.basename(cwd_str) or cwd_str
            q_sz = queue.size()
            mode_str = agent.mode.value.capitalize()
            model_str = agent.session.model if agent.session is not None else agent.config.model
            effort_str = agent.config.thinking_effort

            with pending_interactive_lock:
                is_asking = pending_interactive is not None

            if is_asking:
                line1 = '<ansiyellow><b>│ [INPUT REQUIRED]</b></ansiyellow> <style color="#cccccc">Type answer or approval choice</style>'
            else:
                line1 = f'<ansicyan><b>│</b></ansicyan> <ansicyan><b>{mode_str}</b></ansicyan> <style color="#666666">·</style> <style color="#cccccc">{model_str}</style> <style color="#666666">·</style> <ansiyellow><b>{effort_str}</b></ansiyellow>'

            if agent.is_running:
                status_tag = '<ansigreen><b>[▶ RUNNING]</b></ansigreen> '
                action_tag = '<style color="#888888">/stop to interrupt</style>'
            elif queue.is_paused:
                status_tag = '<ansiyellow><b>[PAUSED]</b></ansiyellow> '
                action_tag = '<style color="#888888">/queue resume</style>'
            else:
                status_tag = ''
                action_tag = '<b>ctrl+p</b> <style color="#888888">commands</style>'

            q_tag = f'  <ansiyellow><b>Queue: {q_sz}</b></ansiyellow>' if q_sz > 0 else ''

            line2 = (
                f'<style color="#888888">{short_cwd}</style>    '
                f'<style color="#888888">{toks_k}/{c_win_k} ({pct}%)</style>  '
                f'{status_tag}{action_tag}{q_tag}  '
                f'• <style color="#55ff55">●</style> <b>Harness {__version__}</b>'
            )
            return HTML(f"{line1}\n{line2}")

        user_input = input_handler.get_input(
            plain_prompt,
            view,
            bottom_toolbar=get_bottom_toolbar,
            refresh_interval=0.5,
            placeholder=placeholder,
        )

        if user_input == SENTINEL_OPEN_AGENTS:
            view = VIEW_AGENTS
            renderer.print_subagent_board(agent.subagent_orchestrator.list_records())
            continue
        if user_input == SENTINEL_BACK:
            view = VIEW_PARENT
            renderer.print_info("Returned to parent agent view.")
            continue
        if not user_input:
            continue

        # Check if an interactive prompt (ask_user or approver) is waiting
        with pending_interactive_lock:
            active_req = pending_interactive

        if active_req is not None:
            if active_req["type"] == "perm":
                allowed = user_input.strip().lower() in ("y", "yes")
                if allowed:
                    renderer.print_success("Action approved.")
                else:
                    renderer.print_warning("Action denied.")
                active_req["resp_q"].put(allowed)
            elif active_req["type"] == "ask":
                resolved = _resolve_ask_response(user_input, active_req, renderer)
                active_req["resp_q"].put(resolved)
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            renderer.print_info("Saving session and shutting down Harness. Goodbye!")
            worker_stop.set()
            agent.request_stop()
            worker_wake.set()
            if agent.session is not None:
                agent.session_manager.save(agent.session)
            try:
                from harness.mesh.server import stop_server
                stop_server()
            except Exception:
                pass
            break

        # Check slash command
        if user_input.startswith("/") and is_command_input(user_input, commands.commands):
            _mirror_prompt(agent, user_input)
            command_result = commands.handle(user_input, agent, renderer, queue=queue)
            if command_result == VIEW_AGENTS:
                view = VIEW_AGENTS
                renderer.print_subagent_board(agent.subagent_orchestrator.list_records())
            elif command_result == VIEW_PARENT:
                view = VIEW_PARENT
            continue

        if view == VIEW_AGENTS:
            renderer.print_info("In the agents view. Use /agent <id>, /back, or ESC to return.")
            continue

        # Free-text user prompt
        _mirror_prompt(agent, user_input)

        if agent.is_running:
            # Enqueue prompt while agent is busy
            item = queue.enqueue(user_input)
            renderer.print_queue_event(item, "enqueued")
        else:
            # Enqueue and wake worker thread to begin processing
            item = queue.enqueue(user_input)
            worker_wake.set()
