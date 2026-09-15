"""
Discord integration for Harness.

Binds the Harness agent into a Discord bot: slash commands for prompts and file
mentions, live tool notices, thinking delivered as ``> `` quote blocks, and the
final response posted in full when the turn completes.
"""
from harness.discord.renderer import (
    DiscordRenderState,
    DiscordOutgoing,
    chunk_message,
    chunk_quote,
    DISCORD_MAX_MESSAGE_LEN,
)
from harness.discord.help_text import DISCORD_HELP_TEXT, GENERAL_HELP_TEXT
from harness.discord.sync import get_relay, MessageRelay

__all__ = [
    "DiscordRenderState",
    "DiscordOutgoing",
    "chunk_message",
    "chunk_quote",
    "DISCORD_MAX_MESSAGE_LEN",
    "DISCORD_HELP_TEXT",
    "GENERAL_HELP_TEXT",
    "get_relay",
    "MessageRelay",
]

try:  # pragma: no cover - depends on optional discord.py install
    from harness.discord.bot import HarnessDiscordBot, run_discord_bot
    __all__ += ["HarnessDiscordBot", "run_discord_bot"]
except ImportError:
    pass