"""
Permissions and Security Governance for Harness.
Controls tool execution rights across Secure, Default, and Full Access profiles.
"""
from enum import Enum
import os
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
        # When True, approval prompts are auto-denied (used by concurrent worker
        # threads so subagents never block on interactive input).
        self.interactive_deny = False

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
            if os.path.isabs(target_file) and not target_file.startswith(str(Path.cwd())):
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

        if action_type == "computer_input":
            # Desktop input (mouse/keyboard/clipboard) requires approval in DEFAULT
            return self._request_user_approval(
                f"Computer input requires approval: {details.get('summary', str(details))}", details
            )

        return True

    @staticmethod
    def _input_with_echo(prompt: str) -> str:
        """Read input with echo restored even inside no_echo_stdin."""
        import sys
        restored = False
        old_attrs = None
        fd = None
        try:
            import termios
            try:
                fd = sys.stdin.fileno()
                if sys.stdin.isatty():
                    old_attrs = termios.tcgetattr(fd)
                    import termios as _t
                    if not (old_attrs[3] & _t.ECHO):
                        new_attrs = termios.tcgetattr(fd)
                        new_attrs[3] |= _t.ECHO | _t.ECHOCTL | _t.ECHOE | _t.ECHOK
                        termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
                        restored = True
            except Exception:
                pass
        except ImportError:
            pass
        try:
            raw = input(prompt)
            # Echo visibly
            try:
                from rich.console import Console
                Console().print(f"[dim]↳ You typed:[/dim] [bold cyan]{raw}[/bold cyan]")
            except Exception:
                print(f"↳ You typed: {raw}")
            return raw
        finally:
            if restored and old_attrs is not None and fd is not None:
                try:
                    import termios
                    termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
                except Exception:
                    pass

    def _request_user_approval(self, message: str, details: Dict[str, Any]) -> bool:
        if self.interactive_deny:
            return False
        if self.approver_callback:
            try:
                return self.approver_callback(message, details)
            except Exception:
                # Fall through to terminal fallback if handler fails
                pass
        import sys
        if not sys.stdin.isatty():
            return False
        # Rich detailed prompt with echo-visible input
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table
            import time as _time
            console = Console()
            # Build details table
            details_lines = []
            # Show action type and summary
            action = details.get("action_type") or details.get("type") or details.get("tool") or "action"
            summary = details.get("summary") or details.get("command") or details.get("path") or str(details)[:120]
            risk = details.get("risk") or ""
            # Panel content
            body_lines = [
                f"[bold white]{message}[/bold white]",
                "",
                f"[dim]Time: {_time.strftime('%Y-%m-%d %H:%M:%S')}  |  Action: {action}  |  Risk: {risk or 'unknown'}[/dim]",
                f"[dim]Details:[/dim] [white]{summary}[/white]",
            ]
            # Show full details dict if not too large
            if len(str(details)) < 300 and details:
                try:
                    import json as _json
                    pretty = _json.dumps(details, indent=2)[:400]
                    if pretty and pretty != "{}":
                        body_lines.append("")
                        body_lines.append(f"[dim]Full details:[/dim]\n[dim]{pretty}[/dim]")
                except Exception:
                    pass
            console.print(Panel("\n".join(body_lines), title="⚠️  PERMISSION REQUIRED", border_style="yellow", padding=(1, 2)))
            console.print("[dim]Your typing will be shown as  ↳ You typed: ...  |  [y]es / [n]o  (default N)[/dim]")
            raw = self._input_with_echo("Allow this action? [y/N]: ").strip().lower()
            allowed = raw in ("y", "yes")
            # Confirmation panel
            if allowed:
                console.print(Panel(f"[bold green]✔ Approved:[/bold green] [white]{message[:120]}[/white]\n\n[dim]Proceeding with action...[/dim]", border_style="green"))
            else:
                console.print(Panel(f"[bold red]✖ Denied:[/bold red] [white]{message[:120]}[/white]\n\n[dim]Action will be skipped.[/dim]", border_style="red"))
            return allowed
        except (EOFError, KeyboardInterrupt):
            try:
                from rich.console import Console
                Console().print("\n[dim]Denied (cancelled).[/dim]")
            except Exception:
                print("\nDenied (cancelled).")
            return False
        except Exception:
            pass
        # Plain fallback
        print(f"\n⚠️  [PERMISSION REQUIRED]: {message}")
        print(f"  Details: {details.get('summary', str(details))[:200]}")
        try:
            raw = self._input_with_echo("Allow this action? [y/N]: ").strip().lower()
            allowed = raw in ("y", "yes")
            print(f"{'✔ Approved' if allowed else '✖ Denied'}: {raw}\n")
            return allowed
        except (EOFError, KeyboardInterrupt):
            print("\nDenied (cancelled).")
            return False
