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
            "evidence": {
                "type": "string",
                "description": "What was checked and the outcome (tests/builds run, outputs observed). Required for code-changing turns; the harness nudges-and-continues without it.",
            },
            "files_changed": {
                "type": "string",
                "description": "Comma-separated list of files created/modified, or 'none' for question-only turns.",
            },
            "next_steps": {
                "type": "string",
                "description": "What remains or what you would do next.",
            },
            "asked": {
                "type": "string",
                "description": "What the user literally asked for (one line). Part of the Intent+Enhancement receipt.",
            },
            "built": {
                "type": "string",
                "description": "What was actually delivered (one line).",
            },
            "delta": {
                "type": "string",
                "description": "How the delivered outcome improves on the literal request, or 'none'.",
            },
            "verified": {
                "type": "boolean",
                "description": "True if verification was actually run this turn (not just claimed).",
            },
        },
        "required": ["summary"],
    }

    def execute(self, summary: str = "", evidence: str = "", files_changed: str = "",
                next_steps: str = "", asked: str = "", built: str = "", delta: str = "",
                verified: bool = False, **kwargs) -> str:
        base = str(summary if summary else "Task complete.")
        # Backward-compat: summary-only calls return verbatim (existing tests/callers).
        if not any([evidence, files_changed, next_steps, asked, built, delta, verified]):
            return base
        parts = [base]
        if files_changed and files_changed.strip().lower() not in ("none", "n/a", "na"):
            parts.append(f"Files changed: {files_changed}")
        if evidence:
            parts.append(f"Evidence: {evidence}")
        parts.append(f"Verified: {'yes' if verified else 'no'}")
        if next_steps:
            parts.append(f"Next steps: {next_steps}")
        if asked or built or delta:
            parts.append(f"Intent receipt — Asked: {asked or '(unspecified)'} | Built: {built or base} | Delta: {delta or 'none'}")
        return "\n".join(parts)