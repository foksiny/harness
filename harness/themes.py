"""
Themes engine for Harness CLI.
Provides 14 rich handcrafted color palettes, custom JSON theme loading,
and interactive visual preview cards.
"""
import os
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax

@dataclass
class Theme:
    name: str
    display_name: str
    primary: str       # Main accent color
    secondary: str     # Secondary accent
    accent: str        # Highlight color
    success: str       # Success / checkmarks
    warning: str       # Warnings / alerts
    error: str         # Errors / failures
    muted: str         # Subdued text / timestamps
    text: str          # Standard text color
    border: str        # Borders / separators
    thinking: str      # Thought accordion color
    code_theme: str    # Pygments syntax theme

THEMES: Dict[str, Theme] = {
    "cyberpunk": Theme(
        name="cyberpunk",
        display_name="Cyberpunk Neon",
        primary="#00ffff",       # Neon Cyan
        secondary="#ff007f",     # Neon Pink
        accent="#ffff00",        # Electric Yellow
        success="#00ff66",       # Neon Green
        warning="#ffaa00",       # Neon Amber
        error="#ff3366",         # Crimson Pink
        muted="#6c7086",         # Slate
        text="#ffffff",
        border="#00ffff",
        thinking="#bd93f9",      # Electric Purple
        code_theme="monokai",
    ),
    "dracula": Theme(
        name="dracula",
        display_name="Dracula",
        primary="#bd93f9",       # Dracula Purple
        secondary="#ff79c6",     # Dracula Pink
        accent="#8be9fd",        # Dracula Cyan
        success="#50fa7b",       # Dracula Green
        warning="#f1fa8c",       # Dracula Yellow
        error="#ff5555",         # Dracula Red
        muted="#6272a4",         # Dracula Comment
        text="#f8f8f2",          # Dracula Foreground
        border="#bd93f9",
        thinking="#ffb86c",      # Dracula Orange
        code_theme="dracula",
    ),
    "nord": Theme(
        name="nord",
        display_name="Nordic Frost",
        primary="#88c0d0",       # Nord Frost Cyan
        secondary="#81a1c1",     # Nord Frost Blue
        accent="#b48ead",        # Nord Aurora Purple
        success="#a3be8c",       # Nord Aurora Green
        warning="#ebcb8b",       # Nord Aurora Yellow
        error="#bf616a",         # Nord Aurora Red
        muted="#4c566a",         # Nord Polar Night Grey
        text="#eceff4",          # Nord Snow Storm
        border="#88c0d0",
        thinking="#d08770",      # Nord Orange
        code_theme="nord",
    ),
    "monokai": Theme(
        name="monokai",
        display_name="Monokai Pro",
        primary="#ffd866",       # Yellow
        secondary="#fc9867",     # Orange
        accent="#78dce8",        # Cyan
        success="#a9dc76",       # Green
        warning="#fc9867",       # Amber
        error="#ff6188",         # Red/Pink
        muted="#727072",         # Muted
        text="#fcfcfa",          # Foreground
        border="#ffd866",
        thinking="#ab9df2",      # Purple
        code_theme="monokai",
    ),
    "catppuccin": Theme(
        name="catppuccin",
        display_name="Catppuccin Mocha",
        primary="#cba6f7",       # Mauve
        secondary="#89b4fa",     # Blue
        accent="#f5c2e7",        # Pink
        success="#a6e3a1",       # Green
        warning="#f9e2af",       # Yellow
        error="#f38ba8",         # Red
        muted="#6c7086",         # Overlay0
        text="#cdd6f4",          # Text
        border="#cba6f7",
        thinking="#fab387",      # Peach
        code_theme="fruity",
    ),
    "matrix": Theme(
        name="matrix",
        display_name="Matrix Phosphor",
        primary="#00ff00",       # Bright Green
        secondary="#00cc00",     # Medium Green
        accent="#33ff33",        # Neon Lime
        success="#00ff66",       # Mint Green
        warning="#88ff00",       # Yellow Green
        error="#ff0033",         # Bright Red
        muted="#006600",         # Dark Green
        text="#e0ffe0",          # Light Green
        border="#00aa00",
        thinking="#00ff88",      # Teal Green
        code_theme="vim",
    ),
    "minimal": Theme(
        name="minimal",
        display_name="Minimal Clean",
        primary="bold white",
        secondary="white",
        accent="bright_cyan",
        success="bright_green",
        warning="bright_yellow",
        error="bright_red",
        muted="dim white",
        text="white",
        border="white",
        thinking="dim cyan",
        code_theme="default",
    ),
    "tokyo_night": Theme(
        name="tokyo_night",
        display_name="Tokyo Night",
        primary="#7aa2f7",       # Deep Blue
        secondary="#bb9af7",     # Violet
        accent="#7dcfff",        # Cyan
        success="#9ece6a",       # Green
        warning="#e0af68",       # Warm Amber
        error="#f7768e",         # Red
        muted="#565f89",         # Slate
        text="#c0caf5",          # Light
        border="#7aa2f7",
        thinking="#e0af68",      # Orange
        code_theme="material",
    ),
    "solarized_dark": Theme(
        name="solarized_dark",
        display_name="Solarized Dark",
        primary="#268bd2",       # Blue
        secondary="#2aa198",     # Cyan
        accent="#b58900",        # Yellow
        success="#859900",       # Green
        warning="#cb4b16",       # Orange
        error="#dc322f",         # Red
        muted="#657b83",         # Base00
        text="#93a1a1",          # Base1
        border="#268bd2",
        thinking="#6c71c4",      # Violet
        code_theme="solarized-dark",
    ),
    "gruvbox": Theme(
        name="gruvbox",
        display_name="Gruvbox Retro",
        primary="#fabd2f",       # Bright Yellow
        secondary="#fe8019",     # Bright Orange
        accent="#8ec07c",        # Bright Aqua
        success="#b8bb26",       # Bright Green
        warning="#d79921",       # Muted Yellow
        error="#fb4934",         # Bright Red
        muted="#928374",         # Gray
        text="#ebdbb2",          # Light
        border="#fabd2f",
        thinking="#d3869b",      # Bright Purple
        code_theme="gruvbox-dark",
    ),
    "synthwave": Theme(
        name="synthwave",
        display_name="Synthwave '84",
        primary="#ff7edb",       # Neon Pink
        secondary="#36f9f6",     # Neon Teal
        accent="#fede5d",        # Sunset Yellow
        success="#72f1b8",       # Neon Mint
        warning="#fe4450",       # Coral
        error="#fe4450",         # Red
        muted="#848bbd",         # Muted Purple
        text="#f92aad",          # Vibrant Pinkish
        border="#ff7edb",
        thinking="#b893fe",      # Neon Violet
        code_theme="monokai",
    ),
    "one_dark": Theme(
        name="one_dark",
        display_name="Atom One Dark",
        primary="#61afef",       # Light Blue
        secondary="#c678dd",     # Purple
        accent="#56b6c2",        # Cyan
        success="#98c379",       # Green
        warning="#e5c07b",       # Gold
        error="#e06c75",         # Red
        muted="#5c6370",         # Comment
        text="#abb2bf",          # Foreground
        border="#61afef",
        thinking="#d19a66",      # Peach/Orange
        code_theme="one-dark",
    ),
    "amber_crt": Theme(
        name="amber_crt",
        display_name="Amber CRT Phosphor",
        primary="#ffb000",       # Amber Phosphor
        secondary="#ff8000",     # Dark Amber
        accent="#ffea00",        # Bright Gold
        success="#ffb000",       # Amber
        warning="#ff5500",       # Flame
        error="#ff2200",         # Dark Red
        muted="#805500",         # Dim Amber
        text="#ffd280",          # Pale Amber
        border="#ffb000",
        thinking="#ffcc00",      # Yellow Amber
        code_theme="vim",
    ),
    "rose_pine": Theme(
        name="rose_pine",
        display_name="Rosé Pine",
        primary="#ebbcba",       # Rose
        secondary="#31748f",     # Pine
        accent="#f6c177",        # Gold
        success="#9ccfd8",       # Foam
        warning="#ea9a97",       # Muted Coral
        error="#eb6f92",         # Love
        muted="#6e6a86",         # Muted
        text="#e0def4",          # Text
        border="#ebbcba",
        thinking="#c4a7e7",      # Iris
        code_theme="fruity",
    ),
}

DEFAULT_THEME = "cyberpunk"

def load_custom_themes():
    """Load user-defined custom themes from ~/.harness/themes.json or .harness/themes.json."""
    candidates = [
        Path.home() / ".harness" / "themes.json",
        Path(".harness") / "themes.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if isinstance(v, dict):
                            theme_obj = Theme(
                                name=k,
                                display_name=v.get("display_name", k.title()),
                                primary=v.get("primary", "#00ffff"),
                                secondary=v.get("secondary", "#ff007f"),
                                accent=v.get("accent", "#ffff00"),
                                success=v.get("success", "#00ff66"),
                                warning=v.get("warning", "#ffaa00"),
                                error=v.get("error", "#ff3366"),
                                muted=v.get("muted", "#6c7086"),
                                text=v.get("text", "#ffffff"),
                                border=v.get("border", "#00ffff"),
                                thinking=v.get("thinking", "#bd93f9"),
                                code_theme=v.get("code_theme", "monokai"),
                            )
                            THEMES[k.lower()] = theme_obj
            except Exception:
                pass

# Load custom themes immediately on module import
load_custom_themes()

def get_theme(name: str) -> Theme:
    """Retrieve a theme by name with fallback to default."""
    clean = (name or "").lower().strip()
    return THEMES.get(clean, THEMES[DEFAULT_THEME])

def list_themes() -> Dict[str, str]:
    """Return dictionary of theme_key -> display_name."""
    return {k: v.display_name for k, v in THEMES.items()}

def render_theme_preview(theme_name: str) -> Panel:
    """Generate a rich visual test card panel showcasing a theme's palette."""
    t = get_theme(theme_name)

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column()
    grid.add_column()

    palette_sample = (
        f"[{t.primary}]■ Primary[/{t.primary}]  "
        f"[{t.secondary}]■ Secondary[/{t.secondary}]  "
        f"[{t.accent}]■ Accent[/{t.accent}]  "
        f"[{t.thinking}]■ Thinking[/{t.thinking}]\n"
        f"[{t.success}]✔ Success[/{t.success}]  "
        f"[{t.warning}]⚠️  Warning[/{t.warning}]  "
        f"[{t.error}]✖ Error[/{t.error}]  "
        f"[{t.muted}]▪ Muted[/{t.muted}]"
    )

    code_snippet = (
        f"def harness_agent():\n"
        f"    mode = '{t.name}'\n"
        f"    return f'Running in {{mode}} palette'"
    )
    syntax = Syntax(code_snippet, "python", theme=t.code_theme, line_numbers=False)

    grid.add_row(palette_sample, syntax)

    return Panel(
        grid,
        title=f"🎨 Theme Preview: [{t.primary} bold]{t.display_name}[/{t.primary} bold] (`{t.name}`)",
        border_style=t.border,
        padding=(1, 2),
    )
