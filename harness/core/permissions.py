"""
Permissions and Security Governance for Harness.
Controls tool execution rights across Secure, Default, and Full Access profiles.
"""
from enum import Enum
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any

class PermissionLevel(str, Enum):
    SECURE = "secure"      # Prompt on all modifications, executions, and file writes
    DEFAULT = "default"    # Prompt only on potentially destructive or out-of-bounds operations
    FULL = "full"          # Autonomous execution without confirmation prompts

    @classmethod
    def from_string(cls, val: str) -> "PermissionLevel":
        v = (val or "").strip().lower()
        if v in ("secure", "strict", "read_only", "safe"):
            return cls.SECURE
        if v in ("full", "full_access", "auto", "unrestricted", "yolo"):
            return cls.FULL
        return cls.DEFAULT

class RiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# High-risk patterns in shell commands
CRITICAL_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\s+[/~]",              # rm -rf / or ~
    r"\brm\s+-[rf]{1,2}\s+\*",                # rm -rf *
    r"\bmkfs\b",                              # format filesystem
    r"\bdd\s+if=",                            # dd raw write
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", # fork bomb
    r">\s*/dev/sd[a-z]",                      # raw disk overwrite
    r"\bchmod\s+-[R]\s+777\s+/",              # wide permissions on root
]

HIGH_RISK_COMMAND_PATTERNS = [
    r"\bsudo\b",                              # privileged escalation
    r"\bcurl\b.*\|\s*(ba)?sh",                # pipe url to shell
    r"\bwget\b.*\|\s*(ba)?sh",
    r"\bgit\s+reset\s+--hard",                # hard git reset
    r"\bgit\s+clean\s+-[fdx]{1,3}",           # clean untracked files
    r"\bkillall\b",
    r"\bpkill\b",
]

MEDIUM_RISK_COMMAND_PATTERNS = [
    r"\brm\b",
    r"\bmv\b",
    r"\bcp\b",
    r"\bpip\s+install",
    r"\bnpm\s+install",
]

def analyze_command_risk(command: str) -> RiskLevel:
    """Classify the risk level of a shell command."""
    cmd = command.strip()
    for pat in CRITICAL_COMMAND_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return RiskLevel.CRITICAL
    for pat in HIGH_RISK_COMMAND_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return RiskLevel.HIGH
    for pat in MEDIUM_RISK_COMMAND_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return RiskLevel.MEDIUM
    return RiskLevel.LOW

class PermissionManager:
    """Evaluates whether an action requires user approval based on active permission level."""

    def __init__(self, level: PermissionLevel = PermissionLevel.DEFAULT, approver_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None):
        self.level = level
        self.approver_callback = approver_callback

    def set_level(self, level: PermissionLevel) -> None:
        self.level = level

    def check_permission(self, action_type: str, details: Dict[str, Any]) -> bool:
        """
        Check if action is permitted.
        Returns True if action should proceed, False if rejected by policy or user.
        """
        if self.level == PermissionLevel.FULL:
            # Under Full Access, only block literally catastrophic self-destruction patterns
            if action_type == "command":
                cmd = details.get("command", "")
                if analyze_command_risk(cmd) == RiskLevel.CRITICAL:
                    return self._request_user_approval(
                        "CRITICAL RISK COMMAND BLOCKED BY SAFETY INTERLOCK: " + cmd, details
                    )
            return True

        if self.level == PermissionLevel.SECURE:
            # In Secure mode, read tools are auto-approved; writes/execs always require prompt
            if action_type in ("read_file", "list_dir", "search", "web_search", "todo", "ask_user"):
                return True
            prompt_msg = f"Secure Mode requires approval for [{action_type}]: {details.get('summary', str(details))}"
            return self._request_user_approval(prompt_msg, details)

        # DEFAULT mode: Balanced
        if action_type in ("read_file", "list_dir", "search", "web_search", "todo", "ask_user"):
            return True

        if action_type in ("write_file", "edit_file"):
            # Editing local workspace files is allowed in Default mode
            target_file = details.get("path", "")
            if target_file.startswith("/") and not target_file.startswith(str(Path.cwd())):
                return self._request_user_approval(f"Modifying file outside workspace: {target_file}", details)
            return True

        if action_type == "command":
            cmd = details.get("command", "")
            risk = analyze_command_risk(cmd)
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                return self._request_user_approval(f"Command flagged as {risk.value.upper()} risk: {cmd}", details)
            return True

        if action_type == "execute_python":
            risk = details.get("risk", RiskLevel.LOW)
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                return self._request_user_approval(f"Python script contains {risk.value.upper()} risk operations", details)
            return True

        return True

    def _request_user_approval(self, message: str, details: Dict[str, Any]) -> bool:
        if self.approver_callback:
            return self.approver_callback(message, details)
        import sys
        if not sys.stdin.isatty():
            return False
        # Default terminal prompt fallback
        print(f"\n⚠️  [PERMISSION REQUIRED]: {message}")
        try:
            choice = input("Allow this action? [y/N]: ").strip().lower()
            return choice in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False
