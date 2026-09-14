"""
Vision helpers for Harness — shared, provider-agnostic describe machinery.
Every consumer of "ask a model to see without vision support" (the agent's
event-yielding `_run_vision_fallback` and the computer-use tools) routes
through the same core in `harness.vision.describe`, so description quality and
failure semantics are identical everywhere.
"""
from harness.vision.describe import (
    describe_media_blocks,
    DEFAULT_VFB_SYSTEM_PROMPT,
)

__all__ = ["describe_media_blocks", "DEFAULT_VFB_SYSTEM_PROMPT"]
