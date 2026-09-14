"""
Shared "vision fallback describe" core (VFB).

This is the single text-out path the harness uses to get a model to *see*
without natively supporting vision: given media blocks (images/video) plus an
optional question/system hint, it streams chunks through the configured
fallback provider and model, joins the deltas, and returns a plain-text
description.

It is intentionally generator-free and dependency-free: both the agent's
`_run_vision_fallback` and the computer-use tools route through it, so the
description quality and failure semantics are identical everywhere. The agent
keeps the *event-yielding* wrapper for UX while this core produces the actual
string.
"""
from typing import Any, Dict, List, Optional, Tuple
from harness.providers.base import BaseProvider, LLMChunk

DEFAULT_VFB_SYSTEM_PROMPT = (
    "You are a precise vision-description sub-agent. Describe each provided "
    "file in exhaustive objective detail so that a text-only model which "
    "cannot see it can fully understand it: subjects, actions, spatial layout, "
    "colors, quantities, any readable text or labels, diagrams, and notable "
    "defects. Be literal and complete rather than brief."
)


def describe_media_blocks(
    provider: BaseProvider,
    model: str,
    media_blocks: List[Dict[str, Any]],
    question: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Run the vision-fallback description for the given media blocks.

    Args:
        provider: The vision-fallback (VFB) provider to stream through.
        model: The VFB model id to use.
        media_blocks: List of media blocks (`{"type": "image"|"video", "path"|"data_uri"}`).
        question: Optional free-form prompt the calling model wants answered about the media.
        system_prompt: Optional override; defaults to the shared VFB system prompt.

    Returns:
        (description, error): exactly one is non-empty. `description` is the
        joined text when the fallback produced usable output; `error` is set
        when it could not.
    """
    labels = "/".join(sorted({b.get("type", "file") for b in media_blocks}))

    try:
        spec = provider.get_model_spec(model)
    except Exception:
        spec = None

    if spec is None:
        return ("", f"could not resolve capabilities for vision fallback model '{model}'")

    if labels == "video":
        capable = bool(spec.supports_video)
    elif labels == "image":
        capable = bool(spec.supports_vision)
    else:
        capable = bool(spec.supports_vision and spec.supports_video)

    if not capable:
        return ("", f"vision fallback model '{model}' is not detected as {labels}-capable")

    content: List[Dict[str, Any]] = [
        {"type": "text", "text": (
            question or "Describe each of the following files precisely:"
        )},
        *media_blocks,
    ]

    parts: List[str] = []
    failed = False
    try:
        for chunk in provider.stream_chat(
            messages=[{"role": "user", "content": content}],
            model=model,
            thinking_effort="off",
            tools=[],
            system_prompt=system_prompt or DEFAULT_VFB_SYSTEM_PROMPT,
        ):
            if chunk.finish_reason == "error":
                failed = True
                continue
            if chunk.delta_text:
                parts.append(chunk.delta_text)
    except Exception as exc:
        return ("", f"vision fallback '{model}' failed while describing: {exc}")

    description = "".join(parts).strip()
    if failed or not description:
        return ("", f"vision fallback model '{model}' returned no usable description")
    return (description, "")
