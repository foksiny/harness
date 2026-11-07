"""Company mode for Harness — a forever companion loop."""

from harness.company.controller import (
    CompanyController,
    DEFAULT_ROLE,
    build_tick_prompt,
    default_stop_path,
    parse_evdev_events,
    speak,
)

__all__ = [
    "CompanyController",
    "DEFAULT_ROLE",
    "build_tick_prompt",
    "default_stop_path",
    "parse_evdev_events",
    "speak",
]
