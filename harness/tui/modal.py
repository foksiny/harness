"""
Interactive modal dialog overlay for Harness TUI.
Displays full-screen/centered overlay dialogs with live search, keyboard navigation (↑/↓),
enter-to-select, and escape-to-dismiss without cluttering scrollback history.
"""
import os
import sys
import time
from dataclasses import dataclass
from io import StringIO
from typing import List, Optional, Callable, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.console import Group


@dataclass
class ModalItem:
    id: str
    title: str
    subtitle: str = ""
    category: str = ""
    badge: str = ""
    is_active: bool = False
    payload: Any = None


class InteractiveModal:
    """Renders a modal overlay on top of the terminal using the alternate screen buffer."""

    def __init__(
        self,
        title: str,
        items: List[ModalItem],
        shortcuts: str = "↑/↓ navigate  ↵ select  esc close",
        search_placeholder: str = "Search",
        width: int = 76,
    ):
        self.title = title
        self.items = items
        self.shortcuts = shortcuts
        self.search_placeholder = search_placeholder
        self.width = width

    def run(self, console: Optional[Console] = None) -> Optional[ModalItem]:
        """Run interactive modal overlay. If non-interactive TTY, renders fallback and returns None."""
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            self._render_fallback(console or Console())
            return None

        # Determine OS support for raw key input
        if os.name == "nt":
            return self._run_windows(console or Console())
        return self._run_unix(console or Console())

    def _render_fallback(self, console: Console) -> None:
        """Render static card in non-interactive environments (CI, pipes, unit tests)."""
        card = self._build_card(filter_query="", selected_index=0, max_rows=12)
        console.print()
        console.print(Align.center(card))
        console.print()

    def _filter_items(self, query: str) -> List[ModalItem]:
        if not query.strip():
            return list(self.items)
        q = query.strip().lower()
        return [
            item for item in self.items
            if q in item.title.lower() or q in item.subtitle.lower() or q in item.category.lower() or q in item.id.lower()
        ]

    def _build_card(self, filter_query: str, selected_index: int, max_rows: int = 14) -> Panel:
        filtered = self._filter_items(filter_query)
        term_width = 80
        try:
            term_width = os.get_terminal_size().columns
        except Exception:
            pass

        card_width = min(max(60, self.width), max(50, term_width - 4))
        inner_width = card_width - 6

        # 1. Header
        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left")
        header_table.add_column(justify="right")
        header_table.add_row(f"[bold white]{self.title}[/bold white]", "[dim]esc[/dim]")

        # 2. Search row
        if filter_query:
            search_text = Text.from_markup(f"[bold white]Search:[/bold white] [cyan]{filter_query}[/cyan][bold yellow]▌[/bold yellow]")
        else:
            search_text = Text.from_markup(f"[dim]{self.search_placeholder}…[/dim]")

        items_group: list = [header_table, Text(""), search_text, Text("")]

        # 3. Item list grouped or flat
        if not filtered:
            items_group.append(Text.from_markup("  [dim]No matching results found[/dim]"))
        else:
            # Scroll window
            start_idx = 0
            if selected_index >= max_rows:
                start_idx = selected_index - max_rows + 1
            visible_items = filtered[start_idx : start_idx + max_rows]

            current_cat = None
            for idx, item in enumerate(visible_items, start=start_idx):
                if item.category and item.category != current_cat and not filter_query:
                    current_cat = item.category
                    items_group.append(Text.from_markup(f"[bold magenta]{current_cat}[/bold magenta]"))

                is_selected = (idx == selected_index)
                title = item.title
                sub = f" · {item.subtitle}" if item.subtitle else ""
                badge = f" [{item.badge}]" if item.badge else ""

                if is_selected:
                    line_raw = f"  ▶ {title}{sub}{badge}"
                    if len(line_raw) > inner_width:
                        line_raw = line_raw[: inner_width - 1] + "…"
                    pad = max(0, inner_width - len(line_raw))
                    items_group.append(Text.from_markup(f"[black on #f5a623]{line_raw}{' ' * pad}[/black on #f5a623]"))
                elif item.is_active:
                    line_raw = f"  ✔ [bold white]{title}[/bold white] [dim]{sub}{badge}[/dim]"
                    items_group.append(Text.from_markup(line_raw))
                else:
                    line_raw = f"    [white]{title}[/white] [dim]{sub}{badge}[/dim]"
                    items_group.append(Text.from_markup(line_raw))

            if len(filtered) > max_rows:
                items_group.append(Text.from_markup(f"  [dim]… {len(filtered) - max_rows} more items (use ↑/↓ or type to filter)[/dim]"))

        # 4. Shortcuts footer
        items_group.append(Text(""))
        items_group.append(Text.from_markup(f"[dim]{self.shortcuts}[/dim]"))

        return Panel(
            Group(*items_group),
            width=card_width,
            border_style="#2a2a2a",
            style="on #161616",
            padding=(1, 2),
        )

    def _run_unix(self, console: Console) -> Optional[ModalItem]:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        # Determine terminal size (fall back to 80x24 if undetectable)
        term_rows, term_cols = 24, 80
        try:
            term_cols = os.get_terminal_size().columns
            term_rows = os.get_terminal_size().lines
        except Exception:
            pass

        query = ""
        selected_idx = 0
        max_rows = 14
        card_start = 0
        card_height = 0

        def _read_key() -> bytes:
            """Read one key/byte without buffering loss.

            ``sys.stdin.read(1)`` wraps the fd in a TextIOWrapper BufferReader that
            reads ahead, so the rest of an escape/arrow sequence ends up in the
            python-user-space buffer and `select` on the raw fd sees nothing — that
            silently breaks ↑/↓/ESC. Reading via ``os.read(fd, 1)`` (the same trick
            ``input_handler._raw_line`` uses) lets us accumulate the full sequence.
            """
            b = os.read(fd, 1)
            if b == b"\x1b":  # Escape or escape sequence
                seq = b"\x1b"
                while True:
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if not r:
                        break
                    nb = os.read(fd, 1)
                    if not nb:
                        break
                    seq += nb
                    if len(seq) > 6:  # cap F-key/other long sequences
                        break
                return seq
            return b

        try:
            tty.setraw(fd)
            # Hide cursor only — no alternate buffer, no full clear, so the
            # conversation stays visible underneath the overlay card.
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()

            query = ""
            while True:
                filtered = self._filter_items(query)
                if selected_idx >= len(filtered):
                    selected_idx = max(0, len(filtered) - 1)

                # Render card to buffer, centered within the real terminal width
                card = self._build_card(query, selected_idx, max_rows=max_rows)
                buf = StringIO()
                draw_console = Console(file=buf, force_terminal=True, width=term_cols)
                draw_console.print()
                draw_console.print(Align.center(card, style="on #161616"))
                rendered = buf.getvalue()
                lines = rendered.split("\n")
                while lines and not lines[-1].strip():
                    lines.pop()
                card_height = len(lines)

                # Bottom-anchored overlay: draw the card over the last rows of the
                # current screen so everything else on screen stays visible.
                card_start = max(1, term_rows - card_height + 1)
                # Paint each overlay row across its FULL width with the card's
                # background first, then draw the line on top. Rich's centered
                # padding leaves the area around/right of the box transparent, so
                # without this the underlying default background shows through and
                # the overlay looks patchy/banded.
                for i, line in enumerate(lines):
                    sys.stdout.write(f"\033[{card_start + i};1H")
                    sys.stdout.write("\033[48;2;22;22;22m" + (" " * term_cols))  # fill band (bg ~#161616)
                    sys.stdout.write("\033[{card_start + i};1H")
                    sys.stdout.write(line[:term_cols])
                    sys.stdout.write("\033[0m")
                    sys.stdout.flush()

                key = _read_key()
                if key == b"\x1b":  # Pure Escape
                    return None
                if key in (b"\r", b"\n"):
                    if filtered and 0 <= selected_idx < len(filtered):
                        return filtered[selected_idx]
                    return None
                if key == b"\x03":  # Ctrl+C
                    return None
                if key in (b"q", b"Q"):
                    return None
                if key in (b"\x1b[A", b"\x1bOA"):  # Up arrow
                    selected_idx = max(0, selected_idx - 1)
                    continue
                if key in (b"\x1b[B", b"\x1bOB"):  # Down arrow
                    selected_idx = min(max(0, len(filtered) - 1), selected_idx + 1)
                    continue
                if key == b"\x1b[3~":  # Delete
                    continue
                if key == b"k":  # Vim up
                    selected_idx = max(0, selected_idx - 1)
                    continue
                if key == b"j":  # Vim down
                    if filtered:
                        selected_idx = min(max(0, len(filtered) - 1), selected_idx + 1)
                    continue
                if key in (b"\x7f", b"\x08"):  # Backspace
                    if query:
                        query = query[:-1]
                        selected_idx = 0
                    continue
                if key == b"\t":  # Tab -> next
                    if filtered:
                        selected_idx = (selected_idx + 1) % len(filtered)
                    continue
                try:
                    ch = key.decode("utf-8")
                    if ch.isprintable():
                        query += ch
                        selected_idx = 0
                except Exception:
                    continue

        finally:
            # Clear the overlay region on the main screen and restore cursor
            for i in range(card_height):
                sys.stdout.write(f"\033[{card_start + i};1H" + (" " * term_cols))
            sys.stdout.write("\033[?25h")
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            sys.stdout.flush()
    def _run_windows(self, console: Console) -> Optional[ModalItem]:
        import msvcrt

        query = ""
        selected_idx = 0
        max_rows = 14
        card_start = 0
        card_height = 0
        term_rows, term_cols = 24, 80
        try:
            term_cols = os.get_terminal_size().columns
            term_rows = os.get_terminal_size().lines
        except Exception:
            pass

        try:
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
            while True:
                filtered = self._filter_items(query)
                if selected_idx >= len(filtered):
                    selected_idx = max(0, len(filtered) - 1)

                card = self._build_card(query, selected_idx, max_rows=max_rows)
                buf = StringIO()
                draw_console = Console(file=buf, force_terminal=True, width=term_cols)
                draw_console.print()
                draw_console.print(Align.center(card, style="on #161616"))
                rendered = buf.getvalue()
                lines = rendered.split("\n")
                while lines and not lines[-1].strip():
                    lines.pop()
                card_height = len(lines)

                card_start = max(1, term_rows - card_height + 1)
                for i, line in enumerate(lines):
                    sys.stdout.write(f"\033[{card_start + i};1H")
                    sys.stdout.write(line[:term_cols])
                    sys.stdout.flush()

                key = msvcrt.getch()
                if key in (b"\x00", b"\xe0"):  # Special key prefix
                    sub = msvcrt.getch()
                    if sub == b"H":  # Up arrow
                        selected_idx = max(0, selected_idx - 1)
                    elif sub == b"P":  # Down arrow
                        selected_idx = min(max(0, len(filtered) - 1), selected_idx + 1)
                    continue
                if key == b"\x1b":  # Escape
                    return None
                if key in (b"\r", b"\n"):
                    if filtered and 0 <= selected_idx < len(filtered):
                        return filtered[selected_idx]
                    return None
                if key == b"\x03":  # Ctrl+C
                    return None
                if key in (b"q", b"Q"):
                    return None
                try:
                    ch = key.decode("utf-8")
                except Exception:
                    continue
                if ch in ("k", "K"):
                    selected_idx = max(0, selected_idx - 1)
                    continue
                if ch in ("j", "J"):
                    if filtered:
                        selected_idx = min(max(0, len(filtered) - 1), selected_idx + 1)
                    continue
                if ch == "\t":
                    if filtered:
                        selected_idx = (selected_idx + 1) % len(filtered)
                    continue
                if ch.isprintable():
                    query += ch
                    selected_idx = 0
        finally:
            for i in range(card_height):
                sys.stdout.write(f"\033[{card_start + i};1H" + (" " * term_cols))
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
