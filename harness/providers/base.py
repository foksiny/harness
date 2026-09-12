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
        into the model's exact parameter format.
        """
        if not model_spec.supports_thinking or effort_setting.lower() in ("off", "none", "false"):
            return {}

        ttype = model_spec.thinking_type
        eff = effort_setting.lower().strip()

        if ttype == "budget_tokens":
            # Anthropic style
            if eff == "low":
                tokens = 2048
            elif eff == "medium":
                tokens = 8192
            elif eff == "high":
                tokens = 16384
            elif eff.isdigit():
                tokens = int(eff)
            else:
                tokens = 8192
            return {"thinking": {"type": "enabled", "budget_tokens": tokens}}

        elif ttype == "thinking_budget":
            # Gemini style
            if eff == "low":
                budget = 2048
            elif eff == "medium":
                budget = 8192
            elif eff == "high":
                budget = 16384
            elif eff.isdigit():
                budget = int(eff)
            else:
                budget = 8192
            return {"thinking_config": {"thinking_budget": budget}}

        elif ttype == "reasoning_effort":
            # OpenAI / DeepSeek / NIM / OpenRouter style
            val = "medium"
            if eff in ("low", "medium", "high"):
                val = eff
            elif eff.isdigit():
                val = "low" if int(eff) < 4000 else ("medium" if int(eff) < 12000 else "high")
            return {"reasoning_effort": val}

        return {}

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
