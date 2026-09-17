"""
Model-Specific Detection & Capability Engine for Harness.

Capability resolution is DATA-DRIVEN — the same architecture opencode, Cline,
and Kilo Code converged on. Nothing here guesses a model's limits by matching
its *family* in the name (``"claude" in name -> 200k``). Instead:

Tier 1  Live provider ``/models`` metadata   (discovery.fetch_remote_models)
Tier 2  Universal models.dev catalog         (discovery, ``~/.harness`` cache)
Tier 3  Bundled flat-data catalog            (KNOWN_MODEL_REGISTRY, exact-id lookup)
Tier 4  Literal capability markers           (suffixes the model id *itself*
                                              declares: ``-1m``/``-128k``/``-vl``/``-reasoning``)
Tier 5  Provider-level defaults              (PROVIDER_FALLBACKS / dialect map)
Tier 6  Conservative universal default       (128k context, no extras)

Only tiers 4/5/6 run without any metadata, and each is explicitly labelled via
``ModelSpec.source`` so callers can tell whether a value is authoritative.

Thinking is negotiated per-provider using a small set of *dialects*, chosen by
provider identity (never by model family):

* ``budget_tokens``            - Anthropic   ``{"thinking": {"type": "enabled", "budget_tokens": N}}``
* ``adaptive``                 - Anthropic 4.6+ ``{"thinking": {"type": "adaptive"}}``
* ``thinking_budget``          - Gemini 2.5 ``{"thinking_config": {"thinking_budget": N, "include_thoughts": true}}``
* ``thinking_level``           - Gemini 3   ``{"thinking_config": {"include_thoughts": true, "thinking_level": L}}``
* ``reasoning_effort``         - OpenAI/xAI/Mistral/Groq/DeepSeek/Ollama/... ``{"reasoning_effort": L}``
* ``reasoning_object``         - OpenRouter effort ``{"reasoning": {"effort": L}}``
* ``reasoning_max_tokens``     - OpenRouter budget ``{"reasoning": {"max_tokens": N}}``
* ``reasoning_toggle``         - Together hybrid ``{"reasoning": {"enabled": bool}}``
* ``chat_template_kwargs``     - NVIDIA NIM (DeepSeek-V4) ``{"chat_template_kwargs": {"thinking": bool}}``
* ``thinking_token_budget``    - Cohere ``{"thinking": {"type": "enabled", "token_budget": N}}``
"""
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

# Thinking negotiation dialects understood by ``normalize_thinking_effort``.
THINKING_DIALECTS = (
    "budget_tokens",
    "adaptive",
    "thinking_budget",
    "thinking_level",
    "reasoning_effort",
    "reasoning_object",
    "reasoning_max_tokens",
    "reasoning_toggle",
    "chat_template_kwargs",
    "thinking_token_budget",
)

# Probe values to test which effort names a dialect actually accepts.
_PROBE_EFFORTS = [
    "off", "none", "disable",
    "low", "minimal", "light",
    "medium", "med", "moderate", "normal",
    "high", "max", "maximum", "full", "deep",
    "500", "8000", "16000",
]

# ─── Literal capability markers ─────────────────────────────────────────────
#
# Tier-4 last resort. These are substrings the *model id itself* declares as a
# capability (OpenRouter/model-hub naming conventions), NOT family guesses.
#   - context:  "-128k", "-1m", "-2m" ... (explicit token-count suffix)
#   - vision:   "-vl", "vlm", "vision", "omni", "4o", "gpt-4.1", ...
#   - thinking: "-reasoning", "-reasoner", "qwq", "r1", "o1/o3/o4/o5", ...

FALLBACK_CONTEXT = 128_000
FALLBACK_OUTPUT = 16_384

VISION_MARKERS = (
    "vision", "-vl", "vlm", "multimodal", "-omni", "neva", "muse",
    "gemma-3", "paligemma", "idefics", "molmo", "smolvlm", "internvl",
    "minicpm-v", "cogvlm", "llava", "moondream", "phi-4-vision",
    "fuyu", "ferret", "cambrian", "4o", "gpt-4.1", "llama-4",
)
VIDEO_MARKERS = ("video", "-video")

REASONING_MARKERS = (
    "reason", "reasoner", "reasoning", "thinking", "thought",
    "qwq", "r1", "o1", "o3", "o4", "o5", "muse",
)

# Marker normalisation map: a marker substring in a model id maps to a strong
# (True) or strong-negative (False) capability answer.
_MARKER_SIGNALS = {m: True for m in VISION_MARKERS}
_MARKER_SIGNALS.update({m: False for m in ("embedding", "embed", "-dry", "-slim", "-textonly")})


@dataclass
class ModelSpec:
    name: str
    provider: str
    context_window: int          # 0 => unknown
    max_output_tokens: int       # 0 => unknown
    supports_thinking: bool
    thinking_type: Optional[str]  # one of THINKING_DIALECTS
    supports_tools: bool = True
    supports_vision: bool = False
    supports_video: bool = False
    reasoning_options: List[str] = field(default_factory=list)  # server-advertised reasoning mechanisms
    source: str = "default"      # catalog | server | universal | literal | provider | default


def _E(context: int, output: int, thinking: bool, ttype: Optional[str] = None,
       vision: Optional[bool] = None, video: Optional[bool] = None,
       reasoning_options: Optional[List[str]] = None) -> Dict[str, Any]:
    """Compact constructor for a bundled catalog entry."""
    e: Dict[str, Any] = {
        "context": context,
        "output": output,
        "thinking": thinking,
        "thinking_type": ttype,
        "vision": vision,
        "video": video,
    }
    if reasoning_options:
        e["reasoning_options"] = list(reasoning_options)
    return e


# ─── Bundled flat-data catalog ──────────────────────────────────────────────
# A hand-maintained *data* snapshot (models.dev style) covering the models this
# harness ships defaults for. New / unknown models are resolved dynamically via
# tier 1/2 (live provider metadata + universal catalog) BEFORE this table, and
# tier 4/5/6 after it. Entry shape:
#   context: int | output: int | thinking: bool | thinking_type: dialect-pin
#   vision/video: pinned True/False (or None => fall through to markers/defaults)
#
# `thinking_type` is only pinned when the *server* exposes a non-default
# reasoning mechanism for that model (e.g. NVIDIA NIM's DeepSeek-V4 uses
# chat-template kwargs). Otherwise the provider dialect (tier 5) applies.
KNOWN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ─── Anthropic ───────────────────────────────────────────────────────────
    "claude-opus-4-5": _E(200000, 64000, True, vision=True, video=True),
    "claude-opus-4-5-20251101": _E(200000, 64000, True, vision=True, video=True),
    "claude-sonnet-4-5": _E(200000, 64000, True, vision=True, video=True),
    "claude-sonnet-4-5-20250929": _E(200000, 64000, True, vision=True, video=True),
    "claude-haiku-4-5": _E(200000, 64000, True, vision=True, video=True),
    "claude-haiku-4-5-20251001": _E(200000, 64000, True, vision=True, video=True),
    "claude-sonnet-4": _E(200000, 64000, True, vision=True, video=True),
    "claude-sonnet-4-20250514": _E(200000, 64000, True, vision=True, video=True),
    "claude-opus-4": _E(200000, 32000, True, vision=True, video=True),
    "claude-opus-4-20250514": _E(200000, 32000, True, vision=True, video=True),
    "claude-4-sonnet": _E(200000, 64000, True, vision=True, video=True),
    "claude-3-7-sonnet": _E(200000, 64000, True, vision=True, video=True),
    "claude-3-7-sonnet-20250219": _E(200000, 64000, True, vision=True, video=True),
    "claude-3-5-sonnet": _E(200000, 8192, False, vision=True, video=True),
    "claude-3-5-sonnet-20241022": _E(200000, 8192, False, vision=True, video=True),
    "claude-3-5-haiku": _E(200000, 8192, False, vision=True, video=True),
    "claude-3-5-haiku-20241022": _E(200000, 8192, False, vision=True, video=True),
    "claude-3-opus": _E(200000, 4096, False, vision=True, video=True),
    "claude-3-opus-20240229": _E(200000, 4096, False, vision=True, video=True),

    # OpenRouter dotted aliases
    "anthropic/claude-sonnet-4.5": _E(200000, 64000, True, vision=True, video=True),
    "anthropic/claude-opus-4.5": _E(200000, 64000, True, vision=True, video=True),
    "anthropic/claude-haiku-4.5": _E(200000, 64000, True, vision=True, video=True),

    # ─── OpenAI ──────────────────────────────────────────────────────────────
    "gpt-5": _E(400000, 128000, True, reasoning_options=["reasoning_effort", "reasoning_summary"]),
    "gpt-5-2025-08-07": _E(400000, 128000, True),
    "gpt-5-mini": _E(400000, 128000, True),
    "gpt-5-mini-2025-08-07": _E(400000, 128000, True),
    "gpt-5-nano": _E(400000, 128000, True),
    "gpt-5.1": _E(400000, 128000, True, reasoning_options=["reasoning_effort", "reasoning_summary"]),
    "gpt-5.1-2025-11-13": _E(400000, 128000, True),
    "gpt-5-codex": _E(400000, 128000, True),
    "gpt-5.1-codex": _E(400000, 128000, True),
    "gpt-5.1-codex-max": _E(400000, 128000, True),
    "gpt-5.1-codex-mini": _E(400000, 128000, True),
    "o1": _E(200000, 100000, True, vision=False),
    "o1-preview": _E(128000, 32768, True, vision=False),
    "o1-mini": _E(128000, 65536, True, vision=False),
    "o3": _E(200000, 100000, True, vision=True),
    "o3-mini": _E(200000, 100000, True, vision=False),
    "o3-pro": _E(200000, 100000, True, vision=True),
    "o4-mini": _E(200000, 100000, True, vision=True),
    "gpt-4.1": _E(1047576, 32768, False, vision=True, video=True),
    "gpt-4.1-mini": _E(1047576, 32768, False, vision=True, video=True),
    "gpt-4.1-nano": _E(1047576, 32768, False, vision=True, video=True),
    "gpt-4o": _E(128000, 16384, False, vision=True, video=True),
    "gpt-4o-2024-11-20": _E(128000, 16384, False, vision=True, video=True),
    "gpt-4o-mini": _E(128000, 16384, False, vision=True, video=True),
    "gpt-4-turbo": _E(128000, 4096, False, vision=True, video=True),

    # ─── Google Gemini ───────────────────────────────────────────────────────
    "gemini-2.5-pro": _E(1048576, 65536, True, vision=True,
                          reasoning_options=["thinking_budget", "thinking_level"]),
    "gemini-2.5-pro-latest": _E(1048576, 65536, True, vision=True),
    "gemini-2.5-flash": _E(1048576, 65536, True, vision=True),
    "gemini-2.5-flash-latest": _E(1048576, 65536, True, vision=True),
    "gemini-2.5-flash-lite": _E(1048576, 65536, True, vision=True),
    "gemini-2.0-flash": _E(1048576, 8192, False, vision=True, video=True),
    "gemini-1.5-pro": _E(2097152, 8192, False, vision=True, video=True),
    "gemini-1.5-flash": _E(1048576, 8192, False, vision=True, video=True),
    "google/gemini-2.5-pro": _E(1048576, 65536, True, vision=True),
    "google/gemini-2.5-flash": _E(1048576, 65536, True, vision=True),
    "google/gemini-2.5-flash-lite": _E(1048576, 65536, True, vision=True),
    "google/gemma-3-27b-it": _E(128000, 8192, False, vision=True, video=False),

    # ─── DeepSeek (direct + NIM-hosted + OpenRouter) ─────────────────────────
    "deepseek-flash": _E(1048576, 8192, True, vision=False),
    "deepseek-chat": _E(65536, 8192, False, vision=False),
    "deepseek-reasoner": _E(65536, 8192, True, vision=False,
                            reasoning_options=["reasoning_effort"]),
    # DeepSeek V4 family — 1M context window
    "deepseek-v4-pro": _E(1048576, 16384, True, vision=False),
    "deepseek-v4-pro-0813": _E(1048576, 16384, True, vision=False),
    "deepseek-v4-flash": _E(1048576, 16384, True, vision=False),
    "deepseek-v4-flash-0731": _E(1310720, 16384, True, vision=False),
    "deepseek-ai/deepseek-v4-pro": _E(1048576, 16384, True, vision=False,
                                      ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "deepseek-ai/deepseek-v4-pro-0813": _E(1048576, 16384, True, vision=False,
                                           ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "deepseek-ai/deepseek-v4-flash": _E(1048576, 16384, True, vision=False,
                                        ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "deepseek-ai/deepseek-v4-flash-0731": _E(1310720, 16384, True, vision=False,
                                             ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "deepseek/deepseek-v4-pro": _E(1048576, 16384, True, vision=False),
    "deepseek/deepseek-v4-pro-0813": _E(1048576, 16384, True, vision=False),
    "deepseek/deepseek-v4-flash": _E(1048576, 16384, True, vision=False),
    "deepseek/deepseek-v4-flash-0731": _E(1310720, 16384, True, vision=False),
    "deepseek-ai/deepseek-r1": _E(128000, 16384, True, vision=False,
                                  reasoning_options=["reasoning_effort", "reasoning_content"]),
    "deepseek-ai/DeepSeek-R1": _E(128000, 16384, True, vision=False),
    "deepseek-ai/deepseek-r1-0528": _E(163840, 16384, True, vision=False),
    "deepseek-ai/deepseek-v3.2": _E(128000, 16384, False, vision=False),
    "deepseek-ai/deepseek-v3.1": _E(128000, 16384, False, vision=False),
    "deepseek-ai/deepseek-v3": _E(128000, 16384, False, vision=False),
    "deepseek-ai/deepseek-v2.5": _E(128000, 8192, False, vision=False),

    # ─── Meta Llama (Groq, Together, Fireworks, Ollama, NIM) ─────────────────
    "llama-3.3-70b-instruct": _E(128000, 8192, False, vision=False),
    "meta-llama/llama-3.3-70b-instruct": _E(128000, 8192, False, vision=False),
    "meta/llama-3.3-70b-instruct": _E(128000, 8192, False, vision=False),
    "llama-3.1-405b-instruct": _E(128000, 8192, False, vision=False),
    "meta-llama/llama-3.1-405b-instruct": _E(128000, 8192, False, vision=False),
    "meta/llama-3.1-405b-instruct": _E(128000, 8192, False, vision=False),
    "llama-3.1-70b-versatile": _E(131072, 32768, False, vision=False),
    "llama-3.1-8b-instant": _E(131072, 131072, False, vision=False),
    "llama-3.3-70b-versatile": _E(131072, 32768, False, vision=False),
    # Llama 4 family
    "meta-llama/llama-4-scout-17b-16e-instruct": _E(512000, 16384, False, vision=True, video=False),
    "meta-llama/llama-4-maverick-17b-128e-instruct": _E(1048576, 16384, False, vision=True, video=False),
    "meta/llama-4-scout-17b-16e-instruct": _E(512000, 16384, False, vision=True, video=False),
    "meta/llama-4-maverick-17b-128e-instruct": _E(1048576, 16384, False, vision=True, video=False),

    # ─── Mistral ─────────────────────────────────────────────────────────────
    "mistral-large-latest": _E(262144, 8192, False, vision=False),
    "mistral-large-2512": _E(262144, 8192, False, vision=False),
    "mistral-medium-3-5": _E(262144, 16384, True, vision=False),
    "mistral-medium-latest": _E(262144, 16384, True, vision=False),
    "mistral-small-latest": _E(262144, 8192, True, vision=False),
    "mistral-small-2603": _E(262144, 8192, True, vision=False),
    "codestral-latest": _E(131072, 8192, False, vision=False),
    "codestral-2508": _E(131072, 8192, False, vision=False),
    "ministral-8b-2512": _E(262144, 8192, False, vision=False),
    "mistral-medium": _E(128000, 8192, False, vision=False),
    "mistral-small": _E(128000, 8192, False, vision=False),
    "mistral-nemo": _E(128000, 8192, False, vision=False),
    "pixtral-large-latest": _E(128000, 8192, False, vision=True, video=False),

    # ─── Qwen ────────────────────────────────────────────────────────────────
    "qwen-2.5-coder-32b": _E(128000, 8192, False, vision=False),
    "qwen/qwen-2.5-coder-32b-instruct": _E(128000, 8192, False, vision=False),
    "qwen-3-235b-a22b": _E(128000, 8192, True, vision=False),
    "qwen/qwen-3-235b-a22b": _E(128000, 8192, True, vision=False),
    "qwen/qwen3-next-80b-a3b-thinking": _E(262144, 16384, True, vision=False),
    "qwq-32b": _E(131072, 32768, True, vision=False),
    "qwq-32b-preview": _E(32768, 8192, True, vision=False),

    # ─── xAI Grok ────────────────────────────────────────────────────────────
    "grok-4.6": _E(500000, 65536, True, vision=True),
    "grok-4.5": _E(500000, 65536, True, vision=True),
    "grok-4.3": _E(1048576, 65536, True, vision=True),
    "grok-4.20-0309-reasoning": _E(1048576, 65536, True, vision=True),
    "grok-4.20-0309-non-reasoning": _E(1048576, 65536, False, vision=True),
    "grok-3": _E(131072, 16384, False, vision=False),
    "grok-3-mini": _E(131072, 16384, True, vision=False),
    "grok-2": _E(131072, 8192, False, vision=False),
    "grok-beta": _E(131072, 8192, False, vision=False),

    # ─── Cohere Command ──────────────────────────────────────────────────────
    "command-a-reasoning-08-2025": _E(256000, 32768, True, vision=True,
                                      ttype="thinking_token_budget", reasoning_options=["thinking_token_budget"]),
    "command-a-plus": _E(128000, 65536, True, vision=True,
                         ttype="thinking_token_budget", reasoning_options=["thinking_token_budget"]),
    "command-a-plus-05-2026": _E(128000, 65536, True, vision=True,
                                 ttype="thinking_token_budget", reasoning_options=["thinking_token_budget"]),
    "command-a-03-2025": _E(256000, 8192, False, vision=True),
    "command-r-plus": _E(128000, 4096, False, vision=False),
    "command-r": _E(128000, 4096, False, vision=False),

    # ─── Perplexity ──────────────────────────────────────────────────────────
    "sonar-pro": _E(200000, 8192, False, vision=False),
    "sonar": _E(128000, 8192, False, vision=False),
    "sonar-reasoning": _E(128000, 8192, True, vision=False,
                          reasoning_options=["reasoning_effort"]),
    "sonar-reasoning-pro": _E(128000, 8192, True, vision=False),
    "sonar-deep-research": _E(128000, 8192, True, vision=False),

    # ─── NVIDIA NIM specific model IDs ───────────────────────────────────────
    "nvidia/nemotron-3-ultra-550b-a55b": _E(262144, 16384, True, vision=False,
                                            reasoning_options=["reasoning_effort"]),
    "nvidia/nemotron-3-super-120b-a12b": _E(262144, 16384, True, vision=False),
    "nvidia/nemotron-3-nano-30b-a3b": _E(262144, 16384, True, vision=False),
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": _E(131072, 16384, True, vision=False),
    "nvidia/llama-3.1-nemotron-nano-8b-v1": _E(131072, 8192, False, vision=False),
    "nvidia/nvidia-nemotron-nano-9b-v2": _E(131072, 16384, True, vision=False),
    "nvidia/deepseek-ai/deepseek-r1": _E(128000, 16384, True, vision=False),
    "nvidia/nemotron-4-340b-instruct": _E(4096, 4096, False, vision=False),

    # ─── Together AI popular models ──────────────────────────────────────────
    "together/deepseek-r1": _E(128000, 16384, True, vision=False),
    "together/qwen-2.5-coder-32b-instruct": _E(128000, 8192, False, vision=False),
    "together/deepseek-v4-pro": _E(1048576, 16384, True, vision=False,
                                   ttype="reasoning_toggle", reasoning_options=["reasoning_toggle"]),
    "moonshotai/kimi-k3": _E(1048576, 16384, True, vision=False,
                             reasoning_options=["reasoning_toggle", "reasoning_effort", "reasoning_object"]),
    "zai-org/glm-5.2": _E(524288, 16384, True, vision=False,
                          reasoning_options=["reasoning_toggle", "reasoning_effort"]),
    "z-ai/glm-5.3": _E(1310720, 16384, True, vision=False,
                        ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "z-ai/glm-5.3-flash": _E(1310720, 16384, True, vision=False,
                              ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "zai-org/glm-5.3": _E(1310720, 16384, True, vision=False,
                          ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "zai-org/glm-5.3-flash": _E(1310720, 16384, True, vision=False,
                                ttype="chat_template_kwargs", reasoning_options=["chat_template_kwargs"]),
    "minimaxai/minimax-m2.7": _E(524288, 16384, True, vision=False,
                                 reasoning_options=["reasoning_toggle", "reasoning_effort"]),
    "minimaxai/minimax-m3": _E(524288, 16384, True, vision=False,
                               reasoning_options=["reasoning_toggle", "reasoning_effort"]),

    # ─── OpenRouter GPT-OSS (also on Groq / Together) ────────────────────────
    "openai/gpt-oss-120b": _E(131072, 65536, True, vision=True),
    "openai/gpt-oss-20b": _E(131072, 65536, True, vision=True),

    # ─── Fireworks popular models ────────────────────────────────────────────
    "accounts/fireworks/models/deepseek-r1": _E(128000, 16384, True, vision=False),
    "accounts/fireworks/models/deepseek-v3": _E(128000, 16384, False, vision=False),
}


def _lookup_registry(model_name: str) -> Optional[Dict[str, Any]]:
    """Exact-id lookup against the bundled catalog.

    Handles OpenRouter ``provider/model:tag`` names, bare model names, and
    dashed-vs-dotted alias families. No fuzzy/prefix matching on arbitrary
    substrings — the id must actually be known.
    """
    name = (model_name or "").strip().lower()
    if not name:
        return None
    name_base = name.split(":")[0]

    # Pass 1: exact match (preferred).
    for k, v in KNOWN_MODEL_REGISTRY.items():
        kl = k.lower()
        if name == kl or name_base == kl or name == kl.replace(".", "-") or name_base == kl.replace(".", "-"):
            return v

    # Pass 2: suffix/prefix match (OpenRouter provider/model patterns).
    for k, v in KNOWN_MODEL_REGISTRY.items():
        kl = k.lower()
        if (
            name.endswith(f"/{kl}")
            or name_base.endswith(f"/{kl}")
            or kl.endswith(f"/{name}")
            or kl.endswith(f"/{name_base}")
        ):
            return v
    return None


def _literal_context(model_name: str) -> Optional[int]:
    """Extract a context window the model id literally declares.

    Handles ``-128k``/``-256k``/``-1m``/``-2m`` style suffixes that are part of
    the canonical model id (e.g. ``llama-4-256k-instruct``, ``qwen-3-512k``).
    """
    name = (model_name or "").lower()
    match_m = re.search(r"[-_]([1-9][0-9]?)\s*m\b", name)
    if match_m:
        return int(match_m.group(1)) * 1_000_000
    match_k = re.search(r"[-_]([1-9][0-9]{1,3})\s*k\b", name)
    if match_k:
        return int(match_k.group(1)) * 1_000
    return None


def _has_marker(model_name: str, markers) -> Optional[bool]:
    name = (model_name or "").lower()
    hit = None
    for m in markers:
        if m in name:
            hit = True
    return hit


# ─── Provider-level fallbacks (tier 5) ──────────────────────────────────────
# Data, keyed by provider identity — NOT by model-family name. Applied only
# when a model has no catalog entry and no literal markers. ``None`` context
# means "no opinion" (falls through to the universal default).
PROVIDER_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "gemini":     {"context": 1048576, "thinking": True,  "vision": True,  "video": True},
    "google":     {"context": 1048576, "thinking": True,  "vision": True,  "video": True},
    "anthropic":  {"context": 200000,  "thinking": True,  "vision": True,  "video": True},
    "openai":     {"context": 128000,  "thinking": True,  "vision": True,  "video": False},
    "xai":        {"context": 131072,  "thinking": True,  "vision": True,  "video": False},
    "openrouter": {"context": 128000,  "thinking": False, "vision": False, "video": False},
    "together":   {"context": 131072,  "thinking": False, "vision": False, "video": False},
    "nvidia":     {"context": 131072,  "thinking": False, "vision": False, "video": False},
    "nim":        {"context": 131072,  "thinking": False, "vision": False, "video": False},
    "mistral":    {"context": 262144,  "thinking": False, "vision": False, "video": False},
    "cohere":     {"context": 128000,  "thinking": False, "vision": False, "video": False},
    "groq":       {"context": 131072,  "thinking": False, "vision": False, "video": False},
    "deepseek":   {"context": 128000,  "thinking": False, "vision": False, "video": False},
    "perplexity": {"context": 128000,  "thinking": False, "vision": False, "video": False},
    "fireworks":  {"context": 128000,  "thinking": False, "vision": False, "video": False},
    "ollama":     {"context": 65536,   "thinking": False, "vision": False, "video": False},
    "opencode":   {"context": 400000,  "thinking": True,  "vision": True,  "video": False},
    "mock":       {"context": 128000,  "thinking": False, "vision": False, "video": False},
}

# Canonical reasoning dialect per provider. This is what ``normalize_thinking_effort``
# uses when a model advertises thinking but no server mechanism pinned it.
PROVIDER_DIALECTS: Dict[str, str] = {
    "anthropic": "budget_tokens",
    "gemini": "thinking_budget",
    "google": "thinking_budget",
    "openai": "reasoning_effort",
    "xai": "reasoning_effort",
    "mistral": "reasoning_effort",
    "groq": "reasoning_effort",
    "deepseek": "reasoning_effort",
    "perplexity": "reasoning_effort",
    "fireworks": "reasoning_effort",
    "ollama": "reasoning_effort",
    "opencode": "reasoning_object",
    "nvidia": "reasoning_effort",
    "nim": "reasoning_effort",
    "openrouter": "reasoning_object",
    "together": "reasoning_effort",
    "cohere": "thinking_token_budget",
    "mock": "reasoning_effort",
}


def _provider_dialect(thinking_type: Optional[str], name: str, provider: str,
                      reasoning_options: Optional[List[str]] = None) -> Optional[str]:
    """Translate a canonical dialect to the exact one the provider understands.

    The dialect is a property of the *provider* (its request schema), never of
    the model family. A pinned ``thinking_type`` (server-advertised mechanism)
    always wins; otherwise the provider map applies.

    Special-case: Together-hosted hybrid models (kimi-k3, glm-5.2, minimax-m2/m3)
    use the reasoning toggle dialect even though Together's default is
    reasoning_effort. Detect those model ids explicitly.
    """
    if thinking_type and thinking_type in THINKING_DIALECTS:
        return thinking_type

    prov = (provider or "").lower().strip()
    nlow = (name or "").lower()
    if prov == "together" and any(x in nlow for x in ("kimi-k3", "kimi_k3", "glm-5.2", "glm_5.2", "minimax-m2", "minimax-m3", "minimax_m2", "minimax_m3")):
        return "reasoning_toggle"
    if prov in PROVIDER_DIALECTS:
        return PROVIDER_DIALECTS[prov]
    if thinking_type:
        return thinking_type
    return "reasoning_effort"


def detect_context_window(model_name: str, provider: str = "") -> int:
    """Resolve a model's context window, newest metadata first.

    Catalog entry -> literal token-count suffix in the id -> provider fallback
    -> conservative universal default. Never guesses from model *family*.
    """
    name = (model_name or "").strip()
    name_base = (name or "").lower().split(":")[0]

    entry = _lookup_registry(name)
    if entry is not None and entry.get("context"):
        return int(entry["context"])

    lit = _literal_context(name_base)
    if lit:
        return lit

    fallback = PROVIDER_FALLBACKS.get((provider or "").lower().strip())
    if fallback and fallback.get("context"):
        return int(fallback["context"])

    return FALLBACK_CONTEXT


def detect_thinking_support(model_name: str, provider: str = "") -> Tuple[bool, Optional[str]]:
    """Resolve thinking support + request dialect for any model id.

    Catalog pin -> literal reasoning marker in the id -> provider fallback.
    The dialect comes from the provider (or the catalog pin), never from the
    model family name.
    """
    name = (model_name or "").strip().lower()
    prov = (provider or "").lower().strip()

    # NVIDIA NIM does not expose Together-style hybrid toggle models as reasoning
    # models. Generic hybrid ids (kimi-k3, glm-5.2, minimax-m2/m3) are flagged
    # as thinking only for providers that actually host them as reasoning models.
    if prov in ("nvidia", "nim") and any(x in name for x in ("kimi-k3", "kimi_k3", "glm-5.2", "glm_5.2", "minimax-m2", "minimax-m3", "minimax_m2", "minimax_m3")):
        # z-ai glm-5.3 family IS hosted on NIM as thinking via chat_template_kwargs
        # (don't blanket-disable that family).
        if "glm-5.3" not in name:
            return False, None

    entry = _lookup_registry(model_name)
    if entry is not None:
        thinking = bool(entry["thinking"])
        if not thinking:
            return False, None
        pinned = entry.get("thinking_type")
        if pinned in THINKING_DIALECTS:
            return True, pinned
        return True, _provider_dialect(entry["thinking_type"], name, prov, entry.get("reasoning_options"))

    if _has_marker(name, REASONING_MARKERS):
        return True, _provider_dialect(None, name, prov)

    fallback = PROVIDER_FALLBACKS.get(prov)
    if fallback and fallback.get("thinking"):
        return True, _provider_dialect(None, name, prov)

    return False, None


def detect_vision_support(model_name: str, provider: str = "") -> bool:
    """Resolve vision-language (image input) support for any model id.

    Catalog pin -> literal modality marker in the id -> provider fallback -> False.
    """
    name = (model_name or "").lower().strip()
    prov = (provider or "").lower().strip()

    entry = _lookup_registry(model_name)
    if entry is not None and entry.get("vision") is not None:
        return bool(entry["vision"])

    sig = _has_marker(name, VISION_MARKERS)
    if sig:
        return sig
    for neg in ("embedding", "embed", "-dry", "-slim", "-textonly"):
        if neg in name:
            return False

    fallback = PROVIDER_FALLBACKS.get(prov)
    if fallback and fallback.get("vision"):
        return True
    return False


# Provider families/APIs that accept native image+video input alongside text.
VIDEO_CAPABLE_PROVIDERS = ("gemini", "google", "anthropic", "nvidia", "nim")


def _model_supports_video(model_name: str, provider: str) -> bool:
    """Video input follows the same data-driven tiers as vision."""
    name = (model_name or "").lower().strip()
    prov = (provider or "").lower().strip()

    entry = _lookup_registry(model_name)
    if entry is not None and entry.get("video") is not None:
        return bool(entry["video"])

    if _has_marker(name, VIDEO_MARKERS):
        return True

    fallback = PROVIDER_FALLBACKS.get(prov)
    if fallback and fallback.get("video"):
        return True
    return False


def inspect_model(model_name: str, provider: str = "") -> ModelSpec:
    """Generate a complete ``ModelSpec`` for any model id and provider.

    Network-free: bundled catalog + literal markers + provider fallbacks +
    universal default. Live provider metadata is merged on top by
    ``discovery.resolve_model_spec_dynamic``.
    """
    clean_name = (model_name or "").strip()
    context = detect_context_window(clean_name, provider)
    supports_thinking, thinking_type = detect_thinking_support(clean_name, provider)

    entry = _lookup_registry(clean_name)

    # Output limit: catalog pin -> proportional heuristic based on context.
    max_output: Optional[int] = None
    if entry is not None and entry.get("output"):
        max_output = int(entry["output"])
    if not max_output:
        if context >= 1_000_000:
            max_output = 65536
        elif context >= 128_000:
            max_output = 16384
        elif context >= 32_000:
            max_output = 8192
        else:
            max_output = 4096

    reasoning_options = list(entry.get("reasoning_options") or []) if entry else []

    source = "catalog"
    if entry is None:
        if _literal_context(clean_name) is not None or _has_marker(clean_name, REASONING_MARKERS) or _has_marker(clean_name, VISION_MARKERS):
            source = "literal"
        elif (provider or "").lower().strip() in PROVIDER_FALLBACKS:
            source = "provider"
        else:
            source = "default"

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
        reasoning_options=reasoning_options,
        source=source,
    )