"""
Terminal Renderer & TUI Engine for Harness.
Renders header HUDs, markdown streams, collapsible thinking, syntax diffs,
theme galleries, and hotkey footers using Rich and active theme styling.
"""
import os
import time
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.live import Live
from harness.themes import Theme, get_theme, THEMES, render_theme_preview
from harness.sysinfo import get_ram_usage_mb


# ── Terminal Renderer ────────────────────────────────────────────────────────

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
        self._subagent_open: Optional[str] = None

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
        return get_ram_usage_mb()

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
            "[bold]F2[/bold]: Agents  |  "
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

    def _sub_id(self, agent_id: str) -> str:
        return f"[bold {self.theme.secondary}]{agent_id}[/bold {self.theme.secondary}]"

    def _truncate(self, text: str, limit: int = 160) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def print_subagent_event(self, etype: str, data):
        """Render a live subagent activity event, clearly scoped to its agent."""
        if etype == "subagent_start":
            self._finish_markdown()
            self._finish_thinking()
            self._subagent_open = data.get("agent_id")
            aid = self._sub_id(data.get("agent_id", "?"))
            task = self._truncate(data.get("task", ""), 120)
            self.console.print(
                f"\n🐝 Subagent {aid} ([dim]{data.get('agent_type', 'general')}[/dim]) spawned"
                + (f" — [dim]{task}[/dim]" if task else "")
            )

        elif etype == "subagent_text_delta":
            aid = self._sub_id(data.get("agent_id", "?"))
            for line in str(data.get("text", "")).rstrip().split("\n"):
                stripped = line.strip()
                if not stripped:
                    self.console.print()
                    continue
                if stripped.startswith("```") or stripped.startswith("#"):
                    self.console.print(f"   {aid} [dim]▸[/dim] [bold]{self._truncate(stripped, 200)}[/bold]")
                else:
                    self.console.print(f"   {aid} [dim]▸[/dim] {self._truncate(stripped, 500)}")

        elif etype == "subagent_tool":
            aid = self._sub_id(data.get("agent_id", "?"))
            tname = data.get("tool", "tool")
            args = self._truncate(str(data.get("args", "")), 100)
            result = self._truncate(str(data.get("result", "")), 120)
            self.console.print(f"   {aid} [dim]└─[/dim] 🔧 [bold]{tname}[/bold] [dim]{args}[/dim]")
            if result:
                first = result.split("\n")[0]
                self.console.print(f"   {aid} [dim]   ✔[/dim] [dim]{first}[/dim]")

        elif etype == "subagent_message":
            aid = self._sub_id(data.get("agent_id", "?"))
            sender = data.get("sender", data.get("agent_id", "?"))
            recipient = data.get("recipient", "all")
            target = "ALL" if recipient == "all" else f"→ {recipient}"
            body = self._truncate(str(data.get("message", "")), 200)
            self.console.print(f"   {aid} [dim]└─[/dim] 📨 {sender} {target}: [bold]{body}[/bold]")

        elif etype == "subagent_end":
            aid = self._sub_id(data.get("agent_id", "?"))
            status = data.get("status", "completed")
            turns = data.get("turns", 0)
            status_color = "green" if status == "completed" else "red"
            out = self._truncate(str(data.get("output", "")), 300)
            self.console.print(f"   {aid} ⚡ [bold {status_color}]{status.upper()}[/bold {status_color}] in [bold]{turns}[/bold] turns")
            if out:
                after_actions = out.split("[Actions performed by this agent]")
                body = after_actions[0].strip()
                if body:
                    for line in body.split("\n")[:8]:
                        self.console.print(f"   {aid} [dim]↓[/dim] [dim]{self._truncate(line, 300)}[/dim]")
                if len(after_actions) > 1 and after_actions[1].strip():
                    self.console.print(f"   {aid} [dim]↳ [/dim][bold]action log:[/bold] {self._truncate(after_actions[1].strip(), 400)}")
            self._subagent_open = None
            self.console.print()

    def print_subagent_board(self, records):
        """Render an overview table of all subagents/spawned agents."""
        if not records:
            self.console.print(f"[{self.theme.muted}]No subagents have been spawned yet.[/{self.theme.muted}]")
            self.console.print("[dim]Run a task that delegates work, or use /subagent <type> <prompt>, or press F2.[/dim]\n")
            return
        table = Table(title="🐝 Agent Swarm Board", border_style=self.theme.border)
        table.add_column("ID", style=f"bold {self.theme.secondary}")
        table.add_column("Type", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Turns", justify="right")
        table.add_column("Last Tool", style="dim")
        table.add_column("Task", style="dim")

        for r in records:
            status_color = "green" if r.status == "completed" else ("red" if r.status in ("failed", "timeout") else "yellow")
            table.add_row(
                r.agent_id,
                r.agent_type,
                f"[{status_color}]{r.status.upper()}[/{status_color}]",
                str(r.turns),
                r.last_tool() or "-",
                self._truncate(r.task[:80], 80),
            )
        self.console.print(table)
        self.console.print("[dim]Detail: /agent <id>  |  Return to parent: ESC (or /back)[/dim]\n")

    def print_subagent_detail(self, record):
        """Render the full activity log for a single subagent."""
        if record is None:
            self.console.print(f"[{self.theme.error}]Unknown agent id.[/{self.theme.error}]")
            return
        lines = [
            f"🐝 [bold {self.theme.secondary}]{record.agent_id}[/bold {self.theme.secondary}] "
            f"([dim]{record.agent_type}[/dim]) — [bold]{record.status.upper()}[/bold] · {record.turns} turns",
            f"[dim]Task: {self._truncate(record.task, 300)}[/dim]",
        ]
        if record.messages:
            lines.append("")
            lines.append("[bold]Swarm messages:[/bold]")
            for m in record.messages:
                target = "ALL" if m.get("recipient") == "all" else f"→ {m.get('recipient')}"
                lines.append(f"  📨 {m.get('sender')} {target}: {self._truncate(m.get('body', ''), 300)}")
        if record.text_log:
            lines.append("")
            lines.append("[bold]Spoken output:[/bold]")
            for t in record.text_log:
                text = str(t).strip()
                if text:
                    lines.append(f"  ▸ {self._truncate(text, 600)}")
        if record.tool_calls:
            lines.append("")
            lines.append("[bold]Tool calls:[/bold]")
            for tc in record.tool_calls:
                lines.append(f"  🔧 [bold]{tc.get('tool')}[/bold] [dim]{self._truncate(tc.get('args', ''), 120)}[/dim]")
                if tc.get("result"):
                    first = str(tc.get("result", "")).split("\n")[0]
                    lines.append(f"     ✔ [dim]{self._truncate(first, 200)}[/dim]")
        if record.output:
            lines.append("")
            lines.append("[bold]Final report:[/bold]")
            lines.append(record.output[:4000])
        self.console.print(Panel("\n".join(lines), border_style=self.theme.border, title=f"Agent {record.agent_id}", expand=False))

    def render_agent_event(self, ev):
        """Render streaming agent events with live timing."""
        etype = ev.type
        data = ev.data

        if etype in ("subagent_start", "subagent_text_delta", "subagent_tool", "subagent_message", "subagent_end"):
            self.print_subagent_event(etype, data)
            return

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

        elif etype == "attachment":
            for f in data.get("files", []):
                kind = "🖼️ image" if f.get("type") == "image" else "🎞️ video"
                self.console.print(
                    f"  [{self.theme.accent}]{kind} attached: [bold]{f.get('path', '')}[/bold][/{self.theme.accent}]"
                )

        elif etype == "attachment_warning":
            self.console.print(f"[{self.theme.warning}]⚠️  {data.get('message', '')}[/{self.theme.warning}]")

        elif etype == "vfb_notice":
            provider = data.get("provider", "")
            model = data.get("model", "")
            labels = data.get("labels", "media")
            files = data.get("files", [])
            shown = ", ".join(os.path.basename(f) for f in files[:3])
            if len(files) > 3:
                shown += f" (+{len(files) - 3} more)"
            self.console.print(
                f"  [{self.theme.accent}]🕶️ Vision fallback: using [bold]{provider} ({model})[/bold] "
                f"to describe {labels}: [bold]{shown}[/bold][/{self.theme.accent}]"
            )

        elif etype == "vfb_result":
            provider = data.get("provider", "")
            model = data.get("model", "")
            self.console.print(
                f"  [{self.theme.success}]✔ {provider} ({model}) description embedded for "
                f"the text-only model[/{self.theme.success}]"
            )

        elif etype == "compaction":
            self._finish_markdown()
            before = data.get("before_tokens", 0)
            after = data.get("after_tokens", 0)
            saved = data.get("saved_tokens", 0)
            pct = data.get("reduction_pct", 0)
            tactics = data.get("tactics", {}) or {}
            trigger = data.get("trigger", "turn boundary")
            parts = []
            if tactics.get("payloads_truncated"):
                parts.append(f"collapsed {tactics['payloads_truncated']} oversized payloads")
            if tactics.get("groups_summarized"):
                parts.append(f"summarized {tactics['groups_summarized']} old turns")
            if tactics.get("checkpointed"):
                parts.append("consolidated checkpoint")
            tactic_str = " · ".join(parts) or "no compression needed"
            self.console.print(
                f"\n[{self.theme.accent}]📦 Context Auto-Compacted ([{self.theme.secondary}]{trigger}[/{self.theme.secondary}]):[/{self.theme.accent}] "
                f"{before:,} -> {after:,} tokens ([bold green]-{saved:,} tokens / {pct}%[/bold green]) · {tactic_str}\n"
            )

        elif etype == "step_end" and data.get("complete"):
            self._finish_markdown()
            self._finish_thinking()
            self.console.print()

        elif etype == "error":
            self._finish_markdown()
            self._finish_thinking()
            self.console.print(f"\n[{self.theme.error}]❌ {data.get('message', 'Unknown error')}[/{self.theme.error}]\n")

        elif etype == "security_warning":
            self.console.print(f"\n[{self.theme.warning}]🛡️ {data.get('message', '')}[/{self.theme.warning}]")

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
        table.add_column("Vision", justify="center")
        table.add_column("Thinking Support", justify="center")
        table.add_column("Reasoning Format", style="dim")

        for m in models_data:
            c_win = f"{m['context']:,} tokens"
            vision_badge = "[bold cyan]👁 Yes[/bold cyan]" if m.get("vision") else "[dim]No[/dim]"
            th_badge = "[bold green]✔ Yes[/bold green]" if m["thinking"] else "[dim]No[/dim]"
            ttype = m["thinking_type"] or "-"
            table.add_row(m["name"], c_win, f"{m['output']:,}", vision_badge, th_badge, ttype)

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
