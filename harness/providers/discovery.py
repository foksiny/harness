"""
Dynamic Model Discovery & Context Resolution for Harness.
Interrogates provider /models endpoints (OpenRouter, NVIDIA NIM, Groq, OpenAI, Ollama, DeepSeek)
with local disk caching in ~/.harness/models_cache.json.
"""
import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional
from harness.providers.detector import inspect_model, ModelSpec

CACHE_FILE = Path.home() / ".harness" / "models_cache.json"
CACHE_TTL = 86400  # 24 hours

def load_cached_models() -> Dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass
    return {}

def save_cached_models(cache_data: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception:
        pass

def fetch_remote_models(
    provider_name: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """Query provider /models endpoint to discover live model list and context window limits."""
    prov = (provider_name or "").lower().strip()
    burl = (base_url or "").rstrip("/")

    # Check cache first unless forced
    cache = load_cached_models()
    prov_cache = cache.get(prov, {})
    if not force and prov_cache and (time.time() - prov_cache.get("timestamp", 0) < CACHE_TTL):
        return prov_cache.get("models", [])

    if not burl:
        return []

    endpoint = f"{burl}/models"
    headers = {
        "User-Agent": "Harness-CLI/1.0",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(endpoint, headers=headers)
    models_found = []

    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        # OpenRouter / OpenAI / NIM format: data is list of dicts with 'id', 'context_length', etc.
        raw_list = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        for item in raw_list:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name")
                if not mid:
                    continue
                # Context length detection across various provider schemas
                c_len = (
                    item.get("context_length")
                    or item.get("context_window")
                    or item.get("max_model_len")
                    or item.get("max_context_length")
                    or item.get("max_input_tokens")
                    or item.get("input_token_limit")
                )
                if not c_len and isinstance(item.get("top_provider"), dict):
                    c_len = item["top_provider"].get("context_length")

                # Fallback to model detector heuristic if provider omitted context_length
                if not c_len:
                    c_len = inspect_model(mid, prov).context_window

                # Thinking / reasoning capability detection
                th_support, th_type = detect_thinking_support(mid, prov)
                desc = str(item.get("description", "")).lower()
                if not th_support and any(x in desc for x in ("reasoning", "thinking", "thought")):
                    th_support = True
                    th_type = "reasoning_effort"

                models_found.append({
                    "id": mid,
                    "context_length": int(c_len) if c_len else 128000,
                    "supports_thinking": th_support,
                    "thinking_type": th_type,
                    "raw": item,
                })

        # Update cache
        if models_found:
            cache[prov] = {
                "timestamp": time.time(),
                "models": models_found,
            }
            save_cached_models(cache)
        return models_found

    except Exception:
        # Gracefully return cached data even if expired on network error
        return prov_cache.get("models", [])

def resolve_model_spec_dynamic(
    model_name: str,
    provider_name: str = "",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ModelSpec:
    """
    Resolve ModelSpec using static catalog + dynamic /models interrogation.
    Guarantees exact context window and thinking detection.
    """
    clean_name = (model_name or "").strip()
    spec = inspect_model(clean_name, provider_name)

    # Check cached or remote provider metadata
    cache = load_cached_models()
    prov = provider_name.lower().strip()
    prov_cache = cache.get(prov, {})
    models_list = prov_cache.get("models", [])

    # If cache miss and credentials exist, attempt live fetch
    if not models_list and base_url and api_key:
        try:
            models_list = fetch_remote_models(prov, api_key, base_url)
        except Exception:
            models_list = []

    clean_lower = clean_name.lower()
    clean_base = clean_lower.split(":")[0]

    for m in models_list:
        m_id = str(m.get("id", "")).strip()
        m_lower = m_id.lower()
        m_base = m_lower.split(":")[0]

        matches = (
            m_lower == clean_lower
            or m_base == clean_base
            or clean_lower.endswith(f"/{m_lower}")
            or clean_base.endswith(f"/{m_base}")
            or m_lower.endswith(f"/{clean_lower}")
            or m_base.endswith(f"/{clean_base}")
        )
        if matches:
            c_len = m.get("context_length")
            if c_len and isinstance(c_len, int) and c_len > 0:
                spec.context_window = c_len
            if m.get("supports_thinking"):
                spec.supports_thinking = True
                if not spec.thinking_type:
                    spec.thinking_type = m.get("thinking_type") or "reasoning_effort"
            break

    return spec
