"""
Shell command execution tool for Harness.
Executes bash commands with strict timeout, directory confinement, and security gates.
"""
import os
import time
import subprocess
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.permissions import PermissionManager, RiskLevel, analyze_command_risk

class RunCommandTool(Tool):
    name = "run_command"
    description = "Execute a shell command in bash, capturing stdout, stderr, and exit code."
    action_type = "command"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The exact bash command line to execute."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60, max: 300)."},
            "cwd": {"type": "string", "description": "Working directory for the command (optional)."},
        },
        "required": ["command"],
    }

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self.permission_manager = permission_manager

    def execute(self, command: str, timeout: int = 60, cwd: Optional[str] = None, **kwargs) -> str:
        cmd = command.strip()
        work_dir = cwd or os.getcwd()

        # Check permissions
        if self.permission_manager:
            risk = analyze_command_risk(cmd)
            details = {
                "command": cmd,
                "cwd": work_dir,
                "risk": risk,
                "summary": f"Execute `{cmd}` (Risk: {risk.value.upper()})",
            }
            allowed = self.permission_manager.check_permission("command", details)
            if not allowed:
                return f"Error: Command execution rejected by security policy or user: `{cmd}`"

        t_limit = min(max(1, timeout), 300)
        start_time = time.time()

        try:
            run_kwargs = dict(
                shell=True,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=t_limit,
            )
            # On Unix force bash so shell pipelines behave identically everywhere.
            # On Windows, shell=True uses the default cmd.exe shell.
            if os.name != "nt":
                run_kwargs["executable"] = "/bin/bash"
            proc = subprocess.run(cmd, **run_kwargs)
            elapsed = round(time.time() - start_time, 2)
            out = proc.stdout
            err = proc.stderr
            code = proc.returncode

            # Format result
            res = [f"Command exited with code {code} ({elapsed}s)"]
            if out:
                res.append(f"--- STDOUT ---\n{out.rstrip()}")
            if err:
                res.append(f"--- STDERR ---\n{err.rstrip()}")
            if not out and not err:
                res.append("(No output produced)")

            # Full output is delivered to the agent untouched — display-level
            # truncation happens only in the TUI renderer.
            return "\n".join(res)

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {t_limit} seconds: `{cmd}`"
        except Exception as ex:
            return f"Error running command `{cmd}`: {str(ex)}"
