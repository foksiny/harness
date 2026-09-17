"""
Interactive User Questioning Tool for Harness.
Allows the model to proactively prompt the user for clarification, design decisions,
or preference selection during execution.
"""
from typing import Dict, Any, List, Optional, Callable
from harness.tools.base import Tool

class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user a structured question when encountering genuine ambiguity, "
        "multiple valid architectural paths, or requiring user confirmation."
    )
    action_type = "ask_user"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The exact question to present to the user."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of selectable answer options (optional).",
            },
            "allow_custom": {"type": "boolean", "description": "Whether the user can type a custom response (default: true)."},
            "recommended_option": {"type": "string", "description": "Which option is recommended by the model (optional)."},
        },
        "required": ["question"],
    }

    def __init__(self, interactive_handler: Optional[Callable[[str, List[str], bool, Optional[str]], str]] = None):
        self.interactive_handler = interactive_handler

    @staticmethod
    def _restore_echo_for_input(prompt: str) -> str:
        """Read input with terminal echo temporarily restored.

        The TUI wraps agent execution in `no_echo_stdin()` which disables ECHO
        via termios to prevent Ctrl-C/ESC noise. While that context is active
        `input()` would be invisible. This helper briefly restores echo, reads,
        echoes the typed line visibly, and lets the outer `no_echo` re-apply.
        """
        import sys
        # Try to restore echo via termios if we're in no_echo
        restored = False
        old_attrs = None
        fd = None
        try:
            import termios
            try:
                fd = sys.stdin.fileno()
                if sys.stdin.isatty():
                    old_attrs = termios.tcgetattr(fd)
                    # Check if ECHO is off, restore it
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
            # Use rich if available for nicer prompt, but ensure input is visible
            try:
                raw = input(prompt)
            finally:
                # Re-disable echo if we restored it (outer context will also restore, but be safe)
                if restored and old_attrs is not None and fd is not None:
                    try:
                        import termios
                        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
                    except Exception:
                        pass
            # Echo the typed value visibly with a rich-styled confirmation
            # (input() already echoed via terminal, this is an extra explicit line)
            try:
                from rich.console import Console
                Console().print(f"[dim]↳ You typed:[/dim] [bold cyan]{raw}[/bold cyan]")
            except Exception:
                print(f"↳ You typed: {raw}")
            return raw
        except (EOFError, KeyboardInterrupt):
            raise

    def execute(
        self,
        question: str,
        options: Optional[List[str]] = None,
        allow_custom: bool = True,
        recommended_option: Optional[str] = None,
        **kwargs,
    ) -> str:
        # Use custom UI handler if attached (e.g. from TUI)
        if self.interactive_handler:
            try:
                return self.interactive_handler(question, options or [], allow_custom, recommended_option)
            except Exception:
                # Fall through to terminal fallback if handler fails
                pass

        # Terminal Fallback Prompt — rich, detailed, and echo-visible
        opts = options or []
        # Try rich rendering for more details
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
            import time as _time
            console = Console()
            # Header panel with question and metadata
            meta_lines = [
                f"[bold white]{question}[/bold white]",
                "",
                f"[dim]Time: {_time.strftime('%Y-%m-%d %H:%M:%S')}  |  Options: {len(opts)}  |  Custom: {'yes' if allow_custom else 'no'}[/dim]",
            ]
            if recommended_option:
                meta_lines.append(f"[yellow]★ Recommended: {recommended_option}[/yellow]")
            # Options table
            if opts:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("No.", style="bold cyan", width=4)
                table.add_column("Option", style="white")
                table.add_column("Badge", style="yellow")
                for idx, opt in enumerate(opts, 1):
                    badge = "★ RECOMMENDED" if (recommended_option and recommended_option.lower() in opt.lower()) else ""
                    table.add_row(f"[{idx}]", opt, badge)
                if allow_custom:
                    table.add_row("[0]", "[dim]Type a custom write-in response[/dim]", "")
                console.print(Panel("\n".join(meta_lines), title="🤖 HARNESS — Question", border_style="cyan", padding=(1, 2)))
                console.print(table)
                console.print("[dim]Tip: type number, or type your answer directly. Your typing will be shown as  ↳ You typed: ...[/dim]")
            else:
                console.print(Panel("\n".join(meta_lines), title="🤖 HARNESS — Question", border_style="cyan", padding=(1, 2)))
                console.print("[dim]Your typing will be echoed visibly.[/dim]")

            print()
            raw = self._restore_echo_for_input("Your selection (number or custom answer): " if opts else "Your answer: ").strip()
            # Detailed confirmation panel
            if raw.isdigit() and opts:
                val = int(raw)
                if 1 <= val <= len(opts):
                    ans = opts[val - 1]
                    console.print(Panel(f"[bold green]✔ Selected option [{val}]:[/bold green]\n[white]{ans}[/white]\n\n[dim]This will be sent to the model as:\n  User selected option [{val}]: {ans}[/dim]", border_style="green"))
                    return f"User selected option [{val}]: {ans}"
                elif val == 0 and allow_custom:
                    console.print("[dim]Custom write-in selected — please provide your answer:[/dim]")
                    custom = self._restore_echo_for_input("Enter your custom answer: ").strip()
                    console.print(Panel(f"[bold green]✔ Custom response:[/bold green]\n[white]{custom}[/white]", border_style="green"))
                    return f"User wrote custom response: {custom}"
            if raw:
                # Check if raw matches an option text exactly
                for idx, opt in enumerate(opts, 1):
                    if raw.lower() == opt.lower():
                        console.print(Panel(f"[bold green]✔ Matched option [{idx}]:[/bold green]\n[white]{opt}[/white]", border_style="green"))
                        return f"User selected option [{idx}]: {opt}"
                console.print(Panel(f"[bold green]✔ You replied:[/bold green]\n[white]{raw}[/white]\n\n[dim]Sent to model as:\n  User replied: {raw}[/dim]", border_style="green"))
                return f"User replied: {raw}"
            # Fallback
            def_ans = recommended_option or (opts[0] if opts else "")
            if def_ans:
                console.print(Panel(f"[yellow]⚠ No input — defaulting to:[/yellow]\n[white]{def_ans}[/white]", border_style="yellow"))
                return f"User defaulted to: {def_ans}"
            return "User provided no answer."
        except (EOFError, KeyboardInterrupt):
            try:
                from rich.console import Console
                Console().print("\n[dim]Skipped / cancelled.[/dim]")
            except Exception:
                print("\nSkipped / cancelled.")
            return "User skipped / cancelled question prompt."
        except Exception:
            # Fallback to plain print if rich fails
            pass

        # Plain fallback (no rich)
        print("\n" + "=" * 60)
        print("🤖 [HARNESS IS ASKING FOR YOUR INPUT]")
        print(f"❓ {question}\n")
        if opts:
            for idx, opt in enumerate(opts, 1):
                rec_badge = " [RECOMMENDED]" if (recommended_option and recommended_option.lower() in opt.lower()) else ""
                print(f"  [{idx}] {opt}{rec_badge}")
            if allow_custom:
                print(f"  [0] Type a custom write-in response")
            print("=" * 60)
            try:
                raw = self._restore_echo_for_input("Your selection (number or custom answer): ").strip()
                if raw.isdigit():
                    val = int(raw)
                    if 1 <= val <= len(opts):
                        ans = opts[val - 1]
                        print(f"\n✔ Selected option [{val}]: {ans}")
                        print(f"  → Sent to model as: User selected option [{val}]: {ans}\n")
                        return f"User selected option [{val}]: {ans}"
                    elif val == 0 and allow_custom:
                        custom = self._restore_echo_for_input("Enter your custom answer: ").strip()
                        print(f"\n✔ Custom response: {custom}\n")
                        return f"User wrote custom response: {custom}"
                if raw:
                    print(f"\n✔ You replied: {raw}\n")
                    return f"User replied: {raw}"
                def_ans = recommended_option or opts[0]
                print(f"\n⚠ No input — defaulting to: {def_ans}\n")
                return f"User defaulted to: {def_ans}"
            except (EOFError, KeyboardInterrupt):
                print("\nSkipped / cancelled.")
                return "User skipped / cancelled question prompt."
        else:
            print("=" * 60)
            try:
                raw = self._restore_echo_for_input("Your answer: ").strip()
                print(f"\n✔ You replied: {raw}\n" if raw else "\n⚠ No answer.\n")
                return f"User replied: {raw}" if raw else "User provided no answer."
            except (EOFError, KeyboardInterrupt):
                print("\nSkipped question.")
                return "User skipped question."
