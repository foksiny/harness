"""
Proactive Context Budget Management for Harness.

Compaction in ``harness/core/compaction.py`` is *reactive*: it only fires once
usage trips a threshold. But a large fraction of context-window bloat comes from
the agent's own tool calls — a single oversized ``run_command`` / ``view_file`` /
``grep_search`` result can consume the window before any compaction runs.

This module provides a *proactive* companion:

  * Per-tool output caps so individual tool results can never blow the context
    (the agent may request more by passing an explicit ``max_chars``).
  * A cheap, real-time pressure gauge (``ContextBudget.check``) the agent loop
    consults *before every provider send* so it compacts *before* the window
    fills, not after.
  * A universally usable ``truncate_output`` helper so any tool or connector can
    clamp oversize payloads consistently.

Keeping this module dependency-light (no network, no provider) makes it trivial
to unit-test and fast enough to call on every turn.
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from harness.core.compaction import calculate_history_tokens

# --------------------------------------------------------------------------
# Pressure tiers
# --------------------------------------------------------------------------

PRESSURE_HEALTHY = "healthy"
PRESSURE_WARNING = "warning"
PRESSURE_CRITICAL = "critical"

# Recommended action labels.
ACTION_NONE = "none"
ACTION_COMPACT = "compact"
ACTION_TRIM = "trim"

# Default per-tool output caps (in characters). A single tool result is never
# allowed to exceed these, protecting the context window from runaway outputs.
# 0 disables the cap for a tool.
DEFAULT_OUTPUT_LIMITS: Dict[str, int] = {
    "view_file": 8000,
    "run_command": 16000,
    "grep_search": 8000,
    "find_files": 4000,
    "list_dir": 4000,
    "execute_python": 16000,
    "exa_search": 8000,
    "read_skill": 12000,
    "spawn_swarm": 12000,
    "spawn_subagent": 12000,
}


# --------------------------------------------------------------------------
# Pressure status
# --------------------------------------------------------------------------

@dataclass
class BudgetStatus:
    """Snapshot of context pressure at a point in time, plus a recommended action."""

    pressure: str
    usage_ratio: float
    used_tokens: int
    context_window: int
    available_tokens: int
    action: str = ACTION_NONE
    breakdown: Dict[str, int] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.pressure == PRESSURE_HEALTHY

    @property
    def is_warning(self) -> bool:
        return self.pressure in (PRESSURE_WARNING, PRESSURE_CRITICAL)

    @property
    def is_critical(self) -> bool:
        return self.pressure == PRESSURE_CRITICAL


def _classify(usage_ratio: float, threshold_ratio: float, cap_ratio: float) -> str:
    if usage_ratio >= cap_ratio:
        return PRESSURE_CRITICAL
    if usage_ratio >= threshold_ratio:
        return PRESSURE_WARNING
    return PRESSURE_HEALTHY


class ContextBudget:
    """Holds the active model's context budget and enforces proactive limits.

    A single instance is bound to the agent's current model spec and is updated
    whenever the model/provider changes, so the thresholds always reflect the
    live model's real context window.
    """

    def __init__(
        self,
        context_window: int,
        threshold_ratio: float = 0.75,
        cap_ratio: float = 0.95,
        output_limits: Optional[Dict[str, int]] = None,
    ):
        self.context_window = max(1, context_window)
        self.threshold_ratio = threshold_ratio
        self.cap_ratio = cap_ratio
        self.output_limits = dict(output_limits) if output_limits else dict(DEFAULT_OUTPUT_LIMITS)
        self._last_pressure: Optional[str] = None

    # ---- model re-binding --------------------------------------------------

    def rebind(self, context_window: int, threshold_ratio: Optional[float] = None,
               cap_ratio: Optional[float] = None) -> None:
        """Update the budget to match a newly selected model/provider."""
        self.context_window = max(1, context_window)
        if threshold_ratio is not None:
            self.threshold_ratio = threshold_ratio
        if cap_ratio is not None:
            self.cap_ratio = cap_ratio
        self._last_pressure = None

    # ---- pressure gauge ----------------------------------------------------

    def check(self, messages: List[Dict[str, Any]], system_prompt: str = "") -> BudgetStatus:
        """Return the current pressure tier and a recommended action.

        ``action`` is ``compact`` when we cross the threshold (so the caller can
        compact *before* the next send) and ``trim`` when at/over the cap.
        """
        if not messages:
            used = calculate_history_tokens([], system_prompt)
        else:
            used = calculate_history_tokens(messages, system_prompt)
        ratio = used / max(1, self.context_window)
        pressure = _classify(ratio, self.threshold_ratio, self.cap_ratio)
        self._last_pressure = pressure

        action = ACTION_NONE
        if pressure == PRESSURE_CRITICAL:
            action = ACTION_TRIM
        elif pressure == PRESSURE_WARNING:
            action = ACTION_COMPACT

        return BudgetStatus(
            pressure=pressure,
            usage_ratio=ratio,
            used_tokens=used,
            context_window=self.context_window,
            available_tokens=max(0, self.context_window - used),
            action=action,
        )

    # ---- output limiting ---------------------------------------------------

    def get_output_limit(self, tool_name: str) -> int:
        """Return the char cap for a tool (0 == unlimited)."""
        return int(self.output_limits.get(_canonical_tool(tool_name), 0))

    def set_output_limit(self, tool_name: str, max_chars: int) -> None:
        """Override the char cap for a tool (0 == unlimited)."""
        self.output_limits[_canonical_tool(tool_name)] = int(max_chars)

    def truncate_output(self, tool_name: str, result: str, max_chars: Optional[int] = None) -> str:
        """Clamp ``result`` to the tool's cap (or an explicit override).

        A trailing marker preserves transparency so the model knows output was
        deliberately elided rather than truncated mid-string. An explicit
        ``max_chars`` always wins — the agent can request more when it needs the
        full payload.
        """
        if not isinstance(result, str):
            result = str(result)
        cap = max_chars if max_chars is not None else self.get_output_limit(tool_name)
        if cap <= 0 or len(result) <= cap:
            return result
        marker = (
            f"\n...[output truncated by context budget: was {len(result)} chars, "
            f"keeping first {cap}]...\n(or pass max_chars=N to this tool for the full result)"
        )
        return result[:cap] + marker


def _canonical_tool(name: str) -> str:
    """Map a tool name to its canonical output-cap key."""
    return name or "unknown"


def truncate_output(result: str, max_chars: int, marker_note: Optional[str] = None) -> str:
    """Standalone helper: clamp ``result`` to ``max_chars`` (0 == unlimited)."""
    if max_chars <= 0 or not isinstance(result, str) or len(result) <= max_chars:
        return result
    note = marker_note or (
        f"...[output truncated: was {len(result)} chars, keeping first {max_chars}]..."
    )
    return result[:max_chars] + "\n" + note