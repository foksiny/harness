"""
Slash Command Registry and Handlers for Harness.
Handles /btw, /steer, /goal, /mode, /perm, /theme, /models, /config, /keys, /setup, /skills, /mcp, /session.
"""
from typing import Dict, Any, List, Optional, Callable
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.themes import THEMES, list_themes, render_theme_preview
from harness.providers import list_providers, PROVIDER_CONFIGS
from harness.core.compaction import calculate_history_tokens
from harness.config import save_config, mask_key
from harness.commands.config_cmd import display_config_table, display_keys_table, run_setup_wizard
from harness.providers.detector import KNOWN_MODEL_REGISTRY, inspect_model

class CommandContext:
    def __init__(self, agent: Any, renderer: Any, raw_args: str):
        self.agent = agent
        self.renderer = renderer
        self.args = raw_args.strip()

class CommandRegistry:
    """Registry of slash commands."""

    def __init__(self):
        self.commands: Dict[str, Callable[[CommandContext], Any]] = {}
        self.descriptions: Dict[str, str] = {}
        self._register_builtins()

    def register(self, name: str, handler: Callable[[CommandContext], Any], description: str):
        self.commands[name.lower()] = handler
        self.descriptions[name.lower()] = description

    def handle(self, input_line: str, agent: Any, renderer: Any) -> bool:
        """Check if input_line is a slash command. Returns True if handled."""
        line = input_line.strip()
        if not line.startswith("/"):
            return False

        parts = line[1:].split(" ", 1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd_name in self.commands:
            ctx = CommandContext(agent, renderer, args)
            self.commands[cmd_name](ctx)
            return True
        else:
            renderer.print_error(f"Unknown command: '/{cmd_name}'. Type '/help' for available commands.")
            return True

    def _register_builtins(self):
        self.register("help", self._cmd_help, "Show help directory of all commands and options.")
        self.register("btw", self._cmd_btw, "Ask a side-question while working without derailing active task.")
        self.register("steer", self._cmd_steer, "Inject immediate directional guidance or constraints into active task.")
        self.register("goal", self._cmd_goal, "Initiate Super Mode autonomous loop toward an explicit goal.")
        self.register("mode", self._cmd_mode, "Switch operational mode: plan, build, super.")
        self.register("perm", self._cmd_perm, "Switch permission profile: secure, default, full.")
        self.register("theme", self._cmd_theme, "Theme gallery, preview, and switching (14 themes available).")
        self.register("models", self._cmd_models, "Browse model catalog for current or specific provider.")
        self.register("config", self._cmd_config, "View, get, or set configuration settings.")
        self.register("keys", self._cmd_keys, "Manage, mask, and test provider API keys.")
        self.register("setup", self._cmd_setup, "Launch interactive onboarding setup wizard.")
        self.register("provider", self._cmd_provider, "Switch active LLM provider (16+ supported).")
        self.register("model", self._cmd_model, "Change model name for the active provider.")
        self.register("effort", self._cmd_effort, "Set thinking effort: off, low, medium, high, or token count.")
        self.register("todo", self._cmd_todo, "Manage task list: /todo, /todo add <title>, /todo clear.")
        self.register("skills", self._cmd_skills, "List or reload available skills.")
        self.register("mcp", self._cmd_mcp, "Manage MCP servers: /mcp list, /mcp add.")
        self.register("subagent", self._cmd_subagent, "Dispatch an isolated subagent: /subagent <type> <prompt>.")
        self.register("compact", self._cmd_compact, "Trigger manual conversation context compaction.")
        self.register("session", self._cmd_session, "Manage sessions: /session list, resume, save, fork, export.")
        self.register("tokens", self._cmd_tokens, "Display live token metrics, context window ratio, and RAM.")
        self.register("diff", self._cmd_diff, "Show uncommitted git changes.")
        self.register("clear", self._cmd_clear, "Clear the terminal screen.")
        self.register("exit", self._cmd_exit, "Exit Harness.")
        self.register("quit", self._cmd_exit, "Exit Harness.")

    def _cmd_help(self, ctx: CommandContext):
        ctx.renderer.print_help(self.descriptions)

    def _cmd_btw(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_warning("Usage: /btw <your side question>")
            return
        ctx.renderer.print_info(f"Asking side question: '{ctx.args}'...")
        ans = ctx.agent.ask_btw(ctx.args)
        ctx.renderer.print_btw_response(ans)

    def _cmd_steer(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_warning("Usage: /steer <instructions for active task>")
            return
        ctx.agent.steer(ctx.args)
        ctx.renderer.print_success(f"Steering guidance queued: '{ctx.args}'")

    def _cmd_goal(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_warning("Usage: /goal <high-level objective to autonomously accomplish>")
            return
        ctx.agent.set_mode(Mode.SUPER)
        ctx.renderer.print_super_banner(ctx.args)
        for ev in ctx.agent.step(f"AUTONOMOUS GOAL: {ctx.args}"):
            ctx.renderer.render_agent_event(ev)

    def _cmd_mode(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_info(f"Current mode: {ctx.agent.mode.value.upper()}. Options: plan, build, super.")
            return
        try:
            m = Mode.from_string(ctx.args)
            ctx.agent.set_mode(m)
            ctx.renderer.print_success(f"Mode switched to: {m.value.upper()}")
        except Exception:
            ctx.renderer.print_error("Invalid mode. Choose from: plan, build, super.")

    def _cmd_perm(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_info(f"Current permission: {ctx.agent.permission_manager.level.value.upper()}. Options: secure, default, full.")
            return
        try:
            p = PermissionLevel.from_string(ctx.args)
            ctx.agent.set_permission(p)
            ctx.renderer.print_success(f"Permission profile switched to: {p.value.upper()}")
        except Exception:
            ctx.renderer.print_error("Invalid permission. Choose from: secure, default, full.")

    def _cmd_theme(self, ctx: CommandContext):
        args = ctx.args.strip()
        if not args:
            ctx.renderer.print_theme_gallery()
            return

        parts = args.split(" ", 1)
        if parts[0] == "preview":
            target = parts[1].lower().strip() if len(parts) > 1 else ctx.renderer.theme.name
            if target in THEMES:
                card = render_theme_preview(target)
                ctx.renderer.console.print(card)
            else:
                ctx.renderer.print_error(f"Unknown theme '{target}'. Use `/theme` to view the 14 available themes.")
            return

        name = args.lower()
        if name in THEMES:
            ctx.renderer.set_theme(name)
            ctx.agent.config.theme = name
            save_config(ctx.agent.config)
            ctx.renderer.print_success(f"Theme switched to: {THEMES[name].display_name}")
            card = render_theme_preview(name)
            ctx.renderer.console.print(card)
        else:
            ctx.renderer.print_error(f"Unknown theme '{name}'. Available: {', '.join(THEMES.keys())}")

    def _cmd_models(self, ctx: CommandContext):
        target_prov = ctx.args.strip().lower() or ctx.agent.provider.name
        models_data = []
        seen = set()

        # Include discovered remote models if cached
        from harness.providers.discovery import load_cached_models
        cache = load_cached_models()
        cached_prov = cache.get(target_prov, {}).get("models", [])
        for rm in cached_prov:
            mid = rm.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                models_data.append({
                    "name": mid,
                    "context": rm.get("context_length") or 128000,
                    "output": 16384,
                    "thinking": rm.get("supports_thinking", False),
                    "thinking_type": rm.get("thinking_type"),
                })

        # Add known registry models filtered by provider relevance
        for mname, mdata in KNOWN_MODEL_REGISTRY.items():
            if mname in seen:
                continue
            spec = inspect_model(mname, target_prov)
            ml = mname.lower()

            include = False
            if target_prov in ("all", "catalog"):
                include = True
            elif target_prov == "anthropic" and "claude" in ml:
                include = True
            elif target_prov == "openai" and any(k in ml for k in ("gpt", "o1", "o3", "o4")):
                include = True
            elif target_prov == "gemini" and "gemini" in ml:
                include = True
            elif target_prov == "deepseek" and "deepseek" in ml:
                include = True
            elif target_prov in ("nvidia", "nim") and any(k in ml for k in ("meta", "llama", "deepseek", "nvidia", "qwen", "mistral")):
                include = True
            elif target_prov == "groq" and any(k in ml for k in ("llama", "gemma", "mixtral", "qwen")):
                include = True
            elif target_prov == "xai" and "grok" in ml:
                include = True
            elif target_prov == "cohere" and "command" in ml:
                include = True
            elif target_prov == "mistral" and any(k in ml for k in ("mistral", "codestral", "pixtral")):
                include = True
            elif target_prov == "perplexity" and "sonar" in ml:
                include = True
            elif target_prov in ("openrouter", "together", "fireworks", "ollama", "mock"):
                include = True
            elif not models_data:
                include = True

            if include:
                seen.add(mname)
                models_data.append({
                    "name": mname,
                    "context": spec.context_window,
                    "output": spec.max_output_tokens,
                    "thinking": spec.supports_thinking,
                    "thinking_type": spec.thinking_type,
                })

        ctx.renderer.print_models_catalog(target_prov, models_data)

    def _cmd_config(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 2)
        action = parts[0].lower() if parts and parts[0] else "list"

        if action == "list":
            display_config_table(ctx.agent.config, ctx.renderer)
        elif action == "get" and len(parts) > 1:
            key = parts[1].lower()
            val = getattr(ctx.agent.config, key, None)
            if val is not None:
                ctx.renderer.print_info(f"{key} = {val}")
            else:
                ctx.renderer.print_error(f"Unknown config key '{key}'")
        elif action == "set" and len(parts) > 2:
            key, val = parts[1].lower(), parts[2]
            success = ctx.agent.config.set_field(key, val)
            if success:
                save_config(ctx.agent.config)
                ctx.renderer.print_success(f"Updated config: {key} = {val}")
            else:
                ctx.renderer.print_error(f"Failed to set '{key}'. Verify field name and type.")
        else:
            ctx.renderer.print_info("Usage: /config, /config get <key>, /config set <key> <val>")

    def _cmd_keys(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 2)
        action = parts[0].lower() if parts and parts[0] else "list"

        if action == "list":
            display_keys_table(ctx.agent.config, ctx.renderer)
        elif action == "set" and len(parts) > 2:
            pname, key = parts[1].lower(), parts[2]
            ctx.agent.config.set_api_key(pname, key)
            save_config(ctx.agent.config)
            ctx.renderer.print_success(f"Saved key for {pname} ({mask_key(key)})")
        elif action == "remove" and len(parts) > 1:
            pname = parts[1].lower()
            if ctx.agent.config.remove_api_key(pname):
                save_config(ctx.agent.config)
                ctx.renderer.print_success(f"Removed key for {pname}")
            else:
                ctx.renderer.print_warning(f"No key was stored for {pname}")
        else:
            ctx.renderer.print_info("Usage: /keys, /keys set <provider> <key>, /keys remove <provider>")

    def _cmd_setup(self, ctx: CommandContext):
        run_setup_wizard(ctx.agent.config, ctx.renderer)

    def _cmd_provider(self, ctx: CommandContext):
        provs = list_providers()
        if not ctx.args:
            md_lines = [
                f"### ⚡ Active Provider: **{ctx.agent.provider.display_name}** (`{ctx.agent.provider.name}`)",
                "",
                "#### Available LLM Providers:",
            ]
            for k, v in provs.items():
                active_str = "  *(active)*" if k == ctx.agent.provider.name else ""
                md_lines.append(f"- **{k}**: {v}{active_str}")
            md_lines.append("")
            md_lines.append("*Switch provider:* `/provider <provider_id>`")
            ctx.renderer.print_markdown("\n".join(md_lines))
            return
        pname = ctx.args.lower().strip()
        if pname in provs:
            ctx.agent.set_provider(pname)
            ctx.renderer.print_success(f"Switched provider to: {provs[pname]} (Model: {ctx.agent.session.model})")
        else:
            ctx.renderer.print_error(f"Unknown provider '{pname}'. Use /provider to view supported list.")

    def _cmd_model(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_info(f"Current model: {ctx.agent.session.model}\nUsage: /model <model_name>")
            return
        new_model = ctx.args.strip()
        ctx.agent.session.model = new_model
        spec = ctx.agent.provider.get_model_spec(new_model)
        ctx.agent.compactor.context_window = spec.context_window
        ctx.renderer.print_success(
            f"Switched model to: {new_model}\n"
            f"Detected context window: {spec.context_window:,} tokens | Thinking supported: {spec.supports_thinking}"
        )

    def _cmd_effort(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_info(f"Current thinking effort: {ctx.agent.config.thinking_effort}. Options: off, low, medium, high, or integer tokens.")
            return
        ctx.agent.config.thinking_effort = ctx.args.strip()
        ctx.renderer.print_success(f"Thinking effort set to: {ctx.args.strip()}")

    def _cmd_todo(self, ctx: CommandContext):
        args = ctx.args.strip()
        if not args or args == "list":
            tasks_md = ctx.agent.todo_manager.format_markdown()
            ctx.renderer.print_markdown(tasks_md)
        elif args.startswith("add "):
            title = args[4:].strip()
            item = ctx.agent.todo_manager.add_task(title)
            ctx.renderer.print_success(f"Added task #{item.id}: '{item.title}'")
        elif args == "clear":
            ctx.agent.todo_manager.clear()
            ctx.renderer.print_success("Cleared all tasks.")
        else:
            ctx.renderer.print_info("Usage: /todo, /todo add <title>, /todo clear")

    def _cmd_skills(self, ctx: CommandContext):
        if ctx.args == "reload":
            ctx.agent.skills_manager.reload()
            ctx.renderer.print_success(f"Reloaded {len(ctx.agent.skills_manager.skills)} skills.")
            return
        skills = ctx.agent.skills_manager.list_skills()
        lines = [f"### Loaded Skills ({len(skills)}):"]
        for s in skills:
            lines.append(f"- **{s.name}** ({'Built-in' if s.is_builtin else 'Custom'}): {s.description}")
        ctx.renderer.print_markdown("\n".join(lines))

    def _cmd_mcp(self, ctx: CommandContext):
        servers = ctx.agent.mcp_manager.get_configured_servers()
        if not servers:
            ctx.renderer.print_info("No MCP servers configured. (Configure in ~/.harness/mcp.json or ask the model using mcp_integrator skill).")
            return
        lines = [f"### Configured MCP Servers ({len(servers)}):"]
        for name, cfg in servers.items():
            lines.append(f"- **{name}**: `{cfg.get('command')}` (Args: {cfg.get('args', [])})")
        ctx.renderer.print_markdown("\n".join(lines))

    def _cmd_subagent(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 1)
        if len(parts) < 2:
            ctx.renderer.print_warning("Usage: /subagent <researcher|planner|coder|tester|reviewer> <prompt>")
            return
        stype, prompt = parts[0], parts[1]
        ctx.renderer.print_info(f"Spawning subagent [{stype.upper()}]...")
        res = ctx.agent.subagent_orchestrator.spawn(stype, prompt)
        ctx.renderer.print_markdown(f"**Subagent ({res.agent_type}) Output ({res.execution_time}s):**\n\n{res.output}")

    def _cmd_compact(self, ctx: CommandContext):
        compacted, stats = ctx.agent.compactor.compact(ctx.agent.session.messages)
        ctx.agent.session.messages = compacted
        ctx.renderer.print_success(
            f"Context Compacted! Before: {stats.get('before_tokens', 0):,} tokens -> "
            f"After: {stats.get('after_tokens', 0):,} tokens (Saved {stats.get('saved_tokens', 0):,} tokens / {stats.get('reduction_pct', 0)}%)"
        )

    def _cmd_session(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 1)
        action = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        if action in ("", "list"):
            sessions = ctx.agent.session_manager.list_all()
            lines = ["### Saved Sessions:"]
            for s in sessions[:15]:
                lines.append(f"- `{s['id']}`: {s['title']} ({s['model']}, {s['turns']} turns)")
            ctx.renderer.print_markdown("\n".join(lines))
        elif action == "fork":
            forked = ctx.agent.session_manager.fork(ctx.agent.session.id, arg or None)
            if forked:
                ctx.agent.session = forked
                ctx.renderer.print_success(f"Forked session to new branch: `{forked.id}` ({forked.title})")
        elif action == "resume" and arg:
            loaded = ctx.agent.session_manager.load(arg)
            if loaded:
                ctx.agent.session = loaded
                ctx.renderer.print_success(f"Resumed session `{loaded.id}`: {loaded.title}")
            else:
                ctx.renderer.print_error(f"Session '{arg}' not found.")
        else:
            ctx.renderer.print_info("Usage: /session [list|resume <id>|fork [title]|save]")

    def _cmd_tokens(self, ctx: CommandContext):
        ram_mb = 0.0
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if "VmRSS:" in line:
                        ram_mb = round(int(line.split()[1]) / 1024, 1)
        except Exception:
            import resource
            ram_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)

        tokens = calculate_history_tokens(ctx.agent.session.messages)
        c_win = ctx.agent.compactor.context_window
        pct = round((tokens / max(1, c_win)) * 100, 2)
        ctx.renderer.print_info(
            f"Tokens: {tokens:,} / {c_win:,} ({pct}%) | "
            f"Turns: {len(ctx.agent.session.messages)} | "
            f"RAM Usage: {ram_mb} MB"
        )

    def _cmd_diff(self, ctx: CommandContext):
        res = ctx.agent.tool_registry.execute("git_diff", {}, ctx.agent.mode)
        ctx.renderer.print_diff(res)

    def _cmd_clear(self, ctx: CommandContext):
        ctx.renderer.clear_screen()

    def _cmd_exit(self, ctx: CommandContext):
        ctx.renderer.print_info("Exiting Harness. Goodbye!")
        import sys
        sys.exit(0)
