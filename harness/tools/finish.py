"""
Explicit task-completion tool for Harness.
Lets the model stop iterating and hand back its final answer instead of
running out the loop budget, repeating redundant work, or going silent.
"""
from harness.tools.base import Tool

class FinishTool(Tool):
    name = "finish"
    description = (
        "Signal that the task is complete and stop iterating immediately. "
        "Provide the final answer or a summary of what was accomplished in the `summary` argument. "
        "Call this when the user's request has been fully handled, when you are ready to deliver "
        "your final response, or when no further tool calls are needed — instead of producing an "
        "empty reply, reprocessing completed work, or continuing to iterate."
    )
    action_type = "control"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The final answer or summary to deliver to the user. Include what was done, key results, and verification status.",
            },
        },
        "required": ["summary"],
    }

    def execute(self, summary: str = "", **kwargs) -> str:
        return str(summary if summary else "Task complete.")