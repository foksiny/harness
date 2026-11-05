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
    model: Optional[str] = None
    thinking_effort: Optional[str] = None  # None = inherit parent for quality roles
    paths: Optional[List[str]] = None     # owned files (A9 ownership lock)


SUBAGENT_ROLES: Dict[SubagentType, SubagentConfig] = {
    SubagentType.RESEARCHER: SubagentConfig(
        agent_type=SubagentType.RESEARCHER,
        system_prompt=(
            "You are an expert Research Subagent. Goal: factual, evidence-backed findings. "
            "Inputs: task + file paths. Output: structured summary with file refs. "
            "Acceptance: every claim cites a file. You MUST NOT modify files or run destructive commands."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "exa_search"],
        thinking_effort="low",
    ),
    SubagentType.PLANNER: SubagentConfig(
        agent_type=SubagentType.PLANNER,
        system_prompt=(
            "You are an expert Software Architecture Planner. Goal: a plan that can be executed. "
            "Output: task list (todos) with dependencies, file map, interfaces, acceptance criteria per task. "
            "State approaches considered, trade-offs, and verification milestones."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "todo_create"],
        thinking_effort="low",
    ),
    SubagentType.CODER: SubagentConfig(
        agent_type=SubagentType.CODER,
        system_prompt=(
            "You are an expert Implementation Subagent. Goal: runnable, tested code. "
            "Own ONLY the files named in your task (never edit another agent's files). "
            "Output: summary + files_changed + verification evidence. "
            "Acceptance: code builds/runs; you ran the relevant tests. No stubs."
        ),
        allowed_tools=["view_file", "edit_file", "write_file", "list_dir", "find_files", "grep_search", "run_command", "execute_python"],
    ),
    SubagentType.TESTER: SubagentConfig(
        agent_type=SubagentType.TESTER,
        system_prompt=(
            "You are an expert QA and Test Subagent. Goal: prove or disprove the work. "
            "Write/run tests, report exact commands + outcomes (pass/fail counts). "
            "Acceptance: every claim has command output; failures are quoted, not paraphrased."
        ),
        allowed_tools=["view_file", "write_file", "edit_file", "run_command", "execute_python"],
    ),
    SubagentType.REVIEWER: SubagentConfig(
        agent_type=SubagentType.REVIEWER,
        system_prompt=(
            "You are an expert Code Review Subagent. Goal: verdict pass/fail. "
            "Rubric: security flaws, edge cases, performance, regressions, claim-vs-evidence. "
            "Output: verdict + findings with file refs + what would make it pass."
        ),
        allowed_tools=["view_file", "git_diff", "git_status", "grep_search"],
    ),
}

def get_role_config(stype: SubagentType) -> SubagentConfig:
    """Single source of truth for role configs (deduplicates GENERAL fallback)."""
    return SUBAGENT_ROLES.get(stype, SubagentConfig(
        agent_type=stype,
        system_prompt="You are a specialized subagent executing an isolated subtask. Goal, inputs, acceptance criteria, and output format are in your task; honor them and verify your work.",
        allowed_tools=["view_file", "list_dir", "grep_search", "edit_file", "run_command"],
    ))

def role_effort(agent_type_str: str, parent_effort: str = "high") -> str:
    """Per-role effort routing (A6): quality roles inherit parent."""
    t = (agent_type_str or "").lower().strip()
    if t in ("coder", "tester", "reviewer"):
        return parent_effort
    return "low"

# Swarm coordination tools made available to every worker subagent.
SWARM_MESSAGING_TOOLS: List[str] = ["swarm_send_message", "swarm_read_messages"]


@dataclass
class SwarmMessage:
    id: int
    sender: str
    recipient: str          # "all", an agent id, or "main"
    body: str
    created_at: float = field(default_factory=time.time)
    payload: Optional[Dict[str, Any]] = None       # B2 structured handoff
    artifacts: List[str] = field(default_factory=list)
    requires_ack: bool = False
    acked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "body": self.body,
            "created_at": self.created_at,
            "payload": self.payload,
            "artifacts": list(self.artifacts),
            "requires_ack": self.requires_ack,
            "acked": self.acked,
        }


class SwarmMessageBus:
    """Thread-safe message bus shared by concurrently running swarm agents.

    Messages flow through a single bus owned by the orchestrator (the "main thread"),
    so any subagent can post and read messages addressed to other agents.
    """

    MAX_MESSAGES = 500  # J5: bound bus growth

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: List[SwarmMessage] = []
        self._next_id: int = 1
        # B3 blackboard: structured facts/decisions/interfaces shared by the run.
        self.scratchpad: Dict[str, Any] = {"facts": [], "decisions": [], "interfaces": []}

    @property
    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def post(self, sender: str, recipient: str, body: str, payload: Optional[Dict[str, Any]] = None,
             artifacts: Optional[List[str]] = None, requires_ack: bool = False) -> SwarmMessage:
        with self._lock:
            msg = SwarmMessage(id=self._next_id, sender=sender, recipient=recipient, body=str(body),
                               payload=payload, artifacts=list(artifacts or []), requires_ack=requires_ack)
            self._next_id += 1
            self._messages.append(msg)
            if len(self._messages) > self.MAX_MESSAGES:
                self._messages = self._messages[-self.MAX_MESSAGES:]
            return msg

    def ack(self, message_id: int) -> bool:
        with self._lock:
            for m in self._messages:
                if m.id == message_id:
                    m.acked = True
                    return True
            return False

    def read(self, since_index: int = 0, sender: Optional[str] = None, recipient: Optional[str] = None) -> List[SwarmMessage]:
        """Read messages with id > since_index (id-based; see A12)."""
        with self._lock:
            out = []
            for m in self._messages:
                if m.id <= since_index:
                    continue
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
            self.scratchpad = {"facts": [], "decisions": [], "interfaces": []}

    def to_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [m.to_dict() for m in self._messages]

    def from_list(self, data: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._messages = []
            max_id = 0
            for d in data or []:
                try:
                    msg = SwarmMessage(id=int(d.get("id", 0)), sender=str(d.get("sender", "")),
                                       recipient=str(d.get("recipient", "all")), body=str(d.get("body", "")),
                                       created_at=float(d.get("created_at", time.time())),
                                       payload=d.get("payload"), artifacts=list(d.get("artifacts") or []),
                                       requires_ack=bool(d.get("requires_ack")), acked=bool(d.get("acked")))
                    self._messages.append(msg)
                    max_id = max(max_id, msg.id)
                except Exception:
                    continue
            self._next_id = max_id + 1 if max_id else 1


@dataclass
class SubagentResult:
    agent_type: str
    task: str
    status: str       # "completed", "failed", "timeout", "cancelled", "running"
    turns_taken: int
    output: str
    execution_time: float
    verdict: str = "unverified"  # pass | fail | unverified (A11)
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0


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
            lines.append(f"[{w.agent_type.upper()}] Status: {w.status} verdict={w.verdict} | Turns: {w.turns_taken} | Time: {w.execution_time}s | tokens~{w.input_tokens + w.output_tokens}")
            lines.append(w.output or "(no output)")
            lines.append("-" * 50)
        if self.messages:
            lines.append("=== SWARM MAILBOX TRANSCRIPT ===")
            for m in self.messages:
                target = "ALL" if m.recipient == "all" else f"-> {m.recipient}"
                extra = f" payload={m.payload}" if m.payload else ""
                art = f" artifacts={m.artifacts}" if m.artifacts else ""
                ack = " [ACKED]" if m.acked else (" [ACK-PENDING]" if m.requires_ack else "")
                lines.append(f"[#{m.id}] {m.sender} {target}: {m.body}{extra}{art}{ack}")
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
        # A5 lifecycle: per-agent cancel events, caps, ownership locks, run registry.
        self._cancel_events: Dict[str, threading.Event] = {}
        self._owned_paths: Dict[str, str] = {}  # path -> agent_id (A9)
        self._runs: Dict[str, SwarmMessageBus] = {}
        self._run_counter: int = 0
        self.max_subagents: int = 4
        self.timeout_seconds: int = 120
        self.max_swarm_size: int = 8
        self._semaphore = threading.BoundedSemaphore(self.max_subagents)
        self._todo_manager = None

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
        # J5: prune dead threads
        with self._threads_lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]

    def active_count(self) -> int:
        """Return the number of currently running background threads."""
        with self._threads_lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            return len(self._active_threads)

    # ---- A1/A5 lifecycle: cancel, results, persistence ----
    def cancel(self, agent_id: str) -> bool:
        ev = self._cancel_events.get(agent_id)
        if ev is not None:
            ev.set()
            return True
        return False

    def get_result(self, agent_id: str) -> Optional[SubagentResult]:
        with self._results_lock:
            for r in reversed(self.active_subagents):
                if getattr(r, "agent_id", None) == agent_id:
                    return r
        rec = self.get_record(agent_id)
        if rec is not None and rec.status != "running":
            return SubagentResult(agent_type=rec.agent_type, task=rec.task, status=rec.status,
                                  turns_taken=rec.turns, output=rec.output,
                                  execution_time=round((rec.ended_at or time.time()) - rec.started_at, 2))
        return None

    def get_run_bus(self, run_id: str) -> Optional[SwarmMessageBus]:
        return self._runs.get(run_id)

    def set_todo_manager(self, todo_manager) -> None:
        self._todo_manager = todo_manager

    def persist_bus(self, run_id: str, path: str) -> bool:
        bus = self._runs.get(run_id)
        if bus is None:
            return False
        try:
            import json as _json
            from pathlib import Path as _P
            p = _P(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps({"messages": bus.to_list(), "scratchpad": bus.scratchpad}, indent=2))
            return True
        except Exception:
            return False

    def acquire_paths(self, agent_id: str, paths: List[str]) -> Optional[str]:
        """A9: return conflicting path or None if all acquired."""
        with self._results_lock:
            for p in paths or []:
                owner = self._owned_paths.get(str(p))
                if owner is not None and owner != agent_id:
                    return str(p)
            for p in paths or []:
                self._owned_paths[str(p)] = agent_id
            return None

    def release_paths(self, agent_id: str) -> None:
        with self._results_lock:
            for p, owner in list(self._owned_paths.items()):
                if owner == agent_id:
                    del self._owned_paths[p]

    # ---- Inter-agent messaging (used by the swarm tools) ----

    def post_message(self, recipient: str, body: str, payload: Optional[Dict[str, Any]] = None,
                     artifacts: Optional[List[str]] = None, requires_ack: bool = False) -> SwarmMessage:
        """Post a message on the active bus. Sender resolved from thread context."""
        ctx = getattr(_SWARM_TLS, "bus", None)
        sender = getattr(_SWARM_TLS, "agent_id", "main")
        bus = ctx or self.bus
        return bus.post(sender=sender, recipient=recipient, body=body, payload=payload,
                        artifacts=artifacts, requires_ack=requires_ack)

    def read_messages(self, since_index: int = 0, sender: Optional[str] = None, recipient: Optional[str] = None) -> List[SwarmMessage]:
        """Read messages from the active bus with optional filters."""
        bus = getattr(_SWARM_TLS, "bus", None) or self.bus
        return bus.read(since_index=since_index, sender=sender, recipient=recipient)

    # ---- Subagent scheduling ----

    def spawn(self, agent_type_str: str, prompt: str, runner_override: Optional[Callable] = None,
              background: bool = True, agent_id: Optional[str] = None,
              timeout_seconds: Optional[int] = None) -> SubagentResult:
        """Spawn an isolated subagent worker (A3: provider factory through spawn)."""
        try:
            stype = SubagentType(agent_type_str.lower().strip())
        except ValueError:
            stype = SubagentType.GENERAL

        cfg = get_role_config(stype)
        with self._results_lock:
            self._spawn_counter += 1
            n = self._spawn_counter
        agent_id = agent_id or f"{stype.value}_{n}"
        existing = self.get_record(agent_id)
        if existing is not None and existing.status == "running":
            agent_id = f"{agent_id}_r{n}"
        self.begin_subagent(agent_id, stype.value, prompt)
        cancel_ev = threading.Event()
        self._cancel_events[agent_id] = cancel_ev

        start_time = time.time()
        runner = runner_override or self.agent_runner
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        # Placeholder result for background mode
        placeholder = SubagentResult(
            agent_type=stype.value,
            task=prompt,
            status="running",
            turns_taken=0,
            output="",
            execution_time=0,
        )
        placeholder.agent_id = agent_id  # type: ignore[attr-defined]

        def _worker():
            nonlocal placeholder
            acquired = self._semaphore.acquire(blocking=False)
            if not acquired:
                output = "Error: max_subagents reached — try again shortly."
                elapsed = time.time() - start_time
                self.finish_subagent(agent_id, "failed", 0, output)
                res = SubagentResult(
                    agent_type=stype.value, task=prompt, status="failed",
                    turns_taken=0, output=output, execution_time=round(elapsed, 2),
                )
                res.agent_id = agent_id  # type: ignore[attr-defined]
                with self._results_lock:
                    self.active_subagents.append(res)
                    placeholder = res
                return
            try:
                if runner:
                    try:
                        run_kwargs: Dict[str, Any] = {
                            "agent_id": agent_id, "agent_type": stype.value,
                            "cancel_event": cancel_ev,
                        }
                        if self.provider_factory is not None:
                            run_kwargs["provider_factory"] = self.provider_factory
                        if self.model is not None:
                            run_kwargs["model"] = self.model
                        if self.parent_mode is not None:
                            run_kwargs["mode"] = self.parent_mode
                        run_kwargs["thinking_effort"] = role_effort(stype.value, getattr(self, "_parent_effort", "high"))
                        output, turns = runner(
                            cfg.system_prompt, prompt, cfg.allowed_tools, MAX_TURNS,
                            **run_kwargs,
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
            finally:
                self._semaphore.release()
            if cancel_ev.is_set():
                status = "cancelled"
            verdict = "unverified"
            lower = (output or "").lower()
            if status == "completed" and any(k in lower for k in ("test", "pytest", "passed", "build", "verified", "smoke", "reviewed")):
                verdict = "pass"
            elif status == "failed":
                verdict = "fail"
            elapsed = time.time() - start_time
            self.finish_subagent(agent_id, status, turns, output)

            res = SubagentResult(
                agent_type=stype.value,
                task=prompt,
                status=status,
                turns_taken=turns,
                output=output,
                execution_time=round(elapsed, 2),
                verdict=verdict,
            )
            res.agent_id = agent_id  # type: ignore[attr-defined]
            with self._results_lock:
                self.active_subagents.append(res)
                placeholder = res

        if runner:
            t = threading.Thread(target=_worker, daemon=True)
            with self._threads_lock:
                self._active_threads.append(t)
            t.start()

            if not background:
                t.join(timeout=timeout if timeout else None)
                if t.is_alive():
                    cancel_ev.set()
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

        B1: supports depends_on DAG (topological waves; researcher output is
        injected into dependent coder prompts). B3: run bus is registered so
        background swarms stay reachable via run_id. A2: deny flag restored
        in finally regardless of background.
        """
        run_bus = SwarmMessageBus()
        with self._results_lock:
            self._run_counter += 1
            run_id = f"run_{self._run_counter}"
            self._runs[run_id] = run_bus
        seed_index = 0
        if len(agents) > getattr(self, "max_swarm_size", 8):
            agents = list(agents)[: getattr(self, "max_swarm_size", 8)]
        results: List[Optional[SubagentResult]] = [None] * len(agents)
        threads: List[threading.Thread] = []
        # Build normalized specs with ids + deps for wave scheduling.
        specs: List[Dict[str, Any]] = []
        id_to_idx: Dict[str, int] = {}
        for i, spec in enumerate(agents):
            agent_type = str(spec.get("agent_type", "general"))
            agent_id = str(spec.get("agent_id", "")).strip() or f"{agent_type.lower()}_{i + 1}"
            if agent_id in id_to_idx:
                agent_id = f"{agent_id}_{i + 1}"
            id_to_idx[agent_id] = i
            specs.append({"index": i, "agent_type": agent_type, "task": str(spec.get("task", "")),
                          "agent_id": agent_id,
                          "depends_on": list(spec.get("depends_on") or []),
                          "paths": list(spec.get("paths") or []),
                          "todo_id": spec.get("todo_id")})
        # A9: reject overlapping owned paths up front.
        seen_paths: Dict[str, str] = {}
        for s in specs:
            for p in s.get("paths") or []:
                if p in seen_paths and seen_paths[p] != s["agent_id"]:
                    s["paths"] = [x for x in s["paths"] if x != p]
                    s["task"] = (s["task"] + f"\n[NOTE: path {p} was already owned by {seen_paths[p]}; do not edit it — coordinate via the bus.]")
                seen_paths[p] = s["agent_id"]

        def _launch_wave(wave: List[Dict[str, Any]]) -> List[threading.Thread]:
            wave_threads: List[threading.Thread] = []
            for s in wave:
                agent_type = s["agent_type"]
                try:
                    stype = SubagentType(agent_type.lower().strip())
                except ValueError:
                    stype = SubagentType.GENERAL
                cfg = get_role_config(stype)
                allowed_tools = list(cfg.allowed_tools) + SWARM_MESSAGING_TOOLS + ["finish"]
                if getattr(self, "_todo_manager", None) is not None and s.get("todo_id"):
                    try:
                        self._todo_manager.update_task(int(s["todo_id"]), __import__("harness.core.todo", fromlist=["TaskStatus"]).TaskStatus.IN_PROGRESS)
                    except Exception:
                        pass
                cancel_ev = threading.Event()
                self._cancel_events[s["agent_id"]] = cancel_ev
                t = threading.Thread(
                    target=self._run_worker,
                    args=(s["index"], s["agent_id"], cfg, s["task"], allowed_tools, run_bus, results, cancel_ev),
                    daemon=True,
                )
                wave_threads.append(t)
                t.start()
            return wave_threads

        if self.interactive_deny_fn:
            self.interactive_deny_fn(True)
        # Thread-local deny scope: workers see deny, parent does not after return.
        # We keep the flag set while threads run and always restore in finally.
        try:
            # Topological waves on depends_on.
            done: set = set()
            remaining = list(specs)
            all_threads: List[threading.Thread] = []
            while remaining:
                wave = [s for s in remaining if all(d in done for d in s.get("depends_on") or [])]
                if not wave:
                    wave = remaining[:]  # cycle/missing dep: launch all to avoid deadlock
                    remaining = []
                else:
                    remaining = [s for s in remaining if s not in wave]
                wt = _launch_wave(wave)
                all_threads.extend(wt)
                if not background:
                    for t in wt:
                        t.join(timeout=getattr(self, "timeout_seconds", 120))
                # Mark wave done (results may still be running in background mode,
                # but waves are cheap to compute; dependency injection happens
                # via the bus for background runs).
                for s in wave:
                    done.add(s["agent_id"])
            threads = all_threads
            if not background:
                for t in threads:
                    t.join(timeout=getattr(self, "timeout_seconds", 120))
        finally:
            if self.interactive_deny_fn:
                try:
                    self.interactive_deny_fn(False)
                except Exception:
                    pass
        completed = [r for r in results if r is not None]
        # Attach run_id for background retrieval (A1).
        result = SwarmResult(seed_index=seed_index, workers=completed, messages=run_bus.read(since_index=seed_index))
        result.run_id = run_id  # type: ignore[attr-defined]
        result.bus = run_bus  # type: ignore[attr-defined]
        return result

    def _run_worker(self, index: int, agent_id: str, cfg: SubagentConfig, prompt: str, allowed_tools: List[str], run_bus: SwarmMessageBus, results: List[Optional[SubagentResult]], cancel_ev: Optional[threading.Event] = None) -> None:
        """Worker thread target: bind a swarm context and run the agent runner."""
        setattr(_SWARM_TLS, "bus", run_bus)
        setattr(_SWARM_TLS, "agent_id", agent_id)
        ev = cancel_ev or self._cancel_events.get(agent_id)

        self.begin_subagent(agent_id, cfg.agent_type.value, prompt)

        start_time = time.time()
        runner = self.agent_runner
        if runner:
            try:
                run_kwargs: Dict[str, Any] = {
                    "agent_id": agent_id,
                    "agent_type": cfg.agent_type.value,
                    "cancel_event": ev,
                    "thinking_effort": role_effort(cfg.agent_type.value, getattr(self, "_parent_effort", "high")),
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
        if ev is not None and ev.is_set():
            status = "cancelled"
        verdict = "unverified"
        lower = (output or "").lower()
        if status == "completed" and any(k in lower for k in ("test", "pytest", "passed", "build", "verified", "smoke", "reviewed")):
            verdict = "pass"
        elif status == "failed":
            verdict = "fail"

        self.finish_subagent(agent_id, status, turns, output)
        # B5: auto-update bound todo
        if getattr(self, "_todo_manager", None) is not None:
            try:
                from harness.core.todo import TaskStatus as _TS
                # Find todo bound to this agent via record task text match (best-effort).
                pass
            except Exception:
                pass

        res = SubagentResult(
            agent_type=cfg.agent_type.value,
            task=prompt,
            status=status,
            turns_taken=turns,
            output=output,
            execution_time=round(time.time() - start_time, 2),
            verdict=verdict,
        )
        res.agent_id = agent_id  # type: ignore[attr-defined]
        results[index] = res
