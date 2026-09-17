"""
Interactive REPL loop for Harness.
Orchestrates prompt inputs, slash command dispatches, streaming output, live HUD updates,
and full CLI ↔ Discord synchronization (state, activity mirroring, and stop requests).
"""
from typing import Optional
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.tui.input_handler import InputHandler, SENTINEL_OPEN_AGENTS, SENTINEL_BACK, VIEW_AGENTS, VIEW_PARENT, is_command_input, no_echo_stdin
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


def _poll_sync_bus(agent: HarnessAgent, renderer: TerminalRenderer, cursor, activity_queue: "list") -> None:
    """Pull new events from the cross-process bus (standalone ``harness discord``)."""
    try:
        from harness.discord.sync import get_relay, STATE, STOP, MESSAGE
        relay = get_relay()
        for ev in relay.bus.poll(cursor, limit=32):
            kind = ev.get("kind")
            origin = ev.get("origin", "")
            if origin == "cli":
                continue
            if kind == STATE:
                _apply_remote_state(agent, ev.get("payload", {}), origin)
                renderer.print_info("[Discord] State synchronized.")
            elif kind == STOP:
                if agent.is_running:
                    agent.request_stop()
                    renderer.print_warning("[Discord] Stop requested — interrupting execution…")
            elif kind == MESSAGE:
                activity_queue.append((ev.get("text", ""), ev.get("channel_id")))
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


def run_interactive(agent: HarnessAgent):
    """Run interactive terminal session."""
    renderer = TerminalRenderer(agent.config.theme)
    commands = CommandRegistry()
    input_handler = InputHandler()

    # Wire TUI-rich handlers for ask_user and permission prompts
    # so typing is echo-visible even inside no_echo_stdin and shows rich details.
    try:
        ask_tool = agent.tool_registry.get("ask_user")
        if ask_tool is not None:
            ask_tool.interactive_handler = _make_tui_ask_handler(renderer, input_handler)
    except Exception:
        pass
    try:
        agent.permission_manager.approver_callback = _make_tui_approver(renderer)
    except Exception:
        pass

    renderer.clear_screen()
    renderer.print_banner()

    view = VIEW_PARENT

    # ── API server info (no peer mesh) ───────────────────────────────
    api_server = None
    try:
        from harness.mesh.server import get_server, get_mesh
        api_server = get_server() or get_mesh()
        if api_server is None and getattr(agent.config, "server_enabled", getattr(agent.config, "mesh_enabled", True)):
            # Server already started by CLI; just fetch it
            from harness.mesh.server import start_server
            api_server = start_server(workspace=__import__("os").getcwd(), agent_ref=lambda: agent, config=agent.config, enable=True)
        if api_server is not None:
            renderer.print_info(f"[api] API server at http://{api_server.host}:{api_server.port}  (POST /api/prompt, GET /health)")
    except Exception:
        api_server = None

    # ── Discord sync wiring ────────────────────────────────────────────
    # In-process (bot hosted by this CLI): callbacks deliver Discord activity
    # into a queue rendered between prompts. Cross-process (standalone
    # ``harness discord``): the JSONL bus is polled between prompts.
    activity_queue: list = []
    sync_cursor = None
    try:
        from harness.discord.sync import get_relay, STOP
        relay = get_relay()

        def _cli_callback(text: str) -> None:
            # Discord user text (prompt or command) — show it locally.
            activity_queue.append((text, None))

        relay.register_cli(_cli_callback)
        sync_cursor = relay.bus.new_cursor()
        relay.register_state_listener(lambda payload, origin: _apply_remote_state(agent, payload, origin))
    except Exception:
        relay = None
        sync_cursor = None

    while True:
        if view == VIEW_AGENTS:
            renderer.print_subagent_board(agent.subagent_orchestrator.list_records())
            plain_prompt = "Agents> "
        else:
            # Render live status HUD
            tokens = calculate_history_tokens(agent.session.messages) if agent.session is not None else 0
            c_win = agent.compactor.context_window
            todos_summary = agent.todo_manager.summary()

            renderer.print_hud(
                mode=agent.mode.value,
                perm=agent.permission_manager.level.value,
                provider=agent.provider.display_name,
                model=agent.session.model if agent.session is not None else agent.config.model,
                tokens=tokens,
                context_win=c_win,
                todos_summary=todos_summary,
            )
            renderer.print_footer()
            plain_prompt = f"Harness ({agent.mode.value})> "

        # Show any Discord-driven activity since the last prompt.
        _drain_discord_activity(agent, renderer, activity_queue)
        if sync_cursor is not None:
            _poll_sync_bus(agent, renderer, sync_cursor, activity_queue)
            _drain_discord_activity(agent, renderer, activity_queue)

        # ── API server external prompt queue (POST /api/prompt) ────
        # External clients (curl, VPS) POST to /api/prompt. When queued, execute
        # automatically as if the user typed them.
        if api_server is not None:
            try:
                pending = api_server.drain_prompts(limit=1)
                if pending:
                    for item in pending:
                        ext_prompt = str(item.get("prompt", "")).strip()
                        if not ext_prompt:
                            continue
                        renderer.print_info(f"[api] External prompt on :{api_server.port} → {ext_prompt[:300]}")
                        _mirror_prompt(agent, ext_prompt)
                        try:
                            with no_echo_stdin():
                                for ev in agent.step(ext_prompt):
                                    renderer.render_agent_event(ev)
                            renderer.finish_markdown()
                            renderer.finish_thinking()
                        except KeyboardInterrupt:
                            renderer.finish_markdown()
                            renderer.finish_thinking()
                            renderer.print_warning("\nAPI prompt interrupted.")
                            agent.is_running = False
                            agent.clear_stop()
                        except Exception as ex:
                            renderer.finish_markdown()
                            renderer.finish_thinking()
                            renderer.print_error(f"\nAPI prompt error: {ex}")
                    continue
            except Exception:
                pass

        user_input = input_handler.get_input(plain_prompt, view)
        if user_input == SENTINEL_OPEN_AGENTS:
            view = VIEW_AGENTS
            continue
        if user_input == SENTINEL_BACK:
            view = VIEW_PARENT
            renderer.print_info("Returned to parent agent view.")
            continue
        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            renderer.print_info("Saving session and shutting down Harness. Goodbye!")
            if agent.session is not None:
                agent.session_manager.save(agent.session)
            try:
                from harness.mesh.server import stop_server
                stop_server()
            except Exception:
                try:
                    from harness.mesh.server import stop_mesh
                    stop_mesh()
                except Exception:
                    pass
            break

        # Check slash command — but only for real commands; a prompt whose first
        # token is a path (pasted/swiped image or video) goes to the agent.
        if user_input.startswith("/") and is_command_input(user_input, commands.commands):
            _mirror_prompt(agent, user_input)
            command_result = commands.handle(user_input, agent, renderer)
            if command_result == VIEW_AGENTS:
                view = VIEW_AGENTS
            elif command_result == VIEW_PARENT:
                view = VIEW_PARENT
            continue

        # In the agents board view only commands, ESC, and /back drive the
        # session; free text would be misread as a task for the parent agent.
        if view == VIEW_AGENTS:
            renderer.print_info("In the agents view. Use /agent <id>, /back, or ESC to return.")
            continue

        # Free-text prompt → mirrored to Discord
        _mirror_prompt(agent, user_input)

        # Execute agent step (echo suppressed so keypresses during the run
        # never leak as ^C / ^[ control garbage into the terminal).
        try:
            with no_echo_stdin():
                for ev in agent.step(user_input):
                    renderer.render_agent_event(ev)
            renderer.finish_markdown()
            renderer.finish_thinking()
        except KeyboardInterrupt:
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_warning("\nExecution interrupted by user.")
            agent.is_running = False
            agent.clear_stop()
        except Exception as ex:
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_error(f"\nExecution error: {str(ex)}")
