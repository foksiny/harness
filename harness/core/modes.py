"""
Operational modes for Harness.
Defines PLAN, BUILD, and SUPER modes and their runtime constraints.
"""
from enum import Enum
from typing import Set

class Mode(str, Enum):
    PLAN = "plan"
    BUILD = "build"
    SUPER = "super"

    @classmethod
    def from_string(cls, val: str) -> "Mode":
        val_clean = (val or "").strip().lower()
        if val_clean in ("plan", "p", "research"):
            return cls.PLAN
        if val_clean in ("super", "s", "turbo", "goal"):
            return cls.SUPER
        return cls.BUILD

MODE_DESCRIPTIONS = {
    Mode.PLAN: "Plan Mode: Read-only architectural design, file inspection, and planning. File mutations are disabled.",
    Mode.BUILD: "Build Mode: Full developer agent. Read, write, edit, execute shell commands, and run tests.",
    Mode.SUPER: "Super Mode: Autonomous multi-step engine. Decomposes goals, delegates to subagents, and self-verifies.",
}

# Tools blocked in PLAN mode to prevent unintended state mutations
PLAN_MODE_BLOCKED_TOOLS: Set[str] = {
    "write_file",
    "edit_file",
    "replace_file_content",
    "execute_python",   # Unless explicitly marked read-only
    # Browser mutations: PLAN may observe the web (navigate/read/screenshot)
    # but not interact with it.
    "browser_launch",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_scroll",
    "browser_evaluate",
    "browser_tab",
    "browser_navigation",
    "browser_close",
}

# Dangerous shell command prefixes blocked in PLAN mode
PLAN_MODE_BLOCKED_COMMANDS: Set[str] = {
    "rm", "mv", "cp", "touch", "mkdir", "git commit", "git push", "git checkout -b", "pip install", "npm install"
}

def is_tool_allowed_in_mode(tool_name: str, mode: Mode) -> bool:
    """Check if tool can be executed in current mode."""
    if mode == Mode.PLAN:
        return tool_name not in PLAN_MODE_BLOCKED_TOOLS
    return True
