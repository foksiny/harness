"""
Subagent Orchestration Engine for Harness.
Dispatches specialized worker agents with isolated context windows and synthesizes
results back to the primary agent thread. Supports concurrent agent swarms that
coordinate through a shared in-memory message bus (the "main thread").

All subagents run in parallel (threaded). The main agent can continue working
while subagents process in the background. Subagents run until they call
`finish` or hit a safety limit (200 turns).
"""
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Callable

# Thread-local swarm runtime context. Worker threads populate this before invoking
# the agent runner so that tools (swarm_send_message, swarm_read_messages) resolve
# the correct message bus and sender identity.
_SWARM_TLS = threading.local()

# Subagents run until they call `finish` or hit this safety limit.
MAX_TURNS = 200


class SubagentType(str, Enum):
    RESEARCHER = "researcher"
    PLANNER = "planner"
    CODER = "coder"
    TESTER = "tester"
    REVIEWER = "reviewer"
    GENERAL = "general"


@dataclass
class SubagentConfig:
    agent_type: SubagentType
    system_prompt: str
    allowed_tools: List[str]


SUBAGENT_ROLES: Dict[SubagentType, SubagentConfig] = {
    SubagentType.RESEARCHER: SubagentConfig(
        agent_type=SubagentType.RESEARCHER,
        system_prompt=(
            "You are an expert Research Subagent. Your role is to deeply explore the codebase, "
            "inspect files, search patterns, and read documentation. You MUST NOT modify any files "
            "or run destructive commands. Provide a clear, structured factual summary of your findings."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "exa_search"],
    ),
    SubagentType.PLANNER: SubagentConfig(
        agent_type=SubagentType.PLANNER,
        system_prompt=(
            "You are an expert Software Architecture Planner. Your role is to analyze requirements, "
            "decompose complex goals into sequential atomic tasks, and define verification milestones."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "todo_create"],
    ),
    SubagentType.CODER: SubagentConfig(
        agent_type=SubagentType.CODER,
        system_prompt=(
            "You are an expert Implementation Subagent. Your role is to write clean, maintainable, "
            "and atomic code changes. Adhere to existing project conventions and verify changes."
        ),
        allowed_tools=["view_file", "edit_file", "write_file", "list_dir", "find_files", "grep_search", "run_command", "execute_python"],
    ),
    SubagentType.TESTER: SubagentConfig(
        agent_type=SubagentType.TESTER,
        system_prompt=(
            "You are an expert QA and Test Subagent. Your role is to write unit/integration tests, "
            "run test commands, analyze failures, and verify fixes."
        ),
        allowed_tools=["view_file", "write_file", "edit_file", "run_command", "execute_python"],
    ),
    SubagentType.REVIEWER: SubagentConfig(
        agent_type=SubagentType.REVIEWER,
        system_prompt=(
            "You are an expert Code Review Subagent. Inspect code diffs, check for security flaws, "
            "edge cases, performance bottlenecks, and regressions."
        ),
        allowed_tools=["view_file", "git_diff", "git_status", "grep_search"],
    ),
}

# Swarm coordination tools made available to every worker subagent.
SWARM_MESSAGING_TOOLS: List[str] = ["swarm_send_message", "swarm_read_messages"]


@dataclass
class SwarmMessage:
    id: int
    sender: str
    recipient: str          # "all", an agent id, or "main"
    body: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "body": self.body,
            "created_at": self.created_at,
        }


class SwarmMessageBus:
    """Thread-safe message bus shared by concurrently running swarm agents.

    Messages flow through a single bus owned by the orchestrator (the "main thread"),
    so any subagent can post and read messages addressed to other agents.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: List[SwarmMessage] = []
        self._next_id: int = 1

    @property
    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def post(self, sender: str, recipient: str, body: str) -> SwarmMessage:
        with self._lock:
            msg = SwarmMessage(id=self._next_id, sender=sender, recipient=recipient, body=str(body))
            self._next_id += 1
            self._messages.append(msg)
            return msg

    def read(self, since_index: int = 0, sender: Optional[str] = None, recipient: Optional[str] = None) -> List[SwarmMessage]:
        with self._lock:
            out = []
            for m in self._messages[since_index:]:
                if sender and m.sender != sender:
                    continue
                if recipient and m.recipient not in ("all", recipient):
                    continue
                out.append(m)
            return out

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._next_id = 1


@dataclass
class SubagentResult:
    agent_type: str
    task: str
    status: str       # "completed", "failed", "timeout"
    turns_taken: int
    output: str
    execution_time: float


@dataclass
class SwarmResult:
    """Combined outcome of a concurrently executed agent swarm."""

    seed_index: int
    workers: List[SubagentResult]
    messages: List[SwarmMessage]

    @property
    def status(self) -> str:
        if not self.workers:
            return "completed"
        statuses = {w.status for w in self.workers}
        if statuses in ({"completed"}, set(), {"completed", "failed"}):
            return "completed" if "failed" not in statuses else "completed-with-errors"
        return "failed"

    def format_report(self) -> str:
        lines = ["=== SWARM EXECUTION REPORT ==="]
        for w in self.workers:
            lines.append(f"[{w.agent_type.upper()}] Status: {w.status} | Turns: {w.turns_taken} | Time: {w.execution_time}s")
            lines.append(w.output or "(no output)")
            lines.append("-" * 50)
        if self.messages:
            lines.append("=== SWARM MAILBOX TRANSCRIPT ===")
            for m in self.messages:
                target = "ALL" if m.recipient == "all" else f"-> {m.recipient}"
                lines.append(f"[#{m.id}] {m.sender} {target}: {m.body}")
        else:
            lines.append("=== SWARM MAILBOX TRANSCRIPT ===\n(no messages exchanged)")
        return "\n".join(lines)


@dataclass
class SubagentRecord:
    """Live, continuously updated record of a subagent's activity.

    Used by the TUI to render what a subagent is doing (tool calls, messages,
    spoken text) and by slash commands for inspection after the fact.
    """

    agent_id: str
    agent_type: str
    task: str = ""
    status: str = "running"
    turns: int = 0
    output: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    text_log: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def last_tool(self) -> Optional[str]:
        if not self.tool_calls:
            return None
        return str(self.tool_calls[-1].get("tool", ""))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "task": self.task,
            "status": self.status,
            "turns": self.turns,
            "output": self.output,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "text_log": list(self.text_log),
            "tool_calls": list(self.tool_calls),
            "messages": list(self.messages),
            "last_tool": self.last_tool(),
        }


class SubagentOrchestrator:
    """Spawns and monitors subagents, and coordinates concurrent agent swarms.

    All subagents run in parallel via daemon threads. The main agent can continue
    working while subagents process in the background. Subagents run until they
    call `finish` or hit MAX_TURNS.
    """

    def __init__(self, agent_runner: Optional[Callable] = None):
        self.agent_runner = agent_runner
        self.active_subagents: List[SubagentResult] = []
        self._spawn_counter: int = 0
        self._active_threads: List[threading.Thread] = []
        self._threads_lock = threading.Lock()
        # Persistent "main thread" bus. Worker threads bind a per-run bus via
        # thread-local context; the main agent falls back to this shared bus.
        self.bus = SwarmMessageBus()
        # Per-thread provider/model/mode wiring (populated by the agent host).
        self.provider_factory: Optional[Callable] = None
        self.model: Optional[str] = None
        self.parent_mode = None
        self.interactive_deny_fn: Optional[Callable[[bool], None]] = None
        # Subagent activity tracking: live records plus a drainable event log.
        self._records: Dict[str, SubagentRecord] = {}
        self._records_lock = threading.Lock()
        self._event_log: List[tuple] = []
        self._event_lock = threading.Lock()
        self._results_lock = threading.Lock()

    # ---- Subagent activity recording (used by the agent host) ----

    def _record_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Thread-safe append to the drainable subagent event log."""
        with self._event_lock:
            self._event_log.append((event_type, data))

    def drain_events(self) -> List[tuple]:
        """Return and clear all buffered subagent events (call from main thread)."""
        with self._event_lock:
            events, self._event_log = list(self._event_log), []
        return events

    def list_records(self) -> List[SubagentRecord]:
        """Return a copy of the active subagent records, most recent first."""
        with self._records_lock:
            records = list(self._records.values())
        records.sort(key=lambda r: r.started_at, reverse=True)
        return records

    def get_record(self, agent_id: str) -> Optional[SubagentRecord]:
        with self._records_lock:
            return self._records.get(agent_id)

    def _ensure_record(self, agent_id: str) -> Optional[SubagentRecord]:
        with self._records_lock:
            if agent_id not in self._records:
                self._records[agent_id] = SubagentRecord(agent_id=agent_id, agent_type="general")
            return self._records[agent_id]

    def begin_subagent(self, agent_id: str, agent_type: str, task: str) -> None:
        with self._records_lock:
            rec = self._records.setdefault(agent_id, SubagentRecord(agent_id=agent_id, agent_type=agent_type, task=task))
            rec.agent_type = agent_type
            rec.task = task
            rec.status = "running"
        self._record_event("subagent_start", {
            "agent_id": agent_id, "agent_type": agent_type, "task": task, "count": len(self.list_records()),
        })

    def subagent_text(self, agent_id: str, text: str) -> None:
        rec = self._ensure_record(agent_id)
        with self._records_lock:
            rec.text_log.append(text)
        self._record_event("subagent_text_delta", {"agent_id": agent_id, "text": text})

    def subagent_tool(self, agent_id: str, tool_name: str, args: str, result: str) -> None:
        rec = self._ensure_record(agent_id)
        entry = {
            "tool": tool_name,
            "args": args[:200],
            "result": result[:200],
            "time": time.time(),
        }
        with self._records_lock:
            rec.tool_calls.append(entry)
        self._record_event("subagent_tool", {
            "agent_id": agent_id, "tool": tool_name, "args": args[:160], "result": result[:160],
        })

    def subagent_message(self, agent_id: str, sender: str, recipient: str, body: str) -> None:
        rec = self._ensure_record(agent_id)
        with self._records_lock:
            rec.messages.append({"sender": sender, "recipient": recipient, "body": body, "time": time.time()})
        self._record_event("subagent_message", {
            "agent_id": agent_id, "sender": sender, "recipient": recipient, "message": body,
        })

    def finish_subagent(self, agent_id: str, status: str, turns: int, output: str) -> None:
        rec = self._ensure_record(agent_id)
        with self._records_lock:
            rec.status = status
            rec.turns = turns
            rec.output = output
            rec.ended_at = time.time()
        self._record_event("subagent_end", {
            "agent_id": agent_id, "status": status, "turns": turns, "output": output[:400],
        })

    def set_swarm_runtime(self, provider_factory: Optional[Callable] = None, model: Optional[str] = None, parent_mode=None,
                          interactive_deny_fn: Optional[Callable[[bool], None]] = None) -> None:
        """Configure runtime context used by concurrently spawned worker threads."""
        self.provider_factory = provider_factory
        self.model = model
        self.parent_mode = parent_mode
        self.interactive_deny_fn = interactive_deny_fn

    def wait_for_all(self, timeout: Optional[float] = None) -> None:
        """Block until all active background threads finish."""
        with self._threads_lock:
            threads = list(self._active_threads)
        for t in threads:
            t.join(timeout=timeout)

    def active_count(self) -> int:
        """Return the number of currently running background threads."""
        with self._threads_lock:
            return sum(1 for t in self._active_threads if t.is_alive())

    # ---- Inter-agent messaging (used by the swarm tools) ----

    def post_message(self, recipient: str, body: str) -> SwarmMessage:
        """Post a message on the active bus. Sender resolved from thread context."""
        ctx = getattr(_SWARM_TLS, "bus", None)
        sender = getattr(_SWARM_TLS, "agent_id", "main")
        bus = ctx or self.bus
        return bus.post(sender=sender, recipient=recipient, body=body)

    def read_messages(self, since_index: int = 0, sender: Optional[str] = None, recipient: Optional[str] = None) -> List[SwarmMessage]:
        """Read messages from the active bus with optional filters."""
        bus = getattr(_SWARM_TLS, "bus", None) or self.bus
        return bus.read(since_index=since_index, sender=sender, recipient=recipient)

    # ---- Subagent scheduling ----

    def spawn(self, agent_type_str: str, prompt: str, runner_override: Optional[Callable] = None,
              background: bool = True) -> SubagentResult:
        """Spawn an isolated subagent worker.

        When ``background=True`` (the default), the subagent runs on a daemon
        thread and returns immediately with a placeholder result. When
        ``background=False``, the thread is joined before returning.
        """
        try:
            stype = SubagentType(agent_type_str.lower().strip())
        except ValueError:
            stype = SubagentType.GENERAL

        cfg = SUBAGENT_ROLES.get(stype, SubagentConfig(
            agent_type=stype,
            system_prompt="You are a specialized subagent executing an isolated subtask.",
            allowed_tools=["view_file", "list_dir", "grep_search", "edit_file", "run_command"],
        ))

        self._spawn_counter += 1
        agent_id = f"{stype.value}_{self._spawn_counter}"
        self.begin_subagent(agent_id, stype.value, prompt)

        start_time = time.time()
        runner = runner_override or self.agent_runner

        # Placeholder result for background mode
        placeholder = SubagentResult(
            agent_type=stype.value,
            task=prompt,
            status="running",
            turns_taken=0,
            output="",
            execution_time=0,
        )

        def _worker():
            nonlocal placeholder
            if runner:
                try:
                    output, turns = runner(
                        cfg.system_prompt, prompt, cfg.allowed_tools, MAX_TURNS,
                        agent_id=agent_id,
                        agent_type=stype.value,
                    )
                    status = "completed"
                except Exception as e:
                    output = f"Subagent error: {str(e)}"
                    turns = 0
                    status = "failed"
            else:
                output = f"Subagent ({stype.value}) completed task analysis: {prompt[:100]}..."
                turns = 1
                status = "completed"

            elapsed = time.time() - start_time
            self.finish_subagent(agent_id, status, turns, output)

            res = SubagentResult(
                agent_type=stype.value,
                task=prompt,
                status=status,
                turns_taken=turns,
                output=output,
                execution_time=round(elapsed, 2),
            )
            with self._results_lock:
                self.active_subagents.append(res)
                placeholder = res

        if runner:
            t = threading.Thread(target=_worker, daemon=True)
            with self._threads_lock:
                self._active_threads.append(t)
            t.start()

            if not background:
                t.join()
                with self._results_lock:
                    return placeholder
            return placeholder
        else:
            # Fallback simulated response
            output = f"Subagent ({stype.value}) completed task analysis: {prompt[:100]}..."
            elapsed = 0.01
            self.finish_subagent(agent_id, "completed", 1, output)
            res = SubagentResult(
                agent_type=stype.value,
                task=prompt,
                status="completed",
                turns_taken=1,
                output=output,
                execution_time=elapsed,
            )
            with self._results_lock:
                self.active_subagents.append(res)
            return res

    def launch_swarm(self, agents: List[Dict[str, Any]], background: bool = True) -> SwarmResult:
        """Execute multiple specialized agents concurrently as a swarm.

        Each agent runs on its own thread with a unique ``agent_id`` and a shared
        per-swarm message bus. When ``background=True`` (the default), threads are
        started and a result is returned immediately. When ``background=False``,
        all threads are joined before returning the combined report.
        """
        run_bus = SwarmMessageBus()
        seed_index = run_bus.length
        results: List[Optional[SubagentResult]] = [None] * len(agents)
        threads: List[threading.Thread] = []

        if self.interactive_deny_fn:
            self.interactive_deny_fn(True)
        try:
            for i, spec in enumerate(agents):
                agent_type = str(spec.get("agent_type", "general"))
                prompt = str(spec.get("task", ""))
                agent_id = str(spec.get("agent_id", "")).strip() or f"{agent_type.lower()}_{i + 1}"
                try:
                    stype = SubagentType(agent_type.lower().strip())
                except ValueError:
                    stype = SubagentType.GENERAL
                cfg = SUBAGENT_ROLES.get(stype, SubagentConfig(
                    agent_type=stype,
                    system_prompt="You are a specialized subagent executing an isolated subtask.",
                    allowed_tools=["view_file", "list_dir", "grep_search", "edit_file", "run_command"],
                ))
                allowed_tools = list(cfg.allowed_tools) + SWARM_MESSAGING_TOOLS + ["finish"]

                t = threading.Thread(
                    target=self._run_worker,
                    args=(i, agent_id, cfg, prompt, allowed_tools, run_bus, results),
                    daemon=True,
                )
                threads.append(t)
                t.start()

            if not background:
                for t in threads:
                    t.join()
        finally:
            if not background and self.interactive_deny_fn:
                self.interactive_deny_fn(False)

        completed = [r for r in results if r is not None]
        return SwarmResult(seed_index=seed_index, workers=completed, messages=run_bus.read(since_index=seed_index))

    def _run_worker(self, index: int, agent_id: str, cfg: SubagentConfig, prompt: str, allowed_tools: List[str], run_bus: SwarmMessageBus, results: List[Optional[SubagentResult]]) -> None:
        """Worker thread target: bind a swarm context and run the agent runner."""
        setattr(_SWARM_TLS, "bus", run_bus)
        setattr(_SWARM_TLS, "agent_id", agent_id)

        self.begin_subagent(agent_id, cfg.agent_type.value, prompt)

        start_time = time.time()
        runner = self.agent_runner
        if runner:
            try:
                run_kwargs: Dict[str, Any] = {
                    "agent_id": agent_id,
                    "agent_type": cfg.agent_type.value,
                }
                if self.provider_factory is not None:
                    run_kwargs["provider_factory"] = self.provider_factory
                if self.model is not None:
                    run_kwargs["model"] = self.model
                if self.parent_mode is not None:
                    run_kwargs["mode"] = self.parent_mode
                output, turns = runner(cfg.system_prompt, prompt, allowed_tools, MAX_TURNS, **run_kwargs)
                status = "completed"
            except Exception as e:
                output = f"Subagent error: {str(e)}"
                turns = 0
                status = "failed"
        else:
            output = f"Subagent ({cfg.agent_type.value}) completed task analysis: {prompt[:100]}..."
            turns = 1
            status = "completed"

        self.finish_subagent(agent_id, status, turns, output)

        results[index] = SubagentResult(
            agent_type=cfg.agent_type.value,
            task=prompt,
            status=status,
            turns_taken=turns,
            output=output,
            execution_time=round(time.time() - start_time, 2),
        )
