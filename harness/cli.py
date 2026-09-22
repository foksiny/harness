"""
Command Line Interface Entrypoint for Harness.
Supports interactive TUI mode, single-shot headless execution, stdin piping,
subcommands (config, keys, setup, theme), and granular runtime flags.
"""
import sys
import argparse
from typing import Optional, List
from harness import __version__
from harness.config import load_config, save_config, mask_key, HarnessConfig
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.core.agent import HarnessAgent
from harness.tui.terminal import TerminalRenderer
from harness.tui.interactive import run_interactive
from harness.themes import THEMES, render_theme_preview
from harness.commands.config_cmd import (
    display_config_table,
    display_keys_table,
    run_setup_wizard,
)
from harness.tools.git_tools import check_git_behind, get_git_update_command

def handle_server_command(args: list, renderer: TerminalRenderer, config) -> None:
    """Handle `harness serve` / `harness mesh` status."""
    # Support both `harness serve` and legacy `harness mesh`
    sub = args[0].lower()
    if sub == "serve":
        # `harness serve [--host HOST] [--port PORT] [--token TOKEN]`
        # Run as headless API server (no TUI)
        import argparse as _ap
        p = _ap.ArgumentParser(prog="harness serve")
        p.add_argument("--host", default=getattr(config, "server_host", "127.0.0.1"), help="Bind host (0.0.0.0 for VPS)")
        p.add_argument("--port", type=int, default=int(getattr(config, "server_port", 0) or 0), help="Port (0=auto hash-based)")
        p.add_argument("--token", default=getattr(config, "server_token", "") or "", help="Bearer token for auth")
        try:
            ns, _ = p.parse_known_args(args[1:])
        except SystemExit:
            return
        host = ns.host
        port = ns.port
        token = ns.token
        # Update config for this run
        config.server_host = host
        config.server_port = port
        if token:
            config.server_token = token
        renderer.print_info(f"Starting Harness API server at http://{host}:{port if port else 'auto'} (workspace {__import__('os').getcwd()})...")
        from harness.core.agent import HarnessAgent
        from harness.mesh.server import start_server
        import time as _time
        agent = HarnessAgent(config=config)
        srv = start_server(workspace=__import__("os").getcwd(), host=host, port=port if port else None, agent_ref=lambda: agent, config=config, enable=True)
        if srv is None:
            renderer.print_error("Failed to start API server.")
            return
        renderer.print_success(f"API server running at http://{srv.host}:{srv.port}")
        renderer.print_info(f"  POST http://{srv.host}:{srv.port}/api/prompt {{\"prompt\": \"...\"}}")
        renderer.print_info(f"  GET  http://{srv.host}:{srv.port}/health")
        renderer.print_info(f"  Use HARNESS_API_TOKEN env or --token for auth. Press Ctrl+C to stop.")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            renderer.print_info("Shutting down API server...")
            try:
                from harness.mesh.server import stop_server
                stop_server()
            except Exception:
                pass
        return

    # Legacy `harness mesh` / `harness server status`
    from harness.mesh.server import get_server, get_mesh
    from harness.mesh.port import compute_base_port
    import os, getpass
    sub2 = args[1].lower() if len(args) > 1 else "status"
    # Try new server first, fallback to old mesh
    srv = None
    try:
        srv = get_server()
    except Exception:
        srv = None
    if srv is None:
        try:
            srv = get_mesh()
        except Exception:
            srv = None
    if sub2 in ("status", "info"):
        if srv is None:
            ws = os.getcwd()
            user = getpass.getuser()
            base = compute_base_port(ws, user)
            renderer.print_info(
                f"API server not active in this process.\n"
                f"  Would bind to 127.0.0.1:{base}00-{base}99 (base {base} hash workspace+user).\n"
                f"  Workspace: {ws}\n  User: {user}\n"
                f"  Run `harness` to start TUI + API, or `harness serve --host 0.0.0.0 --port 8000` for VPS."
            )
        else:
            st = srv.get_status()
            renderer.print_info(
                f"API server active at http://{st.get('host')}:{st.get('port')}\n"
                f"  Workspace: {st.get('workspace')}\n"
                f"  Model: {st.get('provider')}/{st.get('model')}  busy={st.get('is_busy')}\n"
                f"  API: POST http://{st.get('host')}:{st.get('port')}/api/prompt"
            )
        return
    else:
        renderer.print_info("Usage: harness serve [--host HOST] [--port PORT] | harness mesh status")

def handle_subcommands(args: list) -> bool:
    """Handle CLI subcommands: config, keys, setup, theme, update, serve, mesh."""
    if not args:
        return False

    sub = args[0].lower()
    config = load_config()
    renderer = TerminalRenderer(config.theme, show_thinking=config.terminal_show_thinking)

    if sub == "setup":
        run_setup_wizard(config, renderer)
        return True

    elif sub == "config":
        action = args[1].lower() if len(args) > 1 else "list"
        if action == "list":
            display_config_table(config, renderer)
        elif action == "get" and len(args) > 2:
            k = args[2].lower()
            val = getattr(config, k, None)
            if val is not None:
                renderer.print_info(f"{k} = {val}")
            else:
                renderer.print_error(f"Unknown setting '{k}'")
        elif action == "set" and len(args) > 3:
            k, val = args[2].lower(), args[3]
            if config.set_field(k, val):
                save_config(config)
                renderer.print_success(f"Config updated: {k} = {val}")
            else:
                renderer.print_error(f"Failed to set '{k}' to '{val}'")
        else:
            renderer.print_info("Usage: harness config [list | get <key> | set <key> <val>]")
        return True

    elif sub == "keys":
        action = args[1].lower() if len(args) > 1 else "list"
        if action == "list":
            display_keys_table(config, renderer)
        elif action == "set" and len(args) > 2:
            pname = args[2].lower()
            key_val = args[3] if len(args) > 3 else ""
            if not key_val:
                import getpass
                key_val = getpass.getpass(f"Enter API key for {pname}: ").strip()
            if key_val:
                config.set_api_key(pname, key_val)
                save_config(config)
                renderer.print_success(f"API key stored for {pname} ({mask_key(key_val)})")
            else:
                renderer.print_warning("No key provided.")
        elif action == "remove" and len(args) > 2:
            pname = args[2].lower()
            if config.remove_api_key(pname):
                save_config(config)
                renderer.print_success(f"Removed API key for {pname}")
            else:
                renderer.print_warning(f"No key found for {pname}")
        else:
            renderer.print_info("Usage: harness keys [list | set <provider> [key] | remove <provider>]")
        return True

    elif sub == "theme":
        action = args[1].lower() if len(args) > 1 else "list"
        if action == "list":
            renderer.print_theme_gallery()
        elif action == "preview":
            tname = args[2].lower() if len(args) > 2 else config.theme
            if tname in THEMES:
                renderer.console.print(render_theme_preview(tname))
            else:
                renderer.print_error(f"Unknown theme '{tname}'")
        elif action in THEMES:
            config.theme = action
            save_config(config)
            renderer.print_success(f"Default theme updated to: {THEMES[action].display_name}")
            renderer.console.print(render_theme_preview(action))
        else:
            renderer.print_info(f"Usage: harness theme [list | preview <name> | <theme_name>]")
        return True

    elif sub == "update":
        handle_update_command(renderer, args=args[1:] if len(args) > 1 else [])
        return True

    elif sub == "discord":
        from harness.discord import run_discord_bot
        token = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        return run_discord_bot(config, token=token)

    elif sub in ("mesh", "serve", "server"):
        handle_server_command(args, renderer, config)
        return True

    return False


def handle_update_command(renderer: TerminalRenderer, args: Optional[List[str]] = None) -> None:
    """Handle the 'harness update' command with changelog preview and dependency sync."""
    from harness.core.updater import HarnessUpdater
    args = args or []
    check_only = "--check" in args
    force = "--force" in args or "-f" in args

    updater = HarnessUpdater()
    renderer.print_info("Checking for Harness updates...")

    info = updater.check_for_updates()
    if info.error:
        renderer.print_error(f"Update check failed: {info.error}")
        return

    if not info.is_behind:
        renderer.print_success(f"Harness is already up to date! (version {info.current_version})")
        return

    if info.is_git:
        renderer.print_info(
            f"Update available: [bold yellow]{info.commits_behind} commit{'s' if info.commits_behind > 1 else ''}[/bold yellow] "
            f"behind upstream [bold cyan]{info.branch or 'main'}[/bold cyan]."
        )
        if info.commits:
            renderer.print_info("[bold magenta]Recent commits:[/bold magenta]")
            for c in info.commits[:5]:
                renderer.console.print(f"  [dim]•[/dim] [white]{c}[/white]")
    else:
        renderer.print_info(f"New version available: [bold green]{info.latest_version}[/bold green] (current: {info.current_version})")

    if check_only:
        renderer.print_info("Run [bold cyan]harness update[/bold cyan] to install the latest version.")
        return

    renderer.print_info("Applying update...")
    result = updater.apply_update(force=force)

    if result.success:
        renderer.print_success(f"Successfully updated Harness to latest version!")
        if result.commits:
            renderer.print_info(f"Pulled {len(result.commits)} new commits.")
    else:
        renderer.print_error(f"Update failed: {result.error or result.message}")

def _maybe_start_server(config: HarnessConfig, agent=None, renderer=None) -> None:
    """Start the HTTP API server if enabled (replaces old mesh peer network).

    Binds to host/port from config (server_host/server_port) or auto hash-based
    port. No peer detection — just a single API endpoint for external clients
    and VPS usage. Best-effort: failure never blocks the CLI.
    """
    # Support both new server_* and old mesh_* config keys
    enabled = getattr(config, "server_enabled", None)
    if enabled is None:
        enabled = getattr(config, "mesh_enabled", True)
    if not enabled:
        return
    if getattr(config, "server_enabled", True) is False:
        return
    if getattr(config, "mesh_enabled", True) is False:
        # Old mesh flag also disables server if explicitly false
        # (but server_enabled takes precedence if set)
        if getattr(config, "server_enabled", True) is True and config.mesh_enabled is False:
            return
    if "PYTEST_CURRENT_TEST" in __import__("os").environ:
        return
    if __import__("os").environ.get("HARNESS_NO_MESH") == "1" or __import__("os").environ.get("HARNESS_NO_SERVER") == "1":
        return
    try:
        from harness.mesh.server import start_server
        host = getattr(config, "server_host", None) or getattr(config, "mesh_host", "127.0.0.1") or "127.0.0.1"
        port = getattr(config, "server_port", 0) or 0
        # 0 => auto hash-based (base*100+idx)
        srv = start_server(
            workspace=__import__("os").getcwd(),
            host=host,
            port=port if port else None,
            agent_ref=lambda: agent,
            config=config,
            enable=True,
        )
        if srv is None:
            return
        import atexit
        atexit.register(lambda: __import__("harness.mesh.server", fromlist=["stop_server"]).stop_server())
        if renderer:
            # Show single line, no peer prompt
            try:
                renderer.print_info(f"[api] API server at http://{srv.host}:{srv.port}  (POST /api/prompt, GET /health)")
            except Exception:
                pass
        else:
            try:
                print(f"[api] API server at http://{srv.host}:{srv.port}", flush=True)
            except Exception:
                pass
    except Exception as ex:
        try:
            print(f"[api] startup warning: {ex}", file=__import__("sys").stderr)
        except Exception:
            pass

# Backward compat alias
def _maybe_start_mesh(config, agent=None, renderer=None):
    return _maybe_start_server(config, agent, renderer)

def _maybe_start_discord_bot(config: HarnessConfig) -> None:
    """Start the Discord bot in a background daemon thread if auto_start is enabled.

    The thread shares the same in-memory config (with all API keys already
    resolved) so it never needs to touch the OS keyring or re-read config.json.
    Because it's a daemon thread it dies automatically when the CLI exits.
    For a persistent bot, run ``harness discord`` directly.
    """
    if not config.discord_auto_start:
        return
    token = config.get_discord_token()
    if not token:
        return
    try:
        import discord as _discord  # noqa: F401
    except ImportError:
        return

    # Eagerly resolve every known provider key into the in-memory dict so the
    # background thread never touches the keyring (which can fail off the main
    # thread on some platforms — especially macOS Keychain and Windows Credential
    # Locker).
    from harness.providers import PROVIDER_CONFIGS
    for prov in PROVIDER_CONFIGS:
        resolved = config.get_api_key(prov)
        if resolved:
            config.api_keys[prov] = resolved
    # Also resolve the Discord token itself
    if not config.api_keys.get("discord"):
        config.api_keys["discord"] = token

    import threading
    import traceback

    def _run():
        from harness.discord.bot import HarnessDiscordBot
        try:
            bot = HarnessDiscordBot(config, token=token, quiet=True)
            bot.run()
        except KeyboardInterrupt:
            pass
        except Exception:
            traceback.print_exc(file=sys.stderr)

    t = threading.Thread(target=_run, daemon=True, name="harness-discord-bot")
    t.start()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Harness: The Premier Agentic AI Engineering Harness & CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subcommands:
  setup                                 Launch interactive onboarding wizard
  config [list|get|set]                 Inspect or update configuration settings
  keys [list|set|remove]                Manage LLM provider API keys
  theme [list|preview]                  Browse or preview visual themes
  update                                Pull latest changes from upstream repository
  discord [token]                       Launch the Discord bot (bound to your Harness config)
  serve [--host HOST] [--port PORT]     Run as headless HTTP API server (for VPS)

Examples:
  harness                               # Launch interactive TUI (+ API at http://127.0.0.1:<port>/api/prompt)
  harness "Implement a caching layer"   # Run single-shot task
  harness --mode plan "Design system"   # Plan mode (read-only architectural analysis)
  harness --super "Fix all unit tests"  # Autonomous Super Mode loop
  harness serve --host 0.0.0.0 --port 8000  # VPS mode
  harness keys set openai               # Set API key securely
  harness theme preview dracula         # View visual theme preview card
  curl -X POST http://127.0.0.1:PORT/api/prompt -H "Content-Type: application/json" -d '{"prompt":"hello"}'
        """,
    )
    parser.add_argument("prompt", nargs="?", help="Initial user prompt or instruction (optional).")
    parser.add_argument("-m", "--mode", choices=["plan", "build", "super"], help="Execution mode (plan, build, super).")
    parser.add_argument("-p", "--perm", choices=["secure", "default", "full"], help="Permission profile (secure, default, full).")
    parser.add_argument("-s", "--super", action="store_true", help="Shortcut for --mode super (Autonomous Turbo Engine).")
    parser.add_argument("--provider", help="LLM Provider (anthropic, openai, gemini, openrouter, nvidia, etc.).")
    parser.add_argument("--model", help="Specific model name.")
    parser.add_argument("--theme", help="Color theme (14 available).")
    parser.add_argument("--effort", help="Thinking / reasoning effort (off, low, medium, high, or integer tokens).")
    parser.add_argument("--resume", help="Resume existing session by ID.")
    parser.add_argument("--host", help="API server bind host (for serve mode, default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="API server port (0=auto)")
    parser.add_argument("--server", action="store_true", help="Enable the HTTP API server (required for harness TUI; use serve for VPS).")
    parser.add_argument("--no-mesh", action="store_true", help="Disable the API server (deprecated, use --no-server).")
    parser.add_argument("--no-server", action="store_true", help="Disable the HTTP API server.")
    parser.add_argument("-v", "--version", action="version", version=f"Harness v{__version__}")
    return parser

def main():
    # Windows terminal compat: enable ANSI escapes and UTF-8 output
    from harness.tui.win_compat import enable_windows_vt_processing, ensure_utf8_stdout
    enable_windows_vt_processing()
    ensure_utf8_stdout()

    # Intercept subcommands first (serve/mesh handled here to avoid argparse)
    if len(sys.argv) > 1 and sys.argv[1] in ("config", "keys", "setup", "theme", "update", "discord", "mesh", "serve", "server"):
        result = handle_subcommands(sys.argv[1:])
        if isinstance(result, int):
            sys.exit(result)
        return

    parser = build_parser()
    args = parser.parse_args()

    config = load_config()

    # Check if user is behind upstream commits and warn them
    is_behind, commits_behind = check_git_behind()
    if is_behind:
        renderer = TerminalRenderer(config.theme, show_thinking=config.terminal_show_thinking)
        renderer.print_warning(
            f"You are {commits_behind} commit{'s' if commits_behind > 1 else ''} behind the upstream branch.\n"
            f"  Tip: Run '{get_git_update_command()}' to update to the latest version."
        )
        print()  # Add spacing after warning

    # Apply CLI argument overrides
    if args.mode:
        config.mode = args.mode
    elif args.super:
        config.mode = "super"

    if args.perm:
        config.permission = args.perm
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    if args.theme:
        config.theme = args.theme
    if args.effort:
        config.thinking_effort = args.effort
    # Server activation: disabled by default for `harness` TUI, requires explicit flag
    # `harness serve` subcommand always enables (handled separately), but for `harness`
    # we enable only if --server or --host/--port is given, or config server_enabled=true
    server_argv = sys.argv[1:]
    wants_server = args.server or args.host is not None or args.port is not None
    if wants_server:
        config.server_enabled = True
        config.mesh_enabled = True
    if args.no_mesh or args.no_server:
        config.server_enabled = False
        config.mesh_enabled = False
    if args.host:
        config.server_host = args.host
        # --host implies server enabled unless explicitly disabled
        if not args.no_server and not args.no_mesh:
            config.server_enabled = True
            config.mesh_enabled = True
    if args.port:
        config.server_port = args.port
        if not args.no_server and not args.no_mesh:
            config.server_enabled = True
            config.mesh_enabled = True

    # Detect piped stdin
    piped_content = ""
    if not sys.stdin.isatty():
        try:
            piped_content = sys.stdin.read()
        except Exception:
            pass

    full_prompt = args.prompt or ""
    if piped_content:
        if full_prompt:
            full_prompt = f"{full_prompt}\n\n[Piped Input]:\n{piped_content}"
        else:
            full_prompt = piped_content

    # Initialize Agent
    session = None
    if args.resume:
        from harness.core.session import SessionManager
        sm = SessionManager()
        session = sm.load(args.resume)
        if not session:
            print(f"Error: Session '{args.resume}' not found.", file=sys.stderr)
            sys.exit(1)

    agent = HarnessAgent(
        config=config,
        session=session,
    )

    # Start API server (best-effort, no peer mesh)
    _maybe_start_server(config, agent=agent, renderer=TerminalRenderer(config.theme, show_thinking=config.terminal_show_thinking) if not full_prompt else None)

    if full_prompt:
        # Non-interactive / Headless single-shot execution
        renderer = TerminalRenderer(config.theme, show_thinking=config.terminal_show_thinking)
        if agent.mode == Mode.SUPER:
            renderer.print_super_banner(full_prompt)

        try:
            for ev in agent.step(full_prompt):
                renderer.render_agent_event(ev)
        except KeyboardInterrupt:
            renderer.print_warning("\nAborted by user.")
            sys.exit(130)
        except Exception as ex:
            renderer.print_error(f"\nExecution failed: {str(ex)}")
            sys.exit(1)
    else:
        # Launch Interactive TUI
        _maybe_start_discord_bot(config)
        run_interactive(agent)

if __name__ == "__main__":
    main()
