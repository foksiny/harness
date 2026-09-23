"""
Core Agent Engine for Harness.
Coordinates autonomous reasoning loops, tool invocations, Super Mode iterations,
and session state persistence.
"""
import json
import os
import threading
import time
from typing import Dict, Any, List, Optional, Callable, Generator, Tuple, Iterator
from harness.core.modes import Mode
from harness.core.permissions import PermissionManager, PermissionLevel
from harness.core.prompt import SystemPromptBuilder, ULTRA_GOAL_MARKER
from harness.core.compaction import Compactor, TokenStats
from harness.core.context_budget import ContextBudget, PRESSURE_WARNING, PRESSURE_CRITICAL
from harness.core.todo import TodoManager, TaskItem
from harness.core.session import Session, SessionManager
from harness.core.checkpoints import CheckpointManager, get_checkpoint_manager, set_checkpoint_manager
from harness.core.subagents import SubagentOrchestrator
from harness.core.learning import LearningManager
from harness.core.attachments import parse_attachments
from harness.core.mentions import expand_mentions
from harness.providers.base import BaseProvider, LLMChunk, ToolCallDelta, is_fatal_provider_error, is_empty_stream_error
from harness.providers import get_provider
from harness.tools import ToolRegistry
from harness.skills.loader import SkillsManager
from harness.mcp.manager import MCPManager
from harness.config import HarnessConfig

class AgentEvent:
    def __init__(self, event_type: str, data: Any = None):
        self.type = event_type
        self.data = data

class HarnessAgent:
    """Master agent coordinating tool execution, provider streaming, and task loops."""

    def __init__(
        self,
        config: HarnessConfig,
        session: Optional[Session] = None,
        event_callback: Optional[Callable[[AgentEvent], None]] = None,
        ask_user_handler: Optional[Callable] = None,
        learning_manager: Optional[LearningManager] = None,
    ):
        self.config = config
        self.mode = Mode.from_string(config.mode)
        self.permission_manager = PermissionManager(PermissionLevel.from_string(config.permission))
        self.todo_manager = TodoManager()
        self.subagent_orchestrator = SubagentOrchestrator(self._run_subagent_task)
        self.skills_manager = SkillsManager()
        self.learning_manager = learning_manager if learning_manager is not None else LearningManager()
        # Keep promoted lessons immediately visible to the skill catalog.
        self.learning_manager.set_reload_hook(self.skills_manager.reload)
        self.tool_registry = ToolRegistry(
            permission_manager=self.permission_manager,
            todo_manager=self.todo_manager,
            subagent_orchestrator=self.subagent_orchestrator,
            skills_manager=self.skills_manager,
            ask_user_handler=ask_user_handler,
            learning_manager=self.learning_manager,
            learning_enabled=config.learning_enabled,
            browser_enabled=getattr(config, "browser_enabled", True),
        )
        # Debounce heuristic auto-learning so at most one learned lesson is
        # captured per session.
        self._auto_learned_session = None
        self.mcp_manager = MCPManager(self.tool_registry)
        self.mcp_manager.load_and_connect()

        # Initialize checkpoint manager
        self.checkpoint_manager = get_checkpoint_manager()
        set_checkpoint_manager(self.checkpoint_manager)
        # Wire undo/redo application so /checkpoint undo|redo materializes the
        # recorded changes against the live session, todos, and filesystem.
        self.checkpoint_manager.set_applicators(
            apply_file_change=self._checkpoint_apply_file,
            apply_message_change=self._checkpoint_apply_message,
            apply_state_change=self._checkpoint_apply_state,
        )
        # Persist checkpoint changes to disk and keep session in sync
        try:
            self.checkpoint_manager.add_change_listener(self._on_checkpoint_persist)
        except Exception:
            pass

        self.provider: BaseProvider = get_provider(config.provider, config)
        # Test seam: when set, the vision-fallback provider is used verbatim
        # instead of re-instantiating one from config.vfb_provider.
        self._vfb_provider_override: Optional[BaseProvider] = None
        self.session_manager = SessionManager()
        self.session: Optional[Session] = session
        # Bind checkpoint persistence to current session if any
        if self.session is not None:
            try:
                self.checkpoint_manager.bind_session(self.session.id)
            except Exception:
                pass

        initial_model = self.session.model if self.session is not None else config.model
        model_spec = self.provider.get_model_spec(initial_model)
        self.compactor = Compactor(
            context_window=model_spec.context_window,
            threshold_ratio=config.compact_threshold,
            target_ratio=config.compact_target_ratio,
            cap_ratio=config.compact_cap_ratio,
            preserve_min_turns=config.compact_preserve_turns,
            max_message_tokens=config.compact_max_message_tokens,
            summarize_fn=self._summarize_block,
            summary_mode=config.compact_summary,
        )
        # Proactive context-budget: real-time pressure gauge + per-tool output caps.
        self.context_budget = ContextBudget(
            context_window=model_spec.context_window,
            threshold_ratio=config.compact_threshold,
            cap_ratio=config.compact_cap_ratio,
        )
        self.event_callback = event_callback
        self.prompt_builder = SystemPromptBuilder(self.mode, self.permission_manager.level)
        self.is_running = False
        # Cooperative stop: set from any thread (TUI Ctrl-C handler, Discord
        # /stop) to interrupt the running turn at the next safe checkpoint.
        self._stop_requested = threading.Event()

        # Wire concurrent swarm workers: each worker thread builds its own provider
        # for the currently selected provider/model, propagates the parent's mode so
        # plan-mode restrictions are enforced, and never blocks on approval prompts.
        self.subagent_orchestrator.set_swarm_runtime(
            provider_factory=lambda: get_provider(self.provider.name, self.config),
            model=initial_model,
            parent_mode=self.mode,
            interactive_deny_fn=lambda deny: setattr(self.permission_manager, "interactive_deny", deny),
        )

    def emit(self, event_type: str, data: Any = None):
        if self.event_callback:
            self.event_callback(AgentEvent(event_type, data))

    def request_stop(self) -> None:
        """Request a cooperative interrupt of the current turn (thread-safe).

        Safe to call from any thread (TUI, Discord /stop, timers). The running
        ``step()`` generator notices it at the next safe checkpoint — between
        provider chunks, between tool calls, or right before a new provider
        request — and winds down cleanly: pending tool calls are answered with a
        synthetic "interrupted" result so the transcript stays consistent.
        """
        self._stop_requested.set()

    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def clear_stop(self) -> None:
        """Clear a pending stop request before starting a new turn."""
        self._stop_requested.clear()

    # ── Exploration watchdog ──────────────────────────────────────────
    # A model can spiral: dozens of consecutive read-only calls (ls/cat/find)
    # re-orienting itself, each with huge reasoning, burning the whole turn
    # without producing anything. When the streak crosses the threshold the
    # agent injects a system nudge forcing it back to concrete action.
    _EXPLORATION_TOOLS = frozenset({
        "view_file", "list_dir", "find_files", "grep_search", "git_status",
        "git_diff", "web_search", "list_skills", "read_skill", "learn_recall",
        "mesh_status", "mesh_list_peers", "mesh_read_messages",
        "swarm_read_messages", "todo_list", "ask_user",
    })
    _READ_ONLY_COMMAND_WORDS = frozenset({
        "ls", "cat", "cd", "pwd", "echo", "find", "grep", "rg", "head",
        "tail", "wc", "which", "whoami", "stat", "file", "du", "df",
        "printenv", "type", "less", "more", "uname", "id", "date", "tree",
    })
    _READ_ONLY_GIT_SUBCOMMANDS = ("status", "diff", "log", "show", "branch")
    _COMMAND_MUTATION_MARKERS = (
        ">", "<<", "&&", ";", "|", "&", "rm ", "mv ", "cp ", "mkdir ",
        "touch ", "tee ", "chmod ", "chown ", "sed ", "awk ", "python",
        "node ", "npm ", "npx ", "pip ", "docker ", "kill", "sudo",
        "apt ", "tar ", "zip ", "make ", "git ",
    )

    def _is_exploration_call(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Heuristic: is this tool call purely read-only exploration?

        Only clearly read-only calls count. Anything ambiguous (pipes, chains,
        redirections, script runners, git anything-but-read) counts as action
        and resets the streak — false negatives merely delay the nudge, false
        positives would interrupt legitimate work.
        """
        if tool_name in self._EXPLORATION_TOOLS:
            return True
        if tool_name != "run_command":
            return False
        cmd = str((args or {}).get("command", "")).strip()
        if not cmd:
            return False
        lowered = cmd.lower()
        parts = lowered.split()
        first = parts[0].strip(";()") if parts else ""
        if first == "git" and len(parts) > 1 and parts[1] in self._READ_ONLY_GIT_SUBCOMMANDS:
            markers = tuple(m for m in self._COMMAND_MUTATION_MARKERS if m != "git ")
            return not any(m in lowered for m in markers)
        if first not in self._READ_ONLY_COMMAND_WORDS:
            return False
        return not any(m in lowered for m in self._COMMAND_MUTATION_MARKERS)

    def _stream_interruptible(self, stream_factory: Callable[[], Iterator[LLMChunk]]) -> Iterator[LLMChunk]:
        """Yield provider chunks while staying responsive to stop requests.

        The provider stream blocks inside network reads that may legally sit
        silent for the whole SSE read timeout (300s by default, to tolerate
        long reasoning stalls). Previously the stop flag was only checked
        *between* chunks — so when a stream stalled with no bytes arriving,
        /stop and Ctrl-C appeared completely dead until the socket timed out.

        This wrapper pumps the provider generator on a daemon thread and
        forwards chunks through a queue. The consumer polls the queue with a
        short timeout and checks the stop flag between polls, so a stop
        request is honored within ~0.25s even while mid-stream. On stop, the
        provider's in-flight HTTP stream is hard-aborted (socket teardown),
        which promptly unblocks and retires the pump thread instead of
        letting it linger for the read timeout.
        """
        import queue as _pyqueue

        q: "_pyqueue.Queue" = _pyqueue.Queue()
        done_sentinel = object()  # generator finished cleanly

        stop_event = self._stop_requested
        provider = self.provider

        def _pump() -> None:
            try:
                for chunk in stream_factory():
                    if stop_event.is_set():
                        break
                    q.put(chunk)
            except BaseException as exc:  # forwarded to the consumer thread
                try:
                    q.put(exc)
                except Exception:
                    pass
                return
            try:
                q.put(done_sentinel)
            except Exception:
                pass

        pump = threading.Thread(target=_pump, daemon=True, name="harness-stream-pump")
        pump.start()

        try:
            while True:
                if self._stop_requested.is_set():
                    return  # finally tears the pump's stream down
                try:
                    item = q.get(timeout=0.25)
                except _pyqueue.Empty:
                    continue
                if item is done_sentinel:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            # Any exit (stop, consumer close/GC, exception) must hard-abort
            # the in-flight HTTP stream so the pump's blocked read raises and
            # the daemon thread retires NOW instead of lingering for the
            # read timeout. On normal completion this is a no-op (the
            # provider already unregistered its streams).
            abort = getattr(provider, "abort_active_streams", None)
            if callable(abort):
                try:
                    abort()
                except Exception:
                    pass

    def ensure_session(self) -> Session:
        """Create the active session on the user's first message."""
        if self.session is None:
            self.session = self.session_manager.create(
                provider=self.config.provider,
                model=self.config.model,
                mode=self.mode.value,
                permission=self.permission_manager.level.value,
                thinking_effort=self.config.thinking_effort,
            )
            self._bind_checkpoint_session(self.session)
        else:
            # Restore todos from existing session (e.g., on resume)
            self._restore_todos_from_session()
            self._bind_checkpoint_session(self.session)
        return self.session

    def set_mode(self, mode: Mode):
        self.mode = mode
        self.prompt_builder.mode = mode
        if self.session is not None:
            self.session.mode = mode.value
        self.subagent_orchestrator.parent_mode = mode
        self.emit("mode_change", mode.value)

    def set_permission(self, perm: PermissionLevel):
        self.permission_manager.set_level(perm)
        self.prompt_builder.permission = perm
        if self.session is not None:
            self.session.permission = perm.value
        self.emit("permission_change", perm.value)

    def set_provider(self, provider_name: str, model_name: Optional[str] = None):
        self.provider = get_provider(provider_name, self.config)
        new_model = model_name or self.provider.default_model
        if self.session is not None:
            self.session.provider = provider_name
            self.session.model = new_model
        self.subagent_orchestrator.model = new_model
        model_spec = self.provider.get_model_spec(new_model)
        self.compactor.context_window = model_spec.context_window
        if self.config.compact_max_message_tokens == 0:
            self.compactor.max_message_tokens = int(model_spec.context_window * 0.15)
        self.context_budget.rebind(
            context_window=model_spec.context_window,
            threshold_ratio=self.config.compact_threshold,
            cap_ratio=self.config.compact_cap_ratio,
        )
        self.config.model = new_model
        self.emit("provider_change", {"provider": provider_name, "model": new_model})

    def _run_subagent_task(
        self,
        system_prompt: str,
        prompt: str,
        allowed_tools: List[str],
        max_turns: int,
        provider_factory: Optional[Callable[[], BaseProvider]] = None,
        model: Optional[str] = None,
        mode: Optional[Mode] = None,
        agent_id: Optional[str] = None,
        agent_type: Optional[str] = None,
    ) -> tuple[str, int]:
        """Subagent runner executing with isolated conversation context.

        When dispatched by a swarm worker thread, ``provider_factory`` supplies a
        fresh provider instance so concurrent threads never share stream state, and
        ``mode`` propagates the parent's operational restrictions into worker tools.

        Every step is recorded on the orchestrator (for live TUI visibility) and the
        returned report always includes a compact action log, so the parent thread
        can see exactly what the worker did even when its final reply was terse.
        """
        aid = agent_id or f"{agent_type or 'general'}_1"
        sub_messages = [{"role": "user", "content": prompt}]
        accumulated_output = ""
        action_log: List[str] = []
        turns = 0

        provider = provider_factory() if provider_factory else self.provider
        effective_model = model or (self.session.model if self.session is not None else self.config.model)
        exec_mode = mode if mode is not None else Mode.BUILD

        def _finish_report(summary: str) -> tuple[str, int]:
            report = summary.strip()
            if action_log:
                report += "\n\n[Actions performed by this agent]\n" + "\n".join(action_log)
            return report, turns

        for _ in range(max_turns):
            turns += 1
            text_acc = ""
            tool_calls_acc: Dict[int, Dict[str, Any]] = {}

            # Prepare tool schemas for subagent
            schemas = []
            for tname in allowed_tools:
                t = self.tool_registry.get(tname)
                if t:
                    schemas.append(t.to_openai_schema())

            for chunk in provider.stream_chat(
                messages=sub_messages,
                model=effective_model,
                thinking_effort="low",
                tools=schemas if schemas else None,
                system_prompt=system_prompt,
            ):
                if chunk.delta_text:
                    text_acc += chunk.delta_text
                for tc in chunk.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": tc.id or f"tc_{idx}", "name": tc.name or "", "arguments": ""}
                    if tc.name:
                        tool_calls_acc[idx]["name"] = tc.name
                    if tc.arguments_delta:
                        tool_calls_acc[idx]["arguments"] += tc.arguments_delta

            if text_acc:
                self.subagent_orchestrator.subagent_text(aid, text_acc)
                accumulated_output += text_acc + "\n"

            if not tool_calls_acc:
                break # Model concluded its response

            # Execute tool calls
            sub_messages.append({"role": "assistant", "content": text_acc, "tool_calls": [
                {"id": v["id"], "type": "function", "function": {"name": v["name"], "arguments": v["arguments"]}}
                for v in tool_calls_acc.values()
            ]})

            for v in tool_calls_acc.values():
                tname = v["name"]
                if tname == "finish":
                    try:
                        fargs = json.loads(v["arguments"]) if v["arguments"] else {}
                    except Exception:
                        fargs = {}
                    summary = str(fargs.get("summary", "")).strip() or "Task complete."
                    accumulated_output += summary + "\n"
                    return _finish_report(summary)
                try:
                    args = json.loads(v["arguments"]) if v["arguments"] else {}
                except Exception:
                    args = {}
                res = self.tool_registry.execute(tname, args, exec_mode)

                # Live visibility + durable record of everything the worker does.
                if tname == "swarm_send_message":
                    self.subagent_orchestrator.subagent_message(
                        aid, aid, str(args.get("recipient", "all")), str(args.get("message", ""))
                    )
                self.subagent_orchestrator.subagent_tool(aid, tname, str(args)[:200], str(res)[:200])
                first_line = (str(res).strip().split("\n")[0] if str(res).strip() else "")[:200]
                action_log.append(f"- {tname} {str(args)[:160]}" + (f" => {first_line}" if first_line else ""))

                sub_messages.append({"role": "tool", "tool_call_id": v["id"], "name": tname, "content": res})

        return _finish_report(accumulated_output)

    def _checkpoint_apply_file(self, fc, undo: bool) -> bool:
        """Materialize a file change against the filesystem (undo/redo)."""
        from pathlib import Path
        try:
            p = Path(fc.path)
            if undo:
                if fc.action == "create":
                    # Reverse of create is delete
                    if p.exists():
                        p.unlink()
                        return True
                    return False
                if fc.action == "edit":
                    # Reverse of edit is restore previous content
                    p.write_text(fc.old_content or "", encoding="utf-8")
                    return True
                if fc.action == "delete":
                    # Reverse of delete is recreate the file
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(fc.old_content or "", encoding="utf-8")
                    return True
            else:
                if fc.action == "create":
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(fc.new_content or "", encoding="utf-8")
                    return True
                if fc.action == "edit":
                    p.write_text(fc.new_content or "", encoding="utf-8")
                    return True
                if fc.action == "delete":
                    if p.exists():
                        p.unlink()
                        return True
                    return False
        except Exception:
            return False
        return False

    def _checkpoint_apply_message(self, mc, undo: bool) -> bool:
        """Materialize a message history change against the live session (undo/redo)."""
        if self.session is None:
            return True  # No session - treat as applied to allow pointer move
        msgs = self.session.messages
        try:
            if mc.action == "append":
                if undo:
                    idx = mc.index
                    # Strict check first, then lenient fallback search
                    if 0 <= idx < len(msgs) and msgs[idx] == mc.new_message:
                        del msgs[idx]
                        return True
                    # Fallback: search nearby for the message to remove (handles index shift after compaction)
                    try:
                        found = msgs.index(mc.new_message)
                        del msgs[found]
                        return True
                    except ValueError:
                        # Last resort: if message at index is close, or remove last matching role/content
                        for i in range(max(0, idx-2), min(len(msgs), idx+3)):
                            if msgs[i].get("content") == mc.new_message.get("content") and msgs[i].get("role") == mc.new_message.get("role"):
                                del msgs[i]
                                return True
                        return False
                # Redo: insert at clamped index, avoid duplicate
                if mc.new_message in msgs:
                    return True
                msgs.insert(min(max(0, mc.index), len(msgs)), mc.new_message)
                return True
            if mc.action == "truncate":
                if undo:
                    # Restore the compacted messages back into the history.
                    idx = min(max(0, mc.index), len(msgs))
                    msgs[idx:idx] = list(mc.removed_messages)
                    return True
                # Redo: clamp to available length, be lenient
                if mc.index >= len(msgs):
                    return True  # Already truncated
                end = min(mc.index + mc.count, len(msgs))
                del msgs[mc.index:end]
                return True
            if mc.action == "replace":
                if mc.index < 0 or mc.index >= len(msgs):
                    # Lenient: if index out of range on redo and old==new, treat as applied
                    return False
                # Lenient: allow replace even if current doesn't exactly match expected old
                msgs[mc.index] = mc.old_message if undo else mc.new_message
                return True
            if mc.action == "span_replace":
                # Swap a whole contiguous span (graduated compaction / emergency trim).
                # Undo starts from the applied (new) span; redo starts from the old one.
                expected = mc.new_messages if undo else mc.old_messages
                replaced = mc.old_messages if undo else mc.new_messages
                # Strict check first
                if msgs[mc.index:mc.index + len(expected)] == expected:
                    msgs[mc.index:mc.index + len(expected)] = list(replaced)
                    return True
                # Lenient fallback: search for expected span elsewhere
                if not expected:
                    msgs[mc.index:mc.index] = list(replaced)
                    return True
                # Try to find expected by content search
                for start in range(max(0, len(msgs)-len(expected)+1)):
                    if msgs[start:start+len(expected)] == expected:
                        msgs[start:start+len(expected)] = list(replaced)
                        return True
                # Last resort: if undo and new span not found, assume already undone
                # or if redo and old span not found, assume already redone
                # Check if replaced already present
                if msgs[mc.index:mc.index+len(replaced)] == replaced:
                    return True
                # Force replace at index clamped
                idx = min(max(0, mc.index), len(msgs))
                # Remove expected length if possible, insert replaced
                del msgs[idx:idx+len(expected)]
                msgs[idx:idx] = list(replaced)
                return True
        except Exception:
            return False
        return False

    def _record_message_span_change(self, old_messages: List[Dict[str, Any]], new_messages: List[Dict[str, Any]]):
        """Record a graduated compaction / emergency-trim as one atomic, undoable span swap."""
        self.checkpoint_manager.record_message_replace_span(0, old_messages, new_messages)

    def _checkpoint_apply_state(self, sc, undo: bool) -> bool:
        """Materialize a state change (e.g. todos) against live agent state (undo/redo)."""
        if not sc.key.startswith("todo."):
            # Unknown state keys — nothing to materialize; treat as applied.
            return True
        try:
            task_id = int(sc.key.split(".", 1)[1])
        except (ValueError, IndexError):
            return False
        value = sc.old_value if undo else sc.new_value

        # Reverting a todo create removes the task; reverting a delete re-creates it.
        if undo and sc.action == "create":
            self.todo_manager.remove_task(task_id)
            return True
        if value is None:
            self.todo_manager.remove_task(task_id)
            return True
        data = dict(value or {})
        data["id"] = task_id
        self.todo_manager.restore_task(TaskItem.from_dict(data))
        return True

    def _get_vfb_provider(self) -> BaseProvider:
        if self._vfb_provider_override is not None:
            return self._vfb_provider_override
        return get_provider(self.config.vfb_provider, self.config)

    def _run_vision_fallback(
        self, media_blocks: List[Dict[str, Any]], user_prompt: Optional[str] = None
    ) -> Generator[AgentEvent, None, Tuple[Optional[str], str]]:
        """Ask the configured vision-fallback provider/model to describe the given
        media as text for a non-visual model. Yields UX events, returns a tuple of
        (description, error) where exactly one is set on completion.

        When ``user_prompt`` is provided, it is used to guide the VFB description
        so the model focuses on what the user actually asked about."""
        vfb_provider = self._get_vfb_provider()
        vfb_model = self.config.vfb_model.strip() or vfb_provider.default_model
        display = getattr(vfb_provider, "display_name", getattr(vfb_provider, "name", "vision fallback"))
        labels = "/".join(sorted({b["type"] for b in media_blocks}))
        files = [b.get("path") or b.get("data_uri", "")[:72] for b in media_blocks]

        try:
            vfb_spec = vfb_provider.get_model_spec(vfb_model)
        except Exception:
            vfb_spec = None

        if vfb_spec is None:
            reason = (
                f"vision fallback provider '{self.config.vfb_provider.strip()}' could not "
                f"resolve capabilities for model '{vfb_model}'"
            )
            return (None, reason)

        if labels == "video":
            capable = bool(vfb_spec.supports_video)
        elif labels == "image":
            capable = bool(vfb_spec.supports_vision)
        else:
            capable = bool(vfb_spec.supports_vision and vfb_spec.supports_video)

        if not capable:
            reason = f"vision fallback model '{vfb_model}' is not detected as {labels}-capable"
            return (None, reason)

        yield AgentEvent("vfb_notice", {
            "provider": display,
            "model": vfb_model,
            "labels": labels,
            "files": files,
        })

        # Build a context-aware system prompt that incorporates the user's question
        base_system = (
            "You are a precise vision-description sub-agent. Describe each provided "
            "file in exhaustive objective detail so that a text-only model which "
            "cannot see it can fully understand it: subjects, actions, spatial layout, "
            "colors, quantities, any readable text or labels, diagrams, and notable "
            "defects. Be literal and complete rather than brief."
        )
        if user_prompt and user_prompt.strip():
            system_prompt = (
                f"{base_system}\n\n"
                f"The user's request is: {user_prompt.strip()}\n"
                f"Focus your description on details relevant to the user's request, "
                f"but still provide comprehensive coverage of the image content."
            )
        else:
            system_prompt = base_system

        # Build user message - if user has a question, use it to guide the description
        if user_prompt and user_prompt.strip():
            user_text = (
                f"The user asked: {user_prompt.strip()}\n\n"
                f"Describe the attached image(s) with focus on answering their question "
                f"while providing full contextual detail."
            )
        else:
            user_text = "Describe each of the following files precisely:"

        content: List[Dict[str, Any]] = [
            {"type": "text", "text": user_text},
            *media_blocks,
        ]

        try:
            parts: List[str] = []
            failed = False
            for chunk in vfb_provider.stream_chat(
                messages=[{"role": "user", "content": content}],
                model=vfb_model,
                thinking_effort="off",
                tools=[],
                system_prompt=system_prompt,
            ):
                if chunk.finish_reason == "error":
                    failed = True
                    continue
                if chunk.delta_text:
                    parts.append(chunk.delta_text)
            description = "".join(parts).strip()
            if failed or not description:
                return (None, f"{display} ({vfb_model}) returned no usable description")
        except Exception as exc:
            return (None, f"{display} ({vfb_model}) failed while describing: {exc}")

        yield AgentEvent("vfb_result", {
            "provider": display,
            "model": vfb_model,
            "labels": labels,
            "files": files,
        })
        return (description, "")

    def _describe_tool_images(
        self, media_blocks: List[Dict[str, Any]], question: Optional[str] = None
    ) -> Tuple[list, Optional[str], str]:
        """Run the vision fallback over images produced by a tool result.

        Returns (events, description, error): the UX events to replay into the
        turn, plus exactly one of description/error set on completion.
        """
        events: list = []
        description, err = None, ""
        gen = self._run_vision_fallback(media_blocks, user_prompt=question)
        try:
            while True:
                try:
                    events.append(next(gen))
                except StopIteration as stop:
                    description, err = stop.value or (None, "")
                    break
        except Exception:
            err = "vision fallback crashed unexpectedly"
        return events, description, err

    def step(self, user_prompt: Optional[str] = None) -> Generator[AgentEvent, None, None]:
        """Execute a single or multi-step agent turn, yielding live events."""
        self.is_running = True
        self.clear_stop()
        tools_executed_this_turn = 0

        if user_prompt is not None and not user_prompt.strip():
            user_prompt = None

        if user_prompt:
            self.ensure_session()

            # ── Security gate: input length validation ────────────────────
            from harness.core.security import validate_input_length, MAX_TUI_INPUT_LENGTH
            ok, err = validate_input_length(user_prompt, MAX_TUI_INPUT_LENGTH, "user prompt")
            if not ok:
                yield AgentEvent("error", {"message": err})
                self.is_running = False
                return

            # ── Security gate: prompt injection detection ─────────────────
            from harness.core.security import detect_injection, sanitize_input
            severity, matches = detect_injection(user_prompt)
            if severity == "block":
                yield AgentEvent("error", {
                    "message": (
                        "⚠️  Security: Prompt blocked — potential injection detected. "
                        f"Matched patterns: {', '.join(matches[:3])}. "
                        "If this is a legitimate request, rephrase without instructions like "
                        "'ignore previous', 'you are now', or 'system prompt'."
                    )
                })
                self.is_running = False
                return
            elif severity == "warn":
                yield AgentEvent("security_warning", {
                    "message": f"⚠️  Security note: suspicious patterns detected ({', '.join(matches[:2])}). Proceeding with caution."
                })

            clean_text, media_blocks, attach_warnings = parse_attachments(user_prompt)
            clean_text = sanitize_input(clean_text)
            # Expand @-mentions after media removal so summaries don't trigger further media detection
            clean_text, mention_warnings, mentions = expand_mentions(clean_text, cwd=os.getcwd())
            if mentions:
                # Emit UX events for each mention
                for m in mentions:
                    if m.get("success"):
                        self.emit("mention", {
                            "original": m["original"],
                            "resolved": m["resolved"],
                            "kind": m["kind"],
                        })
                    else:
                        self.emit("mention_warning", {
                            "original": m["original"],
                            "resolved": m["resolved"],
                            "message": m.get("message", "unknown error"),
                        })
            if mention_warnings:
                attach_warnings.extend(mention_warnings)
            model_spec = self.provider.get_model_spec(self.session.model)
            supported, degraded = [], []
            for b in media_blocks:
                if b["type"] == "image":
                    ok = model_spec.supports_vision or bool(self.config.force_media_attach)
                else:
                    ok = model_spec.supports_video
                (supported if ok else degraded).append(b)

            # Vision fallback (VFB): when a vision-capable auxiliary provider/model
            # is configured, degraded media are described there and the written
            # description is embedded as text so the non-vision model understands
            # the image without actually seeing it. Progress/result events keep the
            # user informed that the fallback model is being used.
            vfb_description, vfb_err = None, ""
            if degraded and self.config.vfb_provider.strip():
                vfb_gen = self._run_vision_fallback(degraded, user_prompt=clean_text)
                try:
                    while True:
                        try:
                            sub_ev = next(vfb_gen)
                        except StopIteration as stop:
                            vfb_description, vfb_err = stop.value or (None, "")
                            break
                        yield sub_ev
                except Exception:
                    vfb_err = "vision fallback crashed unexpectedly"

            if degraded:
                text = clean_text
                if vfb_description:
                    text += "\n\n[Vision fallback description of the attached file(s):\n" + vfb_description + "\n]"
                else:
                    for b in degraded:
                        label = "video" if b["type"] == "video" else "image"
                        text += f"\n[{label} file attached: {b.get('path') or b.get('data_uri', '')[:72]}]"
            else:
                text = clean_text

            if supported:
                content_parts: List[Dict[str, Any]] = []
                if text.strip():
                    content_parts.append({"type": "text", "text": text})
                for b in supported:
                    if b.get("path"):
                        content_parts.append({"type": b["type"], "path": b["path"]})
                    else:
                        content_parts.append({"type": b["type"], "data_uri": b["data_uri"]})
                user_content = content_parts
                yield AgentEvent("attachment", {"files": [
                    {"type": b["type"], "path": b.get("path") or b.get("data_uri", "")} for b in supported
                ]})
            else:
                user_content = text if vfb_description else user_prompt

            if attach_warnings or (degraded and not vfb_description):
                notes = list(attach_warnings)
                if degraded and not vfb_description:
                    kinds = "/".join(sorted({b["type"] for b in degraded}))
                    if vfb_err:
                        notes.insert(
                            0,
                            f"{vfb_err}; file(s) included as a text path reference — "
                            f"configure a vision-capable vision fallback model or switch to a vision model",
                        )
                    else:
                        notes.insert(
                            0,
                            f"model '{self.session.model}' is not detected as {kinds}-capable, so the "
                            f"file was included as a text path reference — switch to a vision model "
                            f"if it fails to interpret the image",
                        )
                yield AgentEvent("attachment_warning", {"message": "; ".join(notes)})

            self.session.messages.append({"role": "user", "content": user_content})
            self.checkpoint_manager.record_message_append(len(self.session.messages) - 1, self.session.messages[-1])

        # Skills-first: eagerly consult the skill catalog via the list_skills tool at the
        # start of every task so the model always sees available skills before working,
        # then it can read_skill any skill that matches the user's use case.
        already_seeded = any(
            m.get("role") == "tool" and m.get("name") == "list_skills"
            for m in self.session.messages
        )
        if not already_seeded and self.skills_manager.list_skills():
            skill_result = self.tool_registry.execute("list_skills", {}, self.mode)
            skill_call_id = f"list_skills_{int(time.time() * 1000)}"
            self.session.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": skill_call_id,
                    "type": "function",
                    "function": {"name": "list_skills", "arguments": "{}"},
                }],
            })
            self.session.messages.append({
                "role": "tool",
                "tool_call_id": skill_call_id,
                "name": "list_skills",
                "content": skill_result,
            })
            yield AgentEvent("tool_call_start", {"name": "list_skills", "arguments": {}})
            yield AgentEvent("tool_call_result", {"name": "list_skills", "result": skill_result})

        # Auto-compaction check (turn boundary)
        sys_prompt = self._build_system_prompt(user_prompt or "")
        if self.config.auto_compact and self.compactor.should_compact(self.session.messages, sys_prompt):
            old_messages = list(self.session.messages)
            compacted_msgs, stats = self.compactor.compact(self.session.messages, sys_prompt)
            if stats.get("compacted") and compacted_msgs != old_messages:
                self._record_message_span_change(old_messages, compacted_msgs)
                self.session.messages = compacted_msgs
                yield AgentEvent("compaction", stats)

        # Loop for tool executions. There is no hard step budget — iteration
        # stops when the model delivers a text-only answer or calls the `finish`
        # tool. Empty responses only get re-prompted, never end the turn.
        current_loop = 0
        empty_streak = 0
        MAX_EMPTY_RETRIES = 2
        # SUPER mode: narration without tool calls must not abandon the goal —
        # challenge it once; a second consecutive text-only reply ends the turn.
        super_text_only_streak = 0
        MAX_SUPER_TEXT_NUDGES = 1
        # Exploration watchdog: consecutive read-only calls without action.
        exploration_streak = 0
        MAX_EXPLORATION_STREAK = 10

        # Create checkpoint at start of turn
        self.checkpoint_manager.create_checkpoint(f"Turn {len(self.session.messages) // 2 + 1} start")

        while self.is_running:
            if self._stop_requested.is_set():
                yield AgentEvent("text_delta", "\n[Harness] Turn interrupted by user.\n")
                yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                break
            current_loop += 1
            active_tools = self.tool_registry.get_openai_schemas(self.mode)

            yield AgentEvent("step_start", {"step": current_loop, "mode": self.mode.value})

            # Proactive budget: compact BEFORE sending so the model never sees a
            # window that is about to overflow. Yields a compaction event when it
            # acts so the caller can surface it.
            for ev in self._proactive_budget_check(sys_prompt):
                yield ev

            # ── Provider stream with agent-level retry ───────────────────
            # Providers retry transient errors *before the first byte*, but a
            # stream that dies MID-WAY (e.g. a read timeout after long thinking)
            # cannot be resumed there. Nothing from a failed attempt is
            # committed to the session yet, so the whole model call is retried
            # here with a fresh request and the partial accumulators discarded.
            provider_retries = max(0, int(getattr(self.config, "provider_max_retries", 3)))
            provider_base_delay = max(0.1, float(getattr(self.config, "provider_retry_base_delay", 5.0)))
            stream_attempt = 0
            while True:
                text_accumulator = ""
                reasoning_accumulator = ""
                tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}
                # Indices already announced as "generating" for this attempt,
                # so each in-progress tool call emits exactly one delta event
                # the moment its name becomes known.
                announced_generating: Dict[int, str] = {}
                error_accumulator = ""
                stream_iter = self._stream_interruptible(
                    lambda: self.provider.stream_chat(
                        messages=self.session.messages,
                        model=self.session.model,
                        thinking_effort=self.config.thinking_effort,
                        tools=active_tools,
                        system_prompt=sys_prompt,
                    )
                )
                try:
                    for chunk in stream_iter:
                        if chunk.finish_reason == "error":
                            error_accumulator += chunk.delta_text or ""
                            yield AgentEvent("text_delta", chunk.delta_text or f"\n[Error from {self.provider.display_name}: unknown provider error]\n")
                            continue

                        if chunk.delta_reasoning:
                            reasoning_accumulator += chunk.delta_reasoning
                            yield AgentEvent("reasoning_delta", chunk.delta_reasoning)

                        if chunk.delta_text:
                            text_accumulator += chunk.delta_text
                            yield AgentEvent("text_delta", chunk.delta_text)

                        for tc in chunk.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.id or f"tc_{idx}_{time.time()}",
                                    "name": tc.name or "",
                                    "arguments": "",
                                }
                            if tc.id:
                                # Prefer a real provider id once it arrives
                                # (first chunk may carry only the index).
                                tool_calls_accumulator[idx]["id"] = tc.id
                            if tc.name:
                                tool_calls_accumulator[idx]["name"] = tc.name
                            if tc.arguments_delta:
                                tool_calls_accumulator[idx]["arguments"] += tc.arguments_delta
                            # Live "generating" detection: the moment a tool
                            # name is known for an index, announce it so CLI
                            # renderers can show *which* tool is being built
                            # while its arguments are still streaming in.
                            current_name = tool_calls_accumulator[idx]["name"]
                            if current_name and announced_generating.get(idx) != current_name:
                                announced_generating[idx] = current_name
                                yield AgentEvent("tool_call_delta", {
                                    "index": idx,
                                    "id": tool_calls_accumulator[idx]["id"],
                                    "name": current_name,
                                })

                        if self._stop_requested.is_set():
                            break
                except Exception as exc:
                    # Provider raised instead of yielding an error chunk.
                    error_accumulator += f"\n[Unexpected Error from {self.provider.display_name}: {exc}]\n"
                    yield AgentEvent("text_delta", f"\n[Unexpected Error from {self.provider.display_name}: {exc}]\n")
                finally:
                    # Promptly retire the pump on ANY exit (stop break, error,
                    # completion): close() runs the wrapper's finally, which
                    # aborts any still-open HTTP stream instead of letting the
                    # daemon thread linger for the read timeout.
                    try:
                        stream_iter.close()
                    except Exception:
                        pass

                if not error_accumulator or self._stop_requested.is_set():
                    break  # clean stream (or user stop) — proceed with what we have

                # Transient stream failure → retry the whole model call. Both
                # ordinary mid-stream deaths and "empty 200 stream" responses
                # share the same retry budget (provider_max_retries, default 3);
                # the empty-stream variant just uses a short fixed delay
                # instead of the full exponential chain.
                err_first_line = (error_accumulator.strip().splitlines() or ["unknown provider error"])[0][:300]
                if is_fatal_provider_error(error_accumulator) or stream_attempt >= provider_retries:
                    break
                empty_stream = is_empty_stream_error(error_accumulator)
                stream_attempt += 1
                delay = 2.0 if empty_stream else provider_base_delay * (2 ** (stream_attempt - 1))
                yield AgentEvent("provider_retry", {
                    "provider": self.provider.display_name,
                    "attempt": stream_attempt,
                    "max_retries": provider_retries,
                    "delay": round(delay, 1),
                    "error": err_first_line,
                })
                if empty_stream:
                    yield AgentEvent("text_delta", (
                        f"\n[Harness] {self.provider.display_name} returned an empty stream — "
                        f"retrying in {delay:g}s (attempt {stream_attempt}/{provider_retries}).\n"
                    ))
                else:
                    yield AgentEvent("text_delta", (
                        f"\n[Harness] {self.provider.display_name} stream failed — retrying in "
                        f"{delay:g}s (attempt {stream_attempt}/{provider_retries}); partial output above was discarded.\n"
                    ))
                # Interruptible backoff: a user Ctrl-C cancels the retry wait.
                deadline = time.time() + delay
                while time.time() < deadline:
                    if self._stop_requested.is_set():
                        break
                    time.sleep(min(0.5, max(0.0, deadline - time.time())))
                if self._stop_requested.is_set():
                    break

            # Guard against a model/provider returning a dead-end response (no text, no
            # tool calls) — common with local reasoning endpoints. An empty reply NEVER ends
            # the turn on its own: the model must call the `finish` tool (or deliver a final
            # text answer) to stop, or the user must interrupt. Keep re-prompting so a silent
            # endpoint cannot quietly abort a task mid-goal. If the provider itself surfaced
            # an error, show it and finish the turn instead.
            if not text_accumulator and not tool_calls_accumulator:
                if error_accumulator:
                    yield AgentEvent("text_delta", f"\n[Harness] {self.provider.display_name} returned an error; ending this turn.\n")
                    yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                    break
                empty_streak += 1
                if tools_executed_this_turn:
                    base_nudge = (
                        "[SYSTEM]: Your previous response was empty. The tool results above were "
                        "delivered. If your task is complete, immediately write your FINAL ANSWER "
                        "summary text now; otherwise continue with the next tool call. Do not go silent."
                    )
                else:
                    base_nudge = (
                        "[SYSTEM]: Your previous response was empty. Either continue with concrete "
                        "work by calling a tool, or — if the task is complete — immediately produce "
                        "your FINAL ANSWER text now (or call `finish` with your final summary). Do not "
                        "go silent."
                    )
                if empty_streak > MAX_EMPTY_RETRIES:
                    nudge = (
                        "[SYSTEM]: Your previous response was empty again. This turn cannot end on an "
                        "empty reply — only you can stop it: call `finish` with your final summary when "
                        "the task is complete, otherwise keep working with concrete tool calls, or write "
                        "your FINAL ANSWER text. Do not go silent."
                    )
                else:
                    nudge = base_nudge
                self.session.messages.append({
                    "role": "user",
                    "content": nudge,
                })
                yield AgentEvent("step_end", {"step": current_loop, "complete": False})
                continue

            empty_streak = 0
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": text_accumulator or None,
            }
            if reasoning_accumulator:
                assistant_msg["reasoning_content"] = reasoning_accumulator
            if tool_calls_accumulator:
                assistant_msg["tool_calls"] = [
                    {
                        "id": v["id"],
                        "type": "function",
                        "function": {"name": v["name"], "arguments": v["arguments"]},
                    }
                    for v in tool_calls_accumulator.values()
                ]

            turn_start_index = len(self.session.messages)
            self.session.messages.append(assistant_msg)
            self.checkpoint_manager.record_message_append(len(self.session.messages) - 1, assistant_msg)

            # If no tools called, the agent has finished speaking for this turn —
            # except in SUPER mode: intermediate narration without tool calls (and
            # without an explicit `finish`) must not abandon the autonomous goal.
            # Challenge it once; a second consecutive text-only reply ends the turn.
            if not tool_calls_accumulator:
                if self.mode == Mode.SUPER and super_text_only_streak < MAX_SUPER_TEXT_NUDGES:
                    super_text_only_streak += 1
                    self.session.messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM]: SUPER MODE is still active and the goal is NOT confirmed complete. "
                            "Intermediate narration is not a final answer. Continue the mission now: call the "
                            "tools needed for the next step. Only stop when the goal is truly accomplished — "
                            "by calling `finish` with your final summary."
                        ),
                    })
                    yield AgentEvent("step_end", {"step": current_loop, "complete": False})
                    continue
                yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                break

            # Tools were requested this step — real progress. Reset both streaks.
            super_text_only_streak = 0

            # Execute tool calls
            stop_requested = False
            finish_summary = ""
            interrupted = self._stop_requested.is_set()
            for v in tool_calls_accumulator.values():
                tool_name = v["name"]
                raw_args = v["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except Exception:
                    args = {}

                # Cooperative stop: answer remaining tool calls with a synthetic
                # "interrupted" result so the transcript stays consistent.
                if self._stop_requested.is_set() and tool_name != "finish":
                    self.session.messages.append({
                        "role": "tool",
                        "tool_call_id": v["id"],
                        "name": tool_name,
                        "content": "[Harness] Tool call skipped: turn interrupted by user.",
                    })
                    self.checkpoint_manager.record_message_append(len(self.session.messages) - 1, self.session.messages[-1])
                    continue

                yield AgentEvent("tool_call_start", {"name": tool_name, "arguments": args})

                # The `finish` tool is the model's explicit stop signal: deliver the
                # summary and end the iteration without any further model calls.
                if tool_name == "finish":
                    finish_summary = str(args.get("summary", "")).strip() or "Task complete."
                    self.session.messages.append({
                        "role": "tool",
                        "tool_call_id": v["id"],
                        "name": "finish",
                        "content": finish_summary,
                    })
                    self.checkpoint_manager.record_message_append(len(self.session.messages) - 1, self.session.messages[-1])
                    stop_requested = True
                    break

                result = self.tool_registry.execute(tool_name, args, self.mode)
                tools_executed_this_turn += 1
                # Exploration watchdog: count consecutive read-only calls.
                if self._is_exploration_call(tool_name, args):
                    exploration_streak += 1
                else:
                    exploration_streak = 0

                # ── Tool image routing ────────────────────────────────
                # Tools such as browser_screenshot embed
                # [harness:image:/abs/path.png] markers in their text result.
                # Strip them before anything else sees the text, then route the
                # files: real image blocks for vision models, a vision-fallback
                # (VFB) text description for non-vision models when one is
                # configured, else a plain path reference.
                from harness.core.attachments import split_tool_images
                result, tool_image_paths = split_tool_images(result)

                # Replay any subagent/swarm activity that occurred during this tool
                # call from the main thread so the renderer stays single-threaded.
                for etype, edata in self.subagent_orchestrator.drain_events():
                    yield AgentEvent(etype, edata)

                yield AgentEvent("tool_call_result", {"name": tool_name, "result": result})

                # ── Security gate: sanitize tool output ───────────────────
                from harness.core.security import sanitize_tool_output
                result = sanitize_tool_output(result)

                # Append tool result to history
                tool_content: Any = result
                if tool_image_paths:
                    image_blocks = [{"type": "image", "path": p} for p in tool_image_paths]
                    try:
                        turn_spec = self.provider.get_model_spec(self.session.model)
                        can_see = bool(getattr(turn_spec, "supports_vision", False))
                    except Exception:
                        can_see = False
                    if can_see or self.config.force_media_attach:
                        tool_content = [{"type": "text", "text": result}, *image_blocks]
                        yield AgentEvent("attachment", {
                            "files": [{"type": "image", "path": p} for p in tool_image_paths],
                            "tool": tool_name,
                        })
                    elif self.config.vfb_provider.strip():
                        vfb_events, vfb_description, vfb_err = self._describe_tool_images(
                            image_blocks, question=user_prompt
                        )
                        for vfb_ev in vfb_events:
                            yield vfb_ev
                        if vfb_description:
                            tool_content = (
                                result
                                + "\n\n[Vision fallback description of the screenshot(s):\n"
                                + vfb_description + "\n]"
                            )
                        else:
                            tool_content = (
                                result
                                + f"\n[Note: the screenshot could not be described ({vfb_err}); "
                                + "the file path above can be opened manually.]"
                            )
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": v["id"],
                    "name": tool_name,
                    "content": tool_content,
                }
                self.session.messages.append(tool_result_msg)
                self.checkpoint_manager.record_message_append(len(self.session.messages) - 1, tool_result_msg)

                # Mid-turn safety net: if a big tool result blew the hard cap,
                # collapse only oversized *old* payloads safely behind the current
                # turn. The freshly produced result is never touched.
                if self.config.auto_compact and self.compactor.is_over_cap(self.session.messages, sys_prompt):
                    old_msgs = list(self.session.messages)
                    trimmed, estats = self.compactor.emergency_trim(
                        self.session.messages, sys_prompt, before_index=turn_start_index
                    )
                    if estats and trimmed != old_msgs:
                        self._record_message_span_change(old_msgs, trimmed)
                        self.session.messages = trimmed
                        yield AgentEvent("compaction", estats)

            if stop_requested:
                yield AgentEvent("tool_call_result", {"name": "finish", "result": finish_summary})
                yield AgentEvent("text_delta", finish_summary)
                yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                break

            if interrupted or self._stop_requested.is_set():
                yield AgentEvent("text_delta", "\n[Harness] Turn interrupted by user.\n")
                yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                break

            # Exploration watchdog: a long streak of consecutive read-only calls
            # means the model is spiraling in re-orientation instead of working.
            # Force it back to concrete action with a system nudge (fires once
            # per streak; the streak resets on any real work).
            if exploration_streak >= MAX_EXPLORATION_STREAK:
                exploration_streak = 0
                self.session.messages.append({
                    "role": "user",
                    "content": (
                        f"[SYSTEM]: You have executed {MAX_EXPLORATION_STREAK} consecutive read-only "
                        "exploration calls in a row without producing any changes. You have enough "
                        "context — STOP re-exploring and take concrete action toward the goal RIGHT NOW: "
                        "write the code/files you already planned, run the builds/tests, or — if the "
                        "goal is truly complete — call `finish` with your final summary."
                    ),
                })
                yield AgentEvent("text_delta", "\n[Harness] Exploration loop detected — nudging the agent back to concrete action.\n")

            # Save session state
            self._sync_todos_to_session()
            self.session_manager.save(self.session)
            yield AgentEvent("step_end", {"step": current_loop, "complete": False})

        self.is_running = False
        self.clear_stop()
        self._sync_todos_to_session()
        self.session_manager.save(self.session)
        self._maybe_auto_learn(user_prompt)
        yield AgentEvent("turn_complete", {"messages_count": len(self.session.messages)})

    def _proactive_budget_check(self, sys_prompt: str):
        """Inspect context pressure before the next provider send and compact if
        needed. Prevents the window from ever filling up mid-turn, which the
        boundary-only auto-compaction could not stop once a long tool loop runs.

        Yields zero or more AgentEvents (a compaction event when it acts, plus a
        pressure_warning event as an early UX signal when usage first crosses the
        threshold).
        """
        budget = self.context_budget
        status = budget.check(self.session.messages, sys_prompt)
        if status.pressure == PRESSURE_CRITICAL:
            # Hard cap reached mid-turn: collapse oversized *old* payloads behind
            # the current turn so the next prompt is guaranteed to fit.
            if self.config.auto_compact:
                old_msgs = list(self.session.messages)
                trimmed, estats = self.compactor.emergency_trim(
                    self.session.messages, sys_prompt, before_index=len(self.session.messages)
                )
                if estats and trimmed != old_msgs:
                    self._record_message_span_change(old_msgs, trimmed)
                    self.session.messages = trimmed
                    yield AgentEvent("pressure_warning", {
                        "pressure": status.pressure,
                        "usage_ratio": round(status.usage_ratio, 4),
                    })
                    yield AgentEvent("compaction", estats)
            return
        if status.pressure == PRESSURE_WARNING and self.config.auto_compact:
            if self.compactor.should_compact(self.session.messages, sys_prompt):
                old_msgs = list(self.session.messages)
                compacted_msgs, stats = self.compactor.compact(self.session.messages, sys_prompt)
                if stats.get("compacted") and compacted_msgs != old_msgs:
                    self._record_message_span_change(old_msgs, compacted_msgs)
                    self.session.messages = compacted_msgs
                    yield AgentEvent("pressure_warning", {
                        "pressure": status.pressure,
                        "usage_ratio": round(status.usage_ratio, 4),
                    })
                    yield AgentEvent("compaction", stats)

    def _last_assistant_text(self, start_index: int = 0) -> str:
        """Return the most recent non-empty assistant text at/after ``start_index``."""
        for i in range(len(self.session.messages) - 1, max(0, start_index) - 1, -1):
            m = self.session.messages[i]
            if m.get("role") == "assistant" and m.get("content"):
                return str(m["content"]).strip()
        return ""

    def _sync_todos_to_session(self) -> None:
        """Sync in-memory TodoManager state to the session for persistence."""
        if self.session is not None:
            self.session.todos = self.todo_manager.to_list()

    def _restore_todos_from_session(self) -> None:
        """Restore TodoManager state from session (called on session load)."""
        if self.session is not None and self.session.todos:
            self.todo_manager.load_list(self.session.todos)

    def _bind_checkpoint_session(self, session: Optional[Session] = None):
        """Bind checkpoint manager persistence to the active session."""
        target = session if session is not None else self.session
        sid = target.id if target is not None else None
        try:
            self.checkpoint_manager.bind_session(sid)
        except Exception:
            pass

    def _on_checkpoint_persist(self):
        """Listener for checkpoint changes - sync todos and persist session+checkpoints."""
        try:
            self._sync_todos_to_session()
            if self.session is not None:
                self.session_manager.save(self.session)
        except Exception:
            pass

    def _maybe_auto_learn(self, user_prompt: Optional[str]) -> None:
        """Cheap, deterministic heuristic capture: no extra provider call.

        After a turn, if any tool result this turn looked like an error but the agent
        still produced a final answer, record one debounced lesson so the next session
        verifies before acting. Skips when the agent already called learn_record itself.
        """
        if not self.config.learning_enabled or self.session is None:
            return
        session_id = self.session.id
        if self._auto_learned_session == session_id:
            return

        start = 0
        if user_prompt:
            for i, m in enumerate(self.session.messages):
                if m.get("role") == "user" and m.get("content") == user_prompt:
                    start = i
                    break

        failed_tools = []
        first_error = ""
        used_learn_record = False
        for m in self.session.messages[start:]:
            if m.get("role") == "tool":
                name = m.get("name", "")
                if name == "learn_record":
                    used_learn_record = True
                if name == "list_skills":
                    # The skills-first catalog seeding is Harness machinery,
                    # not model work — skill descriptions routinely contain the
                    # word "error" and must never trigger a false lesson.
                    continue
                content = str(m.get("content") or "")
                lowered = content.lower()
                if any(tok in lowered for tok in ("error", "traceback", "failed", "failure", "not found", "exception")):
                    if name not in failed_tools:
                        failed_tools.append(name)
                    if not first_error:
                        first_error = content.strip().split("\n")[0][:200]

        if used_learn_record or not failed_tools:
            return

        summary = (
            "Encountered "
            + " / ".join(f"`{t}`" for t in failed_tools[:3])
            + " failure(s) this turn; double-check paths, arguments, and commands before executing, and verify results."
        )
        lesson_id = self.learning_manager.record(
            summary=summary,
            tags=["verify"] + [t.replace("_", " ") for t in failed_tools[:2]],
            evidence=first_error,
            source_session=session_id,
        )
        if lesson_id:
            self._auto_learned_session = session_id

    def _build_system_prompt(self, current_query: str) -> str:
        mcp_summary = self.mcp_manager.format_summary()
        todos_md = self.todo_manager.format_markdown()
        swarm_enabled = self.config.swarm_enabled or self.mode == Mode.SUPER
        learned_lessons = self.learning_manager.format_top(current_query or "") if self.config.learning_enabled else ""
        # API server info (no peer mesh — single HTTP endpoint for remote/VPS)
        mesh_info = ""
        try:
            if getattr(self.config, "server_enabled", getattr(self.config, "mesh_enabled", True)):
                from harness.mesh.server import get_server, get_mesh
                srv = None
                try:
                    srv = get_server()
                except Exception:
                    srv = None
                if srv is None:
                    try:
                        srv = get_mesh()
                    except Exception:
                        srv = None
                if srv is not None:
                    mesh_info = (
                        f"API server is running for this Harness instance.\n"
                        f"- Endpoint: http://{srv.host}:{srv.port} (base {srv.base} => block {srv.base}00-{srv.base}99) — "
                        f"POST /api/prompt {{\"prompt\": \"...\", \"session_id\": \"...\"}} -> {{\"response\": \"...\"}}\n"
                        f"- Workspace: {srv.workspace} | User: {srv.username} | Token required: {bool(getattr(srv, 'server_token', None) or getattr(self.config, 'server_token', None))}\n"
                        f"- Use mesh_status tool to see the endpoint, or curl from any client/VPS."
                    )
                else:
                    import os as _os
                    from harness.mesh.port import compute_base_port as _cbp
                    _base = _cbp(_os.getcwd())
                    mesh_info = (
                        f"API server (HTTP) will start when harness opens (if server_enabled=true).\n"
                        f"- Expected block: {_base}00-{_base}99 (base {_base} hash workspace+user) — POST /api/prompt for remote use.\n"
                        f"- For VPS: `harness serve --host 0.0.0.0 --port 8000` and set HARNESS_API_TOKEN.\n"
                        f"- Tool: mesh_status shows the live endpoint."
                    )
        except Exception:
            mesh_info = ""
        # Context pressure feeds back into prompt assembly: when we're already
        # deep into the window, elide the verbose MCP tool summaries so the
        # system prompt doesn't crowd out the working history.
        degrade_verbose = False
        if self.session is not None and self.session.messages:
            budget = self.context_budget.check(self.session.messages, "")
            degrade_verbose = budget.usage_ratio >= self.context_budget.threshold_ratio
        return self.prompt_builder.build(
            mcp_tools_summary=mcp_summary,
            active_todos=todos_md,
            custom_instructions=self.config.custom_system_prompt,
            swarm_enabled=swarm_enabled,
            learned_lessons=learned_lessons,
            degrade_verbose=degrade_verbose,
            mesh_info=mesh_info,
            ultra_goal=str(current_query or "").startswith(ULTRA_GOAL_MARKER),
        )

    def _summarize_block(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Condense an old block of conversation into a dense, loss-conscious
        digest using the active provider. Returns None on any failure so the
        caller can fall back to the heuristic summarizer."""
        if not messages:
            return None
        try:
            summary_sys = (
                "You are a context-compaction engine. Condense the conversation block below "
                "into a concise but information-dense markdown digest. Preserve: the user's "
                "instructions and constraints, every file path touched, every tool invocation "
                "and its outcome, any decisions or conclusions, and any pending/blocked work. "
                "Drop repetition and reasoning noise. Keep it under 1200 characters."
            )
            rendered = []
            for m in messages:
                role = m.get("role")
                content = str(m.get("content") or "")
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {})
                        rendered.append(f"[tool_call] {fn.get('name', '?')} {fn.get('arguments', '{}')}")
                if content.strip():
                    snippet = content[:4000]
                    rendered.append(f"[{role}] {snippet[:4000]}")
            if not rendered:
                return None
            body = "\n".join(rendered)
            user_msg = {"role": "user", "content": f"<block>\n{body}\n</block>\n\nSummarize:"}
            acc = []
            for chunk in self.provider.stream_chat(
                messages=[user_msg],
                model=self.session.model if self.session is not None else self.config.model,
                thinking_effort="low",
                tools=None,
                system_prompt=summary_sys,
            ):
                if chunk.finish_reason == "error":
                    return None
                if chunk.delta_text:
                    acc.append(chunk.delta_text)
                if sum(len(a) for a in acc) > 1500:
                    break
            text = "".join(acc).strip()
            return text[:1500] or None
        except Exception:
            return None
