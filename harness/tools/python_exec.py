"""
Python execution tool with sandboxed execution for Harness.
Allows the model to execute Python scripts, calculations, tests, and data analysis
with real isolation via nsjail, Docker, or restricted subprocess.

The sandbox provides:
- Filesystem isolation (read-only root, temp writable layer)
- Network isolation (optional)
- Resource limits (CPU, memory, time)
- Process limits
"""
import ast
import os
import sys
import time
from typing import Dict, Any, Optional, Tuple, List
from harness.tools.base import Tool
from harness.core.permissions import PermissionManager, RiskLevel
from harness.computer.sandbox import execute_sandboxed, detect_sandbox_backend, SandboxResult

# AST safety flags (informational - sandbox provides real isolation)
DANGEROUS_MODULES = {
    "pty": RiskLevel.HIGH,
    "ctypes": RiskLevel.HIGH,
    "subprocess": RiskLevel.HIGH,
    "socket": RiskLevel.MEDIUM,
    "shutil": RiskLevel.MEDIUM,
    "multiprocessing": RiskLevel.MEDIUM,
}

DANGEROUS_ATTRIBUTES = {
    ("os", "system"): RiskLevel.CRITICAL,
    ("os", "popen"): RiskLevel.HIGH,
    ("os", "remove"): RiskLevel.MEDIUM,
    ("os", "unlink"): RiskLevel.MEDIUM,
    ("os", "rmdir"): RiskLevel.MEDIUM,
    ("shutil", "rmtree"): RiskLevel.HIGH,
    ("subprocess", "run"): RiskLevel.HIGH,
    ("subprocess", "Popen"): RiskLevel.HIGH,
    ("subprocess", "call"): RiskLevel.HIGH,
}


class PythonSafetyVisitor(ast.NodeVisitor):
    """Inspects Python AST to flag risky modules, system calls, and constructs."""

    def __init__(self):
        self.risk: RiskLevel = RiskLevel.SAFE
        self.flags: List[str] = []

    def _elevate(self, level: RiskLevel, reason: str):
        self.flags.append(f"[{level.value.upper()}] {reason}")
        priority = [RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        if priority.index(level) > priority.index(self.risk):
            self.risk = level

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_mod = alias.name.split(".")[0]
            if base_mod in DANGEROUS_MODULES:
                self._elevate(DANGEROUS_MODULES[base_mod], f"Importing dangerous module: '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_mod = node.module.split(".")[0]
            if base_mod in DANGEROUS_MODULES:
                self._elevate(DANGEROUS_MODULES[base_mod], f"Importing from dangerous module: '{node.module}'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Detect eval() or exec()
        if isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "__import__"):
                self._elevate(RiskLevel.HIGH, f"Dynamic execution via '{node.func.id}()'")
        # Detect module.func() calls
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in DANGEROUS_ATTRIBUTES:
                    self._elevate(DANGEROUS_ATTRIBUTES[pair], f"Calling sensitive function '{pair[0]}.{pair[1]}()'")
        self.generic_visit(node)


def analyze_python_safety(code: str) -> Tuple[RiskLevel, List[str]]:
    """Analyze Python code AST and return assessed risk level with flags."""
    try:
        tree = ast.parse(code)
    except SyntaxError as se:
        return RiskLevel.LOW, [f"Syntax error during AST parse: {se}"]
    visitor = PythonSafetyVisitor()
    visitor.visit(tree)
    return visitor.risk, visitor.flags


class ExecutePythonTool(Tool):
    name = "execute_python"
    description = (
        "Execute Python code in a REAL sandbox (nsjail/Docker/restricted subprocess). "
        "Code runs with filesystem isolation, resource limits, and optional network isolation. "
        "AST analysis is performed for informational purposes only - the sandbox provides real "
        "security regardless of code content. Safe for untrusted code, tests, and experiments."
    )
    action_type = "execute_python"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The Python code to execute."},
            "timeout": {"type": "integer", "description": "Execution timeout in seconds (default: 30, max: 120)."},
            "network": {"type": "boolean", "description": "Allow network access (default: false)."},
        },
        "required": ["code"],
    }

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self.permission_manager = permission_manager

    def execute(self, code: str, timeout: int = 30, network: bool = False, **kwargs) -> str:
        code_str = code.strip()
        if not code_str:
            return "Error: Empty Python code provided."

        # AST Safety inspection (informational)
        risk, flags = analyze_python_safety(code_str)

        # Check permissions
        if self.permission_manager:
            code_preview = code_str if len(code_str) < 300 else code_str[:300] + "..."
            details = {
                "code": code_str,
                "risk": risk,
                "flags": flags,
                "backend": detect_sandbox_backend(),
                "summary": f"Execute Python ({risk.value.upper()}) in {detect_sandbox_backend()} sandbox\nFlags: {', '.join(flags) if flags else 'None'}\nCode:\n{code_preview}",
            }
            allowed = self.permission_manager.check_permission("execute_python", details)
            if not allowed:
                return f"Error: Python execution rejected by security policy: {', '.join(flags) if flags else 'User rejected'}"

        t_limit = min(max(1, timeout), 120)

        # Execute in sandbox
        result: SandboxResult = execute_sandboxed(
            code=code_str,
            timeout=t_limit,
            memory_limit_mb=256,
            cpu_time_limit=t_limit,
            network_allowed=network,
        )

        if not result.ok and result.error:
            return f"Error: {result.error}"

        # Format output
        res = [f"Python executed in {result.execution_time}s (Exit code {result.exit_code}, Backend: {result.backend}, Risk: {risk.value.upper()})"]
        if result.stdout:
            res.append(f"--- STDOUT ---\n{result.stdout.rstrip()}")
        if result.stderr:
            res.append(f"--- STDERR ---\n{result.stderr.rstrip()}")
        if not result.stdout and not result.stderr:
            res.append("(Script completed with no console output)")
        if flags:
            res.append(f"--- AST FLAGS ---\n{chr(10).join(flags)}")

        return "\n".join(res)
