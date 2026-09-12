"""
Terminal Renderer & TUI Engine for Harness.
Renders header HUDs, markdown streams, collapsible thinking, and syntax diffs
using Rich and active theme styling.
"""
import os
import resource
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from harness.themes import Theme, get_theme

class TerminalRenderer:
    """Renders rich UI elements with custom themes."""

    def __init__(self, theme_name: str = "cyberpunk"):
        self.console = Console()
        self.theme: Theme = get_theme(theme_name)
        self._current_thinking: str = ""
        self._is_thinking_visible = False

    def set_theme(self, theme_name: str):
        self.theme = get_theme(theme_name)

    def clear_screen(self):
        self.console.clear()

    def print_banner(self):
        """Display stylish ascii startup banner."""
        banner_text = """
██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗
██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝
███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗
██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║
██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║
╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝"""
        self.console.print(f"[{self.theme.primary}]{banner_text}[/{self.theme.primary}]")
        self.console.print(f"[{self.theme.muted}]The Premier Agentic AI Engineering Harness v1.0.0[/{self.theme.muted}]\n")

    def _get_ram_usage_mb(self) -> float:
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if "VmRSS:" in line:
                        kb = int(line.split()[1])
                        return round(kb / 1024, 1)
        except Exception:
            pass
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)

    def print_hud(self, mode: str, perm: str, provider: str, model: str, tokens: int, context_win: int, todos_summary: str = ""):
        """Display live header HUD with status indicators."""
        ram_mb = self._get_ram_usage_mb()
        pct = round((tokens / max(1, context_win)) * 100, 1)

        mode_color = {
            "plan": "bright_blue",
            "build": "bright_green",
            "super": "bright_magenta bold",
        }.get(mode.lower(), "white")

        perm_color = {
            "secure": "bright_yellow",
            "default": "bright_cyan",
            "full": "bright_red bold",
        }.get(perm.lower(), "white")

        hud_table = Table.grid(expand=True)
        hud_table.add_column(justify="left")
        hud_table.add_column(justify="right")

        left = (
            f"⚡ [{self.theme.primary}]Harness[/{self.theme.primary}] | "
            f"Mode: [{mode_color}]{mode.upper()}[/{mode_color}] | "
            f"Perm: [{perm_color}]{perm.upper()}[/{perm_color}] | "
            f"Model: [bold white]{model}[/bold white] ({provider})"
        )

        right = (
            f"Context: [{self.theme.accent}]{tokens:,}/{context_win:,} ({pct}%)[/{self.theme.accent}] | "
            f"RAM: [dim]{ram_mb}MB[/dim]"
        )

        hud_table.add_row(left, right)
        if todos_summary:
            hud_table.add_row(f"[dim]📋 Tasks: {todos_summary}[/dim]", "")

        self.console.print(Panel(hud_table, border_style=self.theme.border, padding=(0, 1)))

    def print_super_banner(self, goal: str):
        """Banner for entering Super Mode autonomous execution."""
        p = Panel(
            f"🚀 [bold magenta]SUPER MODE ACTIVATED[/bold magenta]\n"
            f"[bold white]Autonomous Goal:[/bold white] {goal}\n"
            f"[dim]Decomposing tasks, dispatching subagents, and self-verifying until verified...[/dim]",
            border_style="magenta",
            title="⚡ SUPER AGENT LOOP",
        )
        self.console.print(p)

    def print_btw_response(self, text: str):
        """Banner for out-of-band /btw answer."""
        p = Panel(
            Markdown(text),
            title="💡 [bold yellow]By-The-Way Side Note[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
        self.console.print(p)

    def render_agent_event(self, ev):
        """Render streaming agent events."""
        etype = ev.type
        data = ev.data

        if etype == "reasoning_delta":
            self._current_thinking += str(data)
            if not self._is_thinking_visible:
                self.console.print(f"[{self.theme.thinking}]💭 Thinking: [/{self.theme.thinking}]", end="")
                self._is_thinking_visible = True
            # Stream dots or subtle indicator
            self.console.print(f"[{self.theme.thinking}].", end="", highlight=False)

        elif etype == "text_delta":
            if self._is_thinking_visible:
                self.console.print(f" [{self.theme.muted}](Done thinking)[/{self.theme.muted}]\n")
                self._is_thinking_visible = False
            self.console.print(data, end="", highlight=False)

        elif etype == "tool_call_start":
            if self._is_thinking_visible:
                self.console.print(f" [{self.theme.muted}](Done thinking)[/{self.theme.muted}]\n")
                self._is_thinking_visible = False
            tname = data.get("name", "tool")
            args = data.get("arguments", {})
            self.console.print(f"\n[{self.theme.secondary}]🔧 Invoking Tool: [bold]{tname}[/bold][/{self.theme.secondary}]")
            if args:
                preview = str(args)
                if len(preview) > 120:
                    preview = preview[:120] + "..."
                self.console.print(f"   [dim]args: {preview}[/dim]")

        elif etype == "tool_call_result":
            res = str(data.get("result", ""))
            first_line = res.strip().split("\n")[0] if res.strip() else "(empty)"
            if len(first_line) > 140:
                first_line = first_line[:140] + "..."
            self.console.print(f"   [{self.theme.success}]✔ Result:[/{self.theme.success}] [dim]{first_line}[/dim]\n")

        elif etype == "compaction":
            before = data.get("before_tokens", 0)
            after = data.get("after_tokens", 0)
            saved = data.get("saved_tokens", 0)
            pct = data.get("reduction_pct", 0)
            self.console.print(
                f"\n[{self.theme.accent}]📦 Context Auto-Compacted:[/{self.theme.accent}] "
                f"{before:,} -> {after:,} tokens ([bold green]-{saved:,} tokens / {pct}%[/bold green])\n"
            )

        elif etype == "step_end" and data.get("complete"):
            self.console.print()

    def print_markdown(self, md_text: str):
        self.console.print(Markdown(md_text))

    def print_diff(self, diff_text: str):
        if not diff_text.strip():
            self.console.print(f"[{self.theme.muted}]No changes detected.[/{self.theme.muted}]")
            return
        syntax = Syntax(diff_text, "diff", theme=self.theme.code_theme, line_numbers=True)
        self.console.print(Panel(syntax, title="Git Diff", border_style=self.theme.border))

    def print_help(self, command_descriptions: Dict[str, str]):
        table = Table(title="Harness Commands & Shortcuts", border_style=self.theme.border)
        table.add_column("Command", style=f"bold {self.theme.primary}")
        table.add_column("Description", style="white")

        for cmd, desc in sorted(command_descriptions.items()):
            table.add_row(f"/{cmd}", desc)

        self.console.print(table)

    def print_success(self, msg: str):
        self.console.print(f"[{self.theme.success}]✔ {msg}[/{self.theme.success}]")

    def print_warning(self, msg: str):
        self.console.print(f"[{self.theme.warning}]⚠️  {msg}[/{self.theme.warning}]")

    def print_error(self, msg: str):
        self.console.print(f"[{self.theme.error}]❌ {msg}[/{self.theme.error}]")

    def print_info(self, msg: str):
        self.console.print(f"[{self.theme.text}]ℹ️  {msg}[/{self.theme.text}]")
