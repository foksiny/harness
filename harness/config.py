"""
Configuration manager for Harness CLI.
Handles user preferences, workspace settings, API keys, and persistence.
"""
import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional

USER_CONFIG_DIR = Path.home() / ".harness"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.json"
USER_SESSIONS_DIR = USER_CONFIG_DIR / "sessions"
USER_SKILLS_DIR = USER_CONFIG_DIR / "skills"
USER_MCP_FILE = USER_CONFIG_DIR / "mcp.json"

WORKSPACE_CONFIG_DIR = Path(".harness")
WORKSPACE_CONFIG_PATH = WORKSPACE_CONFIG_DIR / "config.json"
WORKSPACE_SKILLS_DIR = WORKSPACE_CONFIG_DIR / "skills"
WORKSPACE_MCP_FILE = WORKSPACE_CONFIG_DIR / "mcp.json"

ENV_KEY_MAPPINGS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "cohere": "CO_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}

@dataclass
class HarnessConfig:
    provider: str = "anthropic"
    model: str = "claude-3-7-sonnet"
    mode: str = "build"              # plan, build, super
    permission: str = "default"      # secure, default, full
    thinking_effort: str = "high"    # off, low, medium, high, or token count
    theme: str = "cyberpunk"
    auto_compact: bool = True
    compact_threshold: float = 0.75  # Compact at 75% of context window
    max_subagents: int = 4
    timeout_seconds: int = 120
    api_keys: Dict[str, str] = field(default_factory=dict)
    base_urls: Dict[str, str] = field(default_factory=lambda: {
        "ollama": "http://localhost:11434",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "opencode": "https://api.opencode.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    })
    custom_system_prompt: Optional[str] = None
    telemetry_enabled: bool = False

    def get_api_key(self, provider_name: str) -> Optional[str]:
        """Get API key from config or environment variable."""
        prov = provider_name.lower().strip()
        # 1. Config explicit override
        if prov in self.api_keys and self.api_keys[prov]:
            return self.api_keys[prov]
        # 2. Standard mapped environment variable
        env_var = ENV_KEY_MAPPINGS.get(prov)
        if env_var and os.environ.get(env_var):
            return os.environ.get(env_var)
        # 3. Fallback generic ENV formats
        generic_env = f"{prov.upper()}_API_KEY"
        return os.environ.get(generic_env)

    def set_api_key(self, provider_name: str, key: str) -> None:
        """Store API key for a provider."""
        self.api_keys[provider_name.lower().strip()] = key.strip()

    def get_base_url(self, provider_name: str) -> Optional[str]:
        """Get base URL for provider if configured."""
        return self.base_urls.get(provider_name.lower().strip())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarnessConfig":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

def load_config() -> HarnessConfig:
    """Load configuration, cascading from global config to workspace config."""
    config = HarnessConfig()

    # Ensure directories exist
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Global config
    if USER_CONFIG_PATH.exists():
        try:
            with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = HarnessConfig.from_dict(data)
        except Exception:
            pass

    # 2. Local workspace config override
    if WORKSPACE_CONFIG_PATH.exists():
        try:
            with open(WORKSPACE_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                workspace_config = HarnessConfig.from_dict(data)
                # Merge non-default values
                for k, v in asdict(workspace_config).items():
                    if v is not None:
                        setattr(config, k, v)
        except Exception:
            pass

    return config

def save_config(config: HarnessConfig, global_only: bool = True) -> None:
    """Save configuration to disk."""
    target_path = USER_CONFIG_PATH if global_only else WORKSPACE_CONFIG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
