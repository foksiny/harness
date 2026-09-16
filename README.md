# Harness ⚡

> **The Premier Agentic AI Engineering Harness & CLI**
> Built for developers, autonomous AI workflows, and software engineers who demand speed, low memory (~24MB RAM), subagent orchestration, and multi-provider intelligence.

```
██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗
██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝
███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗
██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║
██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║
╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝
```

---

## ✨ Key Features

- 🏎️ **Ultra-Fast & Low Memory**: Benchmarked **~60ms cold start**, **~24MB RSS** — no heavy framework bloat.
- 🧠 **Dynamic Thinking Effort & Context Window Detection**:
  - Resolves context window limits and reasoning parameters for **any current or future model** via 5-level priority: explicit registry (200+ models) → provider `/models` endpoint → model name tokens (`-1m`, `-128k`) → model family heuristics → safe default (128k). Registry covers GPT-5, Claude 4.5, Gemini 2.5, Grok 4, DeepSeek V4, and Command-A generations.
  - Provider-aware **thinking dialects**: Claude budget tokens, Gemini thinking budget (incl. `includeThoughts`), OpenAI/xAI/Mistral/Groq/Perplexity/DeepSeek reasoning effort, OpenRouter `reasoning` object, Together hybrid reasoning toggle, NVIDIA NIM DeepSeek-V4 `chat_template_kwargs`, and Cohere `thinking.token_budget`.
- 🌐 **16+ First-Class Providers**:
  - Anthropic, OpenAI, Google Gemini, OpenRouter, NVIDIA NIM, OpenCode Zen, Groq, DeepSeek, Mistral AI, xAI (Grok), Ollama (Local), Together AI, Fireworks AI, Cohere, Perplexity Sonar, and an Offline Mock Engine.
- 🖼️ **Visual & Multimodal Models**:
  - Send image or video files to vision-capable models by typing the path, **swiping/dropping** the file into the terminal, or **pasting** the file path — auto-detected and converted into the model's native format (OpenAI `image_url`, Gemini `inline_data`, Anthropic `image`/`video` blocks, NVIDIA NIM `input_video`, and `data:` URIs).
  - Model-aware gating: images attach only when the model supports vision, videos only when the provider accepts native video (Gemini, Anthropic, NVIDIA NIM); otherwise the path degrades into a text reference with a clear warning.
  - `/models` shows a **Vision** column per model. Capabilities come from the provider's own `/models` metadata (OpenRouter/OpenAI/NIM/Groq `input_modalities` / `vision` flags) when advertised, with a name-heuristic fallback (`4o`, `gemini`, `claude`, `pixtral`, `grok-4`, `llama-4`, `qwen-vl`, `llava`, …) so unknown/future models still get a best-effort guess.
  - **Vision fallback (VFB)**: set `vfb_provider` (and optionally `vfb_model`) via `/config`; when a file is sent to a non-vision model, it is routed to the fallback vision model which writes a precise description that is embedded as text so the text-only model still understands the image — the user is told the fallback model was used (`🕶️ Vision fallback` / `✔ …description embedded`). The VFB description is **guided by your prompt**: your question is injected into both the system prompt and the user message, so the description focuses on what you actually asked about while remaining comprehensive.
  - Handles spaced filenames, `file://` URIs, trailing punctuation, dedupes repeats, and enforces a 20MB inline cap.
- 📎 **File & Folder Mentions (`@`)**:
  - Reference any local file or directory in a prompt with `@path/to/file` or `@/absolute/path`. The mention is expanded inline before the model sees it:
    - **Files**: summary includes line count, character count, and the first 10 lines (rest truncated). Binary files show size and a short byte preview. The model learns from the snippet rather than ingesting the whole file.
    - **Folders**: a limited directory tree is rendered up to 2 levels deep (80 items per directory). You get a quick structural overview without flooding context.
  - Mentions resolve relative to the current working directory or absolute paths. Unresolvable mentions emit a warning but leave the original `@…` text intact.
  - **TUI autocompletion**: type `@` and press Tab to complete files and folders just like slash commands. The completer walks the filesystem relative to cwd and shows directories with a trailing `/`.
  - **UX feedback**: when a prompt is processed, Harness emits `mention` events for each successfully resolved reference and `mention_warning` for failures, so you see exactly what was expanded and what could not be found.
- 🛡️ **Three Permission Profiles**:
  - `Secure`: Full interlock — every modification, Python execution, or shell command prompts the user with diffs.
  - `Default`: Balanced — safe read/write operations auto-approved; destructive commands require approval.
  - `Full Access`: Unrestricted autonomous operation.
- 🔐 **Secure API Key Storage**:
   - OS keychain integration via `keyring` (included by default): stores keys in macOS Keychain, GNOME Keyring, or Windows Credential Manager.
   - Falls back to `~/.harness/api_keys.json` with `0600` permissions (owner read/write only) when OS keychain is unavailable.
   - Automatic one-time migration from legacy plaintext `config.json`.
   - Environment variables remain the first-priority source (ideal for CI/CD).
- 🌐 **Browser Automation (CDP)**:
  - Full browser control via Chrome DevTools Protocol — works with any Chromium-based browser (Chrome, Edge, Brave, Zen, Opera, Vivaldi) and Firefox.
  - Visual overlay banner injected into every page shows the user exactly what the agent is doing (navigating, clicking, typing, etc.) with icons and color-coded status.
  - 11 tools: `browser_launch`, `browser_navigate`, `browser_click`, `browser_type`, `browser_press_key`, `browser_scroll`, `browser_screenshot`, `browser_evaluate`, `browser_get_page_info`, `browser_tab`, `browser_navigation`.
  - Auto-detects installed browser, supports headless mode, tab management, full-page screenshots, and JavaScript evaluation.
  - PLAN mode blocks all browser mutations; `browser_screenshot` and `browser_get_page_info` remain read-only and allowed everywhere.
- 🎯 **Three Operational Modes**:
  - `Plan`: Purely investigatory & architectural mode. Prevents filesystem mutations and destructive commands.
  - `Build`: Full developer mode. Atomic code edits, file creation, command execution, and test runs.
  - `Super`: Autonomous multi-turn goal execution loop with self-verification and automatic error correction.
- 🤖 **Subagent Orchestration**:
  - Dispatch specialized worker subagents (`researcher`, `planner`, `coder`, `tester`, `reviewer`) with isolated context windows to keep the parent context pristine.
  - **Parallel by default**: every `spawn_subagent` runs on a background daemon thread, so the main agent keeps working while the subagent processes. Pass `background: false` to block and wait for the report.
  - **No turn caps**: subagents run until they call `finish` (with a 200-turn safety limit); no arbitrary per-role turn budgets.
- 🐝 **Agent Swarms (`spawn_swarm`)**:
  - Concurrent, coordinated multi-agent execution. The parent "main thread" hosts a shared message bus; subagents run in parallel threads and talk to each other through `swarm_send_message` / `swarm_read_messages`.
  - **Background mode by default**: the swarm launches and returns immediately so the parent keeps working; use `swarm_read_messages` to monitor inter-agent traffic. Pass `background: false` to block until all workers finish and return a combined report with the full mailbox transcript.
  - Enable with `harness config set swarm_enabled true` (always active in Super Mode; read-only research swarms allowed in Plan Mode).

**👀 Watching subagents in real time:**

While any subagent or swarm is running, every worker's activity is streamed live into the terminal (each line is prefixed with its agent id): 🐝 spawn, ▸ spoken output, 🔧 tool calls (with first-line results), 📨 swarm messages, and ⚡ completion with status + turn count.

| Action | What it shows |
|---|---|
| Just watch | Live inline feed of what each subagent does as it happens |
| **F2** (or **Ctrl+G**) | Open the **Agent Swarm Board** — overview table of every spawned agent (id, type, status, turns, last tool, task) |
| `/agents` | Same as F2: open the board |
| `/agent <id>` | Full drill-down panel for one agent: task, swarm messages, spoken output, every tool call + result, and its final report (including the `[Actions performed by this agent]` action log) |
| **ESC** (or `/back`) | Return to the parent conversation view |

When a delegation finishes, the report you receive already includes each agent's `[Actions performed by this agent]` log, so you see exactly what was done even if its final reply was terse.
- ❓ **Model-Driven User Questions (`ask_user`)**:
  - When encountering architectural decisions or ambiguities, the model proactively prompts the user with formatted choices, recommended options, or write-ins.
- 🐍 **Sandboxed Python Execution (`execute_python`)**:
  - Code runs in a **real sandbox** — layered backends picked automatically by what's available on the host:
    1. **nsjail** (strongest): chroot + rlimits + namespace isolation.
    2. **Docker**: throwaway container with `--read-only` rootfs, tmpfs scratch space, no network by default, memory/pids limits.
    3. **Restricted subprocess** (always available): enforced `RLIMIT_AS`/`RLIMIT_CPU`/`RLIMIT_NPROC`/`RLIMIT_FSIZE`/`RLIMIT_CORE` limits plus a scrubbed environment.
  - Filesystem isolation, CPU/memory/process/file-size limits, and optional network isolation (`network: true` to opt in).
  - AST analysis still runs (os.system, subprocess, ctypes, eval/exec, …) but it's informational metadata on top of the sandbox — the sandbox is the real security boundary, so aggressive tests and hostile snippets are contained.
  - Permission gating still applies under DEFAULT/SECURE modes.
- 🔎 **Free Exa Web Search (`exa_search`)**:
  - Built-in real-time web search with zero API key required.
- 📋 **Integrated To-Do Tracking (`todo_create`, `todo_update`, `todo_list`)**:
  - Real-time task planning and HUD progress reporting (`3/5 completed`).
  - **Persistent across sessions**: task state is saved into the session file on every save and restored on resume, so long-running projects survive restarts.
- 🧩 **Extensible Skills & 10 Built-in Skills**:
  - Discovers skills from `~/.harness/skills/` and `.harness/skills/` on top of the 10 built-ins.
  - Includes specialized **`skill_creator`** (generates and installs new skills on user request) and **`mcp_integrator`** (connects and configures MCP servers on user request).
- 🧠 **Continuous Learning & Self-Improvement**:
  - The agent transparently learns across sessions: `learn_record` / `learn_recall` / `learn_promote` let it (and you, via `/learn`) persist reusable lessons, inject top matches into every system prompt, and promote matured lessons into real skills.
  - **Global skill promotion**: When the agent finds a lesson broadly useful across projects (coding patterns, debugging techniques, tool tricks), it promotes it as a **global skill** (`~/.harness/skills/`) so it's available everywhere. Workspace-specific lessons stay scoped to `.harness/skills/`.
- 🔌 **Model Context Protocol (MCP) Client**:
  - Supports `stdio` and `sse` JSON-RPC 2.0 servers configured in `mcp.json`.
- 🎨 **14 Handcrafted Visual Themes**:
  - `cyberpunk` (default neon), `dracula`, `nord`, `monokai`, `catppuccin`, `matrix`, `minimal`, `amber_crt`, `gruvbox`, `one_dark`, `rose_pine`, `solarized_dark`, `synthwave`, `tokyo_night`.
- 🧠 **Proactive Context-Budget Management**:
  - Beyond reactive compaction, Harness now guards the window **proactively** so it is much harder for the agent to fill its own context log:
    - **Per-tool output caps** clamp runaway results at the source (`view_file` ~8k chars, `run_command` ~16k, `grep_search` ~8k, `execute_python` ~16k, …) with a transparent truncation marker. The agent can still request the full payload by passing an explicit `max_chars`.
    - **Pre-send pressure check** runs before every provider call. When usage approaches the threshold it compacts *before* the model is asked to respond (and emergency-trims at the hard cap) — not only at turn boundaries.
    - **Reasoning-stripping**: verbose `reasoning_content` from old turns being summarized is stripped, recovering large amounts of tokens; the recent working set keeps its reasoning verbatim.
    - **Context-aware system prompt**: verbose MCP tool summaries are elided when the window is tight, and `git status` lookups are cached (2s TTL) so prompt assembly stays fast.
- 📦 **Smart Auto-Compaction**:
  - Budget-driven, graduated context compaction. When usage crosses the warning threshold (default 75%), the sampler dials back pressure in three sweeps: oversized verbatim tool payloads are collapsed to head/tail digests, the oldest turn-groups are condensed into structured memory checkpoints down to the target budget (default 60%), and a single global checkpoint is emitted if the window is still hot. Recently-used turns stay verbatim, and undo/redo (`/checkpoint`) can restore the pre-compaction history.
  - Uses the LLM itself to summarize when a provider is live (`compact_summary`), falling back to a heuristic extractor otherwise. A peak-hold hysteresis guard prevents re-firing every turn, and the proactive pre-send gauge plus a mid-turn emergency trim collapse old blobs (never current-turn text) if usage races past the hard cap (~95%). Inspect everything with `/compact` and `/tokens`.

---

## 🚀 Quickstart

### Installation & Launch
```bash
# Clone and enter directory
cd harness

# Launch interactive TUI directly:
./bin/harness

# Or install in editable mode (all features included — no extras needed):
pip install -e .
harness
```

All dependencies install by default — keychain storage (keyring) and the full TUI (prompt-toolkit, httpx, pydantic) are core requirements, not optional extras.

### Command Line Examples
```bash
# Single-shot task
harness "Analyze the repository structure"

# Plan mode (read-only architectural analysis)
harness --mode plan "Design an event-driven architecture"

# Super Mode (autonomous goal loop)
harness --super "Refactor error handling and add unit tests"

# Specify provider, model, and theme
harness --provider openrouter --model anthropic/claude-3.7-sonnet --theme dracula

# Pipe input directly
cat logs/error.log | harness "Diagnose this stack trace"
```

---

## ⌨️ Interactive Slash Commands

| Command | Description |
|---|---|
| `/help` | Display command reference and guide |
| `/goal <objective>` | Launch Super Mode autonomous loop toward an explicit goal |
| `/mode [plan\|build\|super]` | Switch operational mode |
| `/perm [secure\|default\|full]` | Switch permission security profile |
| `/provider <name>` | Switch active LLM provider (16+ supported) |
| `/model <name>` | Change model name for active provider |
| `/models [provider]` | Browse model catalog for current or specific provider |
| `/config [list\|get\|set]` | View, get, or set configuration settings |
| `/keys [list\|set\|remove]` | Manage, mask, and test provider API keys |
| `/setup` | Launch interactive onboarding setup wizard |
| `/effort <level>` | Set thinking effort (`off`, `low`, `medium`, `high`, or tokens) |
| `/theme <name>` | Change visual theme (`cyberpunk`, `dracula`, `nord`, etc.) |
| `/todo [list\|add\|clear]` | Manage active task items |
| `/skills [reload]` | List or reload registered skills |
| `/learn [list\|record\|forget\|promote\|on\|off]` | Manage persistent learned memories; promote proven lessons into skills |
| `/mcp [list\|add]` | Manage Model Context Protocol (MCP) servers |
| `/subagent <type> <prompt>` | Dispatch an isolated subagent worker |
| `/compact` | Trigger manual context compaction; shows tactics used (payload truncations, groups summarized, checkpoint) plus ledger history |
| `/session [list\|create\|delete\|rename\|fork\|resume]` | Full session lifecycle management |
| `/checkpoint [list\|create\|undo\|redo]` | Manage checkpoints for undo/redo of file changes, messages, and state |
| `/tokens` | Display token counts broken down by role (system/user/assistant/reasoning/tool), context percentage versus the target/cap budget, and RAM usage |
| `/diff` | View uncommitted git diffs |
| `/clear` | Clear terminal screen |
| `/exit` | Exit Harness |

---

## 🛠️ Built-in Skills (10 Total)

1. **`skill_creator`**: Autonomous skill generator — writes and registers new skills on user request.
2. **`mcp_integrator`**: Autonomous MCP configurator — connects and verifies external MCP servers.
3. **`git_master`**: Advanced branching, rebase, conflicts, and conventional commits.
4. **`test_architect`**: Unit and integration test authoring and verification.
5. **`code_refactor`**: Structural refactoring, complexity reduction, and pattern optimization.
6. **`database_query`**: SQL optimization, schema design, and safe migrations.
7. **`api_designer`**: REST, OpenAPI 3.1, and GraphQL schema creation.
8. **`docker_deploy`**: Multi-stage Dockerfiles and container orchestration.
9. **`performance_profiler`**: Latency, memory leak diagnosis, and caching strategies.
10. **`documentation_writer`**: Architecture RFCs, user guides, and API references.

> Note: the catalog also auto-discovers **workspace** (`~/.harness/skills/`, `.harness/skills/`)
> and **promoted global** skills on top of these 10 built-ins — on this machine it currently
> resolves to **15 total** skills (see `/skills`).

---

## ⚙️ Configuration (`~/.harness/config.json`)

```json
{
  "provider": "anthropic",
  "model": "claude-3-7-sonnet",
  "mode": "build",
  "permission": "default",
  "thinking_effort": "high",
  "theme": "cyberpunk",
  "auto_compact": true,
  "compact_threshold": 0.75,
  "compact_target_ratio": 0.6,
  "compact_cap_ratio": 0.95,
  "compact_max_message_tokens": 0,
  "compact_preserve_turns": 4,
  "compact_summary": "auto",
  "swarm_enabled": false,
  "discord_channel_mode": "blacklist",
  "discord_user_mode": "blacklist",
  "discord_blacklisted_channels": "",
  "discord_whitelisted_channels": "",
  "discord_blacklisted_users": "",
  "discord_whitelisted_users": "",
  "discord_guild_id": "",
  "discord_workspace": "",
  "discord_permission": "full"
}
```

> The Discord bot token is never stored in `config.json` — it lives in your OS
> keychain (macOS Keychain, GNOME Keyring, Windows Credential Manager) or the
> secure store (`~/.harness/api_keys.json`, owner-only). Set it via
> `harness keys set discord <token>` or `DISCORD_BOT_TOKEN`.

Environment variables are also detected automatically (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, etc.).

---

## 🤖 Discord Bot Integration

Bind Harness into a Discord server and drive it with slash commands — prompts,
file mentions, live tool notices, and full responses, all adapted to Discord's
message model.

### Features
- **Slash commands**: `/ask <prompt>`, `/status`, `/clear`, `/tokens`, `/compact`, `/agents`, `/agent <id>`, `/help discord` for the full setup guide.
- **`/discord` admin command**: manage all Discord settings directly from Discord — channel/user access modes, blacklists, whitelists, permission level, auto-start, guild ID.
- **File mentions**: reference host files inline with `@path/to/file`, pass `file:/path` to `/ask`, or upload an attachment with the command.
- **Discord-native output**: model **thinking** is posted as `> ` quote blocks as the agent iterates; **tool usage** is announced live (`🔧 Using tool: …`); the **final response is sent in full** when the turn completes (no streaming).
- **Per-channel conversations**: every channel keeps its own agent session.
- **Approval buttons**: with `discord_permission` set to `default`/`secure`, risky actions post an **Approve / Deny** button message instead of a terminal prompt.

### Channel & User Access Control

Access is controlled by two independent modes — one for channels, one for users — each in either **blacklist** or **whitelist** mode (default: blacklist).

| Mode | Behavior |
|---|---|
| `blacklist` (default) | Listed items are **blocked**. Empty list = all allowed. |
| `whitelist` | Only listed items are **allowed**. Empty list = all blocked. |

Configure via CLI:
```bash
harness config set discord_channel_mode  blacklist   # or whitelist
harness config set discord_blacklisted_channels  111...,222...
harness config set discord_whitelisted_channels  333...
harness config set discord_user_mode  blacklist       # or whitelist
harness config set discord_blacklisted_users  444...,555...
harness config set discord_whitelisted_users  666...
```

Or via the `/discord` slash command inside Discord:
```
/discord status                   # show all settings
/discord channel_mode whitelist   # only listed channels allowed
/discord block_channel 123456789  # add channel to blacklist
/discord allow_channel 123456789  # add channel to whitelist
/discord user_mode whitelist      # only listed users allowed
/discord block_user 987654321     # add user to blacklist
/discord allow_user 987654321     # add user to whitelist
```

### Setup
```bash
# 1. Install the optional Discord extra (pulls in discord.py)
pip install "harness-cli[discord]"

# 2. Create a bot in the Discord Developer Portal and grab its token.
#    Enable the "bot" + "applications.commands" scopes and the Message Content intent.

# 3. Store the token securely (OS keychain preferred, never plain config.json)
harness keys set discord <your-bot-token>
#    or: /keys set discord <token>   or   DISCORD_BOT_TOKEN=<token>

# 4. Optional configuration
harness config set discord_channel_mode blacklist       # blacklist | whitelist
harness config set discord_blacklisted_channels 111...,222...
harness config set discord_whitelisted_channels 333...
harness config set discord_user_mode blacklist          # blacklist | whitelist
harness config set discord_blacklisted_users 444...,555...
harness config set discord_whitelisted_users 666...
harness config set discord_guild_id    333...           # instant slash sync to a guild
harness config set discord_workspace   /path/to/workdir
harness config set discord_permission  full             # full | default | secure

# 5. Launch the bot
harness discord
```

### `/help discord` inside the bot
The bot's `/help discord` command prints this entire walkthrough plus usage
details, so you can configure everything without leaving Discord.

---

## 🧪 Testing

Harness ships with a comprehensive automated test suite (**331 tests across 23 files**):
```bash
python3 -m unittest discover -s tests -v      # primary runner (recommended)
# or, equivalently:
python3 -m pytest tests/ -v
```

The suite verifies tools, tool output caps / proactive context budgeting, sandboxed execution, model context window detection, provider payloads, graduated compaction + reasoning stripping, subagent/swarm parallelism, checkpoints, skills, learning, slash commands, and the Discord integration.

### Benchmarks

Run `python3 benchmarks/bench_startup.py` to reproduce. Recent results:
- Cold start: **~60ms**
- RSS (after imports): **~24.1 MB**
- Tool registration: **~0.7ms** (26 tools)
- Skill loading: **~0.5ms** (15 skills)
- Model detection: **~0.5ms** (8 models)

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing conventions, and architecture guidelines. Issues and PRs welcome.

---

## 🔒 Security Notes

- **`execute_python` runs in a real sandbox.** Backends in priority order: nsjail → Docker → restricted subprocess with `rlimit` resource confinement (memory, CPU, process count, file size; no core dumps; scrubbed environment). Network is disabled by default (opt in with `network: true`). AST analysis still flags dangerous patterns for transparency, but the sandbox is the real security boundary. Docker/nsjail give the strongest isolation; the restricted-subprocess fallback limits but does not namespace-isolate the filesystem — prefer running with Docker available for untrusted workloads.
- **API keys use secure storage.** Keys are stored in your OS keychain (`keyring`: macOS Keychain, GNOME Keyring, Windows Credential Manager) when available, with a file-based fallback (`~/.harness/api_keys.json`, `0600` permissions, owner-only). Environment variables remain the recommended approach for CI/CD.
- **Full Access mode is unrestricted.** All tools execute without approval. Only use in controlled environments with trusted code.
- **Recommended for untrusted scenarios**: `--mode plan --perm secure`, disposable API keys, and audit commands before approving.
