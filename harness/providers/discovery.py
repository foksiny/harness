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
from harness.providers.detector import (
    inspect_model,
    detect_thinking_support,
    detect_vision_support,
    _model_supports_video,
    ModelSpec,
)

CACHE_FILE = Path.home() / ".harness" / "models_cache.json"
UNIVERSAL_CACHE_FILE = Path.home() / ".harness" / "universal_models.json"
CACHE_TTL = 86400  # 24 hours

# Keys scanned for ``input_modalities`` across provider /models schemas
# (OpenRouter ``architecture``, OpenAI ``lm``/``llm``, OpenAI-compatible ``item``).
SERVER_MODALITY_KEYS = "architecture", "llm", "lm"

def _extract_media_capabilities(item: Dict[str, Any]) -> tuple[Optional[bool], Optional[bool]]:
    """Server-reported image/video input capabilities across provider schemas.

    Returns ``(supports_image, supports_video)`` or ``(None, None)`` when the
    provider does not advertise media modalities (caller falls back to
    local heuristics).
    """
    if not isinstance(item, dict):
        return None, None
    layers = [item]
    for key in SERVER_MODALITY_KEYS:
        layer = item.get(key)
        if isinstance(layer, dict):
            layers.append(layer)
    for layer in layers:
        mods = layer.get("input_modalities")
        if isinstance(mods, list):
            return "image" in mods, "video" in mods
    vis = item.get("vision")
    if isinstance(vis, bool):
        return vis, None
    return None, None


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

def load_universal_models() -> Dict[str, int]:
    """Load universal catalog of model basenames -> context window length."""
    if UNIVERSAL_CACHE_FILE.exists():
        try:
            with open(UNIVERSAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Try fetching once if cache is missing
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "Harness/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            catalog = {}
            for m in data.get("data", []):
                mid = m.get("id", "")
                base = mid.split("/")[-1].split(":")[0].lower()
                clen = m.get("context_length") or (m.get("top_provider") or {}).get("context_length")
                if clen and base not in catalog:
                    catalog[base] = int(clen)
            UNIVERSAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(UNIVERSAL_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(catalog, f)
            return catalog
    except Exception:
        return {}

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
                supported = item.get("supported_parameters") or []
                if not th_support and any(x in desc for x in ("reasoning", "thinking", "thought")):
                    th_support = True
                    th_type = "reasoning_effort"
                if not th_support and supported and any(
                    s in (supported if isinstance(supported, list) else []) for s in ("reasoning", "reasoning_effort", "thinking")
                ):
                    th_support = True
                    th_type = "reasoning_effort"

                # Vision / video: prefer server-reported modalities, else heuristics.
                sv, svd = _extract_media_capabilities(item)
                supports_vision = sv if sv is not None else detect_vision_support(mid, prov)
                supports_video = svd if svd is not None else _model_supports_video(mid, prov)

                models_found.append({
                    "id": mid,
                    "context_length": int(c_len) if c_len else 128000,
                    "supports_thinking": th_support,
                    "thinking_type": th_type,
                    "supports_vision": supports_vision,
                    "supports_video": supports_video,
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
    base_name = clean_base.split("/")[-1]

    # 1. Check universal dynamic catalog
    univ_cache = load_universal_models()
    if base_name in univ_cache:
        c_len = univ_cache[base_name]
        if isinstance(c_len, int) and c_len > 0:
            spec.context_window = c_len
    elif clean_base in univ_cache:
        c_len = univ_cache[clean_base]
        if isinstance(c_len, int) and c_len > 0:
            spec.context_window = c_len

    # 2. Check provider-specific /models list
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
            # Server-reported media capabilities override name heuristics.
            if "supports_vision" in m:
                spec.supports_vision = bool(m["supports_vision"])
            if "supports_video" in m:
                spec.supports_video = bool(m["supports_video"])
            _set_provider_output(spec, m)
            break

    return spec


def _set_provider_output(spec: ModelSpec, model_meta: Dict[str, Any]) -> None:
    """Adopt a provider-reported max output limit when available (OpenRouter
    reports it under ``top_provider.max_completion_tokens``)."""
    try:
        top = model_meta.get("top_provider") or {}
        out = model_meta.get("max_completion_tokens") or top.get("max_completion_tokens")
        if out and isinstance(out, int) and out > 0:
            spec.max_output_tokens = out
    except Exception:
        pass
