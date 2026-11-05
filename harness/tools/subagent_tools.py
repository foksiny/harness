"""
Subagent spawning tool for Harness.
Allows the model to delegate focused, isolated tasks to specialized subagents.
Subagents run in parallel (background threads). The main agent can continue
working while subagents process in the background.
"""
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.subagents import SubagentOrchestrator


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate an isolated subtask to a specialized subagent worker "
        "(researcher, planner, coder, tester, reviewer, general). Background by default; "
        "use subagent_list/subagent_status/subagent_wait to retrieve results."
    )
    action_type = "subagent"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "enum": ["researcher", "planner", "coder", "tester", "reviewer", "general"],
                "description": "The specialization of the subagent to launch.",
            },
            "task": {"type": "string", "description": "Specific task prompt and instructions for the subagent."},
            "background": {
                "type": "boolean",
                "description": "Run in background (default true). Set to false to block until the subagent finishes.",
            },
        },
        "required": ["agent_type", "task"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agent_type: str, task: str, background: bool = True, **kwargs) -> str:
        res = self.orchestrator.spawn(agent_type, task, background=background)
        aid = getattr(res, "agent_id", "?")
        if background:
            return (
                f"=== SUBAGENT SPAWNED ({res.agent_type.upper()} {aid}) ===\n"
                f"Status: {res.status} | Running in background\n"
                f"The subagent is processing in the background. You can continue working.\n"
                f"Use `subagent_list`, `subagent_status <id>`, or `subagent_wait <id>` to retrieve results.\n"
                f"=================================================="
            )
        return (
            f"=== SUBAGENT RESULT ({res.agent_type.upper()} {aid}) ===\n"
            f"Status: {res.status} verdict={getattr(res, 'verdict', 'unverified')} | Execution Time: {res.execution_time}s\n\n"
            f"{res.output}\n"
            f"=================================================="
        )


class SubagentListTool(Tool):
    name = "subagent_list"
    description = "List all subagent/swarm workers with status, turns, and last tool."
    action_type = "subagent"
    is_read_only = True
    parameters = {"type": "object", "properties": {}}

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, **kwargs) -> str:
        recs = self.orchestrator.list_records()
        if not recs:
            return "No subagents spawned yet."
        lines = ["=== SUBAGENTS ==="]
        for r in recs:
            lines.append(f"{r.agent_id} [{r.agent_type}] {r.status} turns={r.turns} last={r.last_tool() or '-'} task={r.task[:80]}")
        return "\n".join(lines)


class SubagentStatusTool(Tool):
    name = "subagent_status"
    description = "Get status + output of one subagent by id."
    action_type = "subagent"
    is_read_only = True
    parameters = {"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agent_id: str, **kwargs) -> str:
        rec = self.orchestrator.get_record(agent_id)
        if rec is None:
            return f"Error: No subagent with id '{agent_id}'."
        return (f"{rec.agent_id} [{rec.agent_type}] {rec.status} turns={rec.turns}\n"
                f"Task: {rec.task}\nOutput:\n{rec.output or '(pending)'}")


class SubagentWaitTool(Tool):
    name = "subagent_wait"
    description = "Block until a background subagent finishes, then return its report."
    action_type = "subagent"
    is_read_only = False
    parameters = {"type": "object", "properties": {"agent_id": {"type": "string", "description": "Leave empty to wait for all."}}, "required": []}

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agent_id: str = "", **kwargs) -> str:
        self.orchestrator.wait_for_all(timeout=float(getattr(self.orchestrator, "timeout_seconds", 120)))
        if agent_id:
            return self.orchestrator.get_result(agent_id).output if self.orchestrator.get_result(agent_id) else f"No result for '{agent_id}'."
        recs = self.orchestrator.list_records()
        lines = ["=== SUBAGENT RESULTS ==="]
        for r in recs:
            lines.append(f"{r.agent_id} [{r.agent_type}] {r.status}\n{r.output or '(no output)'}\n---")
        return "\n".join(lines) or "No subagents."


class SubagentCancelTool(Tool):
    name = "subagent_cancel"
    description = "Cancel a running background subagent by id."
    action_type = "subagent"
    is_read_only = False
    parameters = {"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agent_id: str, **kwargs) -> str:
        return f"Cancelled {agent_id}." if self.orchestrator.cancel(agent_id) else f"Error: no running agent '{agent_id}'."
