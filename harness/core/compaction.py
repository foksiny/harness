"""
Context Window Tracking & Smart Auto-Compaction for Harness.
Maintains token hygiene and prevents context overflow.

Compaction is budget-driven and graduated:
  1. Oversized verbatim tool payloads are collapsed to head/tail digests.
  2. The oldest semantic turn-groups are condensed into structured digests
     (LLM-assisted when a summarizer is available, heuristic otherwise) until
     usage falls back to the target ratio.
  3. If the window is still too hot, the summarized block is consolidated into a
     single global checkpoint.

A peak-hold hysteresis guard prevents re-firing on every turn, and
``emergency_trim()`` protects the window mid-turn without ever touching the
current turn's text. Every compaction records an entry in a bounded ledger for
``/compact`` and ``/tokens`` introspection.
"""
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional, Callable

# --------------------------------------------------------------------------
# Token estimation
# --------------------------------------------------------------------------

CHARS_PER_TOKEN = 3.8
MESSAGE_OVERHEAD = 4          # role / name / tool_call_id wrappers
TOOL_CALL_WEIGHT = 1.25       # JSON arguments inflate real token counts
CODE_BLOCK_WEIGHT = 1.15      # code fences / indentation are more token-dense
REASONING_WEIGHT = 1.10       # reasoning_content is verbose for its value


def estimate_tokens(text_or_obj: Any) -> int:
    """
    Fast, lightweight token estimator (roughly 3.8 chars per token, accounting
    for whitespace and JSON structure).
    """
    if text_or_obj is None:
        return 0
    if isinstance(text_or_obj, str):
        return max(1, int(len(text_or_obj) / CHARS_PER_TOKEN))
    if isinstance(text_or_obj, (dict, list)):
        dumped = json.dumps(text_or_obj, ensure_ascii=False)
        return max(1, int(len(dumped) / CHARS_PER_TOKEN))
    return max(1, int(len(str(text_or_obj)) / CHARS_PER_TOKEN))


def _weighted_content_tokens(content: str) -> int:
    """Estimate tokens for a text blob, weighting code-heavy content."""
    try:
        est = estimate_tokens(content)
    except Exception:
        return 0
    if content.count("```") >= 2:
        return int(est * CODE_BLOCK_WEIGHT)
    return est


def calculate_history_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    """Calculate aggregate tokens for an active message thread, including the
    per-message structural overhead and JSON/reasoning weighting."""
    total = estimate_tokens(system_prompt) if system_prompt else 0
    for msg in messages:
        total += MESSAGE_OVERHEAD
        total += estimate_tokens(msg.get("role", ""))
        content = msg.get("content")
        if content:
            total += _weighted_content_tokens(str(content))
        if "tool_calls" in msg and msg.get("tool_calls"):
            total += int(estimate_tokens(msg["tool_calls"]) * TOOL_CALL_WEIGHT)
        if msg.get("reasoning_content"):
            total += int(estimate_tokens(msg["reasoning_content"]) * REASONING_WEIGHT)
    return total


def _content_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    return calculate_history_tokens(messages, system_prompt)


@dataclass
class TokenStats:
    total_tokens: int
    context_window: int
    usage_ratio: float
    is_critical: bool      # >= 90%
    is_warning: bool       # >= threshold_ratio
    target_ratio: float = 0.60
    cap_ratio: float = 0.95
    threshold_ratio: float = 0.75
    breakdown: Dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Ledger of compaction events
# --------------------------------------------------------------------------

MAX_LEDGER = 20


def _new_ledger_entry(trigger: str, before: int, after: int, tactics: Dict[str, Any]) -> Dict[str, Any]:
    saved = max(0, before - after)
    return {
        "at": time.time(),
        "trigger": trigger,
        "tactics": dict(tactics),
        "before_tokens": before,
        "after_tokens": after,
        "saved_tokens": saved,
        "reduction_pct": round((saved / max(1, before)) * 100, 1),
    }


# --------------------------------------------------------------------------
# Compactor
# --------------------------------------------------------------------------

class Compactor:
    """Monitors context pressure and performs intelligent, budget-driven compaction."""

    def __init__(
        self,
        context_window: int,
        threshold_ratio: float = 0.75,
        target_ratio: float = 0.60,
        cap_ratio: float = 0.95,
        preserve_min_turns: int = 4,
        max_message_tokens: int = 0,
        summarize_fn: Optional[Callable[[List[Dict[str, Any]]], Optional[str]]] = None,
        summary_mode: str = "auto",
    ):
        self.context_window = context_window
        self.threshold_ratio = threshold_ratio
        self.target_ratio = target_ratio
        self.cap_ratio = cap_ratio
        self.preserve_min_turns = max(1, preserve_min_turns)
        self.max_message_tokens = max_message_tokens or int(context_window * 0.15)
        self.summarize_fn = summarize_fn
        # "auto" => LLM summarizer when available, heuristic otherwise.
        # "llm" => require the summarizer (fall back to heuristic on failure).
        # "heuristic" => never call the summarizer.
        self.summary_mode = summary_mode
        self.ledger: List[Dict[str, Any]] = []
        self._last_target_ratio: Optional[float] = None

    # ---- budget inspection -------------------------------------------------

    def check_status(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> TokenStats:
        total = calculate_history_tokens(messages, system_prompt)
        ratio = total / max(1, self.context_window)
        breakdown = self._breakdown(messages, system_prompt)
        return TokenStats(
            total_tokens=total,
            context_window=self.context_window,
            usage_ratio=ratio,
            is_critical=(ratio >= 0.90),
            is_warning=(ratio >= self.threshold_ratio),
            target_ratio=self.target_ratio,
            cap_ratio=self.cap_ratio,
            threshold_ratio=self.threshold_ratio,
            breakdown=breakdown,
        )

    def _breakdown(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> Dict[str, int]:
        base = estimate_tokens(system_prompt) if system_prompt else 0
        user = assistant = reasoning = tool = tool_calls = 0
        for msg in messages:
            role = msg.get("role")
            overhead = MESSAGE_OVERHEAD + estimate_tokens(role or "")
            content = msg.get("content")
            if role == "user":
                user += overhead + (_weighted_content_tokens(str(content)) if content else 0)
            elif role == "assistant":
                assistant += overhead + (_weighted_content_tokens(str(content)) if content else 0)
            elif role == "tool":
                tool += overhead + (_weighted_content_tokens(str(content)) if content else 0)
            if msg.get("reasoning_content"):
                reasoning += int(estimate_tokens(msg["reasoning_content"]) * REASONING_WEIGHT)
            if msg.get("tool_calls"):
                tool_calls += int(estimate_tokens(msg["tool_calls"]) * TOOL_CALL_WEIGHT)
        return {
            "system_prompt": base,
            "user": user,
            "assistant": assistant,
            "reasoning": reasoning,
            "tool": tool,
            "tool_calls": tool_calls,
        }

    def should_compact(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> bool:
        if len(messages) < 6:
            return False
        stats = self.check_status(messages, system_prompt)
        if not stats.is_warning:
            return False
        # Peak-hold hysteresis: after compacting down to a target, don't re-fire
        # until usage climbs back above the target by a meaningful margin.
        if self._last_target_ratio is not None and stats.usage_ratio <= self._last_target_ratio + 0.05:
            return False
        return True

    def is_over_cap(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> bool:
        """True when usage has blown past the hard cap (used mid-turn)."""
        return self.check_status(messages, system_prompt).usage_ratio >= self.cap_ratio

    # ---- full compaction (turn boundary) -------------------------------------

    def compact(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
        preserve_recent_turns: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if len(messages) <= max(1, preserve_recent_turns or self.preserve_min_turns):
            return messages, self._noop_report(messages, system_prompt)

        keep_count = max(1, preserve_recent_turns or self.preserve_min_turns)
        groups = self._split_groups(messages)
        if len(groups) <= keep_count:
            return messages, self._noop_report(messages, system_prompt)

        before_tokens = calculate_history_tokens(messages, system_prompt)
        target_tokens = int(self.target_ratio * self.context_window)
        old_groups = groups[:-keep_count]
        keep_msgs: List[Dict[str, Any]] = []
        for g in groups[-keep_count:]:
            keep_msgs.extend(g)
        old_raw: List[Dict[str, Any]] = [m for g in old_groups for m in g]

        tactics = {"payloads_truncated": 0, "groups_summarized": 0, "checkpointed": False}

        # Sweep 1: collapse oversized verbatim tool payloads in the old block.
        chunks = [self._collapse_payloads(g, tactics) for g in old_groups]

        # Sweep 2: summarize the oldest turn-groups until the whole thread fits
        # under the target budget.
        head_msgs: List[Dict[str, Any]] = []
        while chunks:
            cur = head_msgs + [m for g in chunks for m in g] + keep_msgs
            if calculate_history_tokens(cur, system_prompt) <= target_tokens:
                break
            chunk = chunks.pop(0)
            head_msgs.append(self._digest_group(chunk))
            tactics["groups_summarized"] += 1

        remaining_msgs: List[Dict[str, Any]] = [m for g in chunks for m in g]

        # Sweep 3: if still over budget, consolidate everything old into a single
        # global checkpoint. Extraction happens against the raw old block so the
        # checkpoint is genuinely smaller than the verbatim history.
        if calculate_history_tokens(head_msgs + remaining_msgs + keep_msgs, system_prompt) > target_tokens:
            digest_texts = [str(m.get("content") or "") for m in head_msgs]
            base_cp = self._build_checkpoint(old_raw, digest_texts=None)
            full_cp = self._build_checkpoint(old_raw, digest_texts=digest_texts)
            base_total = calculate_history_tokens([base_cp] + keep_msgs, system_prompt)
            full_total = calculate_history_tokens([full_cp] + keep_msgs, system_prompt)
            if full_total <= target_tokens or (base_total > target_tokens and full_total < base_total):
                checkpoint_msg = full_cp
            else:
                checkpoint_msg = base_cp
            head_msgs = [checkpoint_msg]
            remaining_msgs = []
            tactics["checkpointed"] = True

        new_messages = head_msgs + remaining_msgs + keep_msgs
        if new_messages == messages:
            return messages, self._noop_report(messages, system_prompt)

        after_tokens = calculate_history_tokens(new_messages, system_prompt)
        after_ratio = after_tokens / max(1, self.context_window)
        self._last_target_ratio = after_ratio
        self._record_ledger("turn_boundary", before_tokens, after_tokens, tactics)

        saved = max(0, before_tokens - after_tokens)
        report = {
            "compacted": True,
            "before": len(messages),
            "after": len(new_messages),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": saved,
            "reduction_pct": round((saved / max(1, before_tokens)) * 100, 1),
            "target_ratio": self.target_ratio,
            "after_ratio": round(after_ratio, 4),
            "cap_ratio": self.cap_ratio,
            "tactics": tactics,
            "older_turns_compacted": len(old_groups),
            "messages_removed": len(messages) - len(new_messages),
        }
        return new_messages, report

    def emergency_trim(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
        before_index: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Mid-turn safety net. Collapses only old oversized tool payloads that
        sit strictly before ``before_index``; the current turn's text and the
        freshly-produced tool result are never touched. Returns ``(msgs, None)``
        when nothing qualified."""
        if not self.is_over_cap(messages, system_prompt):
            return messages, None
        idx_limit = len(messages) if before_index is None else max(0, before_index)
        out: List[Dict[str, Any]] = []
        truncated = 0
        before_tokens = calculate_history_tokens(messages, system_prompt)
        for i, m in enumerate(messages):
            if i < idx_limit and m.get("role") == "tool" and m.get("content"):
                if estimate_tokens(str(m["content"])) > self.max_message_tokens:
                    out.append(self._truncate_payload_message(m))
                    truncated += 1
                    continue
            out.append(m)
        if not truncated:
            return messages, None
        after_tokens = calculate_history_tokens(out, system_prompt)
        after_ratio = after_tokens / max(1, self.context_window)
        self._last_target_ratio = min(
            self._last_target_ratio or after_ratio, after_ratio)
        tactics = {"payloads_truncated": truncated}
        self._record_ledger("emergency", before_tokens, after_tokens, tactics)
        report = {
            "compacted": True,
            "trigger": "emergency",
            "before": len(messages),
            "after": len(out),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(0, before_tokens - after_tokens),
            "reduction_pct": round((max(0, before_tokens - after_tokens) / max(1, before_tokens)) * 100, 1),
            "cap_ratio": self.cap_ratio,
            "after_ratio": round(after_ratio, 4),
            "tactics": tactics,
            "messages_removed": len(messages) - len(out),
        }
        return out, report

    # ---- helpers -------------------------------------------------------------

    def _record_ledger(self, trigger: str, before: int, after: int, tactics: Dict[str, Any]) -> None:
        self.ledger.append(_new_ledger_entry(trigger, before, after, tactics))
        if len(self.ledger) > MAX_LEDGER:
            self.ledger = self.ledger[-MAX_LEDGER:]

    def _noop_report(self, messages: Optional[List[Dict[str, Any]]] = None, system_prompt: str = "") -> Dict[str, Any]:
        if messages is not None:
            ratio = calculate_history_tokens(messages, system_prompt) / max(1, self.context_window)
            if ratio <= self.target_ratio:
                self._last_target_ratio = ratio
        return {
            "compacted": False,
            "before": 0,
            "after": 0,
            "before_tokens": 0,
            "after_tokens": 0,
            "saved_tokens": 0,
            "reduction_pct": 0.0,
            "tactics": {"payloads_truncated": 0, "groups_summarized": 0, "checkpointed": False},
        }

    def _split_groups(self, messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Split a message thread into turn-groups. A new group starts at each
        user message; assistant messages and their tool results stay together."""
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "user" and current:
                groups.append(current)
                current = []
            current.append(m)
        if current:
            groups.append(current)
        return groups

    def _collapse_payloads(self, group: List[Dict[str, Any]], tactics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Truncate any tool-result message whose payload exceeds the size cap."""
        out = []
        for m in group:
            if m.get("role") == "tool" and m.get("content"):
                est = estimate_tokens(str(m["content"]))
                if est > self.max_message_tokens:
                    out.append(self._truncate_payload_message(m))
                    if tactics is not None:
                        tactics["payloads_truncated"] += 1
                    continue
            out.append(m)
        return out

    def _truncate_payload_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        trimmed = dict(msg)
        trimmed["content"] = self._truncate_payload(str(msg.get("content") or ""))
        return trimmed

    def _truncate_payload(self, content: str) -> str:
        """Keep the head+tail of an oversized payload with a size-stamped marker."""
        original_chars = len(content)
        allowed_chars = max(64, int(self.max_message_tokens * CHARS_PER_TOKEN))
        if len(content) <= allowed_chars:
            return content
        marker = f"\n...[payload truncated: was {original_chars} chars / ~{int(original_chars / CHARS_PER_TOKEN)} tokens]...\n"
        head_len = int(allowed_chars * 0.6)
        tail_len = max(0, allowed_chars - head_len - len(marker))
        return content[:head_len] + marker + (content[-tail_len:] if tail_len else "")

    def _use_llm_summary(self) -> bool:
        if self.summary_mode == "heuristic":
            return False
        return self.summarize_fn is not None

    def _digest_group(self, group: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Condense a turn-group into a single digest message (role 'assistant' so
        every provider keeps it in-context)."""
        text = None
        if self._use_llm_summary():
            try:
                text = self.summarize_fn(group)  # type: ignore[misc]
            except Exception:
                text = None
        if not text or not text.strip():
            text = self._digest_group_heuristic(group)
        header = f"### [CONTEXT SUMMARY: {len(group)} prior message(s) compacted]\n\n"
        return {"role": "assistant", "content": header + text.strip()}

    def _digest_group_heuristic(self, group: List[Dict[str, Any]]) -> str:
        info = self._extract(group)
        lines = []
        if info["user_goals"]:
            lines.append("**Prior instruction(s):**")
            for g in info["user_goals"][-3:]:
                lines.append(f"- {g}")
        if info["work_log"]:
            lines.append("**Work performed:**")
            for w in info["work_log"][-8:]:
                lines.append(f"- {w}")
        if info["conclusions"]:
            lines.append("**Findings / outcomes:**")
            for c in info["conclusions"][-4:]:
                lines.append(f"- {c}")
        if not lines:
            lines.append("- (Exploration and early setup steps)")
        return "\n".join(lines)

    def _build_checkpoint(self, messages: List[Dict[str, Any]], digest_texts: Optional[List[str]] = None) -> Dict[str, Any]:
        """Consolidate an entire block into a single global checkpoint message.
        ``digest_texts`` are appended only when they carry extra detail the raw
        extraction did not capture; the caller bounds them against the budget."""
        info = self._extract(messages)
        cp = [
            "### [AUTOMATED CONTEXT MEMORY CHECKPOINT]",
            "Earlier conversation history was compacted to optimize the context window.",
            "",
        ]
        if info["user_goals"]:
            cp.append("#### Prior User Directives & Goals:")
            for g in info["user_goals"][-5:]:
                cp.append(f"- {g}")
        if info["files_mentioned"]:
            cp.append("\n#### Active Files Touched / Analyzed:")
            for f in sorted(info["files_mentioned"])[:15]:
                cp.append(f"- `{f}`")
        if info["tools_executed"]:
            cp.append("\n#### Tools Utilized: " + ", ".join(sorted(info["tools_executed"])))
        if info["work_log"]:
            cp.append("\n#### Work Log:")
            for w in info["work_log"][-10:]:
                cp.append(f"- {w}")
        if info["conclusions"]:
            cp.append("\n#### Key Findings & Progress:")
            for c in info["conclusions"][-5:]:
                cp.append(f"- {c}")
        base = "\n".join(cp)
        if digest_texts:
            joined = "\n".join(t for t in digest_texts if t and t.strip())
            if joined:
                base += "\n\n#### Summarized Prior Threads:\n" + joined
        return {"role": "assistant", "content": base}

    def _extract(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Shared extraction used by the heuristic digest and the checkpoint."""
        user_goals: List[str] = []
        files_mentioned = set()
        tools_executed = set()
        conclusions: List[str] = []
        work_log: List[str] = []

        for msg in messages:
            role = msg.get("role")
            content = str(msg.get("content") or "")

            if role == "user":
                if content.strip() and not content.strip().startswith("/"):
                    first_line = content.strip().split("\n")[0][:180]
                    if first_line and first_line not in user_goals:
                        user_goals.append(first_line)
            elif role == "assistant":
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "unknown")
                        tools_executed.add(fn_name)
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        for k in ("path", "file_path", "filename", "TargetFile"):
                            if k in args:
                                files_mentioned.add(str(args[k]))
                        work_log.append(f"{fn_name} -> {self._short_value(args)}")
                elif content.strip():
                    snippet = content.strip().split("\n")[0][:150]
                    lowered = content.lower()
                    if any(tag in lowered for tag in ("result", "completed", "fixed", "resolution", "decided")):
                        conclusions.append(snippet)
            elif role == "tool":
                first_line = content.strip().split("\n")[0][:120] if content.strip() else "(empty)"
                lowered = content.lower()
                status = "OK" if any(s in lowered for s in ("success", "completed", "updated", "created", "✓", "✔")) else "info"
                work_log.append(f"[{status}] {first_line}")

        return {
            "user_goals": user_goals,
            "files_mentioned": files_mentioned,
            "tools_executed": tools_executed,
            "conclusions": conclusions,
            "work_log": work_log,
        }

    @staticmethod
    def _short_value(args: Dict[str, Any]) -> str:
        text = json.dumps(args, ensure_ascii=False)
        return (text if len(text) <= 160 else text[:160] + "...")