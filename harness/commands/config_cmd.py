"""
Configuration & API Key CLI Handlers for Harness.
Provides interactive setup wizard, key management, and settings inspection.
"""
import getpass
from typing import Dict, Any, List, Optional
from rich.table import Table
from rich.panel import Panel
from harness.config import (
    HarnessConfig,
    load_config,
    save_config,
    mask_key,
    USER_CONFIG_PATH,
    WORKSPACE_CONFIG_PATH,
)
from harness.themes import THEMES
from harness.providers import PROVIDER_CONFIGS

def display_config_table(config: HarnessConfig, renderer) -> None:
    """Print formatted table of all configuration values."""
    table = Table(title="Harness Configuration Settings", border_style=renderer.theme.border)
    table.add_column("Setting", style=f"bold {renderer.theme.primary}")
    table.add_column("Current Value", style="white")
    table.add_column("Type", style="dim")

    for k, v in config.to_dict().items():
        if k == "api_keys":
            val_str = f"{len(v)} key(s) stored in config"
        elif k == "base_urls":
            val_str = f"{len(v)} custom endpoint(s)"
        elif k == "discord_bot_token":
            resolved = config.get_discord_token()
            val_str = mask_key(resolved) if resolved else "(not set)"
        else:
            val_str = str(v)
        table.add_row(k, val_str, type(v).__name__)

    renderer.console.print(table)
    renderer.console.print(f"[dim]Config file: {USER_CONFIG_PATH}[/dim]\n")

def display_keys_table(config: HarnessConfig, renderer) -> None:
    """Print formatted table of API key statuses across all providers."""
    table = Table(title="LLM Provider API Key Status", border_style=renderer.theme.border)
    table.add_column("Provider", style=f"bold {renderer.theme.primary}")
    table.add_column("Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Active Key", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Environment Variable", style="dim")

    statuses = config.list_keys_status()
    for s in statuses:
        status_badge = "[bold green]✔ READY[/bold green]" if s["status"] == "configured" else "[dim red]MISSING[/dim red]"
        source_badge = "[cyan]config.json[/cyan]" if s["source"] == "config" else ("[yellow]ENV[/yellow]" if s["source"] == "env" else "-")
        table.add_row(
            s["provider"],
            s["display_name"],
            status_badge,
            s["masked"],
            source_badge,
            s["env_var"],
        )

    renderer.console.print(table)
    renderer.console.print("[dim]Set key via: `harness keys set <provider>` or `/keys set <provider>`[/dim]\n")

def run_setup_wizard(config: HarnessConfig, renderer) -> None:
    """Interactive onboarding wizard to configure Harness."""
    renderer.console.print(Panel(
        "⚡ [bold cyan]Harness Interactive Setup Wizard[/bold cyan]\n"
        "Let's configure your default AI provider, operational mode, visual theme, and optional Discord bot.",
        border_style="cyan",
    ))

    # 1. Choose Provider
    renderer.console.print("\n[bold white]Step 1: Choose Default Provider[/bold white]")
    prov_keys = [k for k in PROVIDER_CONFIGS.keys() if k != "mock"]
    for i, p in enumerate(prov_keys, 1):
        renderer.console.print(f"  [{i}] [bold]{p}[/bold] ({PROVIDER_CONFIGS[p]['display_name']})")
    try:
        p_choice = input(f"Select provider [default: {config.provider}]: ").strip()
        if p_choice.isdigit() and 1 <= int(p_choice) <= len(prov_keys):
            config.provider = prov_keys[int(p_choice) - 1]
            config.model = PROVIDER_CONFIGS[config.provider]["default_model"]
    except (EOFError, KeyboardInterrupt):
        return

    # 2. Set API Key
    renderer.console.print(f"\n[bold white]Step 2: Enter API Key for {config.provider.upper()}[/bold white]")
    current_masked = mask_key(config.get_api_key(config.provider))
    renderer.console.print(f"Current key: [dim]{current_masked}[/dim]")
    try:
        key_input = getpass.getpass(f"Enter API key (leave empty to keep current): ").strip()
        if key_input:
            config.set_api_key(config.provider, key_input)
            renderer.print_success(f"Key saved for {config.provider} ({mask_key(key_input)})")
    except (EOFError, KeyboardInterrupt):
        return

    # 3. Choose Mode
    renderer.console.print("\n[bold white]Step 3: Choose Default Mode[/bold white]")
    renderer.console.print("  [1] build (Standard interactive developer mode)")
    renderer.console.print("  [2] plan  (Read-only architectural analysis)")
    renderer.console.print("  [3] super (Autonomous multi-turn goal engine)")
    try:
        m_choice = input(f"Select mode [default: {config.mode}]: ").strip()
        mode_map = {"1": "build", "2": "plan", "3": "super"}
        if m_choice in mode_map:
            config.mode = mode_map[m_choice]
    except (EOFError, KeyboardInterrupt):
        return

    # 4. Choose Theme
    renderer.console.print("\n[bold white]Step 4: Choose Visual Theme[/bold white]")
    theme_names = list(THEMES.keys())
    for i, t in enumerate(theme_names, 1):
        renderer.console.print(f"  [{i}] {t} ({THEMES[t].display_name})")
    try:
        t_choice = input(f"Select theme [default: {config.theme}]: ").strip()
        if t_choice.isdigit() and 1 <= int(t_choice) <= len(theme_names):
            config.theme = theme_names[int(t_choice) - 1]
            renderer.set_theme(config.theme)
    except (EOFError, KeyboardInterrupt):
        return

    # 5. Discord Bot Setup
    renderer.console.print("\n[bold white]Step 5: Discord Bot Integration[/bold white]")
    renderer.console.print(
        "  Harness can run as a Discord bot, letting you interact with the agent\n"
        "  from any Discord channel. This is optional.\n"
    )
    has_token = bool(config.get_discord_token())
    if has_token:
        renderer.console.print(f"  Current token: [green]configured[/green]")
    else:
        renderer.console.print(f"  Current token: [red]not set[/red]")
    renderer.console.print("  [1] Configure Discord bot now")
    renderer.console.print("  [2] Skip (keep current settings)")
    try:
        d_choice = input("Select [default: 2]: ").strip()
        if d_choice == "1":
            _setup_discord_bot(config, renderer)
    except (EOFError, KeyboardInterrupt):
        return

    save_config(config)
    renderer.print_success(f"Configuration successfully saved to {USER_CONFIG_PATH}!")


def _setup_discord_bot(config: HarnessConfig, renderer) -> None:
    """Guided Discord bot configuration sub-wizard."""
    renderer.console.print(Panel(
        "🤖 [bold cyan]Discord Bot Setup[/bold cyan]\n"
        "Follow the steps below to configure your Discord bot.\n\n"
        "[bold]Prerequisites:[/bold]\n"
        "  1. Go to [link=https://discord.com/developers/applications]https://discord.com/developers/applications[/link]\n"
        "  2. Click 'New Application' → name it → create it\n"
        "  3. Go to 'Bot' tab → copy the bot token\n"
        "  4. Go to 'OAuth2' → 'URL Generator'\n"
        "     Scopes: [bold]bot[/bold], [bold]applications.commands[/bold]\n"
        "     Bot Permissions: [bold]Send Messages[/bold], [bold]Read Message History[/bold],\n"
        "     [bold]Use Slash Commands[/bold], [bold]Attach Files[/bold]\n"
        "  5. Copy the generated URL → open in browser → invite to your server\n"
        "  6. Right-click the channel → 'Copy Channel ID' (enable Developer Mode in Discord settings)\n",
        border_style="cyan",
    ))

    # Bot token
    renderer.console.print("[bold]5a. Bot Token[/bold]")
    current_masked = mask_key(config.get_discord_token())
    renderer.console.print(f"  Current: [dim]{current_masked or '(not set)'}[/dim]")
    try:
        token = getpass.getpass("  Enter bot token (leave empty to keep current): ").strip()
        if token:
            config.set_discord_token(token)
            renderer.print_success("  Token saved to secure store")
    except (EOFError, KeyboardInterrupt):
        return

    # Guild ID (for instant command sync)
    renderer.console.print("\n[bold]5b. Guild (Server) ID[/bold]")
    renderer.console.print(
        "  Optional: setting a guild ID makes slash commands appear instantly\n"
        "  (otherwise global sync takes up to 1 hour)."
    )
    renderer.console.print(f"  Current: [dim]{config.discord_guild_id or '(not set — using global sync)'}[/dim]")
    try:
        guild_id = input("  Enter guild ID (leave empty to skip): ").strip()
        if guild_id:
            config.discord_guild_id = guild_id
            renderer.print_success(f"  Guild ID set: {guild_id}")
    except (EOFError, KeyboardInterrupt):
        return

    # Workspace
    renderer.console.print("\n[bold]5c. Bot Workspace[/bold]")
    renderer.console.print(
        "  The working directory the bot operates in. Leave empty to use the\n"
        "  current directory."
    )
    try:
        workspace = input(f"  Workspace path [default: {config.discord_workspace or '(current dir)'}]: ").strip()
        if workspace:
            config.discord_workspace = workspace
            renderer.print_success(f"  Workspace set: {workspace}")
    except (EOFError, KeyboardInterrupt):
        return

    # Permission level
    renderer.console.print("\n[bold]5d. Bot Permission Level[/bold]")
    renderer.console.print(
        "  Controls what the bot agent can do without asking for approval.\n"
        "  [1] default — reads auto-approve, writes prompt for approval (Recommended)\n"
        "  [2] full    — everything auto-executes (use only in trusted servers)\n"
        "  [3] secure  — everything prompts for approval"
    )
    perm_map = {"1": "default", "2": "full", "3": "secure"}
    try:
        perm_choice = input(f"  Select [default: {config.discord_permission or 'default'}]: ").strip()
        if perm_choice in perm_map:
            config.discord_permission = perm_map[perm_choice]
            renderer.print_success(f"  Permission set: {config.discord_permission}")
    except (EOFError, KeyboardInterrupt):
        return

    # Channel filtering
    renderer.console.print("\n[bold]5e. Channel Filtering[/bold]")
    renderer.console.print(
        "  Restrict which channels the bot responds in.\n"
        "  [1] blacklist (default) — responds everywhere EXCEPT listed channels\n"
        "  [2] whitelist — responds ONLY in listed channels\n"
        "  [3] none — responds in all channels"
    )
    try:
        cf_choice = input("  Select [default: 1]: ").strip()
        if cf_choice == "1":
            config.discord_channel_mode = "blacklist"
            channels = input("  Enter blocked channel IDs (comma-separated, empty to skip): ").strip()
            config.discord_blacklisted_channels = channels
        elif cf_choice == "2":
            config.discord_channel_mode = "whitelist"
            channels = input("  Enter allowed channel IDs (comma-separated): ").strip()
            config.discord_whitelisted_channels = channels
    except (EOFError, KeyboardInterrupt):
        return

    # Auto-start
    renderer.console.print("\n[bold]5f. Auto-Start[/bold]")
    renderer.console.print("  Start the Discord bot automatically when launching Harness TUI?")
    try:
        auto = input("  Auto-start bot? (y/N) [default: N]: ").strip().lower()
        config.discord_auto_start = auto in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return

    renderer.print_success("Discord bot configuration complete!")
