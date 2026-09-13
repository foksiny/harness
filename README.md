# Harness ⚡

> **The Premier Agentic AI Engineering Harness & CLI**
> Built for developers, autonomous AI workflows, and software engineers who demand speed, low memory (<25MB RAM), subagent orchestration, and multi-provider intelligence.

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

- 🏎️ **Ultra-Fast & Low Memory**: Starts in <80ms, runs on **~22MB RAM**, zero heavy framework bloat.
- 🧠 **Dynamic Thinking Effort & Context Window Detection**:
  - Dynamically measures context window limits and reasoning parameters for **any current or future model** via heuristic token extraction (`-1m`, `-2m`, `-128k`, `-256k`), model family registries, and provider specs.
  - Native support for Claude 3.7 budget tokens, OpenAI / NIM / OpenRouter / Groq / DeepSeek reasoning effort (`low`/`medium`/`high`), and Gemini thinking budget.
- 🌐 **16+ First-Class Providers**:
  - Anthropic, OpenAI, Google Gemini, OpenRouter, NVIDIA NIM, OpenCode Zen, Groq, DeepSeek, Mistral AI, xAI (Grok), Ollama (Local), Together AI, Fireworks AI, Cohere, Perplexity Sonar, and an Offline Mock Engine.
- 🛡️ **Three Permission Profiles**:
  - `Secure`: Full interlock — every modification, Python execution, or shell command prompts the user with diffs.
  - `Default`: Balanced — safe read/write operations auto-approved; destructive commands require approval.
  - `Full Access`: Unrestricted autonomous operation.
- 🎯 **Three Operational Modes**:
  - `Plan`: Purely investigatory & architectural mode. Prevents filesystem mutations and destructive commands.
  - `Build`: Full developer mode. Atomic code edits, file creation, command execution, and test runs.
  - `Super`: Autonomous multi-turn goal execution loop with self-verification and automatic error correction.
- 🤖 **Subagent Orchestration**:
  - Dispatch specialized worker subagents (`researcher`, `planner`, `coder`, `tester`, `reviewer`) with isolated context windows to keep the parent context pristine.
- 🐝 **Agent Swarms (`spawn_swarm`)**:
  - Concurrent, coordinated multi-agent execution. The parent "main thread" hosts a shared message bus; subagents run in parallel threads and talk to each other through `swarm_send_message` / `swarm_read_messages`, then return a combined report with the full mailbox transcript.
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
- 🐍 **Python Code Execution with AST Safety Inspection (`execute_python`)**:
  - Model can run Python code with syntax tree analysis detecting sensitive system calls and enforcing permission rules.
- 🔎 **Free Exa Web Search (`exa_search`)**:
  - Built-in real-time web search with zero API key required.
- 📋 **Integrated To-Do Tracking (`todo_create`, `todo_update`, `todo_list`)**:
  - Real-time task planning and HUD progress reporting (`3/5 completed`).
- 🧩 **Extensible Skills & 10 Built-in Skills**:
  - Discovers skills from `~/.harness/skills/` and `.harness/skills/`.
  - Includes specialized **`skill_creator`** (generates and installs new skills on user request) and **`mcp_integrator`** (connects and configures MCP servers on user request).
- 🧠 **Continuous Learning & Self-Improvement**:
  - The agent transparently learns across sessions: `learn_record` / `learn_recall` / `learn_promote` let it (and you, via `/learn`) persist reusable lessons, inject top matches into every system prompt, and promote matured lessons into real skills.
- 🔌 **Model Context Protocol (MCP) Client**:
  - Supports `stdio` and `sse` JSON-RPC 2.0 servers configured in `mcp.json`.
- 🎨 **7 Handcrafted Visual Themes**:
  - `cyberpunk` (default neon), `dracula`, `nord`, `monokai`, `catppuccin`, `matrix`, `minimal`.
- 📦 **Smart Auto-Compaction**:
  - Budget-driven, graduated context compaction. When usage crosses the warning threshold (default 75%), the sampler dials back pressure in three sweeps: oversized verbatim tool payloads are collapsed to head/tail digests, the oldest turn-groups are condensed into structured memory checkpoints down to the target budget (default 60%), and a single global checkpoint is emitted if the window is still hot. Recently-used turns stay verbatim, and undo/redo (`/checkpoint`) can restore the pre-compaction history.
  - Uses the LLM itself to summarize when a provider is live (`compact_summary`), falling back to a heuristic extractor otherwise. A peak-hold hysteresis guard prevents re-firing every turn, and a mid-turn emergency trim collapses old blobs (never current-turn text) if usage races past the hard cap (~95%). Inspect everything with `/compact` and `/tokens`.

---

## 🚀 Quickstart

### Installation & Launch
```bash
# Clone and enter directory
cd harness

# Launch interactive TUI directly:
./bin/harness

# Or install in editable mode:
pip install -e .
harness
```

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
  "api_keys": {
    "anthropic": "sk-ant-...",
    "openai": "sk-proj-...",
    "openrouter": "sk-or-...",
    "nvidia": "nvapi-..."
  }
}
```

Environment variables are also detected automatically (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, etc.).

---

## 🧪 Testing

Harness comes with an automated unit test suite:
```bash
python3 -m unittest discover -s tests -v
```

All 38 test suites verify tools, Python safety, model context window detection, provider payloads, compaction, subagent isolation, skills, and slash commands.
