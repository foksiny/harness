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
    terminal_show_thinking: bool = False  # Stream raw reasoning text in the terminal; when off, show a compact Thinking indicator + token count instead
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
    provider_max_retries: int = 3    # Number of retries for provider errors (exponential backoff)
    provider_retry_base_delay: float = 5.0  # Base delay in seconds for first retry (doubles each retry: 5,10,20...)
    provider_stream_timeout: float = 300.0  # SSE read timeout: max seconds without any bytes mid-stream (reasoning stalls)
    force_media_attach: bool = False # Force-attach images even when the model isn't flagged vision-capable
    vfb_provider: str = "" # Vision fallback provider; when set, degraded media get described via this provider
    vfb_model: str = ""    # Vision fallback model; empty => the provider's default model
    browser_enabled: bool = True   # Register the browser_* web-control tools (Chrome via CDP)
    # API server — disabled by default for `harness`, requires explicit args to activate
    server_enabled: bool = False     # Enable HTTP API server (requires --server / serve / --host/--port)
    server_host: str = "127.0.0.1"    # Bind host: 127.0.0.1 (local) or 0.0.0.0 (VPS)
    server_port: int = 0             # 0 => auto hash-based (base*100+idx), else explicit port
    server_token: str = ""           # Optional bearer token for API auth (or HARNESS_API_TOKEN env)
    # Backward compat: old mesh_* aliases
    mesh_enabled: bool = False       # Deprecated: use server_enabled
    mesh_host: str = "127.0.0.1"      # Deprecated: use server_host
    mesh_auto_connect: bool = False  # Deprecated: no longer used (peer mesh removed)
    discord_bot_token: str = ""   # Discord bot token (stored in the secure store; DISCORD_BOT_TOKEN env fallback)
    discord_channel_ids: str = "" # Deprecated: use discord_blacklisted_channels / discord_whitelisted_channels
    discord_guild_id: str = ""    # Guild ID to sync slash commands to instantly (optional; otherwise global)
    discord_workspace: str = ""   # Working directory the Discord bot operates in (default: current directory)
    discord_permission: str = "full"  # Permission profile for the bot agent (secure, default, full)
    discord_auto_start: bool = False   # Start the Discord bot automatically when running interactive TUI
    discord_channel_mode: str = "blacklist"  # "blacklist" or "whitelist" — how discord_blacklisted/whitelisted_channels is interpreted
    discord_user_mode: str = "blacklist"     # "blacklist" or "whitelist" — how discord_blacklisted/whitelisted_users is interpreted
    discord_blacklisted_channels: str = ""   # Comma-separated channel IDs blocked from the bot (when channel_mode=blacklist)
    discord_whitelisted_channels: str = ""   # Comma-separated channel IDs the bot may operate in (when channel_mode=whitelist)
    discord_blacklisted_users: str = ""      # Comma-separated user IDs blocked from the bot (when user_mode=blacklist)
    discord_whitelisted_users: str = ""      # Comma-separated user IDs the bot may operate for (when user_mode=whitelist)
    discord_ask_timeout: int = 900           # Seconds an ask_user prompt waits for a Discord answer
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

    def get_discord_token(self) -> Optional[str]:
        """Resolve the Discord bot token: secure store -> config field -> environment."""
        # 1. Secure store (OS keychain or restricted file)
        stored = secure_store.get_key("discord")
        if stored:
            return stored
        # 2. Config in-memory override (set during this session)
        if self.discord_bot_token:
            return self.discord_bot_token
        # 3. Environment variables
        return os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_TOKEN")

    def set_discord_token(self, token: str) -> None:
        """Store the Discord bot token in the secure store and in-memory cache."""
        clean = (token or "").strip()
        self.discord_bot_token = clean
        if clean:
            secure_store.set_key("discord", clean)

    def remove_discord_token(self) -> bool:
        """Remove the Discord bot token from the secure store and in-memory cache."""
        self.discord_bot_token = ""
        return secure_store.remove_key("discord")

    def set_discord_channel_ids(self, channel_ids: str) -> None:
        """Store comma-separated allowed Discord channel IDs (normalized)."""
        self.discord_channel_ids = ",".join(
            c.strip() for c in (channel_ids or "").split(",") if c.strip()
        )

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

    def is_server_enabled(self) -> bool:
        """Effective server enabled (supports old mesh_enabled alias)."""
        # New default is False; enable if either flag is True (backward compat)
        return bool(self.server_enabled or self.mesh_enabled)

    def get_server_token(self) -> Optional[str]:
        if self.server_token:
            return self.server_token.strip()
        # Fallback to env
        env = os.environ.get("HARNESS_API_TOKEN") or os.environ.get("HARNESS_SERVER_TOKEN")
        if env:
            return env.strip()
        return None

    def set_field(self, key: str, value: str) -> bool:
        """Update any configuration field with automatic type conversion."""
        k = key.lower().strip()
        # Alias old mesh_* to server_*
        if k == "mesh_enabled":
            k = "server_enabled"
        elif k == "mesh_host":
            k = "server_host"
        if not hasattr(self, k):
            return False

        if k == "discord_bot_token":
            self.set_discord_token(value)
            return True
        if k == "discord_channel_ids":
            self.set_discord_channel_ids(value)
            return True

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

    # Migrate a plaintext discord_bot_token from config.json into secure store
    if config.discord_bot_token:
        stored = secure_store.get_key("discord")
        if not stored:
            secure_store.set_key("discord", config.discord_bot_token)
        config.discord_bot_token = stored or config.discord_bot_token

    # Backward compat: sync old mesh_* to new server_* if server_* not explicitly set
    # (If user set mesh_enabled=false in config, respect it)
    try:
        # If config file had mesh_enabled=false, ensure server_enabled reflects it
        # We can't know if it was explicitly set, so if mesh_enabled is False, force server_enabled False
        if not config.mesh_enabled:
            config.server_enabled = False
        if config.mesh_host != "127.0.0.1" and config.server_host == "127.0.0.1":
            config.server_host = config.mesh_host
    except Exception:
        pass

    # Enforce restrictive file permissions on config files
    secure_store.ensure_file_permissions()

    # Validate and clean up the key store (detect plaintext leaks, fix perms)
    secure_store.validate_keys_store()

    return config

def save_config(config: HarnessConfig, global_only: bool = True) -> None:
    """Save configuration to disk. API keys and the Discord token are stored in the
    secure store, never in config.json."""
    target_path = USER_CONFIG_PATH if global_only else WORKSPACE_CONFIG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.to_dict()
    # Secrets live in secure store; strip from config.json
    data.pop("api_keys", None)
    data.pop("discord_bot_token", None)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # Enforce restrictive permissions
    secure_store.ensure_file_permissions()
