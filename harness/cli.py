"""
Command Line Interface Entrypoint for Harness.
Supports interactive TUI mode, single-shot headless execution, stdin piping,
subcommands (config, keys, setup, theme), and granular runtime flags.
"""
import sys
import argparse
from typing import Optional
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

def handle_subcommands(args: list) -> bool:
    """Handle CLI subcommands: config, keys, setup, theme, update."""
    if not args:
        return False

    sub = args[0].lower()
    config = load_config()
    renderer = TerminalRenderer(config.theme)

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
        handle_update_command(renderer)
        return True

    elif sub == "discord":
        from harness.discord import run_discord_bot
        token = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        return run_discord_bot(config, token=token)

    return False


def handle_update_command(renderer: TerminalRenderer) -> None:
    """Handle the 'harness update' command to pull latest changes from upstream."""
    import subprocess
    
    try:
        # Check if we're in a git repo
        subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except subprocess.CalledProcessError:
        renderer.print_error("Not a git repository. Cannot update.")
        return
    except Exception as ex:
        renderer.print_error(f"Git error: {str(ex)}")
        return

    try:
        # Fetch latest changes
        renderer.print_info("Fetching latest changes from upstream...")
        subprocess.check_output(
            ["git", "fetch"],
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        
        # Check if behind
        is_behind, commits_behind = check_git_behind()
        if not is_behind:
            renderer.print_success("Already up to date!")
            return
        
        renderer.print_info(f"You are {commits_behind} commit{'s' if commits_behind > 1 else ''} behind. Updating...")
        
        # Pull changes
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode == 0:
            renderer.print_success("Successfully updated to the latest version!")
            if result.stdout:
                renderer.print_info(result.stdout.strip())
        else:
            renderer.print_error(f"Update failed: {result.stderr.strip()}")
            
    except subprocess.TimeoutExpired:
        renderer.print_error("Git operation timed out.")
    except subprocess.CalledProcessError as e:
        renderer.print_error(f"Git error: {e.output.decode('utf-8', errors='ignore') if e.output else str(e)}")
    except Exception as ex:
        renderer.print_error(f"Update failed: {str(ex)}")

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

Examples:
  harness                               # Launch interactive TUI
  harness "Implement a caching layer"   # Run single-shot task
  harness --mode plan "Design system"   # Plan mode (read-only architectural analysis)
  harness --super "Fix all unit tests"  # Autonomous Super Mode loop
  harness keys set openai               # Set API key securely
  harness theme preview dracula         # View visual theme preview card
  harness update                        # Update to latest version
  harness keys set discord <token>      # Store your Discord bot token
  harness discord                       # Start the Discord bot
  cat error.log | harness "Debug error" # Read piped stdin input
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
    parser.add_argument("-v", "--version", action="version", version=f"Harness v{__version__}")
    return parser

def main():
    # Intercept subcommands first
    if len(sys.argv) > 1 and sys.argv[1] in ("config", "keys", "setup", "theme", "update", "discord"):
        result = handle_subcommands(sys.argv[1:])
        if isinstance(result, int):  # e.g. `harness discord` returns a process exit code
            sys.exit(result)
        return

    parser = build_parser()
    args = parser.parse_args()

    config = load_config()

    # Check if user is behind upstream commits and warn them
    is_behind, commits_behind = check_git_behind()
    if is_behind:
        renderer = TerminalRenderer(config.theme)
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

    # Wire up computer-use tools (screen_capture, screen_analyze, computer_control, etc.)
    def _make_computer_controller():
        from harness.computer.controller import ComputerController
        return ComputerController()

    def _make_vision_describe(media_blocks, question=None, system_prompt=None):
        from harness.vision.describe import describe_media_blocks
        from harness.providers import get_provider
        vfb_prov_id = config.vfb_provider.strip()
        if not vfb_prov_id:
            return ("", "no vision fallback provider configured (set vfb_provider in config)")
        try:
            provider = get_provider(vfb_prov_id, config)
        except Exception as exc:
            return ("", f"could not init vision fallback provider '{vfb_prov_id}': {exc}")
        vfb_model = config.vfb_model.strip() or provider.default_model
        return describe_media_blocks(provider, vfb_model, media_blocks,
                                    question=question, system_prompt=system_prompt)

    def _make_browser_controller(**kwargs):
        from harness.browser.controller import BrowserController
        return BrowserController(**kwargs)

    agent = HarnessAgent(
        config=config,
        session=session,
        computer_controller_factory=_make_computer_controller,
        vision_describe=_make_vision_describe,
        browser_controller_factory=_make_browser_controller,
    )

    if full_prompt:
        # Non-interactive / Headless single-shot execution
        renderer = TerminalRenderer(config.theme)
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
