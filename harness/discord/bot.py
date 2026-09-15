"""
Harness Discord bot.

Architecture
------------
- **Per-channel serialization**: Each Discord channel gets its own
  ``_ChannelRuntime`` holding a ``threading.Lock`` and a ``HarnessAgent``.
  The lock is held for the **entire** ``agent.step()`` generator lifecycle,
  preventing concurrent mutations of agent state within a single channel.

- **Non-blocking event loop**: ``agent.step()`` (a synchronous generator) runs
  on a thread-pool via ``asyncio.to_thread``.  Events are bridged to the
  discord.py async event loop through a ``queue.Queue``.  The event loop
  consumes events without blocking and sends Discord messages in real time.

- **Thread-safe permission approval**: When the agent asks for permission,
  the worker thread blocks on a ``concurrent.futures.Future``.  The approval
  UI (Approve / Deny buttons) is scheduled onto the discord.py event loop
  via ``asyncio.run_coroutine_threadsafe``.  When the user clicks a button,
  the future is resolved and the worker thread unblocks.

- **No global state mutation**: ``os.chdir()`` is never called from the bot
  thread.  Workspace paths are resolved via config.

- **Non-blocking config saves**: ``save_config()`` calls are wrapped in
  ``asyncio.to_thread`` to avoid blocking the discord.py event loop.

The ``discord.py`` dependency is imported lazily so the rest of Harness works
(and tests run) without it.
"""
import asyncio
import concurrent.futures
import logging
import os
import queue
import re
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.discord.renderer import DiscordRenderState, DiscordOutgoing, chunk_message, chunk_quote
from harness.discord.help_text import DISCORD_HELP_TEXT, GENERAL_HELP_TEXT
from harness.discord.sync import get_relay

log = logging.getLogger("harness.discord")

try:
    import discord
    from discord import app_commands
    HAS_DISCORD = True
except ImportError:  # pragma: no cover - exercised only when discord.py is absent
    discord = None
    app_commands = None
    HAS_DISCORD = False

DEFAULT_PERMISSION = "default"
_SENTINEL = object()


def _require_discord() -> None:
    if not HAS_DISCORD:
        raise RuntimeError(
            "Discord support requires 'discord.py'. Install it with:\n"
            "    pip install 'harness-cli[discord]'\n"
            "or run: pip install discord.py"
        )


if HAS_DISCORD:

    # ── Permission approval UI ───────────────────────────────────────────

    class _ApprovalView(discord.ui.View):
        """Approve / Deny buttons for a pending permission request."""

        def __init__(self, future: concurrent.futures.Future, message: str):
            super().__init__(timeout=900)
            self._future = future
            self.message = message

        async def _resolve(self, interaction: discord.Interaction, approved: bool) -> None:
            if not self._future.done():
                self._future.set_result(approved)
            for child in self.children:
                child.disabled = True
            try:
                await interaction.response.edit_message(view=self)
            except discord.HTTPException:
                log.debug("Could not edit approval message (interaction expired?)")
            try:
                await interaction.followup.send(
                    "✅ Approved" if approved else "⛔ Denied",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
        async def _approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._resolve(interaction, True)

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
        async def _deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._resolve(interaction, False)

    # ── Per-channel runtime ──────────────────────────────────────────────

    class _ChannelRuntime:
        """One agent + serialization lock per Discord channel.

        The ``lock`` is a **threading** lock (not asyncio) because
        ``agent.step()`` runs on a thread pool.  It is held for the full
        duration of a turn, preventing two concurrent ``/ask`` commands on
        the same channel from corrupting agent state.
        """

        def __init__(self, config: HarnessConfig):
            self.config = config
            self.agent: Optional[HarnessAgent] = None
            self.lock = threading.Lock()

        def ensure_agent(self) -> HarnessAgent:
            if self.agent is None:
                perm = (self.config.discord_permission or DEFAULT_PERMISSION).lower()
                discord_cfg = replace(self.config, permission=perm)

                self.agent = HarnessAgent(
                    config=discord_cfg,
                )
            return self.agent

    # ── Bot class ────────────────────────────────────────────────────────

    class HarnessDiscordBot:
        """Discord integration for Harness."""

        def __init__(
            self,
            config: HarnessConfig,
            token: Optional[str] = None,
            workspace: Optional[str] = None,
            quiet: bool = False,
        ):
            _require_discord()
            self.config = config
            self.token = token or config.get_discord_token() or ""
            self.workspace = workspace or config.discord_workspace or os.getcwd()
            self.max_message_len = 1990  # 10-char safety margin below Discord's 2000
            self.quiet = quiet

            intents = discord.Intents.default()
            intents.message_content = True
            self.bot = discord.Client(intents=intents)
            self.tree = app_commands.CommandTree(self.bot)

            # Per-channel runtimes — each channel keeps its own conversation.
            self._runtimes: Dict[int, _ChannelRuntime] = {}

            # Suppress discord.py noisy logging in background mode
            if quiet:
                logging.getLogger("discord").setLevel(logging.CRITICAL)
                logging.getLogger("discord.gateway").setLevel(logging.CRITICAL)
                logging.getLogger("discord.http").setLevel(logging.CRITICAL)

            self._register_events()
            self._register_commands()

        # ── Startup hooks ───────────────────────────────────────────────

        def _register_events(self) -> None:
            @self.bot.event
            async def on_ready() -> None:
                log.info("Bot connected as %s (ID: %s)", self.bot.user, self.bot.user.id)
                guild_id = self.config.discord_guild_id.strip()
                try:
                    # Always sync globally — our commands are registered globally
                    synced = await self.tree.sync()
                    log.info("Synced %d slash commands globally", len(synced))

                    # If a guild is set, also do a guild-specific sync so commands
                    # appear instantly there (Discord propagates global sync slowly)
                    if guild_id:
                        guild = discord.Object(id=int(guild_id))
                        guild_synced = await self.tree.sync(guild=guild)
                        log.info("Synced %d guild-specific commands to guild %s", len(guild_synced), guild_id)
                except Exception as exc:
                    log.error("Slash command sync failed: %s", exc, exc_info=True)
                scope = f"guild {guild_id} + global" if guild_id else "global"
                if not self.quiet:
                    print("=" * 60)
                    print("⚡ Harness Discord bot is online!")
                    print(f"  Provider: {self.config.provider} / Model: {self.config.model}")
                    print(f"  Workspace: {self.workspace}")
                    print(f"  Allowed channels: {self.config.discord_channel_ids or 'all'}")
                    print(f"  Slash commands synced: {scope}")
                    print("  /ask <prompt>  •  /help discord  •  /status")
                    print("=" * 60)

            @self.bot.event
            async def on_error(event_method: str, *args, **kwargs) -> None:
                log.error("on_error in %s", event_method, exc_info=True)

        # ── Slash command registration ───────────────────────────────────

        def _register_commands(self) -> None:
            @self.tree.error
            async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
                log.error("Slash command error: %s", error, exc_info=True)
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(f"❌ Error: {error}", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)
                except discord.HTTPException:
                    pass

            # If a guild ID is set, register commands as guild-specific so they
            # sync instantly (global sync takes up to 1 hour to propagate).
            _guild_obj = None
            gid = self.config.discord_guild_id.strip()
            if gid:
                _guild_obj = discord.Object(id=int(gid))

            def _cmd(name: str, description: str):
                """Wrapper that auto-registers per-guild when discord_guild_id is set."""
                if _guild_obj is not None:
                    return self.tree.command(name=name, description=description, guild=_guild_obj)
                return self.tree.command(name=name, description=description)

            @_cmd(name="ask", description="Run the Harness agent on a prompt.")
            @app_commands.describe(
                prompt="What should the agent do?",
                file="Host file to mention, e.g. src/main.py (optional).",
            )
            async def ask(interaction: discord.Interaction, prompt: str, file: Optional[str] = None):
                try:
                    await self._cmd_ask(interaction, prompt, file)
                except Exception as exc:
                    log.exception("Unhandled crash in /ask handler")
                    try:
                        if interaction.response.is_done():
                            await interaction.followup.send(f"❌ Bot error: {exc}", ephemeral=True)
                        else:
                            await interaction.response.send_message(f"❌ Bot error: {exc}", ephemeral=True)
                    except discord.HTTPException:
                        pass

            @_cmd(name="help", description="Harness help (use 'discord' topic for the setup guide).")
            @app_commands.describe(topic="Topic: 'discord' for the configuration guide, empty for general help.")
            async def help_cmd(interaction: discord.Interaction, topic: Optional[str] = None):
                t = (topic or "").strip().lower()
                text = DISCORD_HELP_TEXT if t in ("discord", "config", "setup") else GENERAL_HELP_TEXT
                await self._send_chunked(interaction, text)

            @_cmd(name="status", description="Show the Harness agent configuration.")
            async def status_cmd(interaction: discord.Interaction):
                cfg = self.config
                token = "✅ configured" if cfg.get_discord_token() else "❌ missing"
                lines = [
                    "**⚡ Harness Discord Bot**",
                    f"• Provider: `{cfg.provider}` / Model: `{cfg.model}`",
                    f"• Mode: `{cfg.mode}` | Bot permission: `{cfg.discord_permission or DEFAULT_PERMISSION}`",
                    f"• Discord token: {token}",
                    f"• Workspace: `{self.workspace}`",
                    f"• Allowed channels: `{cfg.discord_channel_ids or 'all'}`",
                    f"• Guild sync: `{cfg.discord_guild_id or 'global'}`",
                ]
                await self._send_chunked(interaction, "\n".join(lines))

            @_cmd(name="mode", description="Switch operational mode: plan, build, super.")
            @app_commands.describe(mode="plan, build, or super")
            async def mode_cmd(interaction: discord.Interaction, mode: str):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                from harness.core.modes import Mode as HarnessMode
                try:
                    m = HarnessMode.from_string(mode)
                    self.config.mode = m.value
                    await self._async_save_config()
                    await interaction.response.send_message(f"✅ Mode switched to: **{m.value.upper()}**")
                except Exception:
                    await interaction.response.send_message(
                        "❌ Invalid mode. Choose from: `plan`, `build`, `super`.", ephemeral=True
                    )

            @_cmd(name="provider", description="Show or switch the active LLM provider.")
            @app_commands.describe(name="Provider name (empty to list all)")
            async def provider_cmd(interaction: discord.Interaction, name: Optional[str] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                if not name:
                    from harness.providers import list_providers
                    provs = list_providers()
                    lines = [f"**Active provider:** `{self.config.provider}`", "", "**Available providers:**"]
                    for k, v in provs.items():
                        marker = " *(active)*" if k == self.config.provider else ""
                        lines.append(f"• `{k}`: {v}{marker}")
                    await self._send_chunked(interaction, "\n".join(lines))
                    return
                from harness.providers import list_providers
                provs = list_providers()
                pname = name.lower().strip()
                if pname in provs:
                    self.config.provider = pname
                    await self._async_save_config()
                    await interaction.response.send_message(f"✅ Switched provider to: **{provs[pname]}** (`{pname}`)")
                else:
                    await interaction.response.send_message(
                        f"❌ Unknown provider `{pname}`. Use `/provider` to list available ones.", ephemeral=True
                    )

            @_cmd(name="model", description="Show or change the current model.")
            @app_commands.describe(name="Model name (empty to show current)")
            async def model_cmd(interaction: discord.Interaction, name: Optional[str] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                if not name:
                    await interaction.response.send_message(
                        f"**Current model:** `{self.config.model}`\nUsage: `/model <model_name>`"
                    )
                    return
                self.config.model = name.strip()
                await self._async_save_config()
                await interaction.response.send_message(f"✅ Model switched to: **{name.strip()}**")

            @_cmd(name="session", description="Manage sessions: list, create, resume.")
            @app_commands.describe(action="list, create, or resume", arg="Session ID or title")
            async def session_cmd(interaction: discord.Interaction, action: str, arg: Optional[str] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                rt = self._get_runtime(interaction.channel_id)
                agent = rt.ensure_agent()
                action = action.lower().strip()
                if action == "list":
                    sessions = agent.session_manager.list_all()
                    if not sessions:
                        await interaction.response.send_message("ℹ️ No saved sessions.")
                        return
                    lines = ["**Saved Sessions:**"]
                    for s in sessions[:10]:
                        lines.append(f"• `{s['id']}`: {s['title']} ({s['model']}, {s['turns']} turns)")
                    await self._send_chunked(interaction, "\n".join(lines))
                elif action == "create":
                    title = arg or "New Session"
                    ns = agent.session_manager.create(
                        provider=agent.provider.name,
                        model=agent.session.model if agent.session else agent.config.model,
                        mode=agent.mode.value,
                        permission=agent.permission_manager.level.value,
                        thinking_effort=agent.config.thinking_effort,
                        title=title,
                    )
                    await interaction.response.send_message(f"✅ Created session: `{ns.id}` ({ns.title})")
                elif action == "resume" and arg:
                    loaded = agent.session_manager.load(arg)
                    if loaded:
                        agent.session = loaded
                        await interaction.response.send_message(f"✅ Resumed session `{loaded.id}`: {loaded.title}")
                    else:
                        await interaction.response.send_message(f"❌ Session `{arg}` not found.", ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "Usage: `/session list`, `/session create [title]`, `/session resume <id>`", ephemeral=True
                    )

            @_cmd(name="todo", description="Manage tasks: list, add, clear.")
            @app_commands.describe(action="list, add, or clear", arg="Task title")
            async def todo_cmd(interaction: discord.Interaction, action: str, arg: Optional[str] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                rt = self._get_runtime(interaction.channel_id)
                agent = rt.ensure_agent()
                action = action.lower().strip()
                if action == "list" or not action:
                    tasks_md = agent.todo_manager.format_markdown()
                    await self._send_chunked(interaction, tasks_md or "ℹ️ No tasks.")
                elif action == "add" and arg:
                    item = agent.todo_manager.add_task(arg)
                    await interaction.response.send_message(f"✅ Added task #{item.id}: `{item.title}`")
                elif action == "clear":
                    agent.todo_manager.clear()
                    await interaction.response.send_message("✅ Cleared all tasks.")
                else:
                    await interaction.response.send_message(
                        "Usage: `/todo list`, `/todo add <title>`, `/todo clear`", ephemeral=True
                    )

            @_cmd(name="clear", description="Clear bot and your messages from this channel.")
            @app_commands.describe(amount="Number of messages to scan (default 100, max 1000)")
            async def clear_cmd(interaction: discord.Interaction, amount: Optional[int] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                scan = min(max(amount or 100, 1), 1000)
                await interaction.response.defer(thinking=True)
                try:
                    can_manage = interaction.channel.permissions_for(interaction.guild.me).manage_messages
                    from datetime import datetime, timezone
                    deleted_total = 0
                    before = None
                    remaining = scan
                    while remaining > 0:
                        batch = min(remaining, 100)
                        kwargs = {"limit": batch}
                        if before:
                            kwargs["before"] = before
                        to_delete = []
                        async for msg in interaction.channel.history(**kwargs):
                            before = msg
                            remaining -= 1
                            is_bot = msg.author == interaction.guild.me
                            is_mine = msg.author == interaction.user
                            if not (is_bot or is_mine or can_manage):
                                continue
                            if msg.created_at and (datetime.now(timezone.utc) - msg.created_at).days > 14:
                                continue
                            to_delete.append(msg)
                        if to_delete:
                            await interaction.channel.delete_messages(to_delete)
                            deleted_total += len(to_delete)
                        else:
                            break
                    await interaction.followup.send(f"🧹 Deleted {deleted_total} message(s).", ephemeral=True)
                except discord.HTTPException as exc:
                    log.warning("Clear failed: %s", exc)
                    await interaction.followup.send(f"❌ Clear failed: {exc}", ephemeral=True)

            @_cmd(name="skills", description="List or reload available skills.")
            @app_commands.describe(action="list or reload")
            async def skills_cmd(interaction: discord.Interaction, action: Optional[str] = None):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                rt = self._get_runtime(interaction.channel_id)
                agent = rt.ensure_agent()
                act = (action or "list").lower().strip()
                if act == "reload":
                    agent.skills_manager.reload()
                    await interaction.response.send_message(f"✅ Reloaded {len(agent.skills_manager.skills)} skills.")
                    return
                skills = agent.skills_manager.list_skills()
                if not skills:
                    await interaction.response.send_message("ℹ️ No skills loaded.")
                    return
                lines = [f"**Loaded Skills ({len(skills)}):**"]
                for s in skills:
                    kind = "Built-in" if s.is_builtin else "Custom"
                    lines.append(f"• **{s.name}** ({kind}): {s.description[:120]}")
                await self._send_chunked(interaction, "\n".join(lines))

            @_cmd(name="reload", description="Reload config, skills, and permissions from disk.")
            async def reload_cmd(interaction: discord.Interaction):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                if not self._user_allowed(interaction.user.id):
                    await interaction.response.send_message("⛔ You are not allowed.", ephemeral=True)
                    return

                from harness.config import load_config
                from harness.core.permissions import PermissionLevel
                errors = []
                reloaded = []

                # 1. Reload config from disk
                try:
                    fresh = load_config()
                    self.config = fresh
                    for rt in self._runtimes.values():
                        if rt.agent is not None:
                            rt.agent.config = fresh
                    reloaded.append("config")
                except Exception as exc:
                    errors.append(f"config: {exc}")

                # 2. Reload skills across all channel agents
                try:
                    total_skills = 0
                    for rt in self._runtimes.values():
                        if rt.agent is not None:
                            rt.agent.skills_manager.reload()
                            total_skills = len(rt.agent.skills_manager.skills)
                    reloaded.append(f"skills ({total_skills} loaded)")
                except Exception as exc:
                    errors.append(f"skills: {exc}")

                # 3. Re-sync permission level
                try:
                    new_perm = PermissionLevel.from_string(self.config.permission)
                    for rt in self._runtimes.values():
                        if rt.agent is not None:
                            rt.agent.permission_manager.level = new_perm
                    reloaded.append(f"permission → {self.config.permission}")
                except Exception as exc:
                    errors.append(f"permission: {exc}")

                # 4. Re-sync gateway log level
                try:
                    import logging as _logging
                    _logging.getLogger("discord").setLevel(
                        _logging.WARNING if self.config.discord_quiet else _logging.INFO
                    )
                    reloaded.append("log level")
                except Exception:
                    pass

                parts = []
                if reloaded:
                    parts.append("**✅ Reloaded:** " + ", ".join(reloaded))
                if errors:
                    parts.append("**❌ Errors:**\n" + "\n".join(f"• {e}" for e in errors))
                await interaction.response.send_message("\n".join(parts) or "✅ Reload complete.")

            @_cmd(name="compact", description="Trigger manual context compaction.")
            async def compact_cmd(interaction: discord.Interaction):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                rt = self._get_runtime(interaction.channel_id)
                agent = rt.ensure_agent()
                if agent.session is None:
                    await interaction.response.send_message("ℹ️ No active session yet. Send a message first.", ephemeral=True)
                    return
                sys_prompt = agent._build_system_prompt("")
                compacted, stats = agent.compactor.compact(agent.session.messages, sys_prompt)
                if not stats.get("compacted"):
                    await interaction.response.send_message("ℹ️ Nothing to compact yet.")
                    return
                agent._record_message_span_change(agent.session.messages, compacted)
                agent.session.messages = compacted
                await interaction.response.send_message(
                    f"✅ Context compacted: {stats.get('before_tokens', 0):,} → {stats.get('after_tokens', 0):,} tokens "
                    f"(saved {stats.get('saved_tokens', 0):,} tokens)"
                )

            @_cmd(name="tokens", description="Show token metrics and RAM usage.")
            async def tokens_cmd(interaction: discord.Interaction):
                if not self._channel_allowed(interaction.channel_id):
                    await interaction.response.send_message("⛔ Channel not allowed.", ephemeral=True)
                    return
                from harness.sysinfo import get_ram_usage_mb
                ram = get_ram_usage_mb()
                rt = self._get_runtime(interaction.channel_id)
                agent = rt.ensure_agent()
                if agent.session is None:
                    await interaction.response.send_message(f"**Token usage:** 0 tokens | **RAM:** {ram} MB")
                    return
                sys_prompt = agent._build_system_prompt("")
                status = agent.compactor.check_status(agent.session.messages, sys_prompt)
                await interaction.response.send_message(
                    f"**Token usage:** {status.total_tokens:,} / {status.context_window:,} "
                    f"({status.usage_ratio * 100:.1f}%) | **Turns:** {len(agent.session.messages)} | **RAM:** {ram} MB"
                )

            # ── /discord management command ──────────────────────────────

            @_cmd(name="discord", description="Manage Harness Discord bot settings.")
            @app_commands.describe(
                action="Subcommand: status, channel_mode, user_mode, block_channel, unblock_channel, "
                       "block_user, unblock_user, permission, auto_start, set_guild",
                value="Value for the subcommand (ID, mode name, etc.)",
            )
            async def discord_cmd(interaction: discord.Interaction, action: str, value: Optional[str] = None):
                act = (action or "").lower().strip()
                cfg = self.config
                changed = False

                if act == "status":
                    ch_mode = cfg.discord_channel_mode or "blacklist"
                    us_mode = cfg.discord_user_mode or "blacklist"
                    bl_ch = cfg.discord_blacklisted_channels or "(none)"
                    wl_ch = cfg.discord_whitelisted_channels or "(none)"
                    bl_us = cfg.discord_blacklisted_users or "(none)"
                    wl_us = cfg.discord_whitelisted_users or "(none)"
                    guild = cfg.discord_guild_id or "(global sync)"
                    lines = [
                        "**⚡ Harness Discord Bot Settings**",
                        f"• Channel mode: `{ch_mode}`",
                        f"• Blacklisted channels: `{bl_ch}`",
                        f"• Whitelisted channels: `{wl_ch}`",
                        f"• User mode: `{us_mode}`",
                        f"• Blacklisted users: `{bl_us}`",
                        f"• Whitelisted users: `{wl_us}`",
                        f"• Permission: `{cfg.discord_permission}`",
                        f"• Auto-start: `{cfg.discord_auto_start}`",
                        f"• Guild: `{guild}`",
                    ]
                    await self._send_chunked(interaction, "\n".join(lines))
                    return

                if act == "channel_mode":
                    mode = (value or "").lower().strip()
                    if mode not in ("blacklist", "whitelist"):
                        await interaction.response.send_message(
                            "Usage: `/discord channel_mode blacklist` or `/discord channel_mode whitelist`",
                            ephemeral=True,
                        )
                        return
                    cfg.discord_channel_mode = mode
                    changed = True
                    await interaction.response.send_message(f"✅ Channel mode set to: **{mode}**")

                elif act == "user_mode":
                    mode = (value or "").lower().strip()
                    if mode not in ("blacklist", "whitelist"):
                        await interaction.response.send_message(
                            "Usage: `/discord user_mode blacklist` or `/discord user_mode whitelist`",
                            ephemeral=True,
                        )
                        return
                    cfg.discord_user_mode = mode
                    changed = True
                    await interaction.response.send_message(f"✅ User mode set to: **{mode}**")

                elif act == "block_channel":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord block_channel <channel_id>`", ephemeral=True)
                        return
                    ch_id = value.strip()
                    current = cfg.discord_blacklisted_channels
                    ids = [c for c in current.split(",") if c.strip()]
                    if ch_id not in ids:
                        ids.append(ch_id)
                    cfg.discord_blacklisted_channels = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ Channel `{ch_id}` added to blacklist")

                elif act == "unblock_channel":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord unblock_channel <channel_id>`", ephemeral=True)
                        return
                    ch_id = value.strip()
                    ids = [c for c in cfg.discord_blacklisted_channels.split(",") if c.strip() and c.strip() != ch_id]
                    cfg.discord_blacklisted_channels = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ Channel `{ch_id}` removed from blacklist")

                elif act == "allow_channel":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord allow_channel <channel_id>`", ephemeral=True)
                        return
                    ch_id = value.strip()
                    current = cfg.discord_whitelisted_channels
                    ids = [c for c in current.split(",") if c.strip()]
                    if ch_id not in ids:
                        ids.append(ch_id)
                    cfg.discord_whitelisted_channels = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ Channel `{ch_id}` added to whitelist")

                elif act == "disallow_channel":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord disallow_channel <channel_id>`", ephemeral=True)
                        return
                    ch_id = value.strip()
                    ids = [c for c in cfg.discord_whitelisted_channels.split(",") if c.strip() and c.strip() != ch_id]
                    cfg.discord_whitelisted_channels = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ Channel `{ch_id}` removed from whitelist")

                elif act == "block_user":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord block_user <user_id>`", ephemeral=True)
                        return
                    u_id = value.strip()
                    ids = [c for c in cfg.discord_blacklisted_users.split(",") if c.strip()]
                    if u_id not in ids:
                        ids.append(u_id)
                    cfg.discord_blacklisted_users = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ User `{u_id}` added to blacklist")

                elif act == "unblock_user":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord unblock_user <user_id>`", ephemeral=True)
                        return
                    u_id = value.strip()
                    ids = [c for c in cfg.discord_blacklisted_users.split(",") if c.strip() and c.strip() != u_id]
                    cfg.discord_blacklisted_users = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ User `{u_id}` removed from blacklist")

                elif act == "allow_user":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord allow_user <user_id>`", ephemeral=True)
                        return
                    u_id = value.strip()
                    ids = [c for c in cfg.discord_whitelisted_users.split(",") if c.strip()]
                    if u_id not in ids:
                        ids.append(u_id)
                    cfg.discord_whitelisted_users = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ User `{u_id}` added to whitelist")

                elif act == "disallow_user":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord disallow_user <user_id>`", ephemeral=True)
                        return
                    u_id = value.strip()
                    ids = [c for c in cfg.discord_whitelisted_users.split(",") if c.strip() and c.strip() != u_id]
                    cfg.discord_whitelisted_users = ",".join(ids)
                    changed = True
                    await interaction.response.send_message(f"✅ User `{u_id}` removed from whitelist")

                elif act == "permission":
                    perm = (value or "").lower().strip()
                    if perm not in ("secure", "default", "full"):
                        await interaction.response.send_message(
                            "Usage: `/discord permission secure|default|full`", ephemeral=True,
                        )
                        return
                    cfg.discord_permission = perm
                    changed = True
                    await interaction.response.send_message(f"✅ Permission set to: **{perm}**")

                elif act == "auto_start":
                    val = (value or "").lower().strip()
                    if val in ("true", "1", "yes", "on"):
                        cfg.discord_auto_start = True
                        changed = True
                        await interaction.response.send_message("✅ Auto-start **enabled**")
                    elif val in ("false", "0", "no", "off"):
                        cfg.discord_auto_start = False
                        changed = True
                        await interaction.response.send_message("✅ Auto-start **disabled**")
                    else:
                        await interaction.response.send_message(
                            "Usage: `/discord auto_start true` or `/discord auto_start false`", ephemeral=True,
                        )

                elif act == "set_guild":
                    if not value:
                        await interaction.response.send_message("Usage: `/discord set_guild <guild_id>`", ephemeral=True)
                        return
                    cfg.discord_guild_id = value.strip()
                    changed = True
                    await interaction.response.send_message(f"✅ Guild ID set to: `{value.strip()}`")

                else:
                    await interaction.response.send_message(
                        "**Available subcommands:**\n"
                        "• `status` — show all settings\n"
                        "• `channel_mode <blacklist|whitelist>`\n"
                        "• `user_mode <blacklist|whitelist>`\n"
                        "• `block_channel <id>` / `unblock_channel <id>`\n"
                        "• `allow_channel <id>` / `disallow_channel <id>`\n"
                        "• `block_user <id>` / `unblock_user <id>`\n"
                        "• `allow_user <id>` / `disallow_user <id>`\n"
                        "• `permission <secure|default|full>`\n"
                        "• `auto_start <true|false>`\n"
                        "• `set_guild <guild_id>`",
                        ephemeral=True,
                    )
                    return

                if changed:
                    await self._async_save_config()

        # ── Core agent execution ─────────────────────────────────────────

        async def _cmd_ask(
            self,
            interaction: discord.Interaction,
            prompt: str,
            file: Optional[str] = None,
        ) -> None:
            if not (prompt and prompt.strip()):
                await interaction.response.send_message(
                    "⚠️ Please provide a prompt. Usage: `/ask <prompt>`", ephemeral=True,
                )
                return
            if len(prompt) > 32000:
                await interaction.response.send_message(
                    "⚠️ Prompt too long (max ~32,000 characters).", ephemeral=True,
                )
                return
            # Security gate: prompt injection detection
            from harness.core.security import detect_injection
            severity, matches = detect_injection(prompt)
            if severity == "block":
                await interaction.response.send_message(
                    "⛔ Prompt blocked — potential injection detected. "
                    f"Patterns: {', '.join(matches[:2])}. "
                    "Rephrase without instructions like 'ignore previous' or 'you are now'.",
                    ephemeral=True,
                )
                return
            if not self._channel_allowed(interaction.channel_id):
                await interaction.response.send_message(
                    "⛔ This channel is not allowed for Harness. Set `discord_channel_ids` in your config "
                    "(or empty it) to control access.",
                    ephemeral=True,
                )
                return
            if not self._user_allowed(interaction.user.id):
                await interaction.response.send_message(
                    "⛔ You are not allowed to use Harness in this server.",
                    ephemeral=True,
                )
                return

            rt = self._get_runtime(interaction.channel_id)

            # Defer the interaction immediately (3-second timeout).
            # After this, we use followup.send for all messages.
            log.info("/ask received: channel=%s prompt=%s", interaction.channel_id, prompt[:80])
            try:
                await interaction.response.defer(thinking=True)
            except discord.HTTPException as exc:
                log.error("Failed to defer interaction: %s", exc)
                return

            # Acquire the channel lock.  This serializes agent access so two
            # concurrent /ask commands on the same channel never corrupt state.
            # The lock is a threading.Lock (not asyncio) because agent.step()
            # runs on a thread pool.
            try:
                acquired = await asyncio.to_thread(rt.lock.acquire, timeout=300)
            except Exception as exc:
                log.error("Failed to acquire channel lock: %s", exc)
                return
            if not acquired:
                await interaction.followup.send(
                    "⏳ Another request is still running on this channel. Please wait.",
                    ephemeral=True,
                )
                return

            try:
                full_prompt = await self._build_prompt(interaction, prompt, file)
                log.info("Prompt built, starting agent step...")
                event_queue: queue.Queue = queue.Queue()
                state = DiscordRenderState(max_message_len=self.max_message_len)
                loop = asyncio.get_running_loop()
                agent = rt.ensure_agent()
                log.info("Agent ready: provider=%s model=%s", agent.config.provider, agent.session.model if agent.session else "none")

                # ── Worker: runs on thread pool ──────────────────────────
                # Temporarily swaps the agent's event callback and permission
                # approver.  Restores them in a finally block so the agent
                # is never left in a corrupted state.
                def _worker() -> None:
                    old_cb = agent.event_callback
                    old_approver = agent.permission_manager.approver_callback
                    agent.event_callback = lambda ev: event_queue.put(ev)

                    perm = (self.config.discord_permission or DEFAULT_PERMISSION).lower()
                    if perm in ("secure", "default"):
                        agent.permission_manager.approver_callback = self._make_approver(interaction, loop)
                    else:
                        agent.permission_manager.approver_callback = None

                    try:
                        for ev in agent.step(full_prompt):
                            event_queue.put(ev)
                    except Exception as exc:
                        log.exception("Agent step failed")
                        event_queue.put(exc)
                    finally:
                        agent.event_callback = old_cb
                        agent.permission_manager.approver_callback = old_approver
                        event_queue.put(_SENTINEL)

                # Run the worker on a thread pool so the event loop stays
                # responsive for Discord message sends and approval buttons.
                log.info("Starting agent worker thread...")
                worker_task = asyncio.get_event_loop().run_in_executor(None, _worker)

                # ── Consumer: runs on event loop ─────────────────────────
                # Reads events from the queue and sends them to Discord in
                # real time.
                sent_any = False
                relay = get_relay()
                relay.relay_from_discord(prompt, interaction.channel_id)
                event_count = 0

                while True:
                    try:
                        item = await asyncio.to_thread(event_queue.get, timeout=0.5)
                    except queue.Empty:
                        continue
                    if item is _SENTINEL:
                        break
                    if isinstance(item, Exception):
                        await self._send_fallback(interaction, f"❌ **Execution error**: {item}")
                        sent_any = True
                        break
                    for out in state.add_event(item):
                        await self._send_out(interaction, out)
                        sent_any = True
                    event_count += 1

                # Wait for worker thread to finish (callback restoration).
                await worker_task
                log.info("Agent worker finished after %d events", event_count)

                # Flush any remaining buffered output.
                for out in state.finish():
                    await self._send_out(interaction, out)
                    sent_any = True

                if not sent_any:
                    await self._send_fallback(interaction, "✅ Done (no output).")

            except Exception as exc:
                log.exception("Unhandled error in /ask handler")
                try:
                    await interaction.followup.send(f"❌ **Internal error**: {exc}", ephemeral=True)
                except discord.HTTPException:
                    pass
            finally:
                rt.lock.release()

        # ── Permission approval ──────────────────────────────────────────

        def _make_approver(self, interaction: discord.Interaction, loop: asyncio.AbstractEventLoop):
            """Build an approver callback for the agent's PermissionManager.

            The callback blocks the worker thread (via ``future.result()``)
            until the user clicks Approve / Deny in Discord.  The approval
            UI is scheduled onto the discord.py event loop via
            ``run_coroutine_threadsafe``.
            """

            def _approver(message: str, details: dict) -> bool:
                future: concurrent.futures.Future = concurrent.futures.Future()

                async def _ask() -> None:
                    view = _ApprovalView(future, message)
                    try:
                        # interaction.followup is available because we deferred
                        await interaction.followup.send(
                            f"🔐 **Permission required**\n{message}",
                            view=view,
                            ephemeral=True,
                        )
                    except discord.HTTPException as exc:
                        log.warning("Could not send permission request: %s", exc)
                        if not future.done():
                            future.set_result(False)

                asyncio.run_coroutine_threadsafe(_ask(), loop)
                try:
                    return future.result(timeout=900)
                except concurrent.futures.TimeoutError:
                    log.warning("Permission request timed out after 900s")
                    return False

            return _approver

        # ── Prompt construction ──────────────────────────────────────────

        async def _build_prompt(
            self,
            interaction: discord.Interaction,
            prompt: str,
            file: Optional[str] = None,
        ) -> str:
            parts = [prompt.strip()]
            if file and file.strip():
                parts.append(f"@{file.strip().lstrip('@')}")
            for name, url in self._attachments(interaction):
                path = await self._download_attachment(name, url)
                if path:
                    parts.append(f"@{path}")
            return "\n\n".join(parts)

        @staticmethod
        def _attachments(interaction: discord.Interaction) -> List[Tuple[str, str]]:
            data = interaction.data or {}
            resolved = data.get("resolved", {}) or {}
            attachments = resolved.get("attachments", {}) or {}
            out: List[Tuple[str, str]] = []
            for att_id, att in attachments.items():
                url = (att or {}).get("url")
                filename = (att or {}).get("filename") or f"attachment_{att_id}"
                if url:
                    out.append((filename, url))
            return out

        async def _download_attachment(self, filename: str, url: str) -> Optional[str]:
            try:
                import httpx
            except ImportError:  # pragma: no cover - httpx is a core dependency
                return None
            dl_dir = Path(self.workspace) / ".harness" / "discord_attachments"
            try:
                dl_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("Could not create attachment directory: %s", exc)
                return None
            safe = re.sub(r"[^\w.\- ]", "_", filename).strip() or "attachment"
            dest = dl_dir / safe
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                return str(dest)
            except Exception as exc:
                log.warning("Attachment download failed: %s", exc)
                return None

        # ── Discord message helpers ──────────────────────────────────────

        async def _send_out(self, interaction: discord.Interaction, out: DiscordOutgoing) -> None:
            if out.kind == "thinking":
                chunks = chunk_quote(out.text, self.max_message_len)
            else:
                chunks = chunk_message(out.text, self.max_message_len)
            for chunk in chunks:
                await self._send_fallback(interaction, chunk)

        async def _send_chunked(self, interaction: discord.Interaction, text: str) -> None:
            if interaction.response.is_done():
                for chunk in chunk_message(text, self.max_message_len):
                    await self._send_fallback(interaction, chunk)
            else:
                for i, chunk in enumerate(chunk_message(text, self.max_message_len)):
                    if i == 0:
                        await interaction.response.send_message(chunk)
                    else:
                        await self._send_fallback(interaction, chunk)

        async def _send_fallback(self, interaction: discord.Interaction, content: str) -> None:
            """Send a message, retrying with halved content if it exceeds Discord's limit."""
            for attempt in range(3):
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(content, ephemeral=False)
                    else:
                        await interaction.response.send_message(content, ephemeral=False)
                    # Relay to CLI
                    try:
                        get_relay().relay_from_cli(content)
                    except Exception:
                        pass
                    return
                except discord.HTTPException as exc:
                    if "Must be 2000 or fewer" in str(exc) and attempt < 2:
                        safe = max(200, len(content) // 2)
                        content = content[:safe]
                        continue
                    log.error("Failed to send Discord message: %s", exc)
                    return
                except Exception as exc:
                    log.error("Failed to send Discord message: %s", exc)
                    return

        # ── Helpers ─────────────────────────────────────────────────────

        @staticmethod
        def _parse_id_list(raw: str) -> set:
            return {c.strip() for c in raw.split(",") if c.strip()}

        def _channel_allowed(self, channel_id: int) -> bool:
            legacy = self._parse_id_list(self.config.discord_channel_ids)
            new_blocked = self._parse_id_list(self.config.discord_blacklisted_channels)
            new_allowed = self._parse_id_list(self.config.discord_whitelisted_channels)
            ch_id = str(channel_id)

            # If no new fields set but legacy is set, use legacy as whitelist
            if not new_blocked and not new_allowed and legacy:
                return ch_id in legacy

            mode = (self.config.discord_channel_mode or "blacklist").lower()
            if mode == "whitelist":
                return ch_id in new_allowed if new_allowed else True
            else:  # blacklist
                return ch_id not in new_blocked

        def _user_allowed(self, user_id: int) -> bool:
            mode = (self.config.discord_user_mode or "blacklist").lower()
            u_id = str(user_id)
            if mode == "whitelist":
                allowed = self._parse_id_list(self.config.discord_whitelisted_users)
                return u_id in allowed if allowed else True
            else:  # blacklist
                blocked = self._parse_id_list(self.config.discord_blacklisted_users)
                return u_id not in blocked

        def _get_runtime(self, channel_id: int) -> _ChannelRuntime:
            rt = self._runtimes.get(channel_id)
            if rt is None:
                rt = _ChannelRuntime(self.config)
                self._runtimes[channel_id] = rt
            return rt

        async def _async_save_config(self) -> None:
            """Save config without blocking the event loop."""
            from harness.config import save_config
            await asyncio.to_thread(save_config, self.config)

        def run(self) -> None:
            _require_discord()
            if not self.token:
                print(
                    "❌ No Discord bot token configured.\n"
                    "  Run:  harness keys set discord <your-bot-token>\n"
                    "  or set the DISCORD_BOT_TOKEN environment variable.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if self.quiet:
                # Suppress all harness.discord logs in background mode to
                # avoid polluting the TUI.  Errors are still sent to Discord.
                log.addHandler(logging.NullHandler())
            else:
                handler = logging.StreamHandler(sys.stderr)
                handler.setFormatter(logging.Formatter("[discord] %(levelname)s %(name)s: %(message)s"))
                log.addHandler(handler)
                log.setLevel(logging.INFO)
            try:
                self.bot.run(self.token)
            except KeyboardInterrupt:
                pass

else:

    class HarnessDiscordBot:  # type: ignore[no-redef]
        """Placeholder that raises a helpful error when discord.py is missing."""

        def __init__(self, *args, **kwargs):
            _require_discord()


def run_discord_bot(
    config: HarnessConfig,
    token: Optional[str] = None,
    workspace: Optional[str] = None,
) -> int:
    """Entry point for ``harness discord``. Returns a process exit code."""
    try:
        _require_discord()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not token:
        token = config.get_discord_token()
    if not token:
        print(
            "❌ No Discord bot token configured.\n"
            "  Run:  harness keys set discord <your-bot-token>\n"
            "  or set the DISCORD_BOT_TOKEN environment variable.",
            file=sys.stderr,
        )
        return 1
    bot = HarnessDiscordBot(config, token=token, workspace=workspace)
    bot.run()
    return 0
