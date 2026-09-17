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


def run_interactive(agent: HarnessAgent):
    """Run interactive terminal session."""
    renderer = TerminalRenderer(agent.config.theme)
    commands = CommandRegistry()
    input_handler = InputHandler()

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
