"""
Git Version Control tools for Harness.
Provides git status inspection, diff viewing, and commit history tracking.
"""
import subprocess
from typing import Dict, Any, Optional, Tuple
from harness.tools.base import Tool


def check_git_behind() -> Tuple[bool, int]:
    """
    Check if the current branch is behind its upstream branch.
    Returns (is_behind, commits_behind_count).
    """
    try:
        # First check if we're in a git repo
        subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        
        # Get the current branch name
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode("utf-8").strip()
        
        if branch == "HEAD" or not branch:
            # Detached HEAD or no branch
            return False, 0
        
        # Check if upstream is configured
        upstream = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode("utf-8").strip()
        
        if not upstream:
            return False, 0
        
        # Count commits behind
        behind_output = subprocess.check_output(
            ["git", "rev-list", "--count", f"HEAD..{upstream}"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8").strip()
        
        commits_behind = int(behind_output) if behind_output.isdigit() else 0
        return commits_behind > 0, commits_behind
        
    except subprocess.CalledProcessError:
        # Not a git repo, no upstream, or other git error
        return False, 0
    except Exception:
        # Any other error
        return False, 0


def get_git_update_command() -> str:
    """Get the recommended update command for the user."""
    return "harness update"


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
