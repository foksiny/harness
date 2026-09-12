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

def handle_subcommands(args: list) -> bool:
    """Handle CLI subcommands: config, keys, setup, theme."""
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

    return False

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

Examples:
  harness                               # Launch interactive TUI
  harness "Implement a caching layer"   # Run single-shot task
  harness --mode plan "Design system"   # Plan mode (read-only architectural analysis)
  harness --super "Fix all unit tests"  # Autonomous Super Mode loop
  harness keys set openai               # Set API key securely
  harness theme preview dracula         # View visual theme preview card
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
    if len(sys.argv) > 1 and sys.argv[1] in ("config", "keys", "setup", "theme"):
        handle_subcommands(sys.argv[1:])
        return

    parser = build_parser()
    args = parser.parse_args()

    config = load_config()

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

    agent = HarnessAgent(config=config, session=session)

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
        run_interactive(agent)

if __name__ == "__main__":
    main()
