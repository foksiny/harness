"""
Themes engine for Harness CLI.
Provides rich color palettes and formatting styles.
"""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class Theme:
    name: str
    display_name: str
    primary: str       # Main accent color (cyan, purple, etc.)
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
}

DEFAULT_THEME = "cyberpunk"

def get_theme(name: str) -> Theme:
    """Retrieve a theme by name with graceful fallback."""
    clean_name = (name or "").lower().strip()
    return THEMES.get(clean_name, THEMES[DEFAULT_THEME])

def list_themes() -> Dict[str, str]:
    """Return dictionary of theme_key -> display_name."""
    return {k: v.display_name for k, v in THEMES.items()}
