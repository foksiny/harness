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
        "(researcher, planner, coder, tester, reviewer). The subagent executes with "
        "its own isolated context in a background thread and returns immediately. "
        "Use `spawn_subagent` with `background=false` to block until the subagent "
        "finishes. By default subagents run in the background so you can continue working."
    )
    action_type = "subagent"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "enum": ["researcher", "planner", "coder", "tester", "reviewer"],
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
        if background:
            return (
                f"=== SUBAGENT SPAWNED ({res.agent_type.upper()}) ===\n"
                f"Status: {res.status} | Running in background\n"
                f"The subagent is processing in the background. You can continue working.\n"
                f"Use `subagent_list` or `subagent_status` to check progress.\n"
                f"=================================================="
            )
        return (
            f"=== SUBAGENT RESULT ({res.agent_type.upper()}) ===\n"
            f"Status: {res.status} | Execution Time: {res.execution_time}s\n\n"
            f"{res.output}\n"
            f"=================================================="
        )
