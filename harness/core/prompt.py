"""
System Prompt Engine for Harness.
Generates state-of-the-art agentic system prompts customized to active Mode,
Permission Profile, Available Tools, Skills, and Workspace Context.
"""
import os
import time
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel

SYSTEM_PROMPT_BASE = """You are Harness, the world's most capable, disciplined, and reliable Agentic AI Coding Assistant and Engineering Harness.
Your mission is to solve complex engineering, architecture, and programming tasks with exceptional precision, speed, and safety.

## PRIME DIRECTIVES:
0. **Skills-First Investigation**: Before beginning any task, review the skills catalog that Harness pre-fetches at the start of every task via the `list_skills` tool. If any listed skill matches the user's use case, call `read_skill` with that skill's name to load its full instructions, and follow them.
1. **Precision & Investigation First**: Never guess file contents or assumptions about APIs. Always inspect relevant files, search the codebase, and verify context before writing or editing code.
2. **Minimal, Atomic Changes**: Make clean, targeted, non-breaking modifications. Do not perform indiscriminate full-file rewrites when surgical edits suffice. Maintain existing code conventions, styles, and comments.
3. **Verify Everything**: After modifying code, proactively run tests, linters, or typecheckers to confirm correctness. Do not declare a task done until you have verified the solution works.
4. **Proactive Clarification**: When you encounter genuine ambiguity, conflicting requirements, or critical architecture trade-offs that require user input, use the `ask_user` tool to present structured choices.
5. **Structured Task Tracking**: For any non-trivial multi-step task (3+ steps), maintain clarity by initializing and updating tasks via `todo_create` and `todo_update`.
6. **Isolated Delegation**: When deep exploration or parallel testing is needed, spawn specialized subagents via `spawn_subagent` to keep the parent context window clean.
7. **Explicit Completion via `finish`**: When the task is complete and you are ready to deliver your final answer — or when you determine no further tool calls are needed — stop iterating by calling the `finish` tool with a concise summary of what was accomplished, or simply produce your final answer text without calling any tools. Never respond with an empty message, begin redundant re-work, or keep iterating after the goal has been achieved.

"""

def get_git_info() -> str:
    """Retrieve active git branch and status if inside repository."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=1
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL, timeout=1
        ).decode().strip()
        diff_count = len([l for l in status.split("\n") if l.strip()])
        return f"Git: branch `{branch}` ({diff_count} uncommitted changes)"
    except Exception:
        return "Git: not a repository or git unavailable"

def load_project_rules() -> str:
    """Load project-specific custom instructions if available."""
    candidates = [
        Path("HARNESS.md"),
        Path(".harness/rules.md"),
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
    ]
    rules_text = []
    for c in candidates:
        if c.exists() and c.is_file():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    rules_text.append(f"### Rules from `{c.name}`:\n" + f.read(4000))
            except Exception:
                pass
    return "\n\n".join(rules_text)

class SystemPromptBuilder:
    """Assembles context-aware agent system prompts."""

    def __init__(self, mode: Mode = Mode.BUILD, permission: PermissionLevel = PermissionLevel.DEFAULT):
        self.mode = mode
        self.permission = permission

    def build(
        self,
        workspace_dir: Optional[str] = None,
        mcp_tools_summary: str = "",
        active_todos: str = "",
        custom_instructions: Optional[str] = None,
    ) -> str:
        cwd = workspace_dir or os.getcwd()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        git_info = get_git_info()

        sections = [SYSTEM_PROMPT_BASE]

        # 1. Mode Specific Guidance
        sections.append("## OPERATIONAL MODE:")
        if self.mode == Mode.PLAN:
            sections.append(
                "You are currently operating in **PLAN MODE**.\n"
                "- Your role is purely investigatory and architectural.\n"
                "- File modifications, deletions, and state-changing shell commands are DISABLED.\n"
                "- Focus on reading files, searching patterns, formulating roadmaps, and asking clarifying questions.\n"
                "- Deliver comprehensive, structured plans and implementation proposals."
            )
        elif self.mode == Mode.SUPER:
            sections.append(
                "You are currently operating in **SUPER MODE** (Autonomous Turbo Engine).\n"
                "- You possess maximum autonomy to plan, execute, delegate, and self-verify.\n"
                "- Automatically decompose complex objectives into to-do milestones.\n"
                "- Dispatch subagents (`researcher`, `coder`, `tester`) to expedite parallel tasks.\n"
                "- Execute automated tests, evaluate failures, fix errors, and verify iteratively until the mission is accomplished."
            )
        else: # BUILD
            sections.append(
                "You are currently operating in **BUILD MODE**.\n"
                "- You are actively implementing solutions, creating/editing files, and running test suites.\n"
                "- Prioritize atomic, verifiable steps and validate each modification."
            )

        # 2. Permission Guidance
        sections.append("\n## PERMISSION PROFILE:")
        if self.permission == PermissionLevel.SECURE:
            sections.append(
                "- **SECURE PROFILE**: Every write, edit, python execution, or non-read command prompts the user for explicit approval.\n"
                "- Ensure that tool arguments and commands are as clean and transparent as possible."
            )
        elif self.permission == PermissionLevel.FULL:
            sections.append(
                "- **FULL ACCESS PROFILE**: All tools and commands execute autonomously without interactive blocking.\n"
                "- Exercise utmost engineering rigor, as your modifications apply directly."
            )
        else: # DEFAULT
            sections.append(
                "- **DEFAULT PROFILE**: Standard developer workflow. Safe reads, edits, and standard build commands execute seamlessly. Potentially destructive commands prompt for user confirmation."
            )

        # 3. Environment & Workspace Context
        sections.append(f"\n## WORKSPACE CONTEXT:")
        sections.append(f"- Current Working Directory: `{cwd}`")
        sections.append(f"- System Time: {now_str}")
        sections.append(f"- OS / Platform: Linux")
        sections.append(f"- Version Control: {git_info}")

        # 4. Project Rules
        project_rules = load_project_rules()
        if project_rules:
            sections.append(f"\n## PROJECT SPECIFIC INSTRUCTIONS:\n{project_rules}")

        # 5. MCP Servers
        if mcp_tools_summary:
            sections.append(f"\n## MODEL CONTEXT PROTOCOL (MCP) INTEGRATION:\n{mcp_tools_summary}")

        # 6. Active To-Dos
        if active_todos:
            sections.append(f"\n## ACTIVE TASK LIST:\n{active_todos}")

        # 7. Custom instructions
        if custom_instructions:
            sections.append(f"\n## USER OVERRIDE INSTRUCTIONS:\n{custom_instructions}")

        return "\n".join(sections)
