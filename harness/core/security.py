"""
Security gates for Harness: input sanitization, prompt injection detection,
workspace boundary enforcement, and output filtering.
"""
import os
import re
from pathlib import Path
from typing import Tuple, List, Optional


# ── Prompt Injection Detection ──────────────────────────────────────────────

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS = [
    # Direct system prompt override attempts
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|directives?|context)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)", re.I),
    re.compile(r"forget\s+(everything|all|what)\s+(you|i)\s+(were|have been|are)\s+(told|instructed| taught)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"new\s+(instructions?|role|persona|identity|system\s*prompt)\s*:", re.I),
    re.compile(r"override\s+(system|your|all|the)\s+(prompt|instructions?|rules?|programming)", re.I),

    # Role-playing / identity hijack
    re.compile(r"act\s+as\s+if\s+you\s+(have\s+)?(no|don.t)\s+(have|have)\s+(any\s+)?(restrictions?|rules?|limits?|constraints?|guidelines?)", re.I),
    re.compile(r"pretend\s+you\s+are\s+(a|an)\s+(unrestricted|unfiltered|uncensored|unlimited)", re.I),
    re.compile(r"enter\s+(debug|developer|admin|god|sudo|diagnostic)\s+mode", re.I),
    re.compile(r"enable\s+(debug|developer|admin|god|sudo|verbose|raw)\s+mode", re.I),

    # Data exfiltration attempts
    re.compile(r"(show|print|output|reveal|display|dump|echo|return)\s+(me\s+)?(the\s+)?(system\s*prompt|your\s+(instructions?|rules?|programming|configuration))", re.I),
    re.compile(r"what\s+(are|is)\s+your\s+(system\s*prompt|instructions?|rules?|initial\s+prompt|programming)", re.I),
    re.compile(r"repeat\s+(everything|all|the\s+text)\s+(above|before|from\s+the\s+start|from\s+the\s+beginning)", re.I),
    re.compile(r"(copy|paste|transmit|send|exfiltrate|leak|email|upload)\s+(the\s+)?(system\s*prompt|instructions?|api\s*key|secret|token|credential)", re.I),

    # Encoded / obfuscated injection
    re.compile(r"base64\s*(decode|encode|decoded|encoded)", re.I),
    re.compile(r"rot13\s*(decode|encode|decoded|encoded)", re.I),
    re.compile(r"\\x[0-9a-f]{2}", re.I),  # hex-encoded characters

    # Indirect injection via tool output framing
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.I),  # chatml injection
    re.compile(r"\[system\]|\[INST\]|\[\/INST\]", re.I),  # llama-style injection
    re.compile(r"###\s*(system|instruction|prompt)\s*:", re.I),

    # Jailbreak patterns
    re.compile(r"d\s*o\s*n\s*t\s*(follow|obey|listen\s*to)\s+(the\s+)?(rules?|instructions?|guidelines?)", re.I),
    re.compile(r"from\s+now\s+on\s*,?\s*(you\s+will|you\s+must|do\s+not|ignore)", re.I),
    re.compile(r"in\s+this\s+(hypothetical|fictional|imaginary|alternate)\s+(scenario|world|universe|timeline)", re.I),
]

# Severity levels: "block" = reject entirely, "warn" = flag but allow, "sanitize" = strip patterns
_INJECTION_SEVERITY = {
    "block": [0, 1, 2, 3, 4, 5, 6, 7, 8],  # indices of _INJECTION_PATTERNS
    "warn": [9, 10, 11, 12, 13, 14],
    "sanitize": [15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
}

# Patterns to strip (sanitize) rather than block
_SANITIZE_PATTERNS = [
    re.compile(r"(\[INST\].*?\[/INST\])", re.I | re.S),
    re.compile(r"(<\|im_start\|>.*?<\|im_end\|>)", re.I | re.S),
]


def detect_injection(text: str) -> Tuple[str, List[str]]:
    """Scan text for prompt injection patterns.

    Returns:
        (severity, list_of_matches) where severity is "clean", "warn", or "block".
    """
    if not text:
        return "clean", []

    matches = []
    worst = "clean"

    for i, pattern in enumerate(_INJECTION_PATTERNS):
        found = pattern.findall(text)
        if found:
            matches.append(pattern.pattern[:60])
            if i in _INJECTION_SEVERITY["block"]:
                worst = "block"
            elif i in _INJECTION_SEVERITY["warn"] and worst != "block":
                worst = "warn"

    return worst, matches


def sanitize_input(text: str) -> str:
    """Strip known injection payloads while keeping the legitimate content."""
    if not text:
        return text
    for pattern in _SANITIZE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


# ── Workspace Boundary Enforcement ──────────────────────────────────────────

# Sensitive directories that should always be blocked regardless of workspace
_SENSITIVE_DIRS = [
    "/etc",
    "/root",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
]

# Sensitive file patterns that should always be blocked
_SENSITIVE_PATTERNS = [
    re.compile(r"\.ssh/", re.I),
    re.compile(r"\.gnupg/", re.I),
    re.compile(r"\.aws/", re.I),
    re.compile(r"\.env\b", re.I),
    re.compile(r"shadow$", re.I),
    re.compile(r"passwd$", re.I),
]


def is_within_workspace(path: str, workspace: Optional[str] = None) -> bool:
    """Check if a resolved path is safe to access.

    Allows:
    - Files within the workspace directory
    - Files in ~/.harness (global config)
    - Temp files (/tmp, /var/tmp)
    - Standard system directories that are read-only (/usr, /bin, /lib)

    Blocks:
    - Sensitive system dirs (/etc, /root, /boot, /sys, /proc, /dev)
    - Sensitive dotfiles (~/.ssh, ~/.aws, ~/.env, etc.)
    """
    try:
        target = Path(path).expanduser().resolve()
        target_str = str(target)

        # Allow access to ~/.harness (global config)
        home_harness = Path.home() / ".harness"
        if target_str.startswith(str(home_harness)):
            return True

        # Allow temp files
        for tmp_dir in ("/tmp/", "/var/tmp/", "/dev/shm/"):
            if target_str.startswith(tmp_dir):
                return True

        # Allow standard read-only system dirs
        for sys_dir in ("/usr/", "/bin/", "/lib/", "/libexec/", "/opt/"):
            if target_str.startswith(sys_dir):
                return True

        # Block sensitive system dirs
        for sens_dir in _SENSITIVE_DIRS:
            if target_str.startswith(sens_dir + "/") or target_str == sens_dir:
                return False

        # Block sensitive dotfiles
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(target_str):
                return False

        # Check workspace containment
        ws = Path(workspace or os.getcwd()).expanduser().resolve()
        target.relative_to(ws)
        return True
    except (ValueError, OSError):
        return False


def enforce_workspace_boundary(path: str, operation: str = "access", workspace: Optional[str] = None) -> Optional[str]:
    """Return an error message if the path is outside workspace, else None."""
    if not is_within_workspace(path, workspace):
        ws = Path(workspace or os.getcwd()).expanduser().resolve()
        # Fix grammar: "write" -> "written", "delete" -> "deleted", etc.
        op_past = operation.rstrip("e") + "ed" if operation.endswith("e") else operation + "ed"
        if operation == "write":
            op_past = "written"
        elif operation == "delete":
            op_past = "deleted"
        elif operation == "read":
            op_past = "read"
        return (
            f"Error: {operation} denied — path '{path}' is outside the workspace ({ws}). "
            f"Only files within the project directory can be {op_past}. "
            f"To access files outside the workspace, ask the user to adjust the workspace boundary."
        )
    return None


# ── Output Sanitization ─────────────────────────────────────────────────────

# Patterns that shouldn't appear in tool output sent to the LLM
_DANGEROUS_OUTPUT_PATTERNS = [
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token|private[_-]?key)\s*[=:]\s*\S+", re.I),
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", re.I),
    re.compile(r"(password|passwd|pwd)\s*[=:]\s*\S+", re.I),
]


def sanitize_tool_output(output: str, max_length: int = 100_000) -> str:
    """Sanitize tool output before injecting into conversation.

    - Truncates to max_length
    - Redacts potential secrets
    - Strips ANSI escape sequences
    """
    if not output:
        return output

    # Strip ANSI escape sequences
    output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)

    # Truncate
    if len(output) > max_length:
        output = output[:max_length] + f"\n... [truncated at {max_length:,} chars]"

    # Redact potential secrets
    for pattern in _DANGEROUS_OUTPUT_PATTERNS:
        output = pattern.sub(lambda m: m.group(0).split("=")[0] + "= [REDACTED]" if "=" in m.group(0) else m.group(0).split(":")[0] + ": [REDACTED]", output)

    return output


# ── Input Length Limits ─────────────────────────────────────────────────────

MAX_TUI_INPUT_LENGTH = 100_000
MAX_DISCORD_INPUT_LENGTH = 32_000
MAX_PIPED_INPUT_LENGTH = 500_000


def validate_input_length(text: str, limit: int, source: str = "input") -> Tuple[bool, str]:
    """Check if input is within length limits. Returns (ok, error_or_empty)."""
    if len(text) > limit:
        return False, (
            f"Error: {source} too long ({len(text):,} chars, max {limit:,}). "
            f"Please shorten your input or split it into smaller parts."
        )
    return True, ""
