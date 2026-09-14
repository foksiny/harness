"""
Provider Registry and Factory for Harness.
Supports 16+ LLM Providers with model-specific context window resolution
and dynamic thinking effort mapping.
"""
from typing import Dict, Any, List, Optional
from harness.providers.base import BaseProvider
from harness.providers.anthropic_provider import AnthropicProvider
from harness.providers.gemini_provider import GeminiProvider
from harness.providers.openai_compatible import OpenAICompatibleProvider
from harness.providers.mock_provider import MockProvider
from harness.config import HarnessConfig

PROVIDER_CONFIGS = {
    "anthropic": {
        "class": AnthropicProvider,
        "display_name": "Anthropic Claude",
        "default_model": "claude-sonnet-4-5",
        "base_url": "https://api.anthropic.com/v1",
    },
    "openai": {
        "class": OpenAICompatibleProvider,
        "display_name": "OpenAI",
        "default_model": "gpt-5",
        "base_url": "https://api.openai.com/v1",
    },
    "gemini": {
        "class": GeminiProvider,
        "display_name": "Google Gemini",
        "default_model": "gemini-2.5-pro",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
    },
    "openrouter": {
        "class": OpenAICompatibleProvider,
        "display_name": "OpenRouter",
        "default_model": "anthropic/claude-sonnet-4.5",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "nvidia": {
        "class": OpenAICompatibleProvider,
        "display_name": "NVIDIA NIM",
        "default_model": "meta/llama-3.3-70b-instruct",
        "base_url": "https://integrate.api.nvidia.com/v1",
    },
    "opencode": {
        "class": OpenAICompatibleProvider,
        "display_name": "OpenCode Zen",
        "default_model": "big-pickle",
        "base_url": "https://opencode.ai/zen/v1",
        "user_agent": "opencode/1.18.16",
        "opencode_session": True,
    },
    "groq": {
        "class": OpenAICompatibleProvider,
        "display_name": "Groq",
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "deepseek": {
        "class": OpenAICompatibleProvider,
        "display_name": "DeepSeek",
        "default_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1",
    },
    "mistral": {
        "class": OpenAICompatibleProvider,
        "display_name": "Mistral AI",
        "default_model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
    },
    "xai": {
        "class": OpenAICompatibleProvider,
        "display_name": "xAI Grok",
        "default_model": "grok-4.6",
        "base_url": "https://api.x.ai/v1",
    },
    "ollama": {
        "class": OpenAICompatibleProvider,
        "display_name": "Ollama (Local)",
        "default_model": "llama3.3",
        "base_url": "http://localhost:11434/v1",
    },
    "together": {
        "class": OpenAICompatibleProvider,
        "display_name": "Together AI",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "base_url": "https://api.together.xyz/v1",
    },
    "fireworks": {
        "class": OpenAICompatibleProvider,
        "display_name": "Fireworks AI",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "base_url": "https://api.fireworks.ai/inference/v1",
    },
    "cohere": {
        "class": OpenAICompatibleProvider,
        "display_name": "Cohere",
        "default_model": "command-a-03-2025",
        "base_url": "https://api.cohere.com/v2",
    },
    "perplexity": {
        "class": OpenAICompatibleProvider,
        "display_name": "Perplexity Sonar",
        "default_model": "sonar-pro",
        "base_url": "https://api.perplexity.ai",
    },
    "mock": {
        "class": MockProvider,
        "display_name": "Offline Mock Engine",
        "default_model": "mock-harness-model",
        "base_url": "http://localhost",
    },
}

def get_provider(provider_name: str, config: Optional[HarnessConfig] = None) -> BaseProvider:
    """Instantiate a provider by name with credentials and endpoint from config."""
    pname = (provider_name or "anthropic").lower().strip()
    cfg_entry = PROVIDER_CONFIGS.get(pname, PROVIDER_CONFIGS["anthropic"])

    api_key = config.get_api_key(pname) if config else None
    base_url = (config.get_base_url(pname) if config else None) or cfg_entry["base_url"]

    cls = cfg_entry["class"]
    if cls is AnthropicProvider or cls is GeminiProvider:
        return cls(api_key=api_key, base_url=base_url)
    elif cls is MockProvider:
        return MockProvider()
    else:
        extra_headers = dict(cfg_entry.get("extra_headers") or {})
        if cfg_entry.get("opencode_session"):
            import uuid as _uuid
            extra_headers.setdefault("x-opencode-client", "cli")
            extra_headers.setdefault("x-opencode-session", str(_uuid.uuid4()))
        return OpenAICompatibleProvider(
            name=pname,
            display_name=cfg_entry["display_name"],
            default_model=cfg_entry["default_model"],
            base_url=base_url,
            api_key=api_key,
            user_agent=cfg_entry.get("user_agent"),
            extra_headers=extra_headers,
        )

def list_providers() -> Dict[str, str]:
    """Return dictionary of provider_id -> display_name."""
    return {k: v["display_name"] for k, v in PROVIDER_CONFIGS.items()}
