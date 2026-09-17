"""
Base Provider interface for Harness.
Defines unified streaming chunk protocol, model specification resolution,
and parameter normalization for reasoning/thinking effort.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Iterator
from harness.providers.detector import inspect_model, ModelSpec

@dataclass
class ToolCallDelta:
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""

@dataclass
class LLMChunk:
    delta_text: str = ""
    delta_reasoning: str = ""
    tool_calls: List[ToolCallDelta] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None

class BaseProvider(ABC):
    """Abstract interface for all LLM providers."""

    name: str
    display_name: str
    default_model: str

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, max_retries: int = 3, base_delay: float = 5.0):
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.1, float(base_delay))

    def _is_retryable_http_code(self, code: int) -> bool:
        # Retry on rate limit and transient server errors
        return code in (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529)

    def _retry_delay(self, attempt: int) -> float:
        # Exponential backoff: base * 2^attempt  (attempt 0 => 5s, 1=>10s, 2=>20s)
        return self.base_delay * (2 ** attempt)

    def get_model_spec(self, model_name: Optional[str] = None) -> ModelSpec:
        """Resolve model specifications using model-specific dynamic detection and discovery."""
        m = model_name or self.default_model
        try:
            from harness.providers.discovery import resolve_model_spec_dynamic
            return resolve_model_spec_dynamic(m, self.name, self.api_key, self.base_url)
        except Exception:
            return inspect_model(m, self.name)

    def normalize_thinking_effort(self, model_spec: ModelSpec, effort_setting: str) -> Dict[str, Any]:
        """
        Convert user effort setting (off, low, medium, high, or integer)
        into the model's exact parameter format (per-provider thinking dialect).
        """
        if not model_spec.supports_thinking:
            return {}

        ttype = model_spec.thinking_type or "reasoning_effort"
        # NVIDIA NIM does not support the Together-style reasoning toggle.
        # For models wrongly marked toggle but hosted on NVIDIA, silently disable
        # reasoning so we never send {"reasoning": {"enabled": true}} to NIM.
        if ttype == "reasoning_toggle" and getattr(self, "name", "") == "nvidia":
            return {}
        eff = effort_setting.lower().strip()
        off = eff in ("off", "none", "false", "disabled", "no", "0", "disable", "stop", "cancel")

        if ttype == "budget_tokens":
            # Anthropic style
            if off:
                return {}
            tokens = max(1024, self._level_tokens(eff, 2048, 8192, 16384))
            return {"thinking": {"type": "enabled", "budget_tokens": tokens}}

        if ttype == "thinking_budget":
            # Gemini style. includeThoughts=true surfaces the thinking blocks.
            if off:
                return {}
            budget = self._level_tokens(eff, 2048, 8192, 16384)
            return {"thinking_config": {"thinking_budget": budget, "include_thoughts": True}}

        if ttype == "thinking_token_budget":
            # Cohere style
            if off:
                return {"thinking": {"type": "disabled"}}
            budget = max(1024, self._level_tokens(eff, 2048, 8192, 16384))
            return {"thinking": {"type": "enabled", "token_budget": budget}}

        if ttype == "reasoning_object":
            # OpenRouter style
            if off:
                return {"reasoning": {"enabled": False}}
            return {"reasoning": {"effort": self._level_effort(eff)}}

        if ttype == "reasoning_toggle":
            # Together hybrid models
            if off:
                return {"reasoning": {"enabled": False}}
            return {"reasoning": {"enabled": True}}

        if ttype == "chat_template_kwargs":
            # NVIDIA NIM (DeepSeek-V4 etc.) — reasoning_effort none|high|max.
            if off:
                return {"chat_template_kwargs": {"thinking": False}}
            effort = "high"
            if eff in ("max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
                effort = "max"
            elif eff in ("low", "minimal", "light", "brief", "shallow"):
                effort = "high"
            elif eff.isdigit():
                n = int(eff)
                effort = "max" if n >= 12000 else "high"
            return {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": effort}

        # reasoning_effort / default
        if off:
            return {}
        return {"reasoning_effort": self._level_effort(eff)}

    @staticmethod
    def _level_tokens(eff: str, low_t: int, med_t: int, high_t: int) -> int:
        if eff in ("low", "minimal", "light", "brief", "shallow"):
            return low_t
        if eff in ("high", "max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
            return high_t
        if eff.isdigit():
            return int(eff)
        return med_t

    @staticmethod
    def _level_effort(eff: str) -> str:
        if eff in ("low", "minimal", "light", "brief", "shallow"):
            return "low"
        if eff in ("high", "max", "maximum", "full", "deep", "verbose", "detailed", "extensive", "thorough"):
            return "high"
        if eff in ("medium", "med", "moderate", "normal", "default", "standard"):
            return "medium"
        if eff.isdigit():
            n = int(eff)
            return "low" if n < 4000 else ("medium" if n < 12000 else "high")
        return "medium"


def probe_effort_options(dialect: Optional[str]) -> List[str]:
    """Probe which effort names a dialect actually accepts.

    Returns a list of canonical effort level names that produce distinct
    results for the given dialect. Also indicates if numeric token counts
    are accepted (shown as '<n>' in the list).
    """
    if not dialect:
        return []

    def _run(v):
        eff = v.lower().strip()
        off = eff in ("off", "none", "false", "disabled", "no", "0", "disable", "stop", "cancel")
        if dialect == "budget_tokens":
            if off: return {}
            return {"thinking": {"type": "enabled", "budget_tokens": max(1024, BaseProvider._level_tokens(eff, 2048, 8192, 16384))}}
        if dialect == "thinking_budget":
            if off: return {}
            return {"thinking_config": {"thinking_budget": BaseProvider._level_tokens(eff, 2048, 8192, 16384), "include_thoughts": True}}
        if dialect == "thinking_token_budget":
            if off: return {"thinking": {"type": "disabled"}}
            return {"thinking": {"type": "enabled", "token_budget": max(1024, BaseProvider._level_tokens(eff, 2048, 8192, 16384))}}
        if dialect == "reasoning_object":
            if off: return {"reasoning": {"enabled": False}}
            return {"reasoning": {"effort": BaseProvider._level_effort(eff)}}
        if dialect == "reasoning_toggle":
            if off: return {"reasoning": {"enabled": False}}
            return {"reasoning": {"enabled": True}}
        if dialect == "chat_template_kwargs":
            if off: return {"chat_template_kwargs": {"thinking": False}}
            effort = "max" if eff in ("max", "maximum", "full", "deep") or (eff.isdigit() and int(eff) >= 12000) else "high"
            return {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": effort}
        # reasoning_effort / default
        if off: return {}
        return {"reasoning_effort": BaseProvider._level_effort(eff)}

    # Test canonical levels and check which produce distinct results
    levels = [("off", "off"), ("low", "low"), ("medium", "medium"), ("high", "high")]
    results = {}
    for label, probe_val in levels:
        r = repr(_run(probe_val))
        if r not in results:
            results[r] = label

    canonical = list(results.values())

    # For toggle/nim dialects, prefer natural names over level names
    if dialect == "reasoning_toggle" and len(canonical) == 2:
        # off + one other → show as "off, on"
        canonical = ["off", "on"]
    elif dialect == "chat_template_kwargs" and len(canonical) == 2:
        # off + one other → show as "none, high, max"
        canonical = ["none", "high", "max"]

    # Check if numeric values are preserved as actual token counts (not just mapped to levels)
    high_r = _run("high")
    num_r = _run("8000")
    if num_r != high_r and num_r != _run("off"):
        # Check if the numeric value is actually in the result (token count preserved)
        num_str = repr(num_r)
        if "8000" in num_str:
            canonical.append("<n>")

    return canonical

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        thinking_effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Iterator[LLMChunk]:
        """Stream response chunks from provider."""
        pass
