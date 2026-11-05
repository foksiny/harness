"""
Context Window Tracking & Smart Auto-Compaction for Harness.
Maintains token hygiene and prevents context overflow.

Compaction is budget-driven and graduated:
  1. Oversized verbatim tool payloads are collapsed to head/tail digests.
  2. Chain-of-thought (``reasoning_content``) is stripped from the old block —
     at high thinking effort it is the fastest-growing consumer and has
     near-zero retrieval value once summarized. Preserved recent turns keep
     their reasoning verbatim.
  3. The oldest semantic turn-groups are condensed into structured digests
     (LLM-assisted when a summarizer is available, heuristic otherwise) until
     usage falls back to the target ratio.
  4. If the window is still too hot, the model consolidates the digests into a
     single global checkpoint (heuristic extraction is strictly the fallback
     when the model is unavailable, disabled, or over budget).

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
# Attached media have no countable characters, but a real vision model spends
# ~1-2k tokens per image (tiles + encoder). Counting 0 (the old str() of the
# block dict) silently under-budgeted vision turns until overflow. This is a
# deliberately conservative heuristic, not a model-specific truth.
IMAGE_BLOCK_TOKENS = 1500


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


def _content_tokens(content: Any) -> int:
    """Token estimate for a message ``content`` in any provider shape.

    Plain strings use the weighted estimator; content-block lists (vision
    turns: ``[{"type": "text", ...}, {"type": "image", ...}]``) sum their
    text parts and charge a flat heuristic per attached media block.
    """
    if content is None:
        return 0
    if isinstance(content, str):
        return _weighted_content_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _weighted_content_tokens(part)
            elif isinstance(part, dict):
                ptype = str(part.get("type", "")).lower()
                if ptype in ("image", "image_url", "video", "input_image", "input_video"):
                    total += IMAGE_BLOCK_TOKENS
                else:
                    total += _weighted_content_tokens(str(part.get("text", "")))
            else:
                total += estimate_tokens(part)
        return total
    return _weighted_content_tokens(str(content))


def calculate_history_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    """Calculate aggregate tokens for an active message thread, including the
    per-message structural overhead and JSON/reasoning weighting."""
    total = estimate_tokens(system_prompt) if system_prompt else 0
    for msg in messages:
        total += MESSAGE_OVERHEAD
        total += estimate_tokens(msg.get("role", ""))
        content = msg.get("content")
        if content:
            total += _content_tokens(content)
        if "tool_calls" in msg and msg.get("tool_calls"):
            total += int(estimate_tokens(msg["tool_calls"]) * TOOL_CALL_WEIGHT)
        if msg.get("reasoning_content"):
            total += int(estimate_tokens(msg["reasoning_content"]) * REASONING_WEIGHT)
    return total


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

    def rebind(self, context_window: int,
               threshold_ratio: Optional[float] = None,
               target_ratio: Optional[float] = None,
               cap_ratio: Optional[float] = None,
               max_message_tokens: Optional[int] = None) -> None:
        """Re-target the compactor at a newly selected model.

        Updates the window (and optionally the ratios / message cap) and
        resets the peak-hold hysteresis so the new window starts with a
        clean gauge instead of the old model's memory. Pass
        ``max_message_tokens=0`` to recompute the 15%-of-window auto cap.
        """
        self.context_window = max(1, context_window)
        if threshold_ratio is not None:
            self.threshold_ratio = threshold_ratio
        if target_ratio is not None:
            self.target_ratio = target_ratio
        if cap_ratio is not None:
            self.cap_ratio = cap_ratio
        if max_message_tokens is not None:
            self.max_message_tokens = max_message_tokens or int(self.context_window * 0.15)
        self._last_target_ratio = None

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
                user += overhead + (_content_tokens(content) if content else 0)
            elif role == "assistant":
                assistant += overhead + (_content_tokens(content) if content else 0)
            elif role == "tool":
                tool += overhead + (_content_tokens(content) if content else 0)
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

        tactics = {"payloads_truncated": 0, "groups_summarized": 0, "checkpointed": False,
                   "checkpoint_llm": False, "reasoning_stripped": 0}

        # Context bug fix: the preserved recent block is never summarized, so
        # when it ALONE exceeds the target (one giant tool result, or megabytes
        # of high-effort reasoning) every sweep below is futile — the old code
        # returned after_ratios like 6x target. Collapse oversized payloads and
        # strip reasoning in the keep block too; it stays verbatim otherwise.
        if calculate_history_tokens(keep_msgs, system_prompt) > target_tokens:
            keep_msgs = self._collapse_payloads(keep_msgs, tactics)
            stripped_keep: List[Dict[str, Any]] = []
            for m in keep_msgs:
                if m.get("reasoning_content"):
                    nm = dict(m)
                    nm["reasoning_content"] = None
                    stripped_keep.append(nm)
                    tactics["reasoning_stripped"] += 1
                else:
                    stripped_keep.append(m)
            keep_msgs = stripped_keep

        # Sweep 2: strip chain-of-thought from the old block. Reasoning is the
        # fastest-growing context consumer at high thinking effort, and it has
        # near-zero retrieval value once a turn is over — the digests below
        # preserve goals, tool decisions and outcomes, never raw thought.
        # Only the preserved recent turns keep their reasoning verbatim.
        # Sweep 1: collapse oversized verbatim tool payloads in the old block.
        chunks = [self._collapse_payloads(g, tactics) for g in old_groups]
        # Sweep 2: strip chain-of-thought from the old block.
        stripped_chunks: List[List[Dict[str, Any]]] = []
        for g in chunks:
            ng = []
            for m in g:
                if m.get("reasoning_content"):
                    nm = dict(m)
                    nm["reasoning_content"] = None
                    ng.append(nm)
                    tactics["reasoning_stripped"] += 1
                else:
                    ng.append(m)
            stripped_chunks.append(ng)
        chunks = stripped_chunks

        # Sweep 3: summarize the oldest turn-groups until the whole thread fits
        # under the target budget. Token math is incremental (per-group totals
        # computed once) instead of re-dumping the whole thread per pop.
        sys_toks = estimate_tokens(system_prompt) if system_prompt else 0
        keep_toks = sum(calculate_history_tokens([m]) for m in keep_msgs)
        chunk_toks = [calculate_history_tokens(g) for g in chunks]
        remaining = sum(chunk_toks)
        head_msgs: List[Dict[str, Any]] = []
        head_toks = 0
        while chunks:
            if sys_toks + head_toks + remaining + keep_toks <= target_tokens:
                break
            chunk = chunks.pop(0)
            remaining -= chunk_toks.pop(0)
            digest = self._digest_group(chunk)
            head_msgs.append(digest)
            head_toks += calculate_history_tokens([digest])
            tactics["groups_summarized"] += 1

        remaining_msgs: List[Dict[str, Any]] = [m for g in chunks for m in g]

        # Sweep 4: if still over budget, consolidate everything old into a single
        # global checkpoint. The model writes it: the per-group digests above
        # are already small, so consolidating them costs one bounded provider
        # call instead of re-sending the raw history. Heuristic extraction is
        # strictly the fallback (model unavailable, disabled, or over budget).
        if calculate_history_tokens(head_msgs + remaining_msgs + keep_msgs, system_prompt) > target_tokens:
            digest_texts = [str(m.get("content") or "") for m in head_msgs]
            llm_cp = self._digest_checkpoint_llm(digest_texts)
            if llm_cp is not None and (
                calculate_history_tokens([llm_cp] + keep_msgs, system_prompt) <= target_tokens
            ):
                head_msgs = [llm_cp]
                remaining_msgs = []
                tactics["checkpointed"] = True
                tactics["checkpoint_llm"] = True
            else:
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
            "tactics": {"payloads_truncated": 0, "groups_summarized": 0, "checkpointed": False,
                        "checkpoint_llm": False, "reasoning_stripped": 0},
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
                text = self.summarize_fn([self._strip_reasoning(m) for m in group])  # type: ignore[misc]
            except Exception:
                text = None
        if not text or not text.strip():
            text = self._digest_group_heuristic(group)
        header = f"### [CONTEXT SUMMARY: {len(group)} prior message(s) compacted]\n\n"
        return {"role": "assistant", "content": header + text.strip()}

    @staticmethod
    def _strip_reasoning(msg: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of ``msg`` with verbose ``reasoning_content`` removed.

        Reasoning is high-volume and low-value once a turn is being summarized;
        the digest preserves the user goal, tool decisions and outcomes, so the
        raw chain-of-thought adds little for the cost.
        """
        if "reasoning_content" not in msg:
            return msg
        out = dict(msg)
        out["reasoning_content"] = None
        return out

    def _digest_checkpoint_llm(self, digest_texts: List[str]) -> Optional[Dict[str, Any]]:
        """Ask the model to consolidate per-group digests into ONE global checkpoint.

        The input is already-small digests (never the raw history), so this
        costs one bounded provider call. Returns None when the model is
        unavailable, disabled (``summary_mode="heuristic"``), fails, or comes
        back empty — the caller then falls back to the heuristic checkpoint.
        """
        if not self._use_llm_summary():
            return None
        joined = [t for t in (digest_texts or []) if t and t.strip()]
        if not joined:
            return None
        try:
            text = self.summarize_fn(  # type: ignore[misc]
                [{"role": "assistant", "content": t} for t in joined]
            )
        except Exception:
            return None
        if not text or not text.strip():
            return None
        header = (
            "### [AUTOMATED CONTEXT MEMORY CHECKPOINT]\n"
            "Earlier conversation history was compacted to optimize the context window.\n\n"
        )
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
        """Consolidate an entire block into a single global checkpoint message (I3 schema).

        Required sections: goals → decisions → files → open → next. Post-
        compaction fidelity is measured against this schema (J2).
        """
        info = self._extract(messages)
        cp = [
            "### [AUTOMATED CONTEXT MEMORY CHECKPOINT]",
            "Earlier conversation history was compacted to optimize the context window.",
            "",
            "#### Decisions (with rationale):",
        ]
        if info.get("conclusions"):
            for c in info["conclusions"][-5:]:
                cp.append(f"- {c}")
        else:
            cp.append("- (none recorded)")
        cp.append("\n#### Open questions / next steps:")
        cp.append("- (see work log below; follow up unfinished goals above)")
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