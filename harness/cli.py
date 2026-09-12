"""
Command Line Interface Entrypoint for Harness.
Supports interactive TUI mode, single-shot headless execution, stdin piping,
and granular runtime flag configuration.
"""
import sys
import argparse
from typing import Optional
from harness import __version__
from harness.config import load_config, save_config, HarnessConfig
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.core.agent import HarnessAgent
from harness.tui.terminal import TerminalRenderer
from harness.tui.interactive import run_interactive

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Harness: The Premier Agentic AI Engineering Harness & CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  harness                               # Launch interactive TUI
  harness "Implement a caching layer"   # Run single-shot task
  harness --mode plan "Design system"   # Plan mode (read-only architectural analysis)
  harness --super "Fix all unit tests"  # Autonomous Super Mode loop
  cat error.log | harness "Debug error" # Read piped stdin input
        """,
    )
    parser.add_argument("prompt", nargs="?", help="Initial user prompt or instruction (optional).")
    parser.add_argument("-m", "--mode", choices=["plan", "build", "super"], help="Execution mode (plan, build, super).")
    parser.add_argument("-p", "--perm", choices=["secure", "default", "full"], help="Permission profile (secure, default, full).")
    parser.add_argument("-s", "--super", action="store_true", help="Shortcut for --mode super (Autonomous Turbo Engine).")
    parser.add_argument("--provider", help="LLM Provider (anthropic, openai, gemini, openrouter, nvidia, etc.).")
    parser.add_argument("--model", help="Specific model name.")
    parser.add_argument("--theme", help="Color theme (cyberpunk, dracula, nord, monokai, catppuccin, matrix, minimal).")
    parser.add_argument("--effort", help="Thinking / reasoning effort (off, low, medium, high, or integer tokens).")
    parser.add_argument("--resume", help="Resume existing session by ID.")
    parser.add_argument("-v", "--version", action="version", version=f"Harness v{__version__}")
    return parser

def main():
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
