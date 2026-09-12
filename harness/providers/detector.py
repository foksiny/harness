"""
Model-Specific Detection & Capability Engine for Harness.
Dynamically resolves context window limits, token output boundaries, and
reasoning/thinking effort mechanics for any current or future LLM.
"""
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class ModelSpec:
    name: str
    provider: str
    context_window: int
    max_output_tokens: int
    supports_thinking: bool
    thinking_type: Optional[str]  # "budget_tokens", "reasoning_effort", "thinking_budget", None
    supports_tools: bool = True
    supports_vision: bool = False

# Known baseline catalog for exact matches — covers all major providers and model families.
# Future models auto-detected via heuristics; add entries here for precise overrides.
KNOWN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ─── Anthropic ───────────────────────────────────────────────────────────
    "claude-sonnet-4-20250514": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-4-sonnet": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-7-sonnet": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-7-sonnet-20250219": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-5-sonnet": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-sonnet-20241022": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-haiku": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-haiku-20241022": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-opus": {"context": 200000, "output": 4096, "thinking": False, "thinking_type": None},
    "claude-3-opus-20240229": {"context": 200000, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── OpenAI ──────────────────────────────────────────────────────────────
    "gpt-4.1": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4.1-mini": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4.1-nano": {"context": 1047576, "output": 32768, "thinking": False, "thinking_type": None},
    "gpt-4o": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4o-2024-11-20": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4o-mini": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4-turbo": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},
    "o1": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-preview": {"context": 128000, "output": 32768, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-mini": {"context": 128000, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3-mini": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3-pro": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o4-mini": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── Google Gemini ───────────────────────────────────────────────────────
    "gemini-2.5-pro": {"context": 2097152, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-pro-latest": {"context": 2097152, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash-preview-05-20": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.0-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-2.0-flash-thinking-exp": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-1.5-pro": {"context": 2097152, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-1.5-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── DeepSeek (direct + NIM-hosted + OpenRouter) ─────────────────────────
    "deepseek-chat": {"context": 163840, "output": 8192, "thinking": False, "thinking_type": None},
    "deepseek-reasoner": {"context": 64000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    # DeepSeek V4 family — 1M to 1.3M context window
    "deepseek-ai/deepseek-v4-flash-0731": {"context": 1310720, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4-flash": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek/deepseek-v4-flash-0731": {"context": 1310720, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek/deepseek-v4-flash": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4-pro-0813": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4-pro": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek/deepseek-v4-pro-0813": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek/deepseek-v4-pro": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4-0324": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4-0813": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v4": {"context": 1048576, "output": 32768, "thinking": False, "thinking_type": None},
    # DeepSeek V3 family
    "deepseek-ai/deepseek-v3.2": {"context": 163840, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3.1": {"context": 163840, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-chat-v3.1": {"context": 163840, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3-0324": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "deepseek-ai/deepseek-v3-0823": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    # DeepSeek R1 reasoning family
    "deepseek-ai/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/DeepSeek-R1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/deepseek-r1-0528": {"context": 163840, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    # DeepSeek V2.5
    "deepseek-ai/deepseek-v2.5": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Meta Llama (Groq, Together, Fireworks, Ollama, NIM) ─────────────────
    "llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta-llama/llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta/llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta-llama/llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta/llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-70b-versatile": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-8b-instant": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    # Llama 4 family
    "meta-llama/llama-4-scout-17b-16e-instruct": {"context": 512000, "output": 16384, "thinking": False, "thinking_type": None},
    "meta-llama/llama-4-maverick-17b-128e-instruct": {"context": 1048576, "output": 16384, "thinking": False, "thinking_type": None},
    "meta/llama-4-scout-17b-16e-instruct": {"context": 512000, "output": 16384, "thinking": False, "thinking_type": None},
    "meta/llama-4-maverick-17b-128e-instruct": {"context": 1048576, "output": 16384, "thinking": False, "thinking_type": None},

    # ─── Mistral ─────────────────────────────────────────────────────────────
    "mistral-large": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-large-latest": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-large-2": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-medium": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-small": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "codestral": {"context": 256000, "output": 8192, "thinking": False, "thinking_type": None},
    "codestral-latest": {"context": 256000, "output": 8192, "thinking": False, "thinking_type": None},
    "pixtral-large-latest": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "mistral-nemo": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Qwen ────────────────────────────────────────────────────────────────
    "qwen-2.5-coder-32b": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "qwen/qwen-2.5-coder-32b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "qwen-3-235b-a22b": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwen/qwen-3-235b-a22b": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwen-3-30b-a3b": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwq-32b": {"context": 128000, "output": 32768, "thinking": True, "thinking_type": "reasoning_effort"},
    "qwq-32b-preview": {"context": 32768, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── xAI Grok ────────────────────────────────────────────────────────────
    "grok-3": {"context": 131072, "output": 16384, "thinking": False, "thinking_type": None},
    "grok-3-mini": {"context": 131072, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "grok-3-fast": {"context": 131072, "output": 16384, "thinking": False, "thinking_type": None},
    "grok-2": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},
    "grok-beta": {"context": 131072, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Cohere Command ──────────────────────────────────────────────────────
    "command-a-03-2025": {"context": 256000, "output": 16384, "thinking": False, "thinking_type": None},
    "command-r-plus": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},
    "command-r": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── Perplexity ──────────────────────────────────────────────────────────
    "sonar-pro": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "sonar": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "sonar-reasoning-pro": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "sonar-reasoning": {"context": 128000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},

    # ─── NVIDIA NIM specific model IDs ───────────────────────────────────────
    "nvidia/llama-3.1-nemotron-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "nvidia/nemotron-4-340b-instruct": {"context": 4096, "output": 4096, "thinking": False, "thinking_type": None},

    # ─── Together AI popular models ──────────────────────────────────────────
    "together/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "together/qwen-2.5-coder-32b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # ─── Fireworks popular models ────────────────────────────────────────────
    "accounts/fireworks/models/deepseek-r1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},
    "accounts/fireworks/models/deepseek-v3": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
}

def detect_context_window(model_name: str, provider: str = "") -> int:
    """
    Dynamically measure the context window for ANY model (current or future).
    Analyzes exact matches, explicit token naming indicators (-1m, -128k, etc.),
    and model family defaults.
    """
    name = (model_name or "").lower().strip()
    name_base = name.split(":")[0]  # Strip OpenRouter tags like :free, :nitro

    # 1. Exact catalog match (bidirectional prefix/suffix)
    for k, v in KNOWN_MODEL_REGISTRY.items():
        kl = k.lower()
        if (
            name == kl
            or name_base == kl
            or name.endswith(f"/{kl}")
            or name_base.endswith(f"/{kl}")
            or kl.endswith(f"/{name}")
            or kl.endswith(f"/{name_base}")
        ):
            return v["context"]

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
        return 2_097_152 if ("pro" in name or "ultra" in name) else 1_048_576

    if "claude" in name:
        # Future Claude models: 200k base
        return 200_000

    if "gpt-4.1" in name or "gpt-4-1" in name:
        return 1_047_576

    if any(p in name for p in ("o1", "o3", "o4", "gpt-5")):
        return 200_000

    if "v4-flash-0731" in name:
        return 1_310_720

    if "v4" in name and any(x in name for x in ("deepseek", "flash", "pro")):
        return 1_048_576

    if "llama-4-maverick" in name:
        return 1_048_576

    if "llama-4-scout" in name or "llama-4" in name:
        return 512_000

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

def detect_thinking_support(model_name: str, provider: str = "") -> tuple[bool, Optional[str]]:
    """
    Dynamically determine if a model supports thinking/reasoning effort
    and what parameter structure it requires.
    """
    name = (model_name or "").lower().strip()
    name_base = name.split(":")[0]
    prov = (provider or "").lower().strip()

    # 1. Exact registry check (bidirectional prefix/suffix)
    for k, v in KNOWN_MODEL_REGISTRY.items():
        kl = k.lower()
        if (
            name == kl
            or name_base == kl
            or name.endswith(f"/{kl}")
            or name_base.endswith(f"/{kl}")
            or kl.endswith(f"/{name}")
            or kl.endswith(f"/{name_base}")
        ):
            return v["thinking"], v["thinking_type"]

    # 2. Heuristic detection of reasoning patterns in current and future models
    is_reasoning = any(token in name for token in (
        "r1", "reasoner", "reasoning", "o1", "o3", "o4", "thinking", "thought", "qwq"
    ))

    if not is_reasoning and ("claude-3-7" in name or "claude-4" in name or "claude-sonnet-4" in name):
        is_reasoning = True

    if not is_reasoning and ("gemini-2.5" in name or "gemini-3" in name):
        is_reasoning = True

    if not is_reasoning:
        return False, None

    # Determine parameter format
    if prov == "anthropic" or "claude" in name:
        return True, "budget_tokens"
    elif prov == "gemini" or "gemini" in name:
        return True, "thinking_budget"
    else:
        # OpenAI, OpenRouter, Groq, DeepSeek, Together, Fireworks, NVIDIA NIM
        return True, "reasoning_effort"

def inspect_model(model_name: str, provider: str = "") -> ModelSpec:
    """
    Generate complete ModelSpec for any given model name and provider.
    """
    clean_name = model_name.strip()
    context = detect_context_window(clean_name, provider)
    supports_thinking, thinking_type = detect_thinking_support(clean_name, provider)

    # Max output tokens heuristic
    max_output = 8192
    if "claude-3-7" in clean_name.lower():
        max_output = 64000
    elif any(k in clean_name.lower() for k in ("o1", "o3", "gemini-2.5", "r1")):
        max_output = 32768
    elif context >= 1000000:
        max_output = 65536
    elif context >= 128000:
        max_output = 16384

    return ModelSpec(
        name=clean_name,
        provider=provider,
        context_window=context,
        max_output_tokens=max_output,
        supports_thinking=supports_thinking,
        thinking_type=thinking_type,
        supports_tools=True,
        supports_vision=any(v in clean_name.lower() for v in ("vision", "4o", "gemini", "claude", "pixtral")),
    )
