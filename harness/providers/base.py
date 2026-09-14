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

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url

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
        eff = effort_setting.lower().strip()
        off = eff in ("off", "none", "false", "disabled")

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
            return {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": "high"}

        # reasoning_effort / default
        if off:
            return {}
        return {"reasoning_effort": self._level_effort(eff)}

    @staticmethod
    def _level_tokens(eff: str, low_t: int, med_t: int, high_t: int) -> int:
        if eff == "low":
            return low_t
        if eff == "high":
            return high_t
        if eff.isdigit():
            return int(eff)
        return med_t

    @staticmethod
    def _level_effort(eff: str) -> str:
        if eff in ("low", "medium", "high"):
            return eff
        if eff.isdigit():
            n = int(eff)
            return "low" if n < 4000 else ("medium" if n < 12000 else "high")
        return "medium"

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
