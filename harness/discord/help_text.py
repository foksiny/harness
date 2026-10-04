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
harness config set discord_auto_start  true             # auto-host the bot inside the interactive TUI
harness config set temperature         0.3              # sampling temperature (0.0 focused - 1.0 creative)
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
With `discord_auto_start true`, the bot also runs **inside the interactive TUI**
and the two sides stay fully synchronized.

## 6. Usage
- `/ask <prompt>` — run the agent. Optionally pass `file:/path/on/host` to mention a file.
- `/ask <prompt>` with an **attachment** uploaded alongside — the file is saved and mentioned.
- Mention files inline with `@path/to/file` (or `@/abs/path`) in your prompt, exactly like the TUI.
- `/goal <objective>` — start an autonomous **Super Mode** loop toward a high-level goal.
- `/temperature [0.0-1.0]` — set or view the model sampling temperature (synced).
  `0.0` = most focused/deterministic, `1.0` = most creative; `default` resets it.
  Models with fixed sampling (`o3-mini`, `gpt-5`, `deepseek-reasoner`, …) reject
  custom values — tune those with `/effort` instead.
- `/stop` — interrupt the running turn (any channel, and the CLI side too).
- `/help discord` — this guide.
- `/status` — show provider, model, mode, workspace and channel restrictions.

### How output is rendered
- **Thinking** appears as `> ` quote blocks as the model iterates.
- **Tools** are announced live: `🔧 Using tool: name` with a short result.
- The **final response is not streamed** to Discord — the full answer is posted in
  one message (or several, if longer than 2000 characters) when the turn
  completes. Answers produced *in the CLI* do stream: one `💻` message that grows
  as the turn progresses.

### When the agent asks you a question
When the model calls the `ask_user` tool (clarification, design decisions, or
confirmation), the question is posted **directly in the channel**:
- Each option is a **clickable button**; the recommended one is green.
- If custom answers are allowed, a **✏️ Custom answer** button opens a modal.
- The agent waits (up to 15 minutes) until someone answers.

## 7. CLI ↔ Discord synchronization
The TUI and the bot mirror each other **live** — no refresh, `/clear`, or restart:
- **Prompts and commands** typed on one side are shown on the other (`⌨️ CLI: …` / Discord activity lines).
- **Agent answers stream both ways** as a single message that grows token by token
  (`💻` on Discord), instead of one message per chunk.
- Incoming Discord activity (prompts, streamed answers, state changes, `/stop`)
  appears in the CLI's bottom toolbar the moment it arrives, even while the input
  prompt is idle — no screen clear needed.
- **State commands** — `/mode`, `/perm`, `/provider`, `/model`, `/effort`,
  `/temperature`, `/session`, `/goal` — apply on **both sides** at once.
- **`/stop`** interrupts the running turn wherever it is running (CLI or Discord).
- Cross-process sync uses `~/.harness/sync_bus.jsonl` (configurable via
  `HARNESS_SYNC_BUS`), so a standalone `harness discord` process and an
  interactive TUI stay in sync automatically. Each side tracks its own read
  position, so running both at once (or in-process via `discord_auto_start`)
  never double-applies an event.
- `/discord sync` forces a full state snapshot to the other side.
"""

GENERAL_HELP_TEXT = """# ⚡ Harness Discord Bot

I bind the **Harness** agent into Discord. Everything runs on the host machine
the bot is launched from, using your configured provider/model.

## Slash commands
- **`/ask <prompt>`** — run the agent on a prompt.
  - Add `file:/path/on/host` (e.g. `/ask file:src/main.py explain this file`)
  - Upload an attachment alongside `/ask` to mention a file.
  - Reference files inline with `@path/to/file` or `@/abs/path`.
- **`/goal <objective>`** — start an autonomous **Super Mode** loop toward a goal.
- **`/ultragoal <objective>`** — autonomous full app/game build with verification (alias: `/ultra-goal`).
- **`/stop`** — interrupt the currently running turn (any channel + CLI side).
- **`/info`** — inspect workspace, session, model, and context info (alias: `/sidebar`).
- **`/mode <plan|build|super>`** — switch operational mode (synced with the CLI).
- **`/perm <secure|default|full>`** — switch or view permission profile (synced with the CLI).
- **`/effort [level]`** — set or view thinking / reasoning effort level (synced).
- **`/temperature [0.0-1.0]`** — set or view the model sampling temperature (synced).
  `0.0` = most focused/deterministic, `1.0` = most creative; `default` resets it.
- **`/provider [name]`** — show or switch the active LLM provider (synced).
- **`/model [name]`** — show or change the current model (synced).
- **`/diff`** — show uncommitted git changes in the workspace.
- **`/checkpoint <list|create [label]|undo [id]|redo>`** — manage checkpoints.
- **`/learn <list|record <summary>|forget <id>|on|off>`** — manage agent memory lessons.
- **`/mcp`** — inspect configured MCP servers and tools.
- **`/config <list|get <key>|set <key> <val>>`** — view or update configuration.
- **`/session <list|all|create [title]|resume <id>|fork [title]|delete <id>|rename <id> <title>>`** — manage conversation sessions (synced; each session is associated to the workspace folder it was created in).
- **`/todo <list|add|clear>`** — manage the task list.
- **`/skills [list|reload]`** — view or reload available skills.
- **`/reload`** — reload config, skills, and permissions from disk.
- **`/clear`** — bulk-delete bot + user messages in this channel.
- **`/tokens`** — show token metrics and RAM usage.
- **`/compact`** — trigger manual context compaction.
- **`/discord <subcommand>`** — manage bot settings (channel/user access, permissions, auto-start).
- **`/status`** — show provider, model, mode, workspace, channel policy.
- **`/help discord`** — full setup + configuration guide.

## Output style
- 💭 thinking → `> ` quote blocks
- 🔧 tool usage → live notices
- ✅ final answer → sent in full at the end (no streaming)
- 🤖 **agent questions** → posted as clickable buttons (+ custom-answer modal)

## CLI ↔ Discord sync
Both sides share state: prompts, commands, mode/provider/model/session changes,
and `/stop` propagate instantly — whether the bot runs inside the TUI
(`discord_auto_start`) or standalone (`harness discord`).

Type `/help discord` for the complete configuration walkthrough.
"""
