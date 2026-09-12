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
  - Anthropic, OpenAI, Google Gemini, OpenRouter, NVIDIA NIM, OpenCode Zen / Go, Groq, DeepSeek, Mistral AI, xAI (Grok), Ollama (Local), Together AI, Fireworks AI, Cohere, Perplexity Sonar, and an Offline Mock Engine.
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
- 🔌 **Model Context Protocol (MCP) Client**:
  - Supports `stdio` and `sse` JSON-RPC 2.0 servers configured in `mcp.json`.
- 🎨 **7 Handcrafted Visual Themes**:
  - `cyberpunk` (default neon), `dracula`, `nord`, `monokai`, `catppuccin`, `matrix`, `minimal`.
- 📦 **Smart Auto-Compaction**:
  - Automatically summarizes history into structured memory checkpoints at 75% context threshold.
- 💬 **Out-of-Band `/btw` & Steering `/steer`**:
  - `/btw`: Ask side-questions to the agent while it works without interrupting the active task.
  - `/steer`: Inject directional guidance or constraints into the ongoing loop.

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
| `/btw <question>` | Ask an out-of-band side question while the agent is working |
| `/steer <instruction>` | Inject immediate guidance or constraints into active task |
| `/goal <objective>` | Launch Super Mode autonomous loop toward an explicit goal |
| `/mode [plan\|build\|super]` | Switch operational mode |
| `/perm [secure\|default\|full]` | Switch permission security profile |
| `/provider <name>` | Switch active LLM provider (16+ supported) |
| `/model <name>` | Change model name for active provider |
| `/effort <level>` | Set thinking effort (`off`, `low`, `medium`, `high`, or tokens) |
| `/theme <name>` | Change visual theme (`cyberpunk`, `dracula`, `nord`, etc.) |
| `/todo [list\|add\|clear]` | Manage active task items |
| `/skills` | List or reload registered skills |
| `/mcp [list\|add]` | Manage Model Context Protocol (MCP) servers |
| `/subagent <type> <prompt>` | Dispatch an isolated subagent worker |
| `/compact` | Trigger manual context compaction |
| `/session [list\|resume\|fork]` | Full session lifecycle management |
| `/tokens` | Display token counts, context percentage, and RAM usage |
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
