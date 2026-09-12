"""
Terminal Renderer & TUI Engine for Harness.
Renders header HUDs, markdown streams, collapsible thinking, syntax diffs,
theme galleries, and hotkey footers using Rich and active theme styling.
"""
import os
import time
import resource
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.live import Live
from harness.themes import Theme, get_theme, THEMES, render_theme_preview

class TerminalRenderer:
    """Renders rich UI elements with custom themes."""

    def __init__(self, theme_name: str = "cyberpunk"):
        self.console = Console()
        self.theme: Theme = get_theme(theme_name)
        self._current_thinking: str = ""
        self._is_thinking_visible = False
        self._thinking_start_time: float = 0.0
        self._thinking_line_chars: int = 0
        self._md_buffer: str = ""
        self._md_live: Optional[Live] = None

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

    def print_footer(self):
        """Display bottom shortcuts bar."""
        footer_text = (
            "[dim][bold]/help[/bold]: Help  |  "
            "[bold]/mode[/bold]: Switch Mode  |  "
            "[bold]/perm[/bold]: Permissions  |  "
            "[bold]/theme[/bold]: Themes  |  "
            "[bold]/models[/bold]: Model Picker  |  "
            "[bold]/btw[/bold]: Side Note  |  "
            "[bold]/exit[/bold]: Quit[/dim]"
        )
        self.console.print(f" {footer_text}")

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

    def finish_thinking(self):
        """Public method to close thinking panel on turn complete or interrupt."""
        self._finish_thinking()

    def finish_markdown(self):
        """Public method to close Live markdown display on turn complete or interrupt."""
        self._finish_markdown()

    def _finish_thinking(self):
        """Close out the thinking display panel and show timing stats."""
        if not self._is_thinking_visible:
            return
        elapsed = round(time.time() - self._thinking_start_time, 1)
        t_tokens = max(1, len(self._current_thinking) // 4)
        self.console.print()  # newline after streamed thinking text
        self.console.print(
            f"  [{self.theme.muted}]╰─ Finished thinking in {elapsed}s "
            f"(~{t_tokens:,} tokens)[/{self.theme.muted}]"
        )
        self.console.print()
        self._is_thinking_visible = False
        self._current_thinking = ""

    def _finish_markdown(self):
        """Flush the streamed markdown segment once and reset the buffer."""
        if self._md_live is not None:
            self._md_live.stop()
            self._md_live = None
        if self._md_buffer:
            self.console.print(Markdown(self._md_buffer, code_theme=self.theme.code_theme))
            self._md_buffer = ""

    def _md_preview(self) -> str:
        """Return only the tail of the buffer so the Live region stays bounded.

        Re-rendering the whole (ever-growing) document in a Live region causes
        the terminal to scroll and leak duplicate frames into scrollback.
        """
        try:
            height = self.console.height or 24
        except Exception:
            height = 24
        limit = max(3, min(height - 2, 40))
        lines = self._md_buffer.splitlines(keepends=True)
        if len(lines) <= limit:
            return self._md_buffer
        return "".join(lines[-limit:])

    def render_agent_event(self, ev):
        """Render streaming agent events with live timing."""
        etype = ev.type
        data = ev.data

        if etype == "reasoning_delta":
            text = str(data)
            self._current_thinking += text
            if not self._is_thinking_visible:
                self._thinking_start_time = time.time()
                self.console.print(
                    f"  [{self.theme.thinking}]╭─ 💭 Thinking ─────────────────────────────[/{self.theme.thinking}]"
                )
                self.console.print(f"  [{self.theme.thinking}]│ [/{self.theme.thinking}]", end="")
                self._is_thinking_visible = True
                self._thinking_line_chars = 0
            # Stream actual thinking text, wrapping at ~100 chars for readability
            for ch in text:
                if ch == "\n":
                    self.console.print()
                    self.console.print(f"  [{self.theme.thinking}]│ [/{self.theme.thinking}]", end="")
                    self._thinking_line_chars = 0
                else:
                    self.console.print(f"[{self.theme.thinking}]{ch}[/{self.theme.thinking}]", end="", highlight=False)
                    self._thinking_line_chars += 1
                    if self._thinking_line_chars >= 100 and ch == " ":
                        self.console.print()
                        self.console.print(f"  [{self.theme.thinking}]│ [/{self.theme.thinking}]", end="")
                        self._thinking_line_chars = 0

        elif etype == "text_delta":
            self._finish_thinking()
            self._md_buffer += str(data)
            preview = Markdown(self._md_preview(), code_theme=self.theme.code_theme)
            if self._md_live is None:
                self._md_live = Live(
                    preview,
                    console=self.console,
                    refresh_per_second=15,
                    vertical_overflow="ellipsis",
                    transient=True,
                )
                self._md_live.start()
            else:
                self._md_live.update(preview)

        elif etype == "tool_call_start":
            self._finish_markdown()
            self._finish_thinking()
            tname = data.get("name", "tool")
            args = data.get("arguments", {})
            self.console.print(f"\n[{self.theme.secondary}]🔧 Invoking Tool: [bold]{tname}[/bold][/{self.theme.secondary}]")
            if args:
                preview = str(args)
                if len(preview) > 120:
                    preview = preview[:120] + "..."
                self.console.print(f"   [dim]args: {preview}[/dim]")

        elif etype == "tool_call_result":
            self._finish_markdown()
            res = str(data.get("result", ""))
            first_line = res.strip().split("\n")[0] if res.strip() else "(empty)"
            if len(first_line) > 140:
                first_line = first_line[:140] + "..."
            self.console.print(f"   [{self.theme.success}]✔ Result:[/{self.theme.success}] [dim]{first_line}[/dim]\n")

        elif etype == "compaction":
            self._finish_markdown()
            before = data.get("before_tokens", 0)
            after = data.get("after_tokens", 0)
            saved = data.get("saved_tokens", 0)
            pct = data.get("reduction_pct", 0)
            self.console.print(
                f"\n[{self.theme.accent}]📦 Context Auto-Compacted:[/{self.theme.accent}] "
                f"{before:,} -> {after:,} tokens ([bold green]-{saved:,} tokens / {pct}%[/bold green])\n"
            )

        elif etype == "step_end" and data.get("complete"):
            self._finish_markdown()
            self._finish_thinking()
            self.console.print()

    def print_theme_gallery(self):
        """Display interactive gallery of all available themes with color swatches."""
        table = Table(title="🎨 Harness Theme Palette Gallery (14 Themes)", border_style=self.theme.border)
        table.add_column("Theme ID", style="bold white")
        table.add_column("Display Name", style="white")
        table.add_column("Palette Swatches")
        table.add_column("Syntax Style", style="dim")

        for k, t in THEMES.items():
            swatches = (
                f"[{t.primary}]■[/{t.primary}] "
                f"[{t.secondary}]■[/{t.secondary}] "
                f"[{t.accent}]■[/{t.accent}] "
                f"[{t.success}]■[/{t.success}] "
                f"[{t.warning}]■[/{t.warning}] "
                f"[{t.error}]■[/{t.error}] "
                f"[{t.thinking}]■[/{t.thinking}]"
            )
            table.add_row(k, t.display_name, swatches, t.code_theme)

        self.console.print(table)
        self.console.print("[dim]Switch theme: `/theme <id>`  |  Preview card: `/theme preview <id>`[/dim]\n")

    def print_models_catalog(self, provider_name: str, models_data: List[Dict[str, Any]]):
        """Display catalog of available models for provider."""
        table = Table(title=f"🤖 Models for Provider: {provider_name.upper()}", border_style=self.theme.border)
        table.add_column("Model Name", style=f"bold {self.theme.primary}")
        table.add_column("Context Window", justify="right")
        table.add_column("Max Output", justify="right")
        table.add_column("Thinking Support", justify="center")
        table.add_column("Reasoning Format", style="dim")

        for m in models_data:
            c_win = f"{m['context']:,} tokens"
            th_badge = "[bold green]✔ Yes[/bold green]" if m["thinking"] else "[dim]No[/dim]"
            ttype = m["thinking_type"] or "-"
            table.add_row(m["name"], c_win, f"{m['output']:,}", th_badge, ttype)

        self.console.print(table)
        self.console.print(f"[dim]Switch model: `/model <name>`[/dim]\n")

    def print_markdown(self, md_text: str):
        self.console.print(Markdown(md_text, code_theme=self.theme.code_theme))

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
        if any(tok in msg for tok in ("**", "```", "###", "- **", " *", "|")):
            self.console.print(f"[{self.theme.text}]ℹ️ [/{self.theme.text}]", end=" ")
            self.console.print(Markdown(msg, code_theme=self.theme.code_theme))
        else:
            self.console.print(f"[{self.theme.text}]ℹ️  {msg}[/{self.theme.text}]")
