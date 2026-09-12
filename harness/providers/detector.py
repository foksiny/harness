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

# Known baseline catalog for exact matches
KNOWN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Anthropic
    "claude-3-7-sonnet": {"context": 200000, "output": 64000, "thinking": True, "thinking_type": "budget_tokens"},
    "claude-3-5-sonnet": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-5-haiku": {"context": 200000, "output": 8192, "thinking": False, "thinking_type": None},
    "claude-3-opus": {"context": 200000, "output": 4096, "thinking": False, "thinking_type": None},

    # OpenAI
    "gpt-4o": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "gpt-4o-mini": {"context": 128000, "output": 16384, "thinking": False, "thinking_type": None},
    "o1": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-preview": {"context": 128000, "output": 32768, "thinking": True, "thinking_type": "reasoning_effort"},
    "o1-mini": {"context": 128000, "output": 65536, "thinking": True, "thinking_type": "reasoning_effort"},
    "o3-mini": {"context": 200000, "output": 100000, "thinking": True, "thinking_type": "reasoning_effort"},
    "gpt-4-turbo": {"context": 128000, "output": 4096, "thinking": False, "thinking_type": None},

    # Google Gemini
    "gemini-2.5-pro": {"context": 2097152, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.5-flash": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-2.0-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-2.0-flash-thinking-exp": {"context": 1048576, "output": 65536, "thinking": True, "thinking_type": "thinking_budget"},
    "gemini-1.5-pro": {"context": 2097152, "output": 8192, "thinking": False, "thinking_type": None},
    "gemini-1.5-flash": {"context": 1048576, "output": 8192, "thinking": False, "thinking_type": None},

    # DeepSeek
    "deepseek-chat": {"context": 64000, "output": 8192, "thinking": False, "thinking_type": None},
    "deepseek-reasoner": {"context": 64000, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
    "deepseek-ai/DeepSeek-R1": {"context": 128000, "output": 16384, "thinking": True, "thinking_type": "reasoning_effort"},

    # Meta Llama (Groq, Together, Fireworks, Ollama, NIM)
    "llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "meta-llama/llama-3.3-70b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-405b-instruct": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "llama-3.1-70b-versatile": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # Mistral
    "mistral-large": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "codestral": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},

    # Qwen
    "qwen-2.5-coder-32b": {"context": 128000, "output": 8192, "thinking": False, "thinking_type": None},
    "qwq-32b-preview": {"context": 32768, "output": 8192, "thinking": True, "thinking_type": "reasoning_effort"},
}

def detect_context_window(model_name: str, provider: str = "") -> int:
    """
    Dynamically measure the context window for ANY model (current or future).
    Analyzes exact matches, explicit token naming indicators (-1m, -128k, etc.),
    and model family defaults.
    """
    name = (model_name or "").lower().strip()

    # 1. Exact catalog match
    for k, v in KNOWN_MODEL_REGISTRY.items():
        if name == k.lower() or name.endswith(f"/{k.lower()}"):
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

    if any(p in name for p in ("o1", "o3", "o4", "gpt-5")):
        return 200_000

    if any(p in name for p in ("gpt-4", "llama-3", "llama-4", "qwen-2", "qwen-3", "mistral", "deepseek", "grok")):
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
    prov = (provider or "").lower().strip()

    # 1. Exact registry check
    for k, v in KNOWN_MODEL_REGISTRY.items():
        if name == k.lower() or name.endswith(f"/{k.lower()}"):
            return v["thinking"], v["thinking_type"]

    # 2. Heuristic detection of reasoning patterns in current and future models
    is_reasoning = any(token in name for token in (
        "r1", "reasoner", "reasoning", "o1", "o3", "o4", "thinking", "thought", "qwq"
    ))

    if not is_reasoning and "claude-3-7" in name:
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
