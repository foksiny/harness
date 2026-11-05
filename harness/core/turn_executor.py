"""Turn phase helpers (C1). Extracted from agent.py for maintainability.

The agent loop calls these predicates; heavy dispatch stays in agent.py until
a full extraction is proven safe by the test suite.
"""
from typing import Dict, Any


def classify_step(tool_names: list) -> str:
    if any(n in ("view_file", "list_dir", "grep_search") for n in tool_names):
        return "orient"
    if any(n in ("write_file", "edit_file") for n in tool_names):
        return "act"
    if any(n in ("run_command", "execute_python") for n in tool_names):
        return "verify"
    return "act"


def should_require_plan(first_tool: str, has_todos: bool, mutating: bool) -> bool:
    return bool(mutating and not has_todos)


def goal_check(transcript_has_evidence: bool, open_todos: int) -> str:
    if open_todos:
        return f"{open_todos} todo(s) still open — continue or close them."
    if not transcript_has_evidence:
        return "No verification evidence yet — run tests/builds or explain why N/A."
    return "Goal satisfied."
