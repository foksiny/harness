"""
Configuration manager for Harness CLI.
Handles user preferences, workspace settings, API keys, masking, and persistence.
"""
import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from harness import secure_store

USER_CONFIG_DIR = Path.home() / ".harness"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.json"
USER_SESSIONS_DIR = USER_CONFIG_DIR / "sessions"
USER_SKILLS_DIR = USER_CONFIG_DIR / "skills"
USER_MCP_FILE = USER_CONFIG_DIR / "mcp.json"
USER_THEMES_FILE = USER_CONFIG_DIR / "themes.json"

WORKSPACE_CONFIG_DIR = Path(".harness")
WORKSPACE_CONFIG_PATH = WORKSPACE_CONFIG_DIR / "config.json"
WORKSPACE_SKILLS_DIR = WORKSPACE_CONFIG_DIR / "skills"
WORKSPACE_MCP_FILE = WORKSPACE_CONFIG_DIR / "mcp.json"
WORKSPACE_THEMES_FILE = WORKSPACE_CONFIG_DIR / "themes.json"

ENV_KEY_MAPPINGS: Dict[str, List[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY", "OPENROUTER_KEY"],
    "nvidia": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "NV_API_KEY", "NIM_API_KEY"],
    "opencode": ["OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "xai": ["XAI_API_KEY", "GROK_API_KEY"],
    "together": ["TOGETHER_API_KEY", "TOGETHERAI_API_KEY"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "cohere": ["CO_API_KEY", "COHERE_API_KEY"],
    "perplexity": ["PERPLEXITY_API_KEY", "PPLX_API_KEY"],
}

def mask_key(key: Optional[str]) -> str:
    """Mask sensitive API key for safe display (e.g. sk-ant-***a1b2)."""
    if not key:
        return "(not set)"
    k = key.strip()
    if len(k) <= 8:
        return "***"
    prefix = k[:6] if len(k) >= 12 else k[:3]
    suffix = k[-4:]
    return f"{prefix}***{suffix}"

def normalize_provider_base_url(provider_name: str, url: Optional[str]) -> Optional[str]:
    """Normalize legacy/incorrect base URLs for a provider."""
    prov = (provider_name or "").lower().strip()
    if url and prov == "opencode":
        clean = url.strip().rstrip("/")
        for legacy in ("https://api.opencode.ai", "http://api.opencode.ai", "api.opencode.ai"):
            if clean == legacy or clean == legacy + "/v1":
                return "https://opencode.ai/zen/v1"
    return url

@dataclass
class HarnessConfig:
    provider: str = "anthropic"
    model: str = "claude-3-7-sonnet"
    mode: str = "build"              # plan, build, super
    permission: str = "default"      # secure, default, full
    thinking_effort: str = "high"    # off, low, medium, high, or token count
    theme: str = "cyberpunk"
    auto_compact: bool = True
    compact_threshold: float = 0.75  # Arm compaction at 75% of context window
    compact_target_ratio: float = 0.60  # Compact down to 60% of the window
    compact_cap_ratio: float = 0.95  # Hard cap; mid-turn emergency trim above this
    compact_max_message_tokens: int = 0  # 0 => auto (15% of context window)
    compact_preserve_turns: int = 4  # Never summarize the most recent N turns
    compact_summary: str = "auto"  # auto | llm | heuristic
    learning_enabled: bool = True    # Persistent cross-session lessons (learn_record/recall/promote)
    max_subagents: int = 4
    timeout_seconds: int = 120
    swarm_enabled: bool = False      # Enable agent swarms (always active in SUPER mode)
    force_media_attach: bool = False # Force-attach images even when the model isn't flagged vision-capable
    vfb_provider: str = "" # Vision fallback provider; when set, degraded media get described via this provider
    vfb_model: str = ""    # Vision fallback model; empty => the provider's default model
    api_keys: Dict[str, str] = field(default_factory=dict)
    base_urls: Dict[str, str] = field(default_factory=lambda: {
        "ollama": "http://localhost:11434",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "opencode": "https://opencode.ai/zen/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    })
    custom_system_prompt: Optional[str] = None
    telemetry_enabled: bool = False

    def get_api_key(self, provider_name: str) -> Optional[str]:
        """Get API key: secure store -> config in-memory -> environment variables."""
        prov = provider_name.lower().strip()
        # 1. Secure store (OS keychain or restricted file)
        stored = secure_store.get_key(prov)
        if stored:
            return stored
        # 2. Config in-memory override (set during this session)
        if prov in self.api_keys and self.api_keys[prov]:
            return self.api_keys[prov]
        # 3. Standard mapped environment variables with aliases
        env_vars = ENV_KEY_MAPPINGS.get(prov, [])
        for ev in env_vars:
            val = os.environ.get(ev)
            if val:
                return val
        # 4. Fallback generic ENV format
        generic_env = f"{prov.upper()}_API_KEY"
        return os.environ.get(generic_env)

    def set_api_key(self, provider_name: str, key: str) -> None:
        """Store API key in secure store and in-memory cache."""
        prov = provider_name.lower().strip()
        clean = key.strip()
        self.api_keys[prov] = clean
        secure_store.set_key(prov, clean)

    def remove_api_key(self, provider_name: str) -> bool:
        """Remove API key from secure store and in-memory cache."""
        prov = provider_name.lower().strip()
        self.api_keys.pop(prov, None)
        return secure_store.remove_key(prov)

    def list_keys_status(self) -> List[Dict[str, Any]]:
        """Return status and masked keys for all supported providers."""
        from harness.providers import PROVIDER_CONFIGS
        res = []
        for prov_id, prov_meta in sorted(PROVIDER_CONFIGS.items()):
            if prov_id == "mock":
                continue
            cfg_key = self.api_keys.get(prov_id)
            env_vars = ENV_KEY_MAPPINGS.get(prov_id, [f"{prov_id.upper()}_API_KEY"])
            env_val = None
            active_env_var = env_vars[0] if env_vars else f"{prov_id.upper()}_API_KEY"
            for ev in env_vars:
                if os.environ.get(ev):
                    env_val = os.environ.get(ev)
                    active_env_var = ev
                    break

            effective_key = cfg_key or env_val
            source = "config" if cfg_key else ("env" if env_val else "none")

            res.append({
                "provider": prov_id,
                "display_name": prov_meta["display_name"],
                "status": "configured" if effective_key else "missing",
                "masked": mask_key(effective_key),
                "source": source,
                "env_var": active_env_var,
            })
        return res

    def get_base_url(self, provider_name: str) -> Optional[str]:
        url = self.base_urls.get(provider_name.lower().strip())
        return normalize_provider_base_url(provider_name, url)

    def set_base_url(self, provider_name: str, url: str) -> None:
        self.base_urls[provider_name.lower().strip()] = normalize_provider_base_url(provider_name, url.strip())

    def set_field(self, key: str, value: str) -> bool:
        """Update any configuration field with automatic type conversion."""
        k = key.lower().strip()
        if not hasattr(self, k):
            return False

        current_val = getattr(self, k)
        if isinstance(current_val, bool):
            setattr(self, k, value.lower() in ("true", "1", "yes", "on"))
        elif isinstance(current_val, int):
            setattr(self, k, int(value))
        elif isinstance(current_val, float):
            setattr(self, k, float(value))
        elif isinstance(current_val, str):
            setattr(self, k, value)
        else:
            return False
        return True

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

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    if USER_CONFIG_PATH.exists():
        try:
            with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = HarnessConfig.from_dict(data)
        except Exception:
            pass

    if WORKSPACE_CONFIG_PATH.exists():
        try:
            with open(WORKSPACE_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                workspace_config = HarnessConfig.from_dict(data)
                for k, v in asdict(workspace_config).items():
                    if v is not None:
                        setattr(config, k, v)
        except Exception:
            pass

    # Migrate any plaintext api_keys from config.json into secure store
    if config.api_keys:
        secure_store.migrate_plaintext_keys(config.api_keys)
        # Populate in-memory cache from secure store
        for prov in list(config.api_keys.keys()):
            config.api_keys[prov] = secure_store.get_key(prov) or ""

    # Enforce restrictive file permissions on config files
    secure_store.ensure_file_permissions()

    return config

def save_config(config: HarnessConfig, global_only: bool = True) -> None:
    """Save configuration to disk. API keys are stored in secure store, not config.json."""
    target_path = USER_CONFIG_PATH if global_only else WORKSPACE_CONFIG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.to_dict()
    # API keys live in secure store; strip from config.json
    data.pop("api_keys", None)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # Enforce restrictive permissions
    secure_store.ensure_file_permissions()
