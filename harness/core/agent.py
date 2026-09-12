"""
Core Agent Engine for Harness.
Coordinates autonomous reasoning loops, tool invocations, Super Mode iterations,
side-channel /btw inquiries, and session state persistence.
"""
import json
import time
from typing import Dict, Any, List, Optional, Callable, Generator
from harness.core.modes import Mode
from harness.core.permissions import PermissionManager, PermissionLevel
from harness.core.prompt import SystemPromptBuilder
from harness.core.compaction import Compactor, TokenStats
from harness.core.todo import TodoManager
from harness.core.session import Session, SessionManager
from harness.core.subagents import SubagentOrchestrator
from harness.providers.base import BaseProvider, LLMChunk, ToolCallDelta
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
    ):
        self.config = config
        self.mode = Mode.from_string(config.mode)
        self.permission_manager = PermissionManager(PermissionLevel.from_string(config.permission))
        self.todo_manager = TodoManager()
        self.subagent_orchestrator = SubagentOrchestrator(self._run_subagent_task)
        self.skills_manager = SkillsManager()
        self.tool_registry = ToolRegistry(
            permission_manager=self.permission_manager,
            todo_manager=self.todo_manager,
            subagent_orchestrator=self.subagent_orchestrator,
            ask_user_handler=ask_user_handler,
        )
        self.mcp_manager = MCPManager(self.tool_registry)
        self.mcp_manager.load_and_connect()

        self.provider: BaseProvider = get_provider(config.provider, config)
        self.session_manager = SessionManager()
        self.session: Session = session or self.session_manager.create(
            provider=config.provider,
            model=config.model,
            mode=self.mode.value,
            permission=self.permission_manager.level.value,
            thinking_effort=config.thinking_effort,
        )

        model_spec = self.provider.get_model_spec(self.session.model)
        self.compactor = Compactor(model_spec.context_window, config.compact_threshold)
        self.event_callback = event_callback
        self.prompt_builder = SystemPromptBuilder(self.mode, self.permission_manager.level)
        self.is_running = False
        self.steer_queue: List[str] = []

    def emit(self, event_type: str, data: Any = None):
        if self.event_callback:
            self.event_callback(AgentEvent(event_type, data))

    def set_mode(self, mode: Mode):
        self.mode = mode
        self.prompt_builder.mode = mode
        self.session.mode = mode.value
        self.emit("mode_change", mode.value)

    def set_permission(self, perm: PermissionLevel):
        self.permission_manager.set_level(perm)
        self.prompt_builder.permission = perm
        self.session.permission = perm.value
        self.emit("permission_change", perm.value)

    def set_provider(self, provider_name: str, model_name: Optional[str] = None):
        self.provider = get_provider(provider_name, self.config)
        self.session.provider = provider_name
        if model_name:
            self.session.model = model_name
        else:
            self.session.model = self.provider.default_model
        model_spec = self.provider.get_model_spec(self.session.model)
        self.compactor.context_window = model_spec.context_window
        self.emit("provider_change", {"provider": provider_name, "model": self.session.model})

    def steer(self, guidance: str):
        """Inject steering instructions into ongoing execution loop."""
        self.steer_queue.append(guidance)
        self.emit("steer_received", guidance)

    def ask_btw(self, question: str) -> str:
        """
        Handle an out-of-band side-channel query while active without
        derailing the primary task message thread.
        """
        side_prompt = (
            f"The user is asking a quick out-of-band question while the main task proceeds.\n"
            f"Answer concisely without modifying files or interrupting the active work.\n\n"
            f"Question: {question}"
        )
        side_messages = [{"role": "user", "content": side_prompt}]

        response_text = ""
        for chunk in self.provider.stream_chat(
            messages=side_messages,
            model=self.session.model,
            thinking_effort="off",
            system_prompt="You are Harness answering a brief side-question. Keep answers direct and helpful.",
        ):
            if chunk.delta_text:
                response_text += chunk.delta_text

        return response_text or "No response from side-query."

    def _run_subagent_task(self, system_prompt: str, prompt: str, allowed_tools: List[str], max_turns: int) -> tuple[str, int]:
        """Subagent runner executing with isolated conversation context."""
        sub_messages = [{"role": "user", "content": prompt}]
        accumulated_output = ""
        turns = 0

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

            for chunk in self.provider.stream_chat(
                messages=sub_messages,
                model=self.session.model,
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
                try:
                    args = json.loads(v["arguments"]) if v["arguments"] else {}
                except Exception:
                    args = {}
                res = self.tool_registry.execute(tname, args, Mode.BUILD)
                sub_messages.append({"role": "tool", "tool_call_id": v["id"], "name": tname, "content": res})

        return accumulated_output.strip(), turns

    def step(self, user_prompt: Optional[str] = None) -> Generator[AgentEvent, None, None]:
        """Execute a single or multi-step agent turn, yielding live events."""
        self.is_running = True

        if user_prompt:
            self.session.messages.append({"role": "user", "content": user_prompt})

        # Process any pending steering instructions
        while self.steer_queue:
            st = self.steer_queue.pop(0)
            self.session.messages.append({"role": "user", "content": f"[STEERING GUIDANCE]: {st}"})

        # Auto-compaction check
        sys_prompt = self._build_system_prompt(user_prompt or "")
        if self.config.auto_compact and self.compactor.should_compact(self.session.messages, sys_prompt):
            compacted_msgs, stats = self.compactor.compact(self.session.messages)
            self.session.messages = compacted_msgs
            yield AgentEvent("compaction", stats)

        # Loop for tool executions (Super mode allows up to 25 steps, Build up to 10)
        max_loop = 25 if self.mode == Mode.SUPER else 10
        current_loop = 0

        while current_loop < max_loop and self.is_running:
            current_loop += 1
            active_tools = self.tool_registry.get_openai_schemas(self.mode)

            text_accumulator = ""
            reasoning_accumulator = ""
            tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

            yield AgentEvent("step_start", {"step": current_loop, "mode": self.mode.value})

            for chunk in self.provider.stream_chat(
                messages=self.session.messages,
                model=self.session.model,
                thinking_effort=self.config.thinking_effort,
                tools=active_tools,
                system_prompt=sys_prompt,
            ):
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
                    if tc.name:
                        tool_calls_accumulator[idx]["name"] = tc.name
                    if tc.arguments_delta:
                        tool_calls_accumulator[idx]["arguments"] += tc.arguments_delta

            # Append assistant turn to history
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

            self.session.messages.append(assistant_msg)

            # If no tools called, agent has finished speaking for this turn
            if not tool_calls_accumulator:
                yield AgentEvent("step_end", {"step": current_loop, "complete": True})
                break

            # Execute tool calls
            for v in tool_calls_accumulator.values():
                tool_name = v["name"]
                raw_args = v["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except Exception:
                    args = {}

                yield AgentEvent("tool_call_start", {"name": tool_name, "arguments": args})

                result = self.tool_registry.execute(tool_name, args, self.mode)

                yield AgentEvent("tool_call_result", {"name": tool_name, "result": result})

                # Append tool result to history
                self.session.messages.append({
                    "role": "tool",
                    "tool_call_id": v["id"],
                    "name": tool_name,
                    "content": result,
                })

            # Check if user injected steering during tool execution
            while self.steer_queue:
                st = self.steer_queue.pop(0)
                self.session.messages.append({"role": "user", "content": f"[STEERING GUIDANCE]: {st}"})

            # Save session state
            self.session_manager.save(self.session)
            yield AgentEvent("step_end", {"step": current_loop, "complete": False})

        self.is_running = False
        self.session_manager.save(self.session)
        yield AgentEvent("turn_complete", {"messages_count": len(self.session.messages)})

    def _build_system_prompt(self, current_query: str) -> str:
        skills_summary = self.skills_manager.format_summary()
        mcp_summary = self.mcp_manager.format_summary()
        todos_md = self.todo_manager.format_markdown()
        return self.prompt_builder.build(
            skills_summary=skills_summary,
            mcp_tools_summary=mcp_summary,
            active_todos=todos_md,
            custom_instructions=self.config.custom_system_prompt,
        )
