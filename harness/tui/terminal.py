"""
Terminal Renderer & TUI Engine for Harness.
Renders header HUDs, markdown streams, collapsible thinking, syntax diffs,
theme galleries, and hotkey footers using Rich and active theme styling.
"""
import os
import re
import time
from typing import Dict, Any, List, Optional
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.align import Align
from harness import __version__
from harness.providers.base import probe_effort_options
from harness.themes import Theme, get_theme, THEMES, render_theme_preview
from harness.sysinfo import get_ram_usage_mb


class DynamicStdout:
    """Delegates writes to the active sys.stdout, supporting prompt_toolkit.patch_stdout."""
    def write(self, s):
        import sys
        return sys.stdout.write(s)
    def flush(self):
        import sys
        return sys.stdout.flush()
    def isatty(self):
        import sys
        return getattr(sys.stdout, "isatty", lambda: True)()
    def fileno(self):
        import sys
        return sys.stdout.fileno()
    def __getattr__(self, name):
        import sys
        return getattr(sys.stdout, name)


class TerminalRenderer:
    """Renders rich UI elements with custom themes."""

    def __init__(self, theme_name: str = "cyberpunk"):
        self.console = Console(file=DynamicStdout())
        self.theme: Theme = get_theme(theme_name)
        self._current_thinking: str = ""
        self._thinking_buffer: str = ""
        self._is_thinking_visible = False
        self._thinking_start_time: float = 0.0
        self._thinking_line_chars: int = 0
        self._md_buffer: str = ""
        self._md_stream_started: bool = False
        self._md_last_blank: bool = False
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
        self.console.print(f"[{self.theme.muted}]The Premier Agentic AI Engineering Harness v{__version__}[/{self.theme.muted}]\n")

    def _get_ram_usage_mb(self) -> float:
        return get_ram_usage_mb()

    def _get_git_branch(self) -> Optional[str]:
        """Best-effort git branch detection."""
        try:
            import subprocess
            res = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=1,
            ).decode("utf-8").strip()
            return res if res and res != "HEAD" else None
        except Exception:
            return None

    def _render_context_bar(self, tokens: int, max_tokens: int, width: int = 8) -> str:
        """Render a mini visual context gauge."""
        pct = min(1.0, max(0.0, tokens / max(1, max_tokens)))
        filled = int(round(pct * width))
        empty = width - filled
        color = self.theme.success if pct < 0.6 else (self.theme.warning if pct < 0.85 else f"bold {self.theme.error}")
        bar = f"[{color}]{'■' * filled}{'□' * empty}[/{color}]"
        return f"{bar} {tokens:,}/{max_tokens:,} ({round(pct * 100, 1)}%)"

    def print_hud(
        self,
        mode: str,
        perm: str,
        provider: str,
        model: str,
        tokens: int,
        context_win: int,
        todos_summary: str = "",
        status: str = "IDLE",
        queue_size: int = 0,
    ):
        """Display live header HUD with modern cards and status indicators."""
        ram_mb = self._get_ram_usage_mb()
        branch = self._get_git_branch()
        ws_name = os.path.basename(os.getcwd()) or "workspace"

        mode_color = {
            "plan": "bright_blue bold",
            "build": "bright_green bold",
            "super": "bright_magenta bold",
        }.get(mode.lower(), "white")

        perm_color = {
            "secure": "bright_yellow",
            "default": "bright_cyan",
            "full": "bright_red bold",
        }.get(perm.lower(), "white")

        status_lower = status.lower()
        if "run" in status_lower or "busy" in status_lower:
            status_badge = f"[bold green]▶ RUNNING[/bold green]"
        elif "queue" in status_lower:
            status_badge = f"[bold yellow]⧗ QUEUED ({queue_size})[/bold yellow]"
        elif "stop" in status_lower:
            status_badge = f"[bold red]⏹ STOPPING[/bold red]"
        else:
            status_badge = f"[dim green]● IDLE[/dim green]"

        hud_table = Table.grid(expand=True)
        hud_table.add_column(justify="left")
        hud_table.add_column(justify="right")

        branch_str = f" [dim]({branch})[/dim]" if branch else ""
        left_top = f"{status_badge}  📂 [bold white]{ws_name}[/bold white]{branch_str}"
        right_top = f"Model: [bold white]{model}[/bold white] [dim]({provider})[/dim]"

        hud_table.add_row(left_top, right_top)

        # Second row: Mode/Perm on left, Context gauge + queue + RAM on right
        left_bottom = f"Mode: [{mode_color}]{mode.upper()}[/{mode_color}]  [dim]│[/dim]  Perm: [{perm_color}]{perm.upper()}[/{perm_color}]"

        ctx_bar = self._render_context_bar(tokens, context_win)
        right_items = [f"Context: {ctx_bar}"]
        if queue_size > 0:
            right_items.append(f"[bold yellow]⧗ Queue: {queue_size}[/bold yellow]")
        right_items.append(f"RAM: [dim]{ram_mb}MB[/dim]")
        right_bottom = "  [dim]│[/dim]  ".join(right_items)

        hud_table.add_row(left_bottom, right_bottom)

        if todos_summary:
            hud_table.add_row(f"[dim]📋 Tasks: {todos_summary}[/dim]", "")

        title = f"⚡ [{self.theme.primary}]HARNESS[/] · [dim]Agentic Coding & Orchestration[/dim]"
        self.console.print(Panel(hud_table, border_style=self.theme.border, padding=(0, 1), title=title, title_align="left"))

    def print_queue_table(self, queue):
        """Render a rich table of all pending and recently completed queue items."""
        if queue is None or (queue.size() == 0 and not queue.current_item and not queue.list_history()):
            self.console.print(Panel(
                f"[{self.theme.muted}]Queue is currently empty.[/{self.theme.muted}]\n"
                f"[dim]Tip: You can type prompts at any time while the agent is running; "
                f"they will automatically queue and run in sequence.[/dim]",
                title="⧗ Execution Queue",
                border_style=self.theme.border,
            ))
            return

        table = Table(title="⧗ Harness Prompt Execution Queue", border_style=self.theme.border, expand=True)
        table.add_column("#", style=f"bold {self.theme.secondary}", width=4, justify="right")
        table.add_column("Status", justify="center", width=12)
        table.add_column("Prompt", style="white")
        table.add_column("Wait / Duration", justify="right", style="dim", width=16)

        # Active item
        curr = queue.current_item
        if curr:
            dur = f"{curr.duration:.1f}s" if curr.duration else "-"
            table.add_row(
                f"[bold green]{curr.id}[/bold green]",
                "[bold green]▶ RUNNING[/bold green]",
                f"[bold white]{self._truncate(curr.prompt, 100)}[/bold white]",
                f"running ({dur})",
            )

        # Pending items
        for item in queue.list_pending():
            wait_str = f"{item.wait_time:.1f}s"
            table.add_row(
                str(item.id),
                "[bold yellow]⧗ QUEUED[/bold yellow]",
                self._truncate(item.prompt, 100),
                f"waiting {wait_str}",
            )

        # History
        for h_item in queue.list_history(limit=5):
            st = h_item.status
            st_color = "green" if st == "completed" else "red"
            dur_str = f"{h_item.duration:.1f}s" if h_item.duration else "-"
            table.add_row(
                f"[dim]{h_item.id}[/dim]",
                f"[{st_color}]{st.upper()}[/{st_color}]",
                f"[dim]{self._truncate(h_item.prompt, 100)}[/dim]",
                f"[dim]{dur_str}[/dim]",
            )

        self.console.print(table)
        paused_note = " [yellow](QUEUE IS PAUSED - use /queue resume)[/yellow]" if queue.is_paused else ""
        self.console.print(f"[dim]Manage: /queue drop <id>  |  /queue clear  |  /queue pause  |  /queue resume{paused_note}[/dim]\n")

    def print_queue_event(self, item, action: str = "enqueued"):
        """Render a clean notification when a task is enqueued or started from queue."""
        if action == "enqueued":
            self.console.print(
                f"  [{self.theme.accent}]⧗ Queued task [bold]#{item.id}[/bold]:[/{self.theme.accent}] "
                f"[white]{self._truncate(item.prompt, 90)}[/white] "
                f"[dim](will run after current task)[/dim]"
            )
        elif action == "started":
            self.console.print(
                f"\n[{self.theme.primary}]▶ Processing Queued Task [bold]#{item.id}[/bold]:[/{self.theme.primary}] "
                f"[bold white]{self._truncate(item.prompt, 100)}[/bold white]"
            )
        elif action == "dropped":
            self.console.print(
                f"  [{self.theme.warning}]✖ Dropped task [bold]#{item.id}[/bold] from queue.[/{self.theme.warning}]"
            )

    def print_status_card(self, agent, queue=None):
        """Display an extensive system, agent, budget, and queue status card."""
        ram_mb = self._get_ram_usage_mb()
        branch = self._get_git_branch() or "detached"
        tokens = 0
        if agent.session is not None:
            from harness.core.compaction import calculate_history_tokens
            tokens = calculate_history_tokens(agent.session.messages)
        c_win = agent.compactor.context_window
        pct = round((tokens / max(1, c_win)) * 100, 1)

        q_size = queue.size() if queue else 0
        q_curr = queue.current_item if queue else None

        lines = [
            f"⚡ [bold {self.theme.primary}]HARNESS SYSTEM STATUS[/bold {self.theme.primary}]",
            "",
            f"  [bold]Workspace:[/bold]   {os.getcwd()} [dim](git branch: {branch})[/dim]",
            f"  [bold]Session ID:[/bold]  {agent.session.id if agent.session else 'none'}",
            f"  [bold]Agent Mode:[/bold]  {agent.mode.value.upper()}",
            f"  [bold]Permission:[/bold]  {agent.permission_manager.level.value.upper()}",
            f"  [bold]Provider:[/bold]    {agent.provider.display_name} ({agent.config.provider})",
            f"  [bold]Model:[/bold]       {agent.session.model if agent.session else agent.config.model}",
            f"  [bold]Effort:[/bold]      {agent.config.thinking_effort}",
            f"  [bold]Context:[/bold]     {self._render_context_bar(tokens, c_win)}",
            f"  [bold]System RAM:[/bold]  {ram_mb} MB",
            f"  [bold]Queue:[/bold]       {q_size} pending" + (f" (Active: #{q_curr.id})" if q_curr else " (Idle)"),
            f"  [bold]Tasks Todo:[/bold]  {agent.todo_manager.summary() or 'None'}",
        ]
        self.console.print(Panel("\n".join(lines), border_style=self.theme.border, title="System Status", expand=False))

    def print_footer(self):
        """Display bottom shortcuts bar."""
        footer_text = (
            "[dim][bold]/help[/bold]: Help  |  "
            "[bold]/queue[/bold]: Queue  |  "
            "[bold]/stop[/bold]: Stop  |  "
            "[bold]/mode[/bold]: Mode  |  "
            "[bold]/perm[/bold]: Perm  |  "
            "[bold]/models[/bold]: Models  |  "
            "[bold]F2[/bold]: Swarm  |  "
            "[bold]/status[/bold]: Status  |  "
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

    def render_user_prompt(self, text: str):
        """Render user input with OpenCode vertical cyan bar style."""
        lines = text.strip().split("\n")
        self.console.print()
        for line in lines:
            self.console.print(f"  [bold cyan]│[/bold cyan] [bold white]{line}[/bold white]")
        self.console.print()

    def render_turn_capsule(self, mode: str, model: str, duration: float):
        """Render OpenCode completion capsule: ▣ Build · model · 2.4s"""
        self._finish_markdown()
        self._finish_thinking()
        dur_str = f"{duration:.1f}s" if duration >= 1.0 else f"{int(duration * 1000)}ms"
        mode_label = mode.capitalize() if mode else "Build"
        self.console.print(
            f"\n  [bold cyan]▣ {mode_label}[/bold cyan] [dim]·[/dim] "
            f"[dim white]{model}[/dim white] [dim]· {dur_str}[/dim]\n"
        )

    def print_welcome_splash(self, agent=None, queue=None):
        """Display OpenCode-identical minimalist welcome splash centered on screen."""
        self.console.clear()

        # 1. Centered block logo for HARNESS
        logo = (
            "[dim white]█[/dim white]\n"
            "[dim white]█▀█ █▀█ █▀█ [/dim white][bold white]█▄ █ █▀█ █▀▀ █▀▀[/bold white]\n"
            "[dim white]█ █ █▀█ █▀▄ [/dim white][bold white]█ ▀█ ██▄ ▄██ ▄██[/bold white]"
        )
        self.console.print()
        self.console.print(Align.center(logo))
        self.console.print()

        # 2. Shortcuts
        self.console.print(Align.center("[bold white]tab[/bold white] [dim]agents[/dim]    [bold white]ctrl+p[/bold white] [dim]commands[/dim]"))
        self.console.print()

        # 3. Tip
        self.console.print(Align.center("[bold yellow]• Tip[/bold yellow] [dim]Type[/dim] [bold white]/help[/bold white] [dim]for commands or[/dim] [bold white]/sidebar[/bold white] [dim]to inspect session[/dim]"))
        self.console.print()

        # 4. Bottom status row
        cwd = os.getcwd()
        short_cwd = "~" if cwd == os.path.expanduser("~") else os.path.basename(cwd) or cwd
        mcp_count = len(agent.mcp_manager.clients) if (agent and hasattr(agent, "mcp_manager")) else 0
        term_width = self.console.width or 80
        left_str = f"  [dim]{short_cwd}[/dim]  [bold green]●[/bold green] [dim]{mcp_count} MCP[/dim]  [dim]/status[/dim]"
        right_str = f"[dim]{__version__}[/dim]  "
        padding_len = max(2, term_width - 32)
        self.console.print(f"{left_str}{' ' * padding_len}{right_str}\n")

    def show_models_modal(self, provider_name: str, models_data: list, active_model: Optional[str] = None) -> Optional[str]:
        """Open interactive models modal overlay. Returns selected model name or None."""
        from harness.tui.modal import InteractiveModal, ModalItem
        items = []
        for m in models_data:
            mname = m.get("name", "")
            c_win = f"{m.get('context', 0):,}"
            badges = []
            if m.get("thinking"):
                badges.append("thinking")
            if m.get("vision"):
                badges.append("vision")
            badge_str = " · ".join(badges)
            is_active = (active_model and mname.lower() == active_model.lower()) or False
            items.append(ModalItem(
                id=mname,
                title=mname,
                subtitle=f"{c_win} tokens",
                badge=badge_str,
                category=f"{provider_name.upper()} Models",
                is_active=is_active,
                payload=mname,
            ))
        modal = InteractiveModal(
            title="Models",
            items=items,
            shortcuts="↑/↓ navigate  ↵ switch model  esc close",
            search_placeholder="Search models",
        )
        selected = modal.run(self.console)
        return selected.id if selected else None

    def show_sessions_modal(self, sessions: list, active_id: Optional[str] = None) -> Optional[str]:
        """Open interactive sessions modal overlay. Returns selected session id or None."""
        from harness.tui.modal import InteractiveModal, ModalItem
        items = []
        for s in sessions:
            sid = s.get("id", "")
            stitle = s.get("title") or "New session"
            model_tag = s.get("model", "")
            turns = s.get("turns", 0)
            is_active = (active_id and sid == active_id) or False
            items.append(ModalItem(
                id=sid,
                title=stitle,
                subtitle=f"{sid[:8]} · {model_tag} ({turns} turns)",
                category="Recent Sessions",
                is_active=is_active,
                payload=sid,
            ))
        modal = InteractiveModal(
            title="Sessions",
            items=items,
            shortcuts="↑/↓ navigate  ↵ resume session  esc close",
            search_placeholder="Search sessions",
        )
        selected = modal.run(self.console)
        return selected.id if selected else None

    def show_theme_modal(self, active_theme: Optional[str] = None) -> Optional[str]:
        """Open interactive themes modal overlay. Returns selected theme id or None."""
        from harness.tui.modal import InteractiveModal, ModalItem
        items = []
        for tid, t in THEMES.items():
            swatches = f"[{t.primary}]■[/{t.primary}] [{t.secondary}]■[/{t.secondary}] [{t.accent}]■[/{t.accent}] [{t.success}]■[/{t.success}] [{t.thinking}]■[/{t.thinking}]"
            is_active = active_theme and tid.lower() == active_theme.lower()
            items.append(ModalItem(
                id=tid,
                title=tid,
                subtitle=t.display_name,
                badge=swatches,
                category="Available Themes",
                is_active=is_active,
                payload=tid,
            ))
        modal = InteractiveModal(
            title="Themes",
            items=items,
            shortcuts="↑/↓ navigate  ↵ apply theme  esc close",
            search_placeholder="Search themes",
        )
        selected = modal.run(self.console)
        return selected.id if selected else None

    def show_help_modal(self, command_descriptions: Dict[str, str]) -> Optional[str]:
        """Open interactive command palette modal overlay. Returns selected command or None."""
        from harness.tui.modal import InteractiveModal, ModalItem

        # Dynamic generation - ensures all registered commands appear, matching print_help_modal.
        def _category(cmd: str) -> str:
            if cmd in ("help", "sidebar", "queue", "stop", "clear", "update", "discord"):
                return "Core Actions"
            if cmd in ("model", "models", "provider", "effort", "mode", "perm"):
                return "Model & Provider"
            if cmd in ("session", "status", "todo", "theme", "tokens", "compact", "checkpoint", "diff"):
                return "Session & Workspace"
            if cmd in ("config", "keys", "setup", "skills", "reload", "learn", "mcp"):
                return "Config & Tools"
            if cmd in ("subagent", "agents", "agent", "back", "mesh"):
                return "Subagents & Mesh"
            return "Commands"

        items = [
            ModalItem(
                id=f"/{cmd}",
                title=f"/{cmd}",
                subtitle=desc,
                category=_category(cmd),
            )
            for cmd, desc in sorted(command_descriptions.items())
        ]
        modal = InteractiveModal(
            title="Commands & Shortcuts",
            items=items,
            shortcuts="↑/↓ navigate  ↵ run command  esc close",
            search_placeholder="Search commands",
        )
        selected = modal.run(self.console)
        return selected.id if selected else None

    def print_modal_card(self, title: str, content_lines: list[str], shortcuts: str = "esc to close", width: int = 76):
        """Render an OpenCode-identical centered dark modal dialog card."""
        term_width = self.console.width or 80
        card_width = min(max(60, width), max(50, term_width - 4))

        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left")
        header_table.add_column(justify="right")
        header_table.add_row(f"[bold white]{title}[/bold white]", "[dim]esc[/dim]")

        items = [header_table, Text("")]
        for l in content_lines:
            if isinstance(l, str):
                items.append(Text.from_markup(l))
            else:
                items.append(l)
        items.append(Text(""))
        items.append(Text.from_markup(f"[dim]{shortcuts}[/dim]"))

        card = Panel(
            Group(*items),
            width=card_width,
            border_style="#2a2a2a",
            style="on #161616",
            padding=(1, 2),
        )
        self.console.print()
        self.console.print(Align.center(card))
        self.console.print()

    def print_sessions_modal(self, sessions: list, active_id: Optional[str] = None):
        """Render session picker modal matching OpenCode's Sessions dialog."""
        card_w = min(max(60, 76), max(50, (self.console.width or 80) - 4))
        inner_w = card_w - 6

        lines = [
            "[dim]Search[/dim]",
            "",
        ]
        if not sessions:
            lines.append("[dim]No saved sessions found[/dim]")
        else:
            lines.append("[bold magenta]Recent Sessions[/bold magenta]")
            for s in sessions[:12]:
                sid = s.get("id", "")
                stitle = s.get("title") or "New session"
                model_tag = s.get("model", "")
                is_active = (active_id and sid == active_id) or False
                if is_active:
                    label = f"  ▶ {stitle} ({sid[:8]}) · {model_tag}"
                    if len(label) > inner_w:
                        label = label[:inner_w - 1] + "…"
                    pad = max(0, inner_w - len(label))
                    lines.append(f"[black on #f5a623]{label}{' ' * pad}[/black on #f5a623]")
                else:
                    label = f"  {stitle} ({sid[:8]} · {model_tag})"
                    if len(label) > inner_w:
                        stitle = stitle[:max(10, inner_w - 24)] + "…"
                    lines.append(f"  [white]{stitle}[/white] [dim]({sid[:8]} · {model_tag})[/dim]")

        # Same /models|/sidebar style: borderless sidebar-style lines, no card,
        # no panel, no interactive modal. Drops the leading "Search" + card blank.
        self.console.print()
        for _l in lines[2:]:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def print_models_modal(self, provider_name: str, models_data: list, active_model: Optional[str] = None):
        """Borderless sidebar-style model catalog — same look as /sidebar."""
        lines = [
            f"[bold magenta]{provider_name.upper()} Models[/bold magenta]",
        ]
        if not models_data:
            lines.append("[dim]No models discovered for provider[/dim]")
        else:
            for m in models_data[:12]:
                mname = m.get("name", "")
                c_win = f"{m.get('context', 0):,}"
                badges = []
                if m.get("thinking"):
                    badges.append("thinking")
                if m.get("vision"):
                    badges.append("vision")
                badge_str = f" · {', '.join(badges)}" if badges else ""
                is_active = (active_model and mname.lower() == active_model.lower()) or False
                if is_active:
                    lines.append(f"  ▶ [white]{mname}[/white] [dim]· {c_win}{badge_str}[/dim]")
                else:
                    lines.append(f"  [white]{mname}[/white] [dim]· {c_win}{badge_str}[/dim]")
        lines.append("")
        lines.append("[dim]switch active model with /model <name>[/dim]")

        # Mirror print_sidebar: borderless lines, no card, no overlay, no modal.
        self.console.print()
        for line in lines:
            self.console.print(f"  {line}" if line else "")
        self.console.print()

    def print_theme_modal(self, active_theme: Optional[str] = None):
        """Render theme gallery modal matching OpenCode's Themes dialog."""
        card_w = min(max(60, 76), max(50, (self.console.width or 80) - 4))
        inner_w = card_w - 6

        lines = [
            "[dim]Search[/dim]",
            "",
            "[bold magenta]Available Themes[/bold magenta]",
        ]
        for tid, t in THEMES.items():
            swatches = f"[{t.primary}]■[/{t.primary}] [{t.secondary}]■[/{t.secondary}] [{t.accent}]■[/{t.accent}] [{t.success}]■[/{t.success}] [{t.thinking}]■[/{t.thinking}]"
            is_active = active_theme and tid.lower() == active_theme.lower()
            if is_active:
                raw_text = f"  ▶ {tid:<12} {swatches} (active)"
                plain_len = len(f"  ▶ {tid:<12} ■ ■ ■ ■ ■ (active)")
                pad = max(0, inner_w - plain_len)
                lines.append(f"[black on #f5a623]{raw_text}{' ' * pad}[/black on #f5a623]")
            else:
                lines.append(f"  [white]{tid:<14}[/white] {swatches}  [dim]{t.display_name}[/dim]")

        # Same /models|/sidebar style: borderless sidebar-style lines, no card,
        # no panel, no interactive modal. Drops the leading "Search" + card blank.
        self.console.print()
        for _l in lines[2:]:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def print_status_modal(self, agent=None, queue=None):
        """Render system status modal matching OpenCode dialog."""
        tokens = 0
        c_win = 200000
        pct = 0.0
        provider_str = "anthropic"
        model_str = "claude-3-7-sonnet"
        effort_str = "medium"
        mode_str = "BUILD"
        perm_str = "normal"
        if agent:
            if getattr(agent, "session", None) is not None:
                from harness.core.compaction import calculate_history_tokens
                tokens = calculate_history_tokens(agent.session.messages)
            if hasattr(agent, "compactor") and agent.compactor:
                c_win = agent.compactor.context_window
            pct = round((tokens / max(1, c_win)) * 100, 1)
            if hasattr(agent, "provider") and agent.provider:
                provider_str = getattr(agent.provider, "display_name", agent.provider.name if hasattr(agent.provider, "name") else "unknown")
            model_str = agent.session.model if (agent.session and hasattr(agent.session, "model")) else (getattr(agent.config, "model", "claude-3-7-sonnet") if hasattr(agent, "config") else "claude-3-7-sonnet")
            if hasattr(agent, "config") and hasattr(agent.config, "thinking_effort"):
                effort_str = str(agent.config.thinking_effort)
            if hasattr(agent, "mode"):
                mode_str = agent.mode.value.upper()
            if hasattr(agent, "permission_manager"):
                perm_str = agent.permission_manager.level.value

        ram_mb = self._get_ram_usage_mb()
        branch = self._get_git_branch() or "detached"
        q_size = queue.size() if queue else 0
        q_curr = queue.current_item if queue else None

        lines = [
            "[bold magenta]Workspace & Git[/bold magenta]",
            f"  [white]Path:[/white]    [dim]{os.getcwd()}[/dim]",
            f"  [white]Branch:[/white]  [cyan]{branch}[/cyan]",
            "",
            "[bold magenta]Model & Context[/bold magenta]",
            f"  [white]Provider:[/white] [white]{provider_str}[/white]",
            f"  [white]Model:[/white]    [bold cyan]{model_str}[/bold cyan]",
            f"  [white]Context:[/white]  [dim]{tokens:,} / {c_win:,} ({pct}%)[/dim]",
            f"  [white]Effort:[/white]   [bold yellow]{effort_str}[/bold yellow]",
            f"  [white]RAM:[/white]      [dim]{ram_mb} MB[/dim]",
            "",
            "[bold magenta]Queue & Execution[/bold magenta]",
            f"  [white]Mode:[/white]     [bold green]{mode_str}[/bold green] [dim](perm: {perm_str})[/dim]",
            f"  [white]Queue:[/white]    [yellow]{q_size} pending[/yellow]" + (f" ([green]Active #{q_curr.id}[/green])" if q_curr else " [dim](Idle)[/dim]"),
        ]
        # Same /models|/sidebar style: borderless sidebar-style lines, no card,
        # no panel, no interactive modal. Drops the leading "Search" + card blank.
        self.console.print()
        for _l in lines[2:]:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def print_queue_modal(self, queue):
        """Render queue inspection modal matching OpenCode dialog."""
        lines = [
            "[bold magenta]Active Execution[/bold magenta]",
        ]
        curr = queue.current_item if queue else None
        if curr:
            lines.append(f"[black on #f5a623]  ▶ Running #{curr.id}: {self._truncate(curr.prompt, 45)}  [/black on #f5a623]")
        else:
            lines.append("  [dim]No task currently executing (Idle)[/dim]")
        lines.append("")

        pending = queue.list_pending() if queue else []
        lines.append(f"[bold magenta]Pending Queue ({len(pending)})[/bold magenta]")
        if not pending:
            lines.append("  [dim]No pending tasks in queue[/dim]")
        else:
            for item in pending[:8]:
                lines.append(f"  [bold yellow]⧗ #{item.id}:[/bold yellow] [white]{self._truncate(item.prompt, 45)}[/white] [dim]({item.wait_time:.1f}s)[/dim]")
        lines.append("")

        if queue and queue.is_paused:
            lines.append("[bold yellow]⏸ Queue is currently paused[/bold yellow]")

        # Same /models|/sidebar style: borderless sidebar-style lines, no card,
        # no panel, no interactive modal. Drops the leading "Search" + card blank.
        self.console.print()
        for _l in lines[2:]:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def print_help_modal(self, command_descriptions: Dict[str, str]):
        """Render command palette - sidebar style, shows ALL registered commands."""
        # Build dynamic list from registry - ensures /help always reflects actual commands.
        lines = [
            f"[bold magenta]Available Commands ({len(command_descriptions)})[/bold magenta]",
            "",
        ]
        for cmd, desc in sorted(command_descriptions.items()):
            # Align command column for readability (pad to ~12 chars)
            lines.append(f"  [bold cyan]/{cmd:<12}[/bold cyan] [dim]{desc}[/dim]")
        lines.append("")
        lines.append("[dim]Run /<command> [args] · /help for this list[/dim]")
        # Mirror /sidebar|/models: borderless sidebar-style lines, no card.
        self.console.print()
        for _l in lines:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def print_mcp_modal(self, servers: dict):
        """Render MCP servers modal dialog."""
        lines = [
            "[dim]Search[/dim]",
            "",
            f"[bold magenta]Configured MCP Servers ({len(servers)})[/bold magenta]",
        ]
        if not servers:
            lines.append("[dim]No MCP servers configured in ~/.harness/mcp.json[/dim]")
        else:
            for sname, scfg in list(servers.items())[:12]:
                cmd = scfg.get("command", "")
                args_str = " ".join(str(a) for a in scfg.get("args", []))
                lines.append(f"  [bold cyan]{sname}[/bold cyan] [dim]({cmd} {args_str})[/dim]")
        # Same /models|/sidebar style: borderless sidebar-style lines, no card,
        # no panel, no interactive modal. Drops the leading "Search" + card blank.
        self.console.print()
        for _l in lines[2:]:
            self.console.print(f"  {_l}" if _l else "")
        self.console.print()

    def _show_overlay_card(self, card) -> None:
        """Show a Rich card as a bottom-anchored overlay over current TUI content.

        Does **not** switch to the alternate/alt buffer nor wipe the screen, so
        the live conversation underneath stays fully visible. The card is drawn
        as a centered block a few rows up from the bottom of the terminal and
        any key (Esc, q, Enter, or arrow/mouse byte) closes it, restoring the
        prior screen area when the next frame renders.
        """
        import os as _os
        import sys as _sys

        if not (_sys.stdin.isatty() and _sys.stdout.isatty()):
            from rich.align import Align as _Align
            self.console.print()
            self.console.print(_Align.center(card))
            self.console.print()
            return

        import select as _select
        import termios as _termios
        import tty as _tty
        fd = _sys.stdin.fileno()
        old_settings = _termios.tcgetattr(fd)

        term_rows, term_cols = 24, 80
        try:
            size = _os.get_terminal_size()
            term_rows, term_cols = size.lines, size.columns
        except Exception:
            pass

        # Render the card to a standalone buffer once
        from io import StringIO as _SIO
        from rich.console import Console as _RConsole
        from rich.align import Align as _RA
        buf = _SIO()
        draw = _RConsole(file=buf, force_terminal=True, width=term_cols)
        draw.print()
        draw.print(_RA.center(card, style="on #161616"))
        rendered_lines = buf.getvalue().split("\n")
        while rendered_lines and not rendered_lines[-1].strip():
            rendered_lines.pop()
        card_h = min(len(rendered_lines), term_rows - 2)
        card_start = max(1, term_rows - card_h - 1)

        def _read_key() -> bytes:
            """Read one key without buffer-loss (os.read, never sys.stdin.read)."""
            b = _os.read(fd, 1)
            if b != b"\x1b":
                return b
            seq = b"\x1b"
            while True:
                r, _, _ = _select.select([fd], [], [], 0.05)
                if not r:
                    break
                nb = _os.read(fd, 1)
                if not nb:
                    break
                seq += nb
            return seq

        try:
            _tty.setraw(fd)
            _sys.stdout.write("\033[?25l")
            _sys.stdout.flush()
            # Draw overlay on the main screen (no alt buffer, no clear)
            for i, ln in enumerate(rendered_lines[:card_h]):
                _sys.stdout.write(f"\033[{card_start + i};1H" + ln[:term_cols])
            _sys.stdout.flush()
            _read_key()  # Any key closes the overlay
        finally:
            # Clear the overlay region & restore cursor
            for i in range(card_h):
                _sys.stdout.write(f"\033[{card_start + i};1H" + (" " * term_cols))
            _sys.stdout.write("\033[?25h")
            _sys.stdout.flush()
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)

    def show_status_modal(self, agent=None, queue=None) -> None:
        """Interactive status modal with overlay (falls back to static card in headless)."""
        import sys as _sys
        if not (_sys.stdin.isatty() and _sys.stdout.isatty()):
            self.print_status_modal(agent, queue=queue)
            return
        # Build card via same logic but capture Panel
        tokens = 0
        c_win = 200000
        pct = 0.0
        provider_str = "anthropic"
        model_str = "claude-3-7-sonnet"
        effort_str = "medium"
        mode_str = "BUILD"
        perm_str = "normal"
        if agent:
            if getattr(agent, "session", None) is not None:
                from harness.core.compaction import calculate_history_tokens
                tokens = calculate_history_tokens(agent.session.messages)
            if hasattr(agent, "compactor") and agent.compactor:
                c_win = agent.compactor.context_window
            pct = round((tokens / max(1, c_win)) * 100, 1)
            if hasattr(agent, "provider") and agent.provider:
                provider_str = getattr(agent.provider, "display_name", agent.provider.name if hasattr(agent.provider, "name") else "unknown")
            model_str = agent.session.model if (agent.session and hasattr(agent.session, "model")) else (getattr(agent.config, "model", "claude-3-7-sonnet") if hasattr(agent, "config") else "claude-3-7-sonnet")
            if hasattr(agent, "config") and hasattr(agent.config, "thinking_effort"):
                effort_str = str(agent.config.thinking_effort)
            if hasattr(agent, "mode"):
                mode_str = agent.mode.value.upper()
            if hasattr(agent, "permission_manager"):
                perm_str = agent.permission_manager.level.value
        ram_mb = self._get_ram_usage_mb()
        branch = self._get_git_branch() or "detached"
        q_size = queue.size() if queue else 0
        q_curr = queue.current_item if queue else None
        lines = [
            "[bold magenta]Workspace & Git[/bold magenta]",
            f"  [white]Path:[/white]    [dim]{os.getcwd()}[/dim]",
            f"  [white]Branch:[/white]  [cyan]{branch}[/cyan]",
            "",
            "[bold magenta]Model & Context[/bold magenta]",
            f"  [white]Provider:[/white] [white]{provider_str}[/white]",
            f"  [white]Model:[/white]    [bold cyan]{model_str}[/bold cyan]",
            f"  [white]Context:[/white]  [dim]{tokens:,} / {c_win:,} ({pct}%)[/dim]",
            f"  [white]Effort:[/white]   [bold yellow]{effort_str}[/bold yellow]",
            f"  [white]RAM:[/white]      [dim]{ram_mb} MB[/dim]",
            "",
            "[bold magenta]Queue & Execution[/bold magenta]",
            f"  [white]Mode:[/white]     [bold green]{mode_str}[/bold green] [dim](perm: {perm_str})[/dim]",
            f"  [white]Queue:[/white]    [yellow]{q_size} pending[/yellow]" + (f" ([green]Active #{q_curr.id}[/green])" if q_curr else " [dim](Idle)[/dim]"),
        ]
        term_width = self.console.width or 80
        card_width = min(max(60, 76), max(50, term_width - 4))
        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left")
        header_table.add_column(justify="right")
        header_table.add_row(f"[bold white]System Status[/bold white]", "[dim]esc[/dim]")
        items = [header_table, Text("")]
        for l in lines:
            items.append(Text.from_markup(l))
        items.append(Text(""))
        items.append(Text.from_markup("[dim]refresh /status  esc to close[/dim]"))
        card = Panel(Group(*items), width=card_width, border_style="#2a2a2a", style="on #161616", padding=(1, 2))
        self._show_overlay_card(card)

    def show_queue_modal(self, queue) -> None:
        """Interactive queue modal with overlay."""
        import sys as _sys
        if not (_sys.stdin.isatty() and _sys.stdout.isatty()):
            self.print_queue_modal(queue)
            return
        lines = ["[bold magenta]Active Execution[/bold magenta]"]
        curr = queue.current_item if queue else None
        if curr:
            lines.append(f"[black on #f5a623]  ▶ Running #{curr.id}: {self._truncate(curr.prompt, 45)}  [/black on #f5a623]")
        else:
            lines.append("  [dim]No task currently executing (Idle)[/dim]")
        lines.append("")
        pending = queue.list_pending() if queue else []
        lines.append(f"[bold magenta]Pending Queue ({len(pending)})[/bold magenta]")
        if not pending:
            lines.append("  [dim]No pending tasks in queue[/dim]")
        else:
            for item in pending[:8]:
                lines.append(f"  [bold yellow]⧗ #{item.id}:[/bold yellow] [white]{self._truncate(item.prompt, 45)}[/white] [dim]({item.wait_time:.1f}s)[/dim]")
        lines.append("")
        if queue and queue.is_paused:
            lines.append("[bold yellow]⏸ Queue is currently paused[/bold yellow]")
        term_width = self.console.width or 80
        card_width = min(max(60, 76), max(50, term_width - 4))
        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left")
        header_table.add_column(justify="right")
        header_table.add_row(f"[bold white]Execution Queue[/bold white]", "[dim]esc[/dim]")
        items = [header_table, Text("")]
        for l in lines:
            items.append(Text.from_markup(l))
        items.append(Text(""))
        items.append(Text.from_markup("[dim]drop /queue drop <id>  clear /queue clear  pause/resume /queue pause[/dim]"))
        card = Panel(Group(*items), width=card_width, border_style="#2a2a2a", style="on #161616", padding=(1, 2))
        self._show_overlay_card(card)

    def show_mcp_modal(self, servers: dict) -> None:
        """Interactive MCP modal with overlay."""
        import sys as _sys
        if not (_sys.stdin.isatty() and _sys.stdout.isatty()):
            self.print_mcp_modal(servers)
            return
        lines = ["[dim]Search[/dim]", "", f"[bold magenta]Configured MCP Servers ({len(servers)})[/bold magenta]"]
        if not servers:
            lines.append("[dim]No MCP servers configured in ~/.harness/mcp.json[/dim]")
        else:
            for sname, scfg in list(servers.items())[:12]:
                cmd = scfg.get("command", "")
                args_str = " ".join(str(a) for a in scfg.get("args", []))
                lines.append(f"  [bold cyan]{sname}[/bold cyan] [dim]({cmd} {args_str})[/dim]")
        term_width = self.console.width or 80
        card_width = min(max(60, 76), max(50, term_width - 4))
        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left")
        header_table.add_column(justify="right")
        header_table.add_row(f"[bold white]MCP Servers[/bold white]", "[dim]esc[/dim]")
        items = [header_table, Text("")]
        for l in lines:
            items.append(Text.from_markup(l))
        items.append(Text(""))
        items.append(Text.from_markup("[dim]configure ~/.harness/mcp.json  esc[/dim]"))
        card = Panel(Group(*items), width=card_width, border_style="#2a2a2a", style="on #161616", padding=(1, 2))
        self._show_overlay_card(card)

    def _build_sidebar_lines(self, agent, queue=None) -> list[str]:
        lines = []
        sid = agent.session.id if (agent and agent.session) else "New session"
        if len(sid) > 16:
            sid = sid[:16]
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"[bold white]{sid}[/bold white] [dim]- {now_str}[/dim]")
        lines.append("")

        tokens = 0
        if agent and agent.session:
            from harness.core.compaction import calculate_history_tokens
            tokens = calculate_history_tokens(agent.session.messages)
        c_win = agent.compactor.context_window if agent else 200000
        pct = round((tokens / max(1, c_win)) * 100, 1)
        lines.append("[bold white]Context[/bold white]")
        lines.append(f"[dim]{tokens:,} tokens[/dim]")
        lines.append(f"[dim]{pct}% used[/dim]")
        lines.append("[dim]$0.00 spent[/dim]")
        lines.append("")

        mcp_clients = list(agent.mcp_manager.clients.keys()) if (agent and hasattr(agent, "mcp_manager")) else []
        lines.append("[bold white]MCP[/bold white]")
        if mcp_clients:
            for sname in mcp_clients:
                lines.append(f"[bold green]•[/bold green] [white]{sname}[/white] [dim]Connected[/dim]")
        else:
            lines.append("[dim]No MCP servers connected[/dim]")
        lines.append("")

        skills = agent.skills_manager.list_skills() if (agent and hasattr(agent, "skills_manager")) else []
        lines.append("[bold white]Skills[/bold white]")
        lines.append(f"[dim]{len(skills)} skills active[/dim]")
        lines.append("")

        lines.append("[bold white]▼ Todo[/bold white]")
        if agent and hasattr(agent, "todo_manager") and agent.todo_manager.tasks:
            for task in agent.todo_manager.tasks[:8]:
                if task.status.value == "completed":
                    mark = "[bold green]✔[/bold green]"
                elif task.status.value == "in_progress":
                    mark = "[bold yellow]▶[/bold yellow]"
                else:
                    mark = "[dim][ ][/dim]"
                lines.append(f"{mark} [dim]{task.title}[/dim]")
        else:
            lines.append("[dim]No active tasks[/dim]")
        lines.append("")

        if queue and (queue.size() > 0 or queue.current_item):
            lines.append("[bold white]Queue[/bold white]")
            if queue.current_item:
                lines.append(f"[bold green]▶ Running #{queue.current_item.id}:[/bold green] [dim]{queue.current_item.prompt[:35]}[/dim]")
            lines.append(f"[dim]{queue.size()} pending task(s)[/dim]")
            lines.append("")

        cwd = os.getcwd()
        lines.append(f"[dim]{cwd}[/dim]")
        lines.append("")
        lines.append(f"[bold green]•[/bold green] [dim]Harness {__version__}[/dim]")
        return lines

    def render_sidebar(self, agent, queue=None):
        """Render borderless sidebar panel."""
        lines = self._build_sidebar_lines(agent, queue=queue)
        return Panel("\n".join(lines), box=None, padding=(0, 2))

    def print_sidebar(self, agent, queue=None):
        """Print the clean borderless OpenCode-style session & environment sidebar."""
        self.console.print()
        for line in self._build_sidebar_lines(agent, queue=queue):
            self.console.print(f"  {line}" if line else "")
        self.console.print()

    def finish_thinking(self):
        """Public method to close thinking panel on turn complete or interrupt."""
        self._finish_thinking()

    def finish_markdown(self):
        """Flush the trailing partial markdown block on turn end or interrupt."""
        self._finish_markdown()

    def _finish_thinking(self):
        """Close out the thinking display panel and show timing stats."""
        if not self._is_thinking_visible:
            return
        # Flush any buffered thinking blocks (append-only, like markdown).
        if self._thinking_buffer:
            self._thinking_print_block(self._thinking_buffer)
            self._thinking_buffer = ""
        elapsed = time.time() - self._thinking_start_time
        if elapsed < 1.0:
            el_str = f"{int(elapsed * 1000)}ms"
        else:
            el_str = f"{elapsed:.1f}s"
        t_tokens = max(1, len(self._current_thinking) // 4)
        self.console.print()  # newline after streamed thinking text
        self.console.print(
            f"  [bold yellow]Thought[/bold yellow] [dim]· {el_str} (~{t_tokens:,} tokens)[/dim]"
        )
        self.console.print()
        self._is_thinking_visible = False
        self._current_thinking = ""
        self._thinking_buffer = ""

    def _thinking_print_block(self, chunk: str):
        """Print one thinking block with prefix - mirrors _md_print_block structure.

        Uses same append-only, no-Live approach as markdown streaming but renders
        with thinking style (yellow prefix) instead of Markdown. Blank lines are
        preserved as empty console prints for proper paragraph spacing.
        """
        if not chunk:
            return
        # Strip only trailing blank handling handled by caller; print each line
        # with the thinking sidebar prefix. Preserve empty lines.
        lines = chunk.splitlines()
        for line in lines:
            if not line.strip():
                self.console.print()
            else:
                # Use Text to avoid markup injection, with thinking color
                self.console.print(
                    f"  [dim yellow]│[/dim yellow] [{self.theme.thinking}]{line}[/{self.theme.thinking}]",
                    highlight=False,
                )
        # If original chunk ended with newline, ensure trailing blank preserved
        if chunk.endswith("\n") and (not lines or lines[-1].strip()):
            self.console.print()

    def _thinking_flush_completed(self):
        """Append-only thinking stream: print every completed block immediately.

        Mirrors _md_flush_completed - only prints chunks ending at a markdown
        block boundary (blank line outside fenced code). This avoids splitting
        fences/lists mid-block and matches normal response streaming semantics.
        """
        while True:
            completed = self._md_completed_end(self._thinking_buffer)
            if completed <= 0:
                return
            chunk, self._thinking_buffer = (
                self._thinking_buffer[:completed],
                self._thinking_buffer[completed:],
            )
            self._thinking_print_block(chunk)

    def _md_print_block(self, chunk: str):
        """Print one markdown block with whole-document spacing semantics.

        Rich renders lists / quotes / tables with their own leading blank line
        and a horizontal rule with its own trailing blank line; other blocks
        need the caller to supply the separator. This emits exactly one blank
        line between adjacent blocks — byte-identical to rendering the whole
        document at once.
        """
        kind = self._md_block_kind(chunk)
        last_line = next(
            (ln for ln in reversed(chunk.splitlines()) if ln.strip()), ""
        ).lstrip()
        trailing_blank = bool(re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", last_line))
        if (
            self._md_stream_started
            and kind not in ("list", "quote", "table")
            and not self._md_last_blank
        ):
            self.console.print()
        self.console.print(Markdown(chunk, code_theme=self.theme.code_theme), end="")
        self._md_stream_started = True
        self._md_last_blank = trailing_blank

    def _finish_markdown(self):
        """Flush the trailing (not yet printed) markdown segment and reset the buffer."""
        if self._md_buffer:
            self._md_print_block(self._md_buffer)
            self._md_buffer = ""
        self._md_stream_started = False
        self._md_last_blank = False

    @staticmethod
    def _md_completed_end(buffer: str) -> int:
        """Return the length of the longest prefix of ``buffer`` that is safe to
        print right now, i.e. ends at a markdown block boundary.

        A boundary is the newline ending a blank line *outside* any fenced code
        block. Text inside an open fence is held back: a) a fence with a trailing
        blank line would otherwise be printed in two pieces that read as two
        separate blocks; b) more importantly, the moment we print something we
        can never take it back — so we only print what is guaranteed final.
        """
        in_fence = False
        fence_marker = ""
        pos = 0
        last_boundary = 0
        for line in buffer.splitlines(keepends=True):
            stripped = line.strip()
            if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
                in_fence = True
                fence_marker = stripped[:3]
            elif in_fence and stripped.startswith(fence_marker):
                in_fence = False
            pos += len(line)
            if not in_fence and not stripped:
                last_boundary = pos
        return last_boundary

    @staticmethod
    def _md_block_kind(chunk: str) -> str:
        """Classify the first markdown block of ``chunk``.

        Rich renders lists / quotes / tables with a self-supplied leading blank
        line, paragraphs / headings / fences without one, and a horizontal rule
        with a self-supplied *trailing* blank line. The streamer must know the
        kind to emit exactly one blank line between adjacent blocks, matching a
        whole-document render.
        """
        first = next((ln for ln in chunk.splitlines() if ln.strip()), "")
        s = first.lstrip()
        if s.startswith(("```", "~~~")):
            return "fence"
        if re.match(r"^#{1,6}\s", s):
            return "heading"
        if re.match(r"^[-*+]\s", s) or (s and s[0].isdigit() and re.match(r"^\d+[.)]\s", s)):
            return "list"
        if s.startswith(">"):
            return "quote"
        if s.startswith("|") and "<" not in s.split("|")[0]:
            return "table"
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            return "hr"
        return "paragraph"

    def _md_flush_completed(self):
        """Append-only stream: print every completed markdown block immediately.

        This replaces the previous ``rich.live.Live`` repaint. Live repaints
        assume the cursor is still sitting on the frame's last row and climb
        back with ``\\r`` + erase-line + cursor-up pairs. Windows ConPTY and
        legacy consoles wrap the cursor immediately when the final column is
        written (POSIX terminals defer that wrap), so every repaint
        under-climbs by one row and permanently leaks the previous frame's
        first line — on Windows the streamed greeting appeared once per
        refresh, stacked down the left margin. An append-only stream performs
        no cursor repositioning at all, so it cannot leak frames on any
        terminal.
        """
        while True:
            completed = self._md_completed_end(self._md_buffer)
            if completed <= 0:
                return
            chunk, self._md_buffer = (
                self._md_buffer[:completed],
                self._md_buffer[completed:],
            )
            self._md_print_block(chunk)
            self._md_stream_started = True

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

    def _tool_meta(self, name: str) -> tuple[str, str]:
        """Classify tool name into an icon and human readable category."""
        nl = name.lower()
        if any(k in nl for k in ("bash", "cmd", "shell", "run_command", "terminal")):
            return "⚡", "Command"
        elif any(k in nl for k in ("read", "view", "cat", "get_file")):
            return "📖", "Read"
        elif any(k in nl for k in ("edit", "write", "replace", "patch")):
            return "📝", "Edit"
        elif any(k in nl for k in ("search", "find", "grep", "glob")):
            return "🔍", "Search"
        elif any(k in nl for k in ("subagent", "swarm")):
            return "🐝", "Swarm"
        elif any(k in nl for k in ("ask_user", "question")):
            return "❓", "Prompt"
        elif "mcp" in nl:
            return "🔌", "MCP"
        return "🔧", "Tool"

    def render_agent_event(self, ev):
        """Render streaming agent events with live timing."""
        etype = ev.type
        data = ev.data

        if etype in ("subagent_start", "subagent_text_delta", "subagent_tool", "subagent_message", "subagent_end"):
            self.print_subagent_event(etype, data)
            return

        if etype == "reasoning_delta":
            # Mirror text_delta/tool_call updating: finish markdown first, then
            # buffer thinking and flush completed blocks append-only (no Live).
            self._finish_markdown()
            text = str(data)
            self._current_thinking += text
            self._thinking_buffer += text
            if not self._is_thinking_visible:
                self._thinking_start_time = time.time()
                self.console.print(
                    "\n  [bold yellow]Thought[/bold yellow] [dim]· streaming...[/dim]"
                )
                self._is_thinking_visible = True
            # Append-only streaming: print each completed block once (like text_delta).
            # No per-char repaints / cursor repositioning - see _thinking_flush_completed.
            self._thinking_flush_completed()

        elif etype == "text_delta":
            self._finish_thinking()
            self._md_buffer += str(data)
            # Append-only streaming: print each completed markdown block once.
            # No Live repaint / cursor repositioning — see _md_flush_completed.
            self._md_flush_completed()

        elif etype == "tool_call_start":
            self._finish_markdown()
            self._finish_thinking()
            self._active_tool_start = time.time()
            tname = data.get("name", "tool")
            icon, category = self._tool_meta(tname)
            args = data.get("arguments", {})
            self.console.print(f"\n[{self.theme.secondary}]{icon} Invoking {category}: [bold]{tname}[/bold][/{self.theme.secondary}]")
            if args:
                preview = str(args)
                if len(preview) > 120:
                    preview = preview[:120] + "..."
                self.console.print(f"   [dim]args: {preview}[/dim]")

        elif etype == "tool_call_result":
            self._finish_markdown()
            elapsed_str = ""
            if hasattr(self, "_active_tool_start") and self._active_tool_start:
                el = time.time() - self._active_tool_start
                elapsed_str = f" ({el:.2f}s)"
                self._active_tool_start = 0.0
            res = str(data.get("result", ""))
            first_line = res.strip().split("\n")[0] if res.strip() else "(empty)"
            if len(first_line) > 140:
                first_line = first_line[:140] + "..."
            self.console.print(f"   [{self.theme.success}]✔ Result{elapsed_str}:[/{self.theme.success}] [dim]{first_line}[/dim]\n")

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
        table = Table(title=f"Models for Provider: {provider_name.upper()}", border_style=self.theme.border)
        table.add_column("Model", style=f"bold {self.theme.primary}", no_wrap=False)
        table.add_column("Context", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Vision", justify="center")
        table.add_column("Thinking", justify="center")
        table.add_column("Effort Levels", justify="left")
        table.add_column("Budget", justify="center")

        for m in models_data:
            c_win = f"{m['context']:,}"
            vision_badge = "[bold cyan]Yes[/bold cyan]" if m.get("vision") else "[dim]No[/dim]"
            th_badge = "[bold green]Yes[/bold green]" if m["thinking"] else "[dim]No[/dim]"
            ttype = m.get("thinking_type")

            # Dynamically probe what effort names this dialect accepts
            if not m["thinking"]:
                effort_str = "-"
                budget_badge = "-"
            else:
                options = probe_effort_options(ttype)
                effort_str = ", ".join(options) if options else "-"
                budget_badge = "[bold yellow]Yes[/bold yellow]" if "<n>" in options else "[dim]No[/dim]"

            table.add_row(m["name"], c_win, f"{m['output']:,}", vision_badge, th_badge, effort_str, budget_badge)

        self.console.print(table)
        self.console.print(f"[dim]Switch: /model <name> | Effort: /effort <level>[/dim]\n")

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
