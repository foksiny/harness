"""
Model-Specific Detection & Capability Engine for Harness.
Dynamically resolves context window limits, token output boundaries, and
reasoning/thinking effort mechanics for any current or future LLM.

Thinking is negotiated per-provider using a small set of *dialects*:

* ``budget_tokens``           - Anthropic ``{"thinking": {"type": "enabled", "budget_tokens": N}}``
* ``thinking_budget``         - Gemini   ``{"thinking_config": {"thinking_budget": N, "include_thoughts": true}}``
* ``reasoning_effort``        - OpenAI / xAI / Mistral / Groq / Perplexity / DeepSeek / Ollama ``{"reasoning_effort": "low|medium|high"}``
* ``reasoning_object``        - OpenRouter ``{"reasoning": {"effort": "low|medium|high"}}``
* ``reasoning_toggle``        - Together hybrid ``{"reasoning": {"enabled": true|false}}``
* ``chat_template_kwargs``    - NVIDIA NIM (DeepSeek-V4) ``{"chat_template_kwargs": {"thinking": true}}`` + ``reasoning_effort``
* ``thinking_token_budget``   - Cohere ``{"thinking": {"type": "enabled", "token_budget": N}}``
"""
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Thinking negotiation dialects understood by ``normalize_thinking_effort``.
THINKING_DIALECTS = (
    "budget_tokens",
    "thinking_budget",
    "reasoning_effort",
    "reasoning_object",
    "reasoning_toggle",
    "chat_template_kwargs",
    "thinking_token_budget",
)

# Together hybrid (reasoning.toggle) model name hints.
TOGETHER_REASONING_TOGGLE_HINTS = ("kimi", "glm", "minimax", "deepseek-v4")

@dataclass
class ModelSpec:
    name: str
    provider: str
    context_window: int
    max_output_tokens: int
    supports_thinking: bool
    thinking_type: Optional[str]  # one of THINKING_DIALECTS
    supports_tools: bool = True
    supports_vision: bool = False
    supports_video: bool = False

# Known baseline catalog for exact matches — covers all major providers and model families.
# Future models auto-detected via heuristics; add entries here for precise overrides.
# Entry shape: {"context": int, "output": int, "thinking": bool, "thinking_type": dialect}
KNOWN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ─── Anthropic ───────────────────────────────────────────────────────────
    "claude-opus-4-5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-opus-4-5-20251101": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-sonnet-4-5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-sonnet-4-5-20250929": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-haiku-4-5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-haiku-4-5-20251001": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-sonnet-4": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-sonnet-4-20250514": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-opus-4": {"context": 200000, "output": 32000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-opus-4-20250514": {"context": 200000, "output": 32000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-4-sonnet": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-7-sonnet": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-7-sonnet-20250219": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-5-sonnet": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-sonnet-20241022": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-haiku": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-haiku-20241022": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-opus": {"context": 200000, "output": 4096, "thinking": False, "thinking_type": None},
    "claude-3-opus-20240229": {"context": 200000, "output": 4096, "thinking": False, "thinking_type": None},

    # OpenRouter dotted aliases
    "anthropic/claude-sonnet-4.5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "anthropic/claude-opus-4.5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "anthropic/claude-haiku-4.5": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},

    # ─── OpenAI ──────────────────────────────────────────────────────────────
    "gpt-5": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5-2025-08-07": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5-mini": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5-mini-2025-08-07": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5-nano": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5.1": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5.1-2025-11-13": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5-codex": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5.1-codex": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5.1-codex-max": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-5.1-codex-mini": {"context": 400000, "output": 128000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-preview": {"context": 128000, "output": 32768, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-mini": {"context": 128000, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3-mini": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3-pro": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o4-mini": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-4.1": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4.1-mini": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4.1-nano": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4o": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4o-2024-11-20": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4o-mini": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4-turbo": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── Google Gemini ───────────────────────────────────────────────────────
    "gemini-2.5-pro": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-pro-latest": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash-latest": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash-lite": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.0-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-1.5-pro": {"context": 2097152, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-1.5-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},
    "google/gemini-2.5-pro": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "google/gemini-2.5-flash": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "google/gemini-2.5-flash-lite": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},

    # ─── DeepSeek (direct + NIM-hosted + OpenRouter) ─────────────────────────
    "deepseek-flash": {"context": 1048576, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-chat": {"context": 65536, "output": 8192, "thinking": False, "thinking_type": None},
    "deepseek-reasoner": {"context": 65536, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    # DeepSeek V4 family — 1M context window
    "deepseek-v4-pro": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-v4-pro-0813": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-v4-flash": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-v4-flash-0731": {"context": 1310720, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-v4-pro": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-v4-pro-0813": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-v4-flash": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-v4-flash-0731": {"context": 1310720, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek/deepseek-v4-pro": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek/deepseek-v4-pro-0813": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek/deepseek-v4-flash": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek/deepseek-v4-flash-0731": {"context": 1310720, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/DeepSeek-R1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-r1-0528": {"context": 163840, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-v3.2": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3.1": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v2.5": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Meta Llama (Groq, Together, Fireworks, Ollama, NIM) ─────────────────
    "llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta-llama/llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta/llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta-llama/llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta/llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-70b-versatile": {"context": 131072, "output": 32768, "thinking": False, "thinking_type": None},
    "llama-3.1-8b-instant": {"context": 131072, "output": 131072, "thinking": False, "thinking_type": None},
    "llama-3.3-70b-versatile": {"context": 131072, "output": 32768, "thinking": False, "thinking_type": None},
    # Llama 4 family
    "meta-llama/llama-4-scout-17b-16e-instruct": {"context": 512000, "output": 16384, "thinking": False, "thinking_type": None},
    "meta-llama/llama-4-maverick-17b-128e-instruct": {"context": 1048576, "output": 16384, "thinking": False, "thinking_type": None},
    "meta/llama-4-scout-17b-16e-instruct": {"context": 512000, "output": 16384, "thinking": False, "thinking_type": None},
    "meta/llama-4-maverick-17b-128e-instruct": {"context": 1048576, "output": 16384, "thinking": False, "thinking_type": None},

    # ─── Mistral ─────────────────────────────────────────────────────────────
    "mistral-large-latest": {"context": 262144, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-large-2512": {"context": 262144, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-medium-3-5": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "mistral-medium-latest": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "mistral-small-latest": {"context": 262144, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "mistral-small-2603": {"context": 262144, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "codestral-latest": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},
    "codestral-2508": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},
    "ministral-8b-2512": {"context": 262144, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-medium": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-small": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-nemo": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "pixtral-large-latest": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Qwen ────────────────────────────────────────────────────────────────
    "qwen-2.5-coder-32b": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "qwen/qwen-2.5-coder-32b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "qwen-3-235b-a22b": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwen/qwen-3-235b-a22b": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwen/qwen3-next-80b-a3b-thinking": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwq-32b": {"context": 131072, "output": 32768, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwq-32b-preview": {"context": 32768, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── xAI Grok ────────────────────────────────────────────────────────────
    "grok-4.6": {"context": 500000, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-4.5": {"context": 500000, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-4.3": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-4.20-0309-reasoning": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-4.20-0309-non-reasoning": {"context": 1048576, "output": 65536, "thinking": False, "thinking_type": None},
    "grok-3": {"context": 131072, "output": 16384, "thinking": False, "thinking_type": None},
    "grok-3-mini": {"context": 131072, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-2": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},
    "grok-beta": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Cohere Command ──────────────────────────────────────────────────────
    "command-a-reasoning-08-2025": {"context": 256000, "output": 32768, "thinking": True, "thinking_type": "thinking_token_budget"},
    "command-a-plus-05-2026": {"context": 128000, "output": 65536, "thinking": True, "thinking_type": "thinking_token_budget"},
    "command-a-03-2025": {"context": 256000, "output": 8192, "thinking": False, "thinking_type": None},
    "command-r-plus": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},
    "command-r": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── Perplexity ──────────────────────────────────────────────────────────
    "sonar-pro": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "sonar": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "sonar-reasoning": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "sonar-reasoning-pro": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "sonar-deep-research": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── NVIDIA NIM specific model IDs ───────────────────────────────────────
    "nvidia/nemotron-3-ultra-550b-a55b": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/nemotron-3-super-120b-a12b": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/nemotron-3-nano-30b-a3b": {"context": 262144, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": {"context": 131072, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/llama-3.1-nemotron-nano-8b-v1": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},
    "nvidia/nvidia-nemotron-nano-9b-v2": {"context": 131072, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/deepseek-ai/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/nemotron-4-340b-instruct": {"context": 4096, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── Together AI popular models ──────────────────────────────────────────
    "together/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "together/qwen-2.5-coder-32b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "together/deepseek-v4-pro": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_toggle"},
    "moonshotai/kimi-k3": {"context": 1048576, "output": 16384, "thinking": True, "thinking_type": "reasoning_toggle"},
    "zai-org/glm-5.2": {"context": 524288, "output": 16384, "thinking": True, "thinking_type": "reasoning_toggle"},
    "minimaxai/minimax-m2.7": {"context": 524288, "output": 16384, "thinking": True, "thinking_type": "reasoning_toggle"},
    "minimaxai/minimax-m3": {"context": 524288, "output": 16384, "thinking": True, "thinking_type": "reasoning_toggle"},

    # ─── OpenRouter GPT-OSS (also on Groq / Together) ────────────────────────
    "openai/gpt-oss-120b": {"context": 131072, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "openai/gpt-oss-20b": {"context": 131072, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── Fireworks popular models ────────────────────────────────────────────
    "accounts/fireworks/models/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "accounts/fireworks/models/deepseek-v3": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
}

# Reasoning-name hints used for future / unregistered models.
REASONING_HINTS = (
    "r1", "reasoner", "reasoning", "o1", "o3", "o4", "thinking", "thought", "qwq",
    "gpt-5", "grok-4", "nemotron", "magistral", "muse",
)

# Vision-language name hints. Positive hints cover the known omni / VLM families
# (OpenAI "4o / 4.1 / 5", all Gemini, all Claude, Grok-4, Pixtral, Llama-4, and
# the common open VLMs); negative hints beat positives for text-only variants.
VISION_POSITIVE_HINTS = (
    "vision", "vlm", "multimodal", "omni", "4o", "gpt-4.1", "gpt-5", "pixtral",
    "nvlm", "cogvlm", "llava", "moondream", "phi-4-vision", "salamandra",
    "qwen2.5-vl", "qwen3-vl", "-vl", "minicpm-v", "internvl", "gemini", "claude",
    "grok-4", "llama-4", "command-",
    # Explicit multimodal families.
    "muse", "gemma-3", "paligemma", "neva", "chameleon", "idefics", "molmo",
    "fuyu", "ferret", "cambrian", "smolvlm",
)
VISION_NEGATIVE_HINTS = (
    "text", "embedding", "embed", "dry", "slim", "nemotron",
)


def detect_vision_support(model_name: str, provider: str = "") -> bool:
    """Best-effort vision-language capability detection for ANY model.

    Priority: explicit registry override -> name heuristics (positive beats
    absent; negative beats positive) -> provider family default (all Gemini
    models are multimodal). Server-reported capabilities discovered from each
    provider's ``/models`` endpoint override the heuristic in
    ``resolve_model_spec_dynamic``.
    """
    name = (model_name or "").lower().strip()
    prov = (provider or "").lower().strip()

    entry = _lookup_registry(model_name)
    if entry is not None and "vision" in entry:
        return bool(entry["vision"])

    if any(tok in name for tok in VISION_NEGATIVE_HINTS):
        return False
    if any(tok in name for tok in VISION_POSITIVE_HINTS):
        return True
    if prov in ("gemini", "google"):
        return True
    return False


def _lookup_registry(model_name: str) -> Optional[Dict[str, Any]]:
    """Bidirectional prefix/suffix match against the known-model catalog.

    Handles OpenRouter-style ``provider/model:tag`` names, bare model names,
    and dashed-vs-dotted alias families."""
    name = (model_name or "").lower().strip()
    name_base = name.split(":")[0]

    for k, v in KNOWN_MODEL_REGISTRY.items():
        kl = k.lower()
        if (
            name == kl
            or name_base == kl
            or name.endswith(f"/{kl}")
            or name_base.endswith(f"/{kl}")
            or kl.endswith(f"/{name}")
            or kl.endswith(f"/{name_base}")
            or name == kl.replace(".", "-")
            or name_base == kl.replace(".", "-")
        ):
            return v
    return None


def detect_context_window(model_name: str, provider: str = "") -> int:
    """
    Dynamically measure the context window for ANY model (current or future).
    Analyzes exact matches, explicit token naming indicators (-1m, -128k, etc.),
    and model family defaults.
    """
    name = (model_name or "").lower().strip()
    name_base = name.split(":")[0]  # Strip OpenRouter tags like :free, :nitro

    # 1. Exact catalog match (bidirectional prefix/suffix)
    entry = _lookup_registry(name)
    if entry:
        return entry["context"]

    # 2. Extract explicit context tokens from model name suffix/infix
    # Examples: llama-3.1-8b-instruct-128k, qwen-2.5-1m, gpt-4o-256k
    match_m = re.search(r"[-_]([1-9][0-9]?)\s*m\b", name)
    if match_m:
        return int(match_m.group(1)) * 1_000_000

    match_k = re.search(r"[-_]([1-9][0-9]{1,3})\s*k\b", name)
    if match_k:
        return int(match_k.group(1)) * 1_000

    # 3. Model family heuristics for future models
    if "gemini" in name:
        return 1_048_576  # 1M for the 2.5+ / 3.x generation

    if "claude" in name:
        return 200_000  # Future Claude models: 200k base

    if "gpt-4.1" in name or "gpt-4-1" in name:
        return 1_047_576

    if "gpt-5" in name or name.startswith("gpt-5"):
        return 400_000

    if any(p in name for p in ("o1", "o3", "o4")):
        return 200_000

    if "grok-4.20" in name or "grok-4.3" in name:
        return 1_048_576

    if "grok-4" in name:
        return 500_000

    if "v4-flash-0731" in name:
        return 1_310_720

    if "v4" in name and any(x in name for x in ("deepseek", "flash", "pro")):
        return 1_048_576

    if "deepseek-flash" in name or "deepseek-v4-pro" in name:
        return 1_048_576

    if "llama-4-maverick" in name:
        return 1_048_576

    if "llama-4-scout" in name or "llama-4" in name:
        return 512_000

    if "command-a" in name:
        return 256_000 if "plus" not in name else 128_000

    if "sonar-pro" in name:
        return 200_000

    if "mistral" in name and any(x in name for x in ("large", "medium", "small", "ministral")):
        return 262_144

    if "qwen-3" in name or "qwen3" in name:
        return 1_000_000

    if "v3.2" in name or "v3.1" in name:
        return 163_840

    if any(p in name for p in ("gpt-4", "llama-3", "qwen-2", "mistral", "deepseek", "grok")):
        return 128_000

    # 4. Provider-specific heuristics
    prov = (provider or "").lower()
    if prov == "gemini":
        return 1_048_576
    if prov == "anthropic":
        return 200_000

    # 5. Safe universal default
    return 128_000


def _provider_dialect(thinking_type: Optional[str], name: str, provider: str) -> Optional[str]:
    """Translate a canonical dialect to the exact one a provider understands.

    An explicitly configured provider always wins over model-name inference,
    because a model name like ``anthropic/claude-*`` served through OpenRouter
    must use OpenRouter's ``reasoning`` object rather than Anthropic's
    ``budget_tokens``."""
    prov = (provider or "").lower().strip()
    n = (name or "").lower()

    if not thinking_type:
        return None

    if prov == "openrouter":
        return "reasoning_object"
    if prov == "together":
        return "reasoning_toggle" if any(h in n for h in TOGETHER_REASONING_TOGGLE_HINTS) else "reasoning_effort"
    if prov == "nvidia" or "nim" in prov:
        # NVIDIA exposes per-model mechanisms; DeepSeek-V4 uses chat-template kwargs.
        if "deepseek-v4" in n:
            return "chat_template_kwargs"
        return "reasoning_effort"
    if prov in ("anthropic", "gemini", "cohere", "openai", "deepseek", "xai",
                "mistral", "groq", "perplexity", "google", "nim", "fireworks", "ollama"):
        if prov in ("anthropic",) or "claude" in n:
            return "budget_tokens"
        if prov in ("gemini", "google") or "gemini" in n:
            return "thinking_budget"
        if prov == "cohere" or "command" in n:
            return "thinking_token_budget"
        return "reasoning_effort"

    # Unknown provider: infer the dialect from the model family name.
    if "claude" in n:
        return "budget_tokens"
    if "gemini" in n:
        return "thinking_budget"
    if "command" in n:
        return "thinking_token_budget"

    # Leave known dialects as-is; anything unexpected normalizes to OpenAI-style.
    return thinking_type if thinking_type in THINKING_DIALECTS else "reasoning_effort"


def detect_thinking_support(model_name: str, provider: str = "") -> tuple[bool, Optional[str]]:
    """
    Dynamically determine if a model supports thinking/reasoning effort
    and what parameter structure (dialect) it requires.
    """
    name = (model_name or "").lower().strip()
    name_base = name.split(":")[0]
    prov = (provider or "").lower().strip()

    # 1. Exact registry check
    entry = _lookup_registry(name)
    if entry is not None:
        return entry["thinking"], _provider_dialect(entry["thinking_type"], name, provider)

    # 2. Heuristic detection of reasoning patterns in current and future models
    is_reasoning = any(token in name for token in REASONING_HINTS)

    if not is_reasoning and ("claude-3-7" in name or "claude-4" in name or "claude-sonnet-4" in name):
        is_reasoning = True

    if not is_reasoning and ("gemini-2.5" in name or "gemini-3" in name):
        is_reasoning = True

    if not is_reasoning:
        return False, None

    # Determine canonical parameter format, then normalize to the provider dialect.
    if "claude" in name:
        canonical = "budget_tokens"
    elif "gemini" in name:
        canonical = "thinking_budget"
    elif "command" in name:
        canonical = "thinking_token_budget"
    else:
        canonical = "reasoning_effort"

    return True, _provider_dialect(canonical, name, provider)


def inspect_model(model_name: str, provider: str = "") -> ModelSpec:
    """
    Generate complete ModelSpec for any given model name and provider.
    Uses the static catalog's output limits when present, heuristics otherwise.
    """
    clean_name = model_name.strip()
    context = detect_context_window(clean_name, provider)
    supports_thinking, thinking_type = detect_thinking_support(clean_name, provider)

    # Prefer catalog output limits; fall back to heuristics for unknown models.
    max_output: Optional[int] = None
    entry = _lookup_registry(clean_name)
    if entry is not None and entry.get("output"):
        max_output = int(entry["output"])

    if not max_output:
        if "claude-3-7" in clean_name.lower() or "claude-sonnet-4" in clean_name.lower():
            max_output = 64000
        elif any(k in clean_name.lower() for k in ("o1", "o3", "o4")):
            max_output = 100000
        elif "gpt-5" in clean_name.lower():
            max_output = 128000
        elif any(k in clean_name.lower() for k in ("o1-preview", "o1-mini", "gemini-2.5", "r1")):
            max_output = 32768
        elif context >= 1000000:
            max_output = 65536
        elif context >= 128000:
            max_output = 16384
        else:
            max_output = 8192

    return ModelSpec(
        name=clean_name,
        provider=provider,
        context_window=context,
        max_output_tokens=max_output,
        supports_thinking=supports_thinking,
        thinking_type=thinking_type,
        supports_tools=True,
        supports_vision=detect_vision_support(clean_name, provider),
        supports_video=_model_supports_video(clean_name, provider),
    )


# Provider families that accept native video input alongside images.
VIDEO_CAPABLE_PROVIDERS = ("gemini", "google", "anthropic", "nvidia", "nim")


def _model_supports_video(model_name: str, provider: str) -> bool:
    """Video input requires a provider that natively accepts media blobs.

    Gemini (inline_data), Anthropic (base64 video blocks), and NVIDIA NIM
    (``input_video`` content) all accept inline video. Generic OpenAI-style
    completions endpoints have no standard video schema, so they opt out.
    """
    prov = (provider or "").lower().strip()
    if prov in VIDEO_CAPABLE_PROVIDERS:
        return True
    name = (model_name or "").lower()
    if prov in ("openrouter",) and "gemini" in name and ("video" in name or "gemini-2" in name):
        return True
    return False