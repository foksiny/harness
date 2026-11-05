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

# In-memory memoization so hot paths (every agent turn calls get_model_spec,
# which reads both files) pay for disk/network at most once per process.
# File-backed caches are re-read only when the file mtime changes; the
# universal catalog (which may require a network fetch on first miss) is
# fetched at most once per process unless force-refreshed.
import threading as _threading

_cache_lock = _threading.Lock()
_models_file_cache: Dict[str, Any] = {"mtime": None, "data": {}}
_universal_mem: Dict[str, Any] = {"state": "cold", "data": {}, "mtime": None}

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


def _extract_thinking_capabilities(item: Dict[str, Any], provider: str = "") -> tuple[bool, Optional[str]]:
    """Server-reported thinking capabilities — the provider-driven half.

    Checks (in order): the structured ``reasoning`` block OpenRouter exposes
    (``{mandatory, default_enabled, supported_efforts}``), ``supported_parameters``
    containing reasoning/thinking keys, the description, then static heuristics
    via :func:`detect_thinking_support`. Returns ``(supports, dialect)`` where
    the dialect is the provider's native request dialect for the model.
    """
    from harness.providers.detector import PROVIDER_DIALECTS

    def _dialect_for() -> Optional[str]:
        prov = (provider or "").lower().strip()
        return PROVIDER_DIALECTS.get(prov, "reasoning_effort")

    if isinstance(item, dict):
        # Structured reasoning block (OpenRouter / OpenAI-compatible gateways).
        r = item.get("reasoning")
        if isinstance(r, dict):
            if r.get("mandatory") is True or r.get("default_enabled") is True:
                return True, _dialect_for()
            if r.get("supported_efforts"):
                return True, _dialect_for()
        elif r is True:
            return True, _dialect_for()
        # supported_parameters advertises the reasoning channel.
        supported = item.get("supported_parameters")
        if isinstance(supported, list) and any(
            str(s) in ("reasoning", "reasoning_effort", "thinking", "include_reasoning")
            for s in supported
        ):
            return True, _dialect_for()
        desc = str(item.get("description", "") or "").lower()
        if any(x in desc for x in ("reasoning", "thinking", "thought", "reasoner")):
            # Description alone is weak — require a parameter or block to confirm?
            # For gateways that omit the block but ship reasoning models, treat
            # the description as sufficient when the id also looks reasoning-y.
            mid = str(item.get("id") or item.get("name") or "").lower()
            if any(x in mid for x in ("reason", "thinking", "r1", "o1", "o3", "kimi", "glm", "minimax", "nemotron", "muse", "qwq", "magistral")):
                return True, _dialect_for()
    # Fall back to static detection (catalog/markers/provider fallback).
    return detect_thinking_support(str(item.get("id") or item.get("name") or "") if isinstance(item, dict) else "", provider)


def load_cached_models() -> Dict[str, Any]:
    # Serve from memory unless the file changed on disk (mtime guard keeps
    # this to one stat() per call instead of a full read + json parse).
    try:
        mtime = CACHE_FILE.stat().st_mtime if CACHE_FILE.exists() else None
    except Exception:
        mtime = None
    with _cache_lock:
        if mtime == _models_file_cache["mtime"]:
            return _models_file_cache["data"]
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                with _cache_lock:
                    _models_file_cache.update({"mtime": mtime, "data": data})
                return data
        except Exception:
            pass
    with _cache_lock:
        _models_file_cache.update({"mtime": mtime, "data": {}})
    return {}

def save_cached_models(cache_data: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception:
        pass

def load_universal_models(force: bool = False) -> Dict[str, int]:
    """Load universal catalog of model basenames -> context window length.

    Memoized per process: the first call may hit disk (or, on a cold cache,
    the network once with a 3s timeout); later calls return memory unless the
    file changed on disk. Pass ``force=True`` to re-fetch explicitly.
    """
    with _cache_lock:
        mem = _universal_mem
        if mem["state"] == "ready" and not force:
            try:
                mtime = UNIVERSAL_CACHE_FILE.stat().st_mtime if UNIVERSAL_CACHE_FILE.exists() else None
            except Exception:
                mtime = None
            if mtime == mem["mtime"]:
                return mem["data"]
    if UNIVERSAL_CACHE_FILE.exists() and not force:
        try:
            with open(UNIVERSAL_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                with _cache_lock:
                    _universal_mem.update({"state": "ready", "data": data,
                                           "mtime": UNIVERSAL_CACHE_FILE.stat().st_mtime})
                return data
        except Exception:
            pass

    # Try fetching once if cache is missing (at most once per process —
    # previously EVERY get_model_spec call re-hit the network on a cold
    # cache, stalling agent turns for seconds on slow networks).
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
            with _cache_lock:
                try:
                    mtime = UNIVERSAL_CACHE_FILE.stat().st_mtime
                except Exception:
                    mtime = None
                _universal_mem.update({"state": "ready", "data": catalog, "mtime": mtime})
            return catalog
    except Exception:
        with _cache_lock:
            # Negative-cache the miss for this process so a dead network
            # doesn't cost 3s on every subsequent turn.
            if _universal_mem["state"] != "ready":
                _universal_mem.update({"state": "ready", "data": {}, "mtime": None})
            return _universal_mem["data"]

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

                # Thinking / reasoning: provider-driven (structured block +
                # supported_parameters), falling back to static heuristics only.
                th_support, th_type = _extract_thinking_capabilities(item, prov)

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

    # Check cached or remote provider metadata. Live provider data ALWAYS wins
    # over the bundled catalog for unknown models (stealth/*, brand-new ids):
    # the registry only pins what the provider has already advertised.
    cache = load_cached_models()
    prov = provider_name.lower().strip()
    prov_cache = cache.get(prov, {})
    models_list = prov_cache.get("models", []) or []
    cache_ts = prov_cache.get("timestamp", 0) if isinstance(prov_cache, dict) else 0
    cache_stale = (not cache_ts) or (time.time() - cache_ts > CACHE_TTL)

    clean_lower = clean_name.lower()
    clean_base = clean_lower.split(":")[0]

    def _model_in_list(lst: List[Dict[str, Any]]) -> bool:
        for m in (lst or []):
            m_id = str(m.get("id", "") or "").strip()
            m_lower = m_id.lower()
            m_base = m_lower.split(":")[0]
            if (
                m_lower == clean_lower
                or m_base == clean_base
                or clean_lower.endswith(f"/{m_lower}")
                or clean_base.endswith(f"/{m_base}")
                or m_lower.endswith(f"/{clean_lower}")
                or m_base.endswith(f"/{clean_base}")
            ):
                return True
        return False

    # Live-fetch whenever the model isn't found in cache, the cache is stale,
    # or the model is unknown to the bundled catalog (so new releases like
    # stealth/crouton-alpha resolve without manual registry edits).
    static_unknown = spec.source != "catalog"
    need_fetch = base_url is not None and (
        cache_stale or static_unknown or not _model_in_list(models_list)
    )
    if need_fetch:
        try:
            fetched = fetch_remote_models(prov, api_key, base_url, force=True)
            if fetched:
                models_list = fetched
        except Exception:
            pass

    clean_lower = clean_name.lower()
    clean_base = clean_lower.split(":")[0]
    base_name = clean_base.split("/")[-1]

    # 1. Check universal dynamic catalog (context only; thinking/vision come
    # from the provider record below).
    univ_cache = load_universal_models()
    if base_name in univ_cache:
        c_len = univ_cache[base_name]
        if isinstance(c_len, int) and c_len > 0:
            spec.context_window = c_len
            if static_unknown:
                spec.source = "universal"
    elif clean_base in univ_cache:
        c_len = univ_cache[clean_base]
        if isinstance(c_len, int) and c_len > 0:
            spec.context_window = c_len
            if static_unknown:
                spec.source = "universal"

    # 2. Check provider-specific /models list (authoritative when found).
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
                spec.source = "server"
            if m.get("supports_thinking"):
                spec.supports_thinking = True
                # Live dialect wins for unknown models; never clobber a
                # catalog pin (e.g. chat_template_kwargs) for known models.
                if static_unknown or not spec.thinking_type:
                    spec.thinking_type = m.get("thinking_type") or spec.thinking_type or "reasoning_effort"
            # Server-reported media capabilities override name heuristics.
            if "supports_vision" in m:
                spec.supports_vision = bool(m["supports_vision"])
            if "supports_video" in m:
                spec.supports_video = bool(m["supports_video"])
            _set_provider_output(spec, m)
            # Preserve reasoning options advertised by the server.
            raw = m.get("raw") or {}
            if isinstance(raw, dict) and raw.get("reasoning", {}).get("supported_efforts"):
                spec.reasoning_options = list(raw["reasoning"]["supported_efforts"])
            break

    # Post-correction: stale cache may have marked NVIDIA models with the
    # Together-style reasoning_toggle dialect, which NIM rejects. Convert to
    # NIM's native reasoning_effort instead of disabling thinking entirely.
    if prov in ("nvidia", "nim") and spec.supports_thinking and spec.thinking_type == "reasoning_toggle":
        spec.thinking_type = "reasoning_effort"

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
