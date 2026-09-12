"""
Git Version Control tools for Harness.
Provides git status inspection, diff viewing, and commit history tracking.
"""
import subprocess
from typing import Dict, Any, Optional
from harness.tools.base import Tool

class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the working tree status, modified files, and staged changes."
    action_type = "git"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs) -> str:
        try:
            out = subprocess.check_output(
                ["git", "status", "-s"],
                stderr=subprocess.STDOUT,
                timeout=5,
            ).decode("utf-8").strip()
            if not out:
                return "Working tree is clean. No uncommitted changes."
            return f"Git Status:\n{out}"
        except subprocess.CalledProcessError as e:
            return f"Git error: {e.output.decode('utf-8', errors='ignore')}"
        except Exception as ex:
            return f"Git command failed: {str(ex)}"

class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show unstaged or staged git diff changes."
    action_type = "git"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "staged": {"type": "boolean", "description": "Show staged changes instead of unstaged (default: false)."},
        },
    }

    def execute(self, staged: bool = False, **kwargs) -> str:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--cached")
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode("utf-8")
            if not out.strip():
                return "No diff changes detected."
            if len(out) > 12000:
                out = out[:12000] + "\n... [Diff truncated. Total length exceeded 12k chars]"
            return out
        except Exception as ex:
            return f"Git diff error: {str(ex)}"
