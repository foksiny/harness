"""
Slash Command Registry and Handlers for Harness.
Handles /goal, /mode, /perm, /theme, /models, /config, /keys, /setup, /skills, /mcp, /session.
"""
import os
import sys
import time
from typing import Dict, Any, List, Optional, Callable
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel
from harness.themes import THEMES, BUILTIN_THEME_NAMES, list_themes, render_theme_preview
from harness.providers import list_providers, PROVIDER_CONFIGS
from harness.config import save_config, mask_key
from harness.commands.config_cmd import display_config_table, display_keys_table, run_setup_wizard
from harness.providers.detector import KNOWN_MODEL_REGISTRY, inspect_model
from harness.core.checkpoints import get_checkpoint_manager
from harness.core.agent import AgentEvent
from harness.tui.input_handler import VIEW_AGENTS, VIEW_PARENT, no_echo_stdin

# Provider-to-model prefix mappings for /models filtering.
# Models with explicit provider prefixes (e.g. "together/deepseek-v4-pro") belong
# to that provider. Bare model names use family-based matching.
_PROVIDER_PREFIXES = {
    "anthropic": ("anthropic/",),
    "openai": ("openai/",),
    "gemini": ("google/",),
    "google": ("google/",),
    "deepseek": ("deepseek/",),
    "nvidia": ("deepseek-ai/", "nvidia/", "meta/", "qwen/", "mistral/"),
    "nim": ("deepseek-ai/", "nvidia/", "meta/", "qwen/", "mistral/"),
    "groq": ("openai/",),
    "xai": (),
    "cohere": (),
    "mistral": (),
    "perplexity": (),
    "together": ("together/", "meta/", "minimaxai/", "moonshotai/", "zai-org/"),
    "openrouter": ("anthropic/", "google/", "openai/", "deepseek-ai/", "meta-llama/",
                   "meta/", "nvidia/", "qwen/", "mistral/", "together/", "minimaxai/",
                   "moonshotai/", "zai-org/", "accounts/fireworks/"),
    "fireworks": ("accounts/fireworks/",),
    "ollama": (),
    "mock": (),
}

# Bare model name families → provider (used when no prefix).
_MODEL_FAMILY_PROVIDER = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai", "o3": "openai", "o4": "openai", "o5": "openai",
    "gemini": "gemini",
    "grok": "xai",
    "command": "cohere",
    "mistral": "mistral", "codestral": "mistral", "pixtral": "mistral",
    "ministral": "mistral",
    "sonar": "perplexity",
    "deepseek": "deepseek",
    "llama": "meta",
    "gemma": "google",
    "qwen": "qwen", "qwq": "qwen",
    "glm": "zai-org",
}


def _model_belongs_to_provider(model_name: str, provider: str) -> bool:
    """Check if a model name belongs to a given provider."""
    ml = model_name.lower().strip()
    prov = provider.lower().strip()

    # 1. Check explicit prefix matches (e.g. "together/deepseek-v4-pro" → together)
    prefixes = _PROVIDER_PREFIXES.get(prov, ())
    for pfx in prefixes:
        if ml.startswith(pfx):
            return True

    # 2. Check bare model name families
    family_prov = None
    for family, p in _MODEL_FAMILY_PROVIDER.items():
        if family in ml:
            family_prov = p
            break

    if family_prov is None:
        return False

    # Map family provider to target provider (with aliases)
    prov_aliases = {
        "gemini": ("gemini", "google"),
        "google": ("gemini", "google"),
        "meta": ("nvidia", "nim", "groq", "together", "fireworks", "ollama"),
        "qwen": ("nvidia", "nim", "groq", "together", "openrouter"),
    }

    if family_prov == prov:
        return True
    if prov in prov_aliases.get(family_prov, ()):
        return True
    if family_prov in prov_aliases.get(prov, ()):
        return True

    return False


class CommandContext:
    def __init__(self, agent: Any, renderer: Any, raw_args: str, queue: Any = None):
        self.agent = agent
        self.renderer = renderer
        self.args = raw_args.strip()
        self.queue = queue

def _publish_state(payload: Dict, ctx: CommandContext) -> None:
    """Broadcast a state change to the Discord side (in-process + cross-process).

    Best-effort: failures are swallowed because sync is an enhancement, never a
    prerequisite for a command to work.
    """
    try:
        from harness.discord.sync import get_relay
        relay = get_relay()
        if relay is not None:
            relay.publish_state(payload, origin="cli")
    except Exception:
        pass


def _publish_output(text: str, ctx: CommandContext) -> None:
    """Mirror command output to the Discord side (display only)."""
    try:
        from harness.discord.sync import get_relay
        relay = get_relay()
        if relay is not None:
            relay.relay_output(text, origin="cli")
    except Exception:
        pass

class CommandRegistry:
    """Registry of slash commands."""

    def __init__(self):
        self.commands: Dict[str, Callable[[CommandContext], Any]] = {}
        self.descriptions: Dict[str, str] = {}
        self._register_builtins()

    def register(self, name: str, handler: Callable[[CommandContext], Any], description: str):
        self.commands[name.lower()] = handler
        self.descriptions[name.lower()] = description

    def handle(self, input_line: str, agent: Any, renderer: Any, queue: Any = None) -> bool:
        """Check if input_line is a slash command. Returns True if handled."""
        line = input_line.strip()
        if not line.startswith("/"):
            return False

        parts = line[1:].split(" ", 1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd_name in self.commands:
            ctx = CommandContext(agent, renderer, args, queue=queue)
            return self.commands[cmd_name](ctx)
        else:
            renderer.print_error(f"Unknown command: '/{cmd_name}'. Type '/help' for available commands.")
            return True

    def _register_builtins(self):
        self.register("help", self._cmd_help, "Show help directory of all commands and options.")
        self.register("goal", self._cmd_goal, "Initiate Super Mode autonomous loop toward an explicit goal.")
        self.register("ultra-goal", self._cmd_ultra_goal, "Full app/game build: agent interviews you first, then max-capability Super Mode mission with parallel subagent/squad generation and enforced verification.")
        self.register("ultragoal", self._cmd_ultra_goal, "Alias of /ultra-goal: full app/game build mission.")
        self.register("stop", self._cmd_stop, "Interrupt running agent turn (use '/stop all' to also clear queue).")
        self.register("queue", self._cmd_queue, "View, drop, pause, resume, or clear prompt execution queue.")
        self.register("sidebar", self._cmd_sidebar, "Toggle or view workspace, session, model, and context sidebar.")
        self.register("status", self._cmd_status, "Display full system, agent, budget, and queue status.")
        self.register("mode", self._cmd_mode, "Switch operational mode: plan, build, super.")
        self.register("perm", self._cmd_perm, "Switch permission profile: secure, default, full.")
        self.register("theme", self._cmd_theme, "Theme gallery, preview, switching, and custom creation: /theme, /theme list, /theme <name>, /theme preview <name>, /theme create, /theme delete <name>.")
        self.register("models", self._cmd_models, "Browse model catalog for current or specific provider.")
        self.register("config", self._cmd_config, "View, get, or set configuration settings.")
        self.register("keys", self._cmd_keys, "Manage, mask, and test provider API keys.")
        self.register("setup", self._cmd_setup, "Launch interactive onboarding setup wizard.")
        self.register("provider", self._cmd_provider, "Switch active LLM provider (16+ supported).")
        self.register("model", self._cmd_model, "Change model name for the active provider.")
        self.register("effort", self._cmd_effort, "Set thinking effort: off, low, medium, high, or token count.")
        self.register("todo", self._cmd_todo, "Manage task list: /todo, /todo add <title>, /todo clear.")
        self.register("skills", self._cmd_skills, "List or reload available skills.")
        self.register("reload", self._cmd_reload, "Reload config, skills, and permissions from disk.")
        self.register("learn", self._cmd_learn, "Manage learned memories: /learn [list], record <summary>, forget <id>, promote <id>, on, off.")
        self.register("mcp", self._cmd_mcp, "Manage MCP servers: /mcp list, /mcp add.")
        self.register("subagent", self._cmd_subagent, "Dispatch an isolated subagent: /subagent <type> <prompt>.")
        self.register("agents", self._cmd_agents, "Show subagent swarm board (F2).")
        self.register("agent", self._cmd_agent, "Inspect a subagent: /agent <id>.")
        self.register("back", self._cmd_back, "Return to the parent agent view (ESC).")
        self.register("compact", self._cmd_compact, "Trigger manual conversation context compaction.")
        self.register("session", self._cmd_session, "Manage sessions: /session list [all], create [title], delete <id>, rename <id> <title>, fork [title], resume <id>. Sessions are workspace-associated (list shows this folder's; 'all' shows every workspace).")
        self.register("mesh", self._cmd_mesh, "Mesh network: /mesh status, /mesh peers, /mesh send <port> <msg>, /mesh broadcast <msg>.")
        self.register("checkpoint", self._cmd_checkpoint, "Manage checkpoints: /checkpoint list, create [label], undo, redo.")
        self.register("tokens", self._cmd_tokens, "Display live token metrics, context window ratio, and RAM.")
        self.register("diff", self._cmd_diff, "Show uncommitted git changes.")
        self.register("update", self._cmd_update, "Check for and apply latest updates from Git / PyPI.")
        self.register("discord", self._cmd_discord, "Manage Discord bot daemon and sync connection.")
        self.register("clear", self._cmd_clear, "Clear the terminal screen.")
        self.register("exit", self._cmd_exit, "Exit Harness.")
        self.register("quit", self._cmd_exit, "Exit Harness.")

    def _cmd_help(self, ctx: CommandContext):
        # Mirror /models - /sidebar: always the static borderless sidebar-style print.
        ctx.renderer.print_help_modal(self.descriptions)

    def _cmd_goal(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_warning("Usage: /goal <high-level objective to autonomously accomplish>")
            return
        ctx.agent.set_mode(Mode.SUPER)
        _publish_state({"mode": "super", "goal": ctx.args}, ctx)
        ctx.renderer.print_super_banner(ctx.args)
        _publish_output(f"🚀 SUPER MODE ACTIVATED — Autonomous Goal: {ctx.args}", ctx)
        if ctx.queue is not None:
            ctx.queue.enqueue(f"AUTONOMOUS GOAL: {ctx.args}", mode="super")
        else:
            with no_echo_stdin():
                for ev in ctx.agent.step(f"AUTONOMOUS GOAL: {ctx.args}"):
                    ctx.renderer.render_agent_event(ev)

    def _cmd_ultra_goal(self, ctx: CommandContext):
        """/ultra-goal: maximum-capability full app/game build.

        Upside of /goal with a dedicated ULTRA-GOAL system prompt. The AGENT
        interviews the user via ask_user before writing any files, then runs
        mandatory parallel swarm file generation and enforced verification
        (deps + tests + launch) before finishing.
        """
        if not ctx.args:
            ctx.renderer.print_warning(
                "Usage: /ultra-goal <high-level objective to build as a full app/game>"
            )
            return

        from harness.commands.ultra_goal import (
            build_ultra_goal_brief,
            print_ultra_goal_banner,
        )

        # Launch the ULTRA-GOAL mission in Super Mode; the agent interviews
        # the user itself (ask_user) so it knows exactly what is wanted.
        ctx.agent.set_mode(Mode.SUPER)
        brief = build_ultra_goal_brief(ctx.args)
        print_ultra_goal_banner(ctx.renderer, ctx.args)
        _publish_state({
            "mode": "super",
            "goal": ctx.args,
            "ultra_goal": True,
        }, ctx)
        _publish_output(f"🎯 ULTRA-GOAL ACTIVATED — Full Build: {ctx.args}", ctx)

        if ctx.queue is not None:
            ctx.queue.enqueue(brief, mode="super")
        else:
            with no_echo_stdin():
                for ev in ctx.agent.step(brief):
                    ctx.renderer.render_agent_event(ev)

    def _cmd_stop(self, ctx: CommandContext):
        """Cooperatively interrupt the running turn from the CLI side."""
        was_running = ctx.agent.is_running
        ctx.agent.request_stop()
        if ctx.args.lower() in ("all", "clear", "queue") and ctx.queue is not None:
            cleared = ctx.queue.clear()
            if cleared:
                ctx.renderer.print_info(f"Purged {cleared} pending task{'s' if cleared != 1 else ''} from queue.")

        if was_running:
            ctx.renderer.print_warning("⏹ Stop requested — interrupting current execution…")
        else:
            ctx.renderer.print_info("⏹ Stop flag set.")
            ctx.agent.clear_stop()

    def _cmd_queue(self, ctx: CommandContext):
        """Manage prompt execution queue."""
        queue = ctx.queue
        if queue is None:
            ctx.renderer.print_info("Execution queue is not active in this mode.")
            return

        parts = ctx.args.split(" ", 1)
        sub = parts[0].lower() if parts[0] else "list"
        arg = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("list", ""):
            # Mirror /sidebar|/models: always static borderless print, no modal.
            ctx.renderer.print_queue_modal(queue)
        elif sub in ("clear", "purge", "flush"):
            cleared = queue.clear()
            ctx.renderer.print_success(f"Cleared {cleared} queued task{'s' if cleared != 1 else ''}.")
        elif sub in ("drop", "rm", "remove", "delete"):
            if not arg or not arg.isdigit():
                ctx.renderer.print_warning("Usage: /queue drop <task_id>")
                return
            target_id = int(arg)
            dropped = queue.remove(target_id)
            if dropped:
                ctx.renderer.print_success(f"Removed task #{target_id} from queue.")
            else:
                ctx.renderer.print_warning(f"No pending task with ID #{target_id} found.")
        elif sub == "pause":
            queue.pause()
            ctx.renderer.print_warning("Queue paused. Running task will finish, but subsequent queued tasks will wait.")
        elif sub in ("resume", "unpause", "start"):
            queue.resume()
            ctx.renderer.print_success("Queue resumed. Next pending task will run automatically.")
        elif sub == "add":
            if not arg:
                ctx.renderer.print_warning("Usage: /queue add <prompt>")
                return
            item = queue.enqueue(arg)
            ctx.renderer.print_queue_event(item, "enqueued")
        else:
            ctx.renderer.print_info("Usage: /queue [list] | /queue add <prompt> | /queue drop <id> | /queue clear | /queue pause | /queue resume")

    def _cmd_sidebar(self, ctx: CommandContext):
        """Display OpenCode-style right-side info panel."""
        ctx.renderer.print_sidebar(ctx.agent, queue=ctx.queue)

    def _cmd_status(self, ctx: CommandContext):
        """Display comprehensive system, agent, budget, and queue status card."""
        # Mirror /sidebar|/models: always static borderless print, no modal.
        ctx.renderer.print_status_modal(ctx.agent, queue=ctx.queue)

    def _cmd_mode(self, ctx: CommandContext):
        if not ctx.args:
            ctx.renderer.print_info(f"Current mode: {ctx.agent.mode.value.upper()}. Options: plan, build, super.")
            return
        try:
            m = Mode.from_string(ctx.args)
            ctx.agent.set_mode(m)
            ctx.agent.config.mode = m.value
            save_config(ctx.agent.config)
            _publish_state({"mode": m.value}, ctx)
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
            ctx.agent.config.permission = p.value
            save_config(ctx.agent.config)
            _publish_state({"permission": p.value}, ctx)
            ctx.renderer.print_success(f"Permission profile switched to: {p.value.upper()}")
        except Exception:
            ctx.renderer.print_error("Invalid permission. Choose from: secure, default, full.")

    def _cmd_theme(self, ctx: CommandContext):
        args = ctx.args.strip()
        if not args:
            # Mirror /sidebar|/models: always static borderless print, no modal.
            ctx.renderer.print_theme_modal(active_theme=ctx.agent.config.theme)
            return

        parts = args.split(" ", 1)
        sub = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if sub == "preview":
            target = arg.lower() if arg else ctx.renderer.theme.name
            if target in THEMES:
                card = render_theme_preview(target)
                ctx.renderer.console.print(card)
            else:
                ctx.renderer.print_error(f"Unknown theme '{target}'. Use `/theme` to view available themes.")
            return

        if sub == "list":
            # List all themes with indicators for built-in vs custom
            lines = ["### Available Themes:"]
            for name, theme in sorted(THEMES.items()):
                marker = "[dim](built-in)[/dim]" if name in BUILTIN_THEME_NAMES else "[green](custom)[/green]"
                active = " ▶" if name == ctx.agent.config.theme else ""
                lines.append(f"- `{name}`: {theme.display_name} {marker}{active}")
            ctx.renderer.print_markdown("\n".join(lines))
            return

        if sub == "create":
            # Interactive theme creation wizard
            from harness.themes import interactive_create_theme
            ctx.renderer.print_info("Starting interactive theme creator...")
            success, message = interactive_create_theme(global_scope=True)
            if success:
                ctx.renderer.print_success(message)
                # Show preview of new theme
                new_name = message.split("`")[1] if "`" in message else ""
                if new_name and new_name in THEMES:
                    card = render_theme_preview(new_name)
                    ctx.renderer.console.print(card)
                    ctx.renderer.print_info(f"Switch to it with: /theme {new_name}")
            else:
                ctx.renderer.print_error(message)
            return

        if sub == "delete":
            if not arg:
                ctx.renderer.print_warning("Usage: /theme delete <theme_name>")
                return
            from harness.themes import delete_custom_theme
            success, message = delete_custom_theme(arg)
            if success:
                ctx.renderer.print_success(message)
                # If deleted theme was active, revert to default
                if ctx.agent.config.theme == arg.lower():
                    ctx.agent.config.theme = "cyberpunk"
                    ctx.renderer.set_theme("cyberpunk")
                    save_config(ctx.agent.config)
            else:
                ctx.renderer.print_error(message)
            return

        # Switch theme
        name = args.lower()
        if name in THEMES:
            ctx.renderer.set_theme(name)
            ctx.agent.config.theme = name
            save_config(ctx.agent.config)
            ctx.renderer.print_success(f"Theme switched to: {THEMES[name].display_name}")
            card = render_theme_preview(name)
            ctx.renderer.console.print(card)
        else:
            ctx.renderer.print_error(f"Unknown theme '{name}'. Use `/theme list` to see available themes.")

    def _cmd_models(self, ctx: CommandContext):
        target_prov = ctx.args.strip().lower() or ctx.agent.provider.name
        models_data = []
        seen = set()

        from harness.providers.discovery import load_cached_models, load_universal_models
        cache = load_cached_models()
        univ = load_universal_models()
        cached_prov = cache.get(target_prov, {}).get("models", [])

        def _resolve_context(name: str, spec_ctx: int) -> int:
            base = name.lower().split("/")[-1].split(":")[0]
            if base in univ and isinstance(univ[base], int):
                return univ[base]
            return spec_ctx

        broad_prov = target_prov in ("all", "catalog", "mock", "ollama")
        for rm in cached_prov:
            mid = rm.get("id")
            if not mid or mid in seen:
                continue
            if not broad_prov and not _model_belongs_to_provider(mid.lower(), target_prov):
                continue
            seen.add(mid)
            spec = inspect_model(mid, target_prov)
            ctx_from_cache = rm.get("context_length") or 0
            cache_thinking = rm.get("supports_thinking")
            cache_tt = rm.get("thinking_type")
            models_data.append({
                "name": mid,
                "context": _resolve_context(mid, ctx_from_cache or spec.context_window),
                "output": spec.max_output_tokens,
                "thinking": spec.supports_thinking or bool(cache_thinking),
                "thinking_type": spec.thinking_type or cache_tt,
                "vision": bool(rm.get("supports_vision", spec.supports_vision)),
                "reasoning_options": list(rm.get("reasoning_options") or spec.reasoning_options),
            })

        for mname, mdata in KNOWN_MODEL_REGISTRY.items():
            if mname in seen:
                continue
            spec = inspect_model(mname, target_prov)
            ml = mname.lower()

            include = False
            if target_prov in ("all", "catalog"):
                include = True
            elif _model_belongs_to_provider(ml, target_prov):
                include = True
            elif not models_data and target_prov in ("mock", "ollama"):
                # Generic local providers with no prefix mapping: show registry as fallback
                include = True

            if include:
                seen.add(mname)
                models_data.append({
                    "name": mname,
                    "context": _resolve_context(mname, spec.context_window),
                    "output": spec.max_output_tokens,
                    "thinking": spec.supports_thinking,
                    "thinking_type": spec.thinking_type,
                    "vision": spec.supports_vision,
                    "reasoning_options": list(spec.reasoning_options),
                })

        active_m = ctx.agent.session.model if ctx.agent.session else ctx.agent.config.model
        # Mirror /sidebar: always render the static sidebar-style catalog print.
        # No interactive modal, no overlay, no raw-mode loop. Switch via /model <name>.
        ctx.renderer.print_models_modal(
            target_prov,
            models_data,
            active_model=active_m,
        )

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
            ctx.agent.config.provider = pname
            current_model = ctx.agent.session.model if ctx.agent.session is not None else ctx.agent.provider.default_model
            ctx.agent.config.model = current_model
            save_config(ctx.agent.config)
            _publish_state({"provider": pname, "model": current_model}, ctx)
            ctx.renderer.print_success(f"Switched provider to: {provs[pname]} (Model: {current_model})")
        else:
            ctx.renderer.print_error(f"Unknown provider '{pname}'. Use /provider to view supported list.")

    def _cmd_model(self, ctx: CommandContext):
        if not ctx.args:
            current = ctx.agent.session.model if ctx.agent.session is not None else ctx.agent.config.model
            ctx.renderer.print_info(f"Current model: {current}\nUsage: /model <model_name>")
            return
        new_model = ctx.args.strip()
        if ctx.agent.session is not None:
            ctx.agent.session.model = new_model
        ctx.agent.config.model = new_model
        save_config(ctx.agent.config)
        spec = ctx.agent.provider.get_model_spec(new_model)
        ctx.agent.compactor.context_window = spec.context_window
        _publish_state({"provider": ctx.agent.provider.name, "model": new_model}, ctx)
        ctx.renderer.print_success(
            f"Switched model to: {new_model}\n"
            f"Detected context window: {spec.context_window:,} tokens | Thinking supported: {spec.supports_thinking}"
        )

    def _cmd_effort(self, ctx: CommandContext):
        if not ctx.args:
            current = ctx.agent.config.thinking_effort
            model = ctx.agent.session.model if ctx.agent.session else ctx.agent.config.model
            spec = ctx.agent.provider.get_model_spec(model)
            dialect = spec.thinking_type or "reasoning_effort"
            budget = dialect in ("budget_tokens", "thinking_budget", "thinking_token_budget")
            budget_note = " | Budget: set any integer token count" if budget else ""
            ctx.renderer.print_info(
                f"Current thinking effort: {current} (dialect: {dialect}{budget_note}). "
                f"Options: off, low, medium, high, or integer tokens."
            )
            return
        setting = ctx.args.strip()
        ctx.agent.config.thinking_effort = setting
        save_config(ctx.agent.config)
        model = ctx.agent.session.model if ctx.agent.session else ctx.agent.config.model
        spec = ctx.agent.provider.get_model_spec(model)
        dialect = spec.thinking_type or "reasoning_effort"
        params = ctx.agent.provider.normalize_thinking_effort(spec, setting)
        ctx.renderer.print_success(f"Thinking effort set to: {setting} (dialect: {dialect})")
        if params:
            ctx.renderer.print_info(f"Request params: {params}")
        _publish_state({"thinking_effort": setting}, ctx)

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

    def _cmd_reload(self, ctx: CommandContext):
        from harness.config import load_config
        from harness.core.permissions import PermissionLevel
        reloaded = []
        errors = []

        # 1. Reload config from disk
        try:
            fresh = load_config()
            ctx.agent.config = fresh
            reloaded.append("config")
        except Exception as exc:
            errors.append(f"config: {exc}")

        # 2. Reload skills
        try:
            ctx.agent.skills_manager.reload()
            reloaded.append(f"skills ({len(ctx.agent.skills_manager.skills)} loaded)")
        except Exception as exc:
            errors.append(f"skills: {exc}")

        # 3. Re-sync permission level
        try:
            ctx.agent.permission_manager.level = PermissionLevel.from_string(ctx.agent.config.permission)
            reloaded.append(f"permission → {ctx.agent.config.permission}")
        except Exception as exc:
            errors.append(f"permission: {exc}")

        if reloaded:
            ctx.renderer.print_success("Reloaded: " + ", ".join(reloaded))
        for e in errors:
            ctx.renderer.print_error(e)

    def _cmd_learn(self, ctx: CommandContext):
        lm = ctx.agent.learning_manager
        args = ctx.args.strip()
        if not args or args == "list":
            lessons = sorted(lm.list(), key=lambda l: l.created_at, reverse=True)
            if not lessons:
                ctx.renderer.print_info("No learned lessons yet. The agent records them automatically (and via learn_record) when it discovers reusable insights.")
                return
            lines = [f"### Learned Lessons ({len(lessons)}):"]
            for l in lessons:
                tags = f" [tags: {', '.join(l.tags)}]" if l.tags else ""
                promoted = " (promoted)" if l.promoted else ""
                lines.append(f"- `{l.id}`{tags} (hits: {l.hits}){promoted}: {l.summary}")
            lines.append("")
            stats = lm.stats()
            lines.append(f"_Promoted to skills: {stats['promoted']} | Total: {stats['total']}_")
            ctx.renderer.print_markdown("\n".join(lines))
        elif args.startswith("record "):
            summary = args[7:].strip()
            if not summary:
                ctx.renderer.print_warning("Usage: /learn record <summary> [--tags tag1,tag2]")
                return
            tags = []
            if "--tags" in summary:
                summary, _, tag_part = summary.partition(" --tags ")
                tags = [t.strip() for t in tag_part.split(",") if t.strip()]
            session_id = ctx.agent.session.id if ctx.agent.session else ""
            lid = lm.record(summary=summary.strip(), tags=tags, source_session=session_id)
            if lid:
                ctx.renderer.print_success(f"Recorded lesson `{lid}`.")
            else:
                ctx.renderer.print_error("Failed to record lesson (empty summary).")
        elif args.startswith("forget "):
            lid = args[7:].strip()
            if lm.forget(lid):
                ctx.renderer.print_success(f"Forgot lesson `{lid}`.")
            else:
                ctx.renderer.print_error(f"Lesson `{lid}` not found.")
        elif args.startswith("promote "):
            parts = args[8:].split(None, 1)
            lid = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else None
            if lm.promote(lid, name=name):
                ctx.renderer.print_success(f"Promoted lesson `{lid}` into a skill (catalog reloaded).")
            else:
                ctx.renderer.print_error(f"Lesson `{lid}` not found or could not be promoted.")
        elif args == "on":
            ctx.agent.config.learning_enabled = True
            save_config(ctx.agent.config)
            ctx.renderer.print_success("Learning enabled: the agent will record and re-inject past lessons.")
        elif args == "off":
            ctx.agent.config.learning_enabled = False
            save_config(ctx.agent.config)
            ctx.renderer.print_info("Learning disabled for this and future sessions.")
        else:
            ctx.renderer.print_info("Usage: /learn [list|record <summary> [--tags a,b]|forget <id>|promote <id> [name]|on|off]")

    def _cmd_mcp(self, ctx: CommandContext):
        servers = ctx.agent.mcp_manager.get_configured_servers()
        # Mirror /sidebar|/models: always static borderless print, no modal.
        ctx.renderer.print_mcp_modal(servers)

    def _cmd_subagent(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 1)
        if len(parts) < 2:
            ctx.renderer.print_warning("Usage: /subagent <researcher|planner|coder|tester|reviewer> <prompt>")
            return
        stype, prompt = parts[0], parts[1]
        ctx.renderer.print_info(f"Spawning subagent [{stype.upper()}]...")
        with no_echo_stdin():
            res = ctx.agent.subagent_orchestrator.spawn(stype, prompt)
        ctx.renderer.print_markdown(f"**Subagent ({res.agent_type}) Output ({res.execution_time}s):**\n\n{res.output}")
        for etype, edata in ctx.agent.subagent_orchestrator.drain_events():
            ctx.renderer.render_agent_event(AgentEvent(etype, edata))

    def _cmd_agents(self, ctx: CommandContext):
        """Return sentinel so the interactive loop switches to the agents view."""
        return VIEW_AGENTS

    def _cmd_agent(self, ctx: CommandContext):
        aid = ctx.args.strip()
        if not aid:
            ctx.renderer.print_warning("Usage: /agent <id>  (see /agents for ids)")
            return
        record = ctx.agent.subagent_orchestrator.get_record(aid)
        ctx.renderer.print_subagent_detail(record)

    def _cmd_back(self, ctx: CommandContext):
        """Return sentinel so the interactive loop switches back to the parent view."""
        return VIEW_PARENT

    def _cmd_compact(self, ctx: CommandContext):
        if ctx.agent.session is None:
            ctx.renderer.print_warning("No active session to compact. Send a message first.")
            return
        sys_prompt = ctx.agent._build_system_prompt("")
        compacted, stats = ctx.agent.compactor.compact(ctx.agent.session.messages, sys_prompt)
        if not stats.get("compacted"):
            ctx.renderer.print_info("Nothing to compact yet — not enough history or below the threshold.")
            return
        ctx.agent._record_message_span_change(ctx.agent.session.messages, compacted)
        ctx.agent.session.messages = compacted

        tactics = stats.get("tactics", {})
        tactic_desc = []
        if tactics.get("payloads_truncated"):
            tactic_desc.append(f"{tactics['payloads_truncated']} oversized payload(s) collapsed")
        if tactics.get("groups_summarized"):
            tactic_desc.append(f"{tactics['groups_summarized']} old turn-group(s) summarized")
        if tactics.get("checkpointed"):
            tactic_desc.append("global checkpoint consolidated")
        tactic_str = "; ".join(tactic_desc) or "no compression needed"

        ctx.renderer.print_success(
            f"Context Compacted! Before: {stats.get('before_tokens', 0):,} tokens -> "
            f"After: {stats.get('after_tokens', 0):,} tokens (Saved {stats.get('saved_tokens', 0):,} tokens "
            f"/ {stats.get('reduction_pct', 0)}%). Tactics: {tactic_str}"
        )
        ledger = getattr(ctx.agent.compactor, "ledger", [])
        if len(ledger) > 1:
            ctx.renderer.print_info(
                f"Last {min(len(ledger), 5)} compactions (recent first): "
                + ", ".join(
                    f"{e['trigger']} -{e['saved_tokens']:,}t" for e in list(reversed(ledger))[:5]
                )
            )

    def _cmd_session(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 1)
        action = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        if action in ("", "list"):
            # Sessions are global but associated to a workspace: list this
            # workspace's sessions by default; `/session list all` shows every
            # workspace's history.
            arg_l = arg.strip().lower()
            ws = None if arg_l in ("all", "--all", "*") else ctx.agent.session_manager.workspace
            sessions = ctx.agent.session_manager.list_all(workspace=ws)
            active_id = ctx.agent.session.id if ctx.agent.session else None
            # Mirror /sidebar|/models: always static borderless print, no modal.
            ctx.renderer.print_sessions_modal(sessions, active_id=active_id)
            lines = ["### Saved Sessions:"]
            for s in sessions[:15]:
                ws_tag = f", {os.path.basename(s['workspace'].rstrip('/\\'))}" if s.get("workspace") else ""
                lines.append(f"- `{s['id']}`: {s['title']} ({s['model']}, {s['turns']} turns{ws_tag})")
            _publish_output("\n".join(lines), ctx)
        elif action == "create":
            # Explicit session creation — allowed even before a first message.
            title = arg.strip() if arg else "New Session"
            new_session = ctx.agent.session_manager.create(
                provider=ctx.agent.provider.name,
                model=ctx.agent.session.model if ctx.agent.session is not None else ctx.agent.config.model,
                mode=ctx.agent.mode.value,
                permission=ctx.agent.permission_manager.level.value,
                thinking_effort=ctx.agent.config.thinking_effort,
                title=title,
            )
            ctx.agent.session = new_session
            try:
                ctx.agent._bind_checkpoint_session(new_session)
            except Exception:
                pass
            _publish_state({"session_id": new_session.id, "session_title": new_session.title, "session_action": "create"}, ctx)
            ctx.renderer.print_success(f"Created new session: `{new_session.id}` ({new_session.title})")
        elif action == "delete":
            if not arg:
                ctx.renderer.print_warning("Usage: /session delete <session_id>")
                return
            # Don't allow deleting the current session
            if ctx.agent.session is not None and arg == ctx.agent.session.id:
                ctx.renderer.print_error("Cannot delete the currently active session. Switch to another session first.")
                return
            success = ctx.agent.session_manager.delete(arg)
            if success:
                ctx.renderer.print_success(f"Deleted session: `{arg}`")
            else:
                ctx.renderer.print_error(f"Session '{arg}' not found.")
        elif action == "rename":
            if not arg:
                ctx.renderer.print_warning("Usage: /session rename <session_id> <new_title>")
                return
            rename_parts = arg.split(" ", 1)
            session_id = rename_parts[0]
            new_title = rename_parts[1] if len(rename_parts) > 1 else ""
            if not new_title:
                ctx.renderer.print_warning("Usage: /session rename <session_id> <new_title>")
                return
            session = ctx.agent.session_manager.load(session_id)
            if not session:
                ctx.renderer.print_error(f"Session '{session_id}' not found.")
                return
            session.title = new_title
            ctx.agent.session_manager.save(session)
            if ctx.agent.session is not None and session_id == ctx.agent.session.id:
                ctx.agent.session = session
                try:
                    ctx.agent._bind_checkpoint_session(session)
                except Exception:
                    pass
            _publish_state({"session_id": session_id, "session_title": new_title, "session_action": "rename"}, ctx)
            ctx.renderer.print_success(f"Renamed session `{session_id}` to: `{new_title}`")
        elif action == "fork":
            if ctx.agent.session is None:
                ctx.renderer.print_warning("No active session to fork. Send a message first.")
                return
            forked = ctx.agent.session_manager.fork(ctx.agent.session.id, arg or None)
            if forked:
                ctx.agent.session = forked
                try:
                    ctx.agent._bind_checkpoint_session(forked)
                except Exception:
                    pass
                _publish_state({"session_id": forked.id, "session_title": forked.title, "session_action": "fork"}, ctx)
                ctx.renderer.print_success(f"Forked session to new branch: `{forked.id}` ({forked.title})")
        elif action == "resume" and arg:
            loaded = ctx.agent.session_manager.load(arg)
            if loaded:
                ctx.agent.session = loaded
                try:
                    ctx.agent._bind_checkpoint_session(loaded)
                    ctx.agent._restore_todos_from_session()
                except Exception:
                    pass
                _publish_state({"session_id": loaded.id, "session_title": loaded.title, "session_action": "resume"}, ctx)
                ctx.renderer.print_success(f"Resumed session `{loaded.id}`: {loaded.title}")
            else:
                ctx.renderer.print_error(f"Session '{arg}' not found.")
        else:
            ctx.renderer.print_info("Usage: /session [list [all]|create [title]|delete <id>|rename <id> <title>|fork [title]|resume <id>]")

    def _cmd_tokens(self, ctx: CommandContext):
        from harness.sysinfo import get_ram_usage_mb
        ram_mb = get_ram_usage_mb()

        if ctx.agent.session is None:
            ctx.renderer.print_info(f"Tokens: 0 | RAM Usage: {ram_mb} MB")
            return

        sys_prompt = ctx.agent._build_system_prompt("")
        status = ctx.agent.compactor.check_status(ctx.agent.session.messages, sys_prompt)
        bd = status.breakdown
        marker = f"{status.total_tokens:,} / {status.context_window:,} ({status.usage_ratio * 100:.1f}%)"
        if status.is_warning:
            marker += " ⚠"
        ctx.renderer.print_info(f"Tokens: {marker} | Turns: {len(ctx.agent.session.messages)} | RAM: {ram_mb} MB")

        ctx.renderer.print_markdown(
            "\n".join([
                "**Token breakdown (est.):**",
                f"- System prompt: `{bd.get('system_prompt', 0):,}`",
                f"- User messages: `{bd.get('user', 0):,}`",
                f"- Assistant text: `{bd.get('assistant', 0):,}`",
                f"- Reasoning: `{bd.get('reasoning', 0):,}`",
                f"- Tool results: `{bd.get('tool', 0):,}`",
                f"- Tool call args: `{bd.get('tool_calls', 0):,}`",
                "",
                f"**Budget:** arm @ `{status.threshold_ratio:.0%}`, compact to `{status.target_ratio:.0%}`, cap `{status.cap_ratio:.0%}`",
                f"Max message before payload truncation: `{ctx.agent.compactor.max_message_tokens:,}` tokens",
                f"Compactions this session: `{len(ctx.agent.compactor.ledger)}`",
            ])
        )

    def _cmd_mesh(self, ctx: CommandContext):
        args = ctx.args.strip()
        if not args or args.lower() in ("status", "info"):
            res = ctx.agent.tool_registry.execute("mesh_status", {}, ctx.agent.mode)
            ctx.renderer.print_info(res)
            return
        parts = args.split(" ", 2)
        sub = parts[0].lower()
        if sub == "peers":
            res = ctx.agent.tool_registry.execute("mesh_list_peers", {}, ctx.agent.mode)
            ctx.renderer.print_info(res)
        elif sub == "send" and len(parts) >= 3:
            try:
                port = int(parts[1])
                msg = parts[2]
                res = ctx.agent.tool_registry.execute("mesh_send_message", {"port": port, "message": msg}, ctx.agent.mode)
                ctx.renderer.print_info(res)
            except Exception as ex:
                ctx.renderer.print_error(f"Usage: /mesh send <port> <message> ({ex})")
        elif sub == "broadcast":
            msg = args[len("broadcast"):].strip() or (parts[1] if len(parts) > 1 else "")
            if not msg:
                ctx.renderer.print_error("Usage: /mesh broadcast <message>")
                return
            res = ctx.agent.tool_registry.execute("mesh_broadcast", {"message": msg}, ctx.agent.mode)
            ctx.renderer.print_info(res)
        elif sub == "read":
            since = 0
            try:
                since = int(parts[1]) if len(parts) > 1 else 0
            except Exception:
                since = 0
            res = ctx.agent.tool_registry.execute("mesh_read_messages", {"since_id": since}, ctx.agent.mode)
            ctx.renderer.print_info(res)
        else:
            ctx.renderer.print_info(
                "Mesh commands:\n"
                "  /mesh status — show mesh port, block, peers, and API\n"
                "  /mesh peers — list peers in this workspace+user\n"
                "  /mesh send <port> <msg> — send to a specific peer\n"
                "  /mesh broadcast <msg> — broadcast to all peers\n"
                "  /mesh read [since_id] — read inbound messages"
            )

    def _cmd_checkpoint(self, ctx: CommandContext):
        parts = ctx.args.split(" ", 1)
        action = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        cp_manager = ctx.agent.checkpoint_manager

        if action in ("", "list"):
            checkpoints = cp_manager.list_checkpoints()
            if not checkpoints:
                ctx.renderer.print_info("No checkpoints available.")
                return
            lines = ["### Checkpoints:"]
            for cp in checkpoints:
                marker = " ▸" if cp["is_current"] else ""
                ts = time.strftime("%H:%M:%S", time.localtime(cp["timestamp"]))
                lines.append(f"- `{cp['id']}` [{ts}]: {cp['label']} "
                             f"(files: {cp['file_changes_count']}, msgs: {cp['message_changes_count']}, "
                             f"state: {cp['state_changes_count']}){marker}")
            ctx.renderer.print_markdown("\n".join(lines))
        elif action == "create":
            label = arg.strip() if arg else f"Checkpoint {len(cp_manager.checkpoints) + 1}"
            cp = cp_manager.create_checkpoint(label)
            if cp:
                ctx.renderer.print_success(f"Created checkpoint: `{cp.id}` ({cp.label})")
            else:
                ctx.renderer.print_info("No pending changes to checkpoint.")
        elif action == "undo":
            if arg:
                self._checkpoint_navigate(ctx, cp_manager, arg)
                return
            if not cp_manager.can_undo():
                ctx.renderer.print_warning("Nothing to undo.")
                return
            target = cp_manager.undo()
            if target:
                ctx.renderer.print_success(
                    f"Undone to checkpoint: `{target.id}` ({target.label}) — "
                    f"{self._checkpoint_applied_summary(cp_manager)}"
                )
            else:
                ctx.renderer.print_success("Undone to initial state (no checkpoints).")
        elif action == "redo":
            if arg:
                self._checkpoint_navigate(ctx, cp_manager, arg)
                return
            if not cp_manager.can_redo():
                ctx.renderer.print_warning("Nothing to redo.")
                return
            target = cp_manager.redo()
            if target:
                ctx.renderer.print_success(
                    f"Redone to checkpoint: `{target.id}` ({target.label}) — "
                    f"{self._checkpoint_applied_summary(cp_manager)}"
                )
            else:
                ctx.renderer.print_error("Redo failed.")
        else:
            ctx.renderer.print_info("Usage: /checkpoint [list|create [label]|undo [id]|redo [id]]")

    @staticmethod
    def _checkpoint_navigate(ctx: CommandContext, cp_manager, checkpoint_id: str):
        target, moved = cp_manager.navigate_to(checkpoint_id)
        if target is None:
            ctx.renderer.print_error(f"Checkpoint not found: `{checkpoint_id}` (see /checkpoint list)")
        elif not moved:
            ctx.renderer.print_info(f"Already at checkpoint: `{target.id}` ({target.label})")
        else:
            verb = "Undone" if cp_manager.last_direction() == "undo" else "Redone"
            ctx.renderer.print_success(
                f"{verb} to checkpoint: `{target.id}` ({target.label}) — "
                f"{CommandRegistry._checkpoint_applied_summary(cp_manager)}"
            )

    @staticmethod
    def _checkpoint_applied_summary(cp_manager) -> str:
        """Human-readable summary of what the last undo/redo materialized."""
        last = cp_manager.last_apply_results()
        if not last:
            return "no recorded changes materialized"
        verb = "reverted" if cp_manager.last_direction() == "undo" else "re-applied"
        return (f"{verb} {last.get('messages', 0)} message(s), "
                f"{last.get('states', 0)} state change(s), "
                f"{last.get('files', 0)} file(s)")

    def _cmd_update(self, ctx: CommandContext):
        """Check and apply updates directly from interactive REPL."""
        from harness.core.updater import HarnessUpdater
        updater = HarnessUpdater()
        ctx.renderer.print_info("Checking for Harness updates...")
        info = updater.check_for_updates()
        if info.error:
            ctx.renderer.print_error(f"Update check failed: {info.error}")
            return
        if not info.is_behind:
            ctx.renderer.print_success(f"Harness is up to date (version {info.current_version})")
            return
        if info.is_git:
            ctx.renderer.print_info(f"Update available: [bold yellow]{info.commits_behind} commits[/bold yellow] behind upstream.")
            if info.commits:
                for c in info.commits[:4]:
                    ctx.renderer.console.print(f"  [dim]•[/dim] [white]{c}[/white]")
        else:
            ctx.renderer.print_info(f"New version available: [bold green]{info.latest_version}[/bold green]")

        parts = ctx.args.split()
        if "--check" in parts:
            return

        ctx.renderer.print_info("Applying update...")
        res = updater.apply_update(force="--force" in parts)
        if res.success:
            ctx.renderer.print_success("Successfully updated Harness!")
        else:
            ctx.renderer.print_error(f"Update failed: {res.error or res.message}")

    def _cmd_discord(self, ctx: CommandContext):
        """Manage Discord bot and sync."""
        parts = ctx.args.split(" ", 1)
        sub = parts[0].lower() if parts and parts[0] else "status"
        arg = parts[1].strip() if len(parts) > 1 else ""

        from harness.discord.sync import get_relay
        relay = get_relay()

        if sub == "status":
            token = ctx.agent.config.get_discord_token()
            has_token = bool(token)
            token_display = f"configured ({token[:4]}...{token[-4:]})" if has_token else "not configured"
            status = relay.get_status()
            conn_str = "[green]● Connected[/green]" if status["is_connected"] else "[dim]○ Not connected[/dim]"
            last_sync = status["last_sync_ts"]
            last_str = time.strftime("%H:%M:%S", time.localtime(last_sync)) if last_sync else "never"
            active_ch = status["active_channel"] or "—"
            q_size = ctx.queue.size() if ctx.queue else 0
            lines = [
                "[bold magenta]Discord Bot Status[/bold magenta]",
                f"  [white]Token:[/white]         [dim]{token_display}[/dim]",
                f"  [white]Auto-start:[/white]    [cyan]{'Enabled' if ctx.agent.config.discord_auto_start else 'Disabled'}[/cyan]",
                f"  [white]Permission:[/white]    [green]{ctx.agent.config.discord_permission or 'default'}[/green]",
                f"  [white]Connection:[/white]    {conn_str}",
                f"  [white]Active channel:[/white] [dim]{active_ch}[/dim]",
                f"  [white]Last sync:[/white]     [dim]{last_str}[/dim]",
                f"  [white]Sync Bus:[/white]      [dim]{relay.bus.path}[/dim]",
                f"  [white]Queue:[/white]         [dim]{q_size} pending[/dim]",
                "",
                "[bold magenta]Sync Usage[/bold magenta]",
                "  [dim]Start bot:[/dim]       [bold cyan]/discord start [token][/bold cyan]",
                "  [dim]Stop bot:[/dim]        [bold cyan]/discord stop[/bold cyan]",
                "  [dim]Force sync:[/dim]      [bold cyan]/discord sync[/bold cyan]",
                "  [dim]Configure token:[/dim] [bold cyan]/discord token <bot_token>[/bold cyan]",
            ]
            ctx.renderer.print_modal_card("Discord Integration", lines, shortcuts="start /discord start  stop /discord stop  esc close")
        elif sub == "token":
            if not arg:
                ctx.renderer.print_warning("Usage: /discord token <your_bot_token>")
                return
            ctx.agent.config.set_discord_token(arg)
            from harness.config import save_config
            save_config(ctx.agent.config)
            ctx.renderer.print_success("Saved Discord bot token securely.")
        elif sub == "start":
            tok = arg or ctx.agent.config.get_discord_token()
            if not tok:
                ctx.renderer.print_error("No Discord token provided. Use: /discord start <token> or /discord token <token>")
                return
            from harness.cli import _maybe_start_discord_bot
            ctx.agent.config.discord_auto_start = True
            ctx.agent.config.set_discord_token(tok)
            from harness.config import save_config
            save_config(ctx.agent.config)
            _maybe_start_discord_bot(ctx.agent.config)
            relay.set_connected(True)
            ctx.renderer.print_success("Started background Discord bot daemon.")
        elif sub == "stop":
            ctx.agent.config.discord_auto_start = False
            from harness.config import save_config
            save_config(ctx.agent.config)
            relay.set_connected(False)
            relay.publish_state({"discord_stop": True}, origin="cli")
            ctx.renderer.print_success("Discord auto-start disabled. Restart Harness to fully stop background bot (daemon threads exit on process exit).")
        elif sub == "sync":
            relay.publish_state({"manual_sync": True, "ts": time.time()}, origin="cli")
            ctx.renderer.print_success("Broadcasted manual sync pulse to Discord bus.")
        else:
            ctx.renderer.print_info("Usage: /discord [status|start [token]|stop|sync|token <val>]")

    def _cmd_diff(self, ctx: CommandContext):
        res = ctx.agent.tool_registry.execute("git_diff", {}, ctx.agent.mode)
        ctx.renderer.print_diff(res)

    def _cmd_clear(self, ctx: CommandContext):
        ctx.renderer.clear_screen()
        ctx.renderer.print_welcome_splash(ctx.agent, queue=ctx.queue)

    def _cmd_exit(self, ctx: CommandContext):
        ctx.renderer.print_info("Exiting Harness. Goodbye!")
        import sys
        sys.exit(0)
