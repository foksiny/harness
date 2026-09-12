"""
Context Window Tracking & Smart Auto-Compaction for Harness.
Maintains token hygiene, prevents context overflow, and generates structured
memory checkpoints while preserving critical project context.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional
import json

@dataclass
class TokenStats:
    total_tokens: int
    context_window: int
    usage_ratio: float
    is_critical: bool     # > 90%
    is_warning: bool      # > 75%

def estimate_tokens(text_or_obj: Any) -> int:
    """
    Fast, lightweight token estimator (roughly 3.8 chars per token for code/text,
    accounting for whitespace and JSON structure).
    """
    if text_or_obj is None:
        return 0
    if isinstance(text_or_obj, str):
        return max(1, int(len(text_or_obj) / 3.8))
    if isinstance(text_or_obj, (dict, list)):
        dumped = json.dumps(text_or_obj, ensure_ascii=False)
        return max(1, int(len(dumped) / 3.8))
    return max(1, int(len(str(text_or_obj)) / 3.8))

def calculate_history_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    """Calculate aggregate tokens for an active message thread."""
    total = estimate_tokens(system_prompt)
    for msg in messages:
        total += estimate_tokens(msg.get("role", ""))
        total += estimate_tokens(msg.get("content", ""))
        if "tool_calls" in msg:
            total += estimate_tokens(msg["tool_calls"])
        if "reasoning_content" in msg:
            total += estimate_tokens(msg["reasoning_content"])
    return total

class Compactor:
    """Monitors context pressure and performs intelligent history compaction."""

    def __init__(self, context_window: int, threshold_ratio: float = 0.75):
        self.context_window = context_window
        self.threshold_ratio = threshold_ratio

    def check_status(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> TokenStats:
        total = calculate_history_tokens(messages, system_prompt)
        ratio = total / max(1, self.context_window)
        return TokenStats(
            total_tokens=total,
            context_window=self.context_window,
            usage_ratio=ratio,
            is_critical=(ratio >= 0.90),
            is_warning=(ratio >= self.threshold_ratio),
        )

    def should_compact(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> bool:
        if len(messages) < 6:
            return False
        stats = self.check_status(messages, system_prompt)
        return stats.is_warning

    def compact(self, messages: List[Dict[str, Any]], preserve_recent_turns: int = 4) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Condense historical messages into a structured memory checkpoint
        while keeping the most recent interaction turns intact.
        """
        if len(messages) <= preserve_recent_turns:
            return messages, {"compacted": False, "before": len(messages), "after": len(messages), "saved_tokens": 0}

        split_idx = max(1, len(messages) - preserve_recent_turns)
        older_messages = messages[:split_idx]
        recent_messages = messages[split_idx:]

        before_tokens = calculate_history_tokens(messages)

        # Extract structured details from older messages
        user_goals = []
        files_mentioned = set()
        tools_executed = []
        key_conclusions = []

        for msg in older_messages:
            role = msg.get("role")
            content = str(msg.get("content") or "")

            if role == "user":
                # First 200 chars of user prompts
                if len(content.strip()) > 0 and not content.startswith("/"):
                    snippet = content.strip().split("\n")[0][:180]
                    user_goals.append(snippet)

            elif role == "assistant":
                if "tool_calls" in msg and msg["tool_calls"]:
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "unknown")
                        tools_executed.append(fn_name)
                        # Detect target files
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                            for k in ("path", "file_path", "filename", "TargetFile"):
                                if k in args:
                                    files_mentioned.add(args[k])
                        except Exception:
                            pass
                elif len(content) > 0 and ("result" in content.lower() or "completed" in content.lower() or "fixed" in content.lower()):
                    first_line = content.strip().split("\n")[0][:150]
                    key_conclusions.append(first_line)

        # Build clean summary checkpoint
        checkpoint_lines = [
            "### [AUTOMATED CONTEXT MEMORY CHECKPOINT]",
            "Earlier conversation history was compacted to optimize context window.",
            "",
            "#### Prior User Directives & Goals:",
        ]
        if user_goals:
            for g in user_goals[-5:]:
                checkpoint_lines.append(f"- {g}")
        else:
            checkpoint_lines.append("- (Initial setup and research steps)")

        if files_mentioned:
            checkpoint_lines.append("\n#### Active Files Touched / Analyzed:")
            for f in sorted(list(files_mentioned))[:15]:
                checkpoint_lines.append(f"- `{f}`")

        if tools_executed:
            unique_tools = sorted(list(set(tools_executed)))
            checkpoint_lines.append(f"\n#### Tools Utilized: {', '.join(unique_tools)}")

        if key_conclusions:
            checkpoint_lines.append("\n#### Key Findings & Progress:")
            for c in key_conclusions[-4:]:
                checkpoint_lines.append(f"- {c}")

        compacted_summary = "\n".join(checkpoint_lines)

        compacted_msg = {
            "role": "system",
            "content": compacted_summary,
        }

        new_messages = [compacted_msg] + recent_messages
        after_tokens = calculate_history_tokens(new_messages)
        saved_tokens = max(0, before_tokens - after_tokens)

        return new_messages, {
            "compacted": True,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": saved_tokens,
            "reduction_pct": round((saved_tokens / max(1, before_tokens)) * 100, 1),
            "older_turns_compacted": len(older_messages),
        }
