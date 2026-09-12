"""
Subagent spawning tool for Harness.
Allows the model to delegate focused, isolated tasks to specialized subagents.
"""
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.subagents import SubagentOrchestrator

class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate an isolated subtask to a specialized subagent worker "
        "(researcher, planner, coder, tester, reviewer). The subagent executes with "
        "its own isolated context and returns a concise synthesized report."
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
        },
        "required": ["agent_type", "task"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agent_type: str, task: str, **kwargs) -> str:
        res = self.orchestrator.spawn(agent_type, task)
        return (
            f"=== SUBAGENT RESULT ({res.agent_type.upper()}) ===\n"
            f"Status: {res.status} | Execution Time: {res.execution_time}s\n\n"
            f"{res.output}\n"
            f"=================================================="
        )
