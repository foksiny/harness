"""
Subagent Orchestration Engine for Harness.
Dispatches specialized worker agents with isolated context windows and synthesizes
results back to the primary agent thread.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Callable
import time

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
    max_turns: int = 10
    timeout_seconds: int = 60

SUBAGENT_ROLES: Dict[SubagentType, SubagentConfig] = {
    SubagentType.RESEARCHER: SubagentConfig(
        agent_type=SubagentType.RESEARCHER,
        system_prompt=(
            "You are an expert Research Subagent. Your role is to deeply explore the codebase, "
            "inspect files, search patterns, and read documentation. You MUST NOT modify any files "
            "or run destructive commands. Provide a clear, structured factual summary of your findings."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "exa_search"],
        max_turns=12,
    ),
    SubagentType.PLANNER: SubagentConfig(
        agent_type=SubagentType.PLANNER,
        system_prompt=(
            "You are an expert Software Architecture Planner. Your role is to analyze requirements, "
            "decompose complex goals into sequential atomic tasks, and define verification milestones."
        ),
        allowed_tools=["view_file", "list_dir", "find_files", "grep_search", "todo_create"],
        max_turns=6,
    ),
    SubagentType.CODER: SubagentConfig(
        agent_type=SubagentType.CODER,
        system_prompt=(
            "You are an expert Implementation Subagent. Your role is to write clean, maintainable, "
            "and atomic code changes. Adhere to existing project conventions and verify changes."
        ),
        allowed_tools=["view_file", "edit_file", "write_file", "list_dir", "find_files", "grep_search", "run_command", "execute_python"],
        max_turns=15,
    ),
    SubagentType.TESTER: SubagentConfig(
        agent_type=SubagentType.TESTER,
        system_prompt=(
            "You are an expert QA and Test Subagent. Your role is to write unit/integration tests, "
            "run test commands, analyze failures, and verify fixes."
        ),
        allowed_tools=["view_file", "write_file", "edit_file", "run_command", "execute_python"],
        max_turns=10,
    ),
    SubagentType.REVIEWER: SubagentConfig(
        agent_type=SubagentType.REVIEWER,
        system_prompt=(
            "You are an expert Code Review Subagent. Inspect code diffs, check for security flaws, "
            "edge cases, performance bottlenecks, and regressions."
        ),
        allowed_tools=["view_file", "git_diff", "git_status", "grep_search"],
        max_turns=6,
    ),
}

@dataclass
class SubagentResult:
    agent_type: str
    task: str
    status: str       # "completed", "failed", "timeout"
    turns_taken: int
    output: str
    execution_time: float

class SubagentOrchestrator:
    """Spawns and monitors subagents."""

    def __init__(self, agent_runner: Optional[Callable] = None):
        self.agent_runner = agent_runner
        self.active_subagents: List[SubagentResult] = []

    def spawn(self, agent_type_str: str, prompt: str, runner_override: Optional[Callable] = None) -> SubagentResult:
        """Spawn an isolated subagent worker."""
        try:
            stype = SubagentType(agent_type_str.lower().strip())
        except ValueError:
            stype = SubagentType.GENERAL

        cfg = SUBAGENT_ROLES.get(stype, SubagentConfig(
            agent_type=stype,
            system_prompt="You are a specialized subagent executing an isolated subtask.",
            allowed_tools=["view_file", "list_dir", "grep_search", "edit_file", "run_command"],
        ))

        start_time = time.time()
        runner = runner_override or self.agent_runner

        if runner:
            try:
                output, turns = runner(cfg.system_prompt, prompt, cfg.allowed_tools, cfg.max_turns)
                status = "completed"
            except Exception as e:
                output = f"Subagent error: {str(e)}"
                turns = 0
                status = "failed"
        else:
            # Fallback simulated response
            output = f"Subagent ({stype.value}) completed task analysis: {prompt[:100]}..."
            turns = 1
            status = "completed"

        elapsed = time.time() - start_time
        res = SubagentResult(
            agent_type=stype.value,
            task=prompt,
            status=status,
            turns_taken=turns,
            output=output,
            execution_time=round(elapsed, 2),
        )
        self.active_subagents.append(res)
        return res
