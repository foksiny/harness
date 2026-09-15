"""
Help text for the Harness Discord bot.
"""

DISCORD_HELP_TEXT = """# 🤖 Harness Discord Bot — Setup & Usage

## 1. Install the extra
```
pip install "harness-cli[discord]"
```
This pulls in `discord.py`. (Without it, `harness discord` prints setup steps.)

## 2. Create a Discord bot
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. **New Application** → **Bot** → **Reset Token** and copy it.
3. Under **OAuth2 → URL Generator**, scope: `bot` + `applications.commands`.
   Permissions: **Send Messages**, **Read Message History**, **Attach Files**,
   **Embed Links** (and **Use Slash Commands** / **Use Application Commands** if offered).
4. Invite the bot to your server with that URL.

## 3. Store the token securely
```
harness keys set discord <your-bot-token>
```
or from inside the TUI: `/keys set discord <token>`, or `harness config set discord_bot_token <token>`.
The token is saved in the secure store (keyring / `~/.harness/api_keys.json`, owner-only),
never in plain `config.json`. The `DISCORD_BOT_TOKEN` environment variable also works.

## 4. Optional configuration
```
harness config set discord_channel_ids 111...,222...   # restrict bot to these channel IDs (empty = all)
harness config set discord_guild_id    333...           # guild to sync slash commands instantly (default: global)
harness config set discord_workspace   /path/to/workdir # directory the bot operates in (default: launch dir)
harness config set discord_permission  full             # full | default | secure (bot approval profile)
```
- **`discord_permission`** defaults to `full` so the bot can edit files and run
  commands without a human at a keyboard. With `default`/`secure`, write/risky
  actions post an **Approve / Deny** button message for you to click.
- Files are referenced from the **bot's workspace** — the machine it runs on.
  It does not see other people's computers.

## 5. Launch the bot
```
harness discord
```
A banner prints when the bot is online. Slash commands sync within a few seconds
in the configured guild, or up to an hour globally.

## 6. Usage
- `/ask <prompt>` — run the agent. Optionally pass `file:/path/on/host` to mention a file.
- `/ask <prompt>` with an **attachment** uploaded alongside — the file is saved and mentioned.
- Mention files inline with `@path/to/file` (or `@/abs/path`) in your prompt, exactly like the TUI.
- `/help discord` — this guide.
- `/status` — show provider, model, mode, workspace and channel restrictions.

### How output is rendered
- **Thinking** appears as `> ` quote blocks as the model iterates.
- **Tools** are announced live: `🔧 Using tool: name` with a short result.
- The **final response is not streamed** — the full answer is posted in one message
  (or several, if longer than 2000 characters) when the turn completes.
"""

GENERAL_HELP_TEXT = """# ⚡ Harness Discord Bot

I bind the **Harness** agent into Discord. Everything runs on the host machine
the bot is launched from, using your configured provider/model.

## Slash commands
- **`/ask <prompt>`** — run the agent on a prompt.
  - Add `file:/path/on/host` (e.g. `/ask file:src/main.py explain this file`)
  - Upload an attachment alongside `/ask` to mention a file.
  - Reference files inline with `@path/to/file` or `@/abs/path`.
- **`/mode <plan|build|super>`** — switch operational mode.
- **`/provider [name]`** — show or switch the active LLM provider.
- **`/model [name]`** — show or change the current model.
- **`/session <list|create|resume>`** — manage conversation sessions.
- **`/todo <list|add|clear>`** — manage the task list.
- **`/skills [list|reload]`** — view or reload available skills.
- **`/reload`** — reload config, skills, and permissions from disk.
- **`/clear`** — bulk-delete bot + user messages in this channel.
- **`/tokens`** — show token metrics and RAM usage.
- **`/compact`** — trigger manual context compaction.
- **`/agents`** — show active subagents / swarms.
- **`/discord <subcommand>`** — manage bot settings (channel/user access, permissions, auto-start).
- **`/status`** — show provider, model, mode, workspace, channel policy.
- **`/help discord`** — full setup + configuration guide.

## Output style
- 💭 thinking → `> ` quote blocks
- 🔧 tool usage → live notices
- ✅ final answer → sent in full at the end (no streaming)

Type `/help discord` for the complete configuration walkthrough.
"""