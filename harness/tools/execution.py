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
from harness.core.context_budget import DEFAULT_OUTPUT_LIMITS, truncate_output

_RUN_MAX_CHARS = int(DEFAULT_OUTPUT_LIMITS.get("run_command", 16000))

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
            "max_chars": {"type": "integer", "description": "Max chars of combined output to return (default ~16000; pass a larger value to capture verbose output in full)."},
        },
        "required": ["command"],
    }

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self.permission_manager = permission_manager

    def execute(self, command: str, timeout: int = 60, cwd: Optional[str] = None, max_chars: Optional[int] = None, **kwargs) -> str:
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
            else:
                run_kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
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

            combined = "\n".join(res)
            # Proactive context-budget cap. The agent can pass max_chars to
            # capture verbose output in full (e.g. test suites / benchmarks).
            cap = _RUN_MAX_CHARS if max_chars is None else int(max_chars)
            if cap > 0 and len(combined) > cap:
                return truncate_output(combined, cap, marker_note=(
                    f"...[output truncated by context budget: was {len(combined)} chars, "
                    f"keeping first {cap}]... (pass max_chars={len(combined)} to this "
                    f"run_command call to capture the full output)"
                ))
            return combined

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {t_limit} seconds: `{cmd}`"
        except Exception as ex:
            return f"Error running command `{cmd}`: {str(ex)}"
