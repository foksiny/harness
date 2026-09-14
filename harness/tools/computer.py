"""
Computer-use tools for Harness.

These are the model-facing half of the computer stack. They are intentionally
thin, structured-output, and **never raise**: each `execute()` returns a plain
dict with `ok` / structured fields / or `error` + `hints`, and the underlying
`ComputerController` is injected so tests can swap in a hermetic backend (see
`tests/test_computer.py`) without a display.

Two design rules keep them hermetic and provider-agnostic:

* **Sight is universal.** `screen_capture` returns a path + geometry +
  monitor/metadata dict that *every* model can reason about, and
  `screen_analyze` routes the captured image through the shared vision-fallback
  describe core (`harness.vision.describe.describe_media_blocks`) so even a
  non-vision model can "see" the screen as text. Vision-capable main models get
  the actual pixels attached when the provider supports it (see agent seam).

* **Input is batched + gated.** `computer_control` takes an *ordered list* of
  actions (move/click/double/right/drag/scroll/type/keys/combos/shortcuts,
  window focus, clipboard, screenshots) and executes them in sequence through
  the injected `input_exec`, stopping on the first failure and returning
  per-action results. Because it mutates a real machine, it is gated behind a
  permission check (`action_type="computer_input"`) that surfaces an approval
  prompt under DEFAULT/SECURE and is auto-approved/denied per mode policy.
"""
from typing import Any, Dict, List, Optional, Union
import inspect

from harness.tools.base import Tool
from harness.computer.controller import ComputerController


def _pp(result: Any) -> str:
    """Deterministic pretty-print for tool results (keeps them JSON-safe)."""
    import json

    return json.dumps(result, default=str, ensure_ascii=False, sort_keys=True)


class ScreenCaptureTool(Tool):
    name = "screen_capture"
    description = (
        "Capture the current screen (or a region / monitor) to a PNG file and "
        "return its path, resolution, monitor layout, and session/backend info "
        "so the model can see what is on screen. Sight is available to EVERY "
        "model: pair this with `screen_analyze` to have the screen described as "
        "text (works without native vision), or attach the pixels for "
        "vision-capable models."
    )
    parameters = {
        "type": "object",
        "properties": {
            "region": {
                "type": "object",
                "description": "Optional region dict {\"x\",\"y\",\"width\",\"height\"} to capture instead of the full screen.",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                               "width": {"type": "integer"}, "height": {"type": "integer"}},
            },
            "monitor_index": {
                "type": "integer",
                "description": "0-based monitor index from `screen_geometry`; use -1 for the full virtual screen.",
            },
            "out_dir": {
                "type": "string",
                "description": "Optional directory to store the screenshot (defaults to ~/.harness/screenshots).",
            },
        },
    }
    is_read_only = True
    action_type = "screen_read"

    def __init__(self, controller: Optional[ComputerController] = None):
        self.controller = controller or ComputerController()

    def execute(self, **kwargs) -> str:
        res = self.controller.capture(
            region=kwargs.get("region"),
            monitor_index=kwargs.get("monitor_index", 0),
            out_dir=kwargs.get("out_dir"),
        )
        return _pp(res)


class ScreenAnalyzeTool(Tool):
    name = "screen_analyze"
    description = (
        "Capture the current screen and return a detailed text description of "
        "what is shown. Internally routes the image through the configured "
        "vision-fallback (VFB) describe core, so this works for EVERY model — "
        "including text-only models with no native vision. Use `question` to "
        "ask about specific content (e.g. 'what error is shown?', 'read the "
        "labels'). Returns the description text plus the screenshot path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Optional free-form question about the screen content to answer precisely.",
            },
            "region": {
                "type": "object",
                "description": "Optional capture region dict as in `screen_capture`.",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                               "width": {"type": "integer"}, "height": {"type": "integer"}},
            },
            "monitor_index": {"type": "integer", "description": "Optional monitor index (see screen_capture)."},
            "out_dir": {"type": "string", "description": "Optional screenshot directory."},
        },
    }
    is_read_only = True
    action_type = "screen_read"

    def __init__(
        self,
        controller: Optional[ComputerController] = None,
        vision_describe: Optional[Any] = None,
    ):
        self.controller = controller or ComputerController()
        # Injectable vision seam: callable(provider, model, media_blocks,
        # question=None, system_prompt=None) -> (description, error).
        self.vision_describe = vision_describe

    def execute(self, **kwargs) -> str:
        question = kwargs.get("question")
        cap = self.controller.capture(
            region=kwargs.get("region"),
            monitor_index=kwargs.get("monitor_index", 0),
            out_dir=kwargs.get("out_dir"),
        )
        if not cap.get("ok"):
            return _pp({"ok": False, "error": cap.get("error", "capture failed"),
                        "hints": cap.get("hints", [])})

        if self.vision_describe is None:
            return _pp({"ok": False,
                        "error": "no vision describe core configured for screen_analyze",
                        "path": cap.get("path")})

        try:
            description, err = self.vision_describe(
                media_blocks=[{"type": "image", "path": cap.get("path")}],
                question=question or ("Describe the captured screen precisely: "
                                      "what is shown, any text or error messages, "
                                      "layout, and notable details."),
            )
        except Exception as exc:
            return _pp({"ok": False, "error": f"vision describe failed: {exc}",
                        "path": cap.get("path")})

        if err:
            return _pp({"ok": False, "error": err, "path": cap.get("path")})
        return _pp({"ok": True, "description": description,
                    "path": cap.get("path"),
                    "geometry": cap.get("geometry"),
                    "monitors": cap.get("monitors"),
                    "backend": cap.get("backend")})


class ScreenDescribeViaVfbTool(Tool):
    """Universal machinery: the model can ask the vision fallback anything about
    a screenshot via a question on the *latest* capture, without needing a
    native-vision main model. This is the 'ask the VFB for sight' primitive that
    computer use leans on when the main model cannot see pixels.
    """

    name = "screen_describe_via_vfb"
    description = (
        "Ask the configured vision-fallback sub-agent to describe the most "
        "recently captured screenshot (by path, or capture a fresh one first "
        "with `screen_capture`). Use a precise `question` to focus on what you "
        "care about. Returns plain-text description(s) + the image path. Works "
        "for ANY main model — text-only models can leverage this to 'see'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to an existing screenshot to describe. Defaults to the most recent capture if omitted.",
            },
            "question": {
                "type": "string",
                "description": "Precise question about the image (recommended).",
            },
            "region": {
                "type": "object",
                "description": "Optional region to capture+describe instead of an existing path.",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                               "width": {"type": "integer"}, "height": {"type": "integer"}},
            },
        },
    }
    is_read_only = True
    action_type = "screen_read"

    def __init__(
        self,
        controller: Optional[ComputerController] = None,
        vision_describe: Optional[Any] = None,
    ):
        self.controller = controller or ComputerController()
        self.vision_describe = vision_describe

    def execute(self, **kwargs) -> str:
        path = kwargs.get("path")
        if not path and kwargs.get("region"):
            cap = self.controller.capture(region=kwargs.get("region"), out_dir=None)
            if cap.get("ok"):
                path = cap.get("path")
            else:
                return _pp({"ok": False, "error": cap.get("error"), "hints": cap.get("hints", [])})
        if not path:
            latest = self.controller.latest_capture()
            if not latest:
                return _pp({"ok": False,
                            "error": "no screenshot path given and no previous capture found; "
                                     "call `screen_capture` first or pass `path`"})
            path = latest

        if self.vision_describe is None:
            return _pp({"ok": False, "error": "no vision describe core configured", "path": path})

        from harness.vision.describe import describe_media_blocks  # inner import: no cycle

        try:
            description, err = describe_media_blocks(
                provider=req_provider,
                model=vfb_model,
                media_blocks=[{"type": "image", "path": path}],
                question=kwargs.get("question"),
            )
        except Exception as exc:
            return _pp({"ok": False, "error": f"vfb describe failed: {exc}", "path": path})
        if err:
            return _pp({"ok": False, "error": err, "path": path})
        return _pp({"ok": True, "description": description, "path": path})


class ComputerControlTool(Tool):
    name = "computer_control"
    description = (
        "Execute an ordered batch of computer-input actions on the real machine "
        "(hermetic driver, injectable for tests). Each action is one of: "
        "move {x,y}, click {button(left|middle|right),double}, "
        "scroll {x,y}, type {text}, keys {keys:[...], combo:bool}, "
        "combo {keys:[...]}, hotkey {keys:[...]}, window {op,window_id} "
        "(list|focus|active), clipboard {op(read|write),text}, "
        "sleep {ms}. All actions run in order; the batch stops on the first "
        "failure and returns per-action results so the model can see exactly "
        "which step was rejected. Requires approval under DEFAULT/SECURE modes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Ordered list of input action dicts (see tool description).",
            },
        },
        "required": ["actions"],
    }
    is_read_only = False
    action_type = "computer_input"

    def __init__(
        self,
        controller: Optional[ComputerController] = None,
        permission_manager: Optional[Any] = None,
    ):
        self.controller = controller or ComputerController()
        self.permission_manager = permission_manager

    def execute(self, actions: List[Dict[str, Any]], **kwargs) -> str:
        if not actions:
            return _pp({"ok": True, "actions": [], "summary": "empty batch"})

        # Gate behind permission seam (mirrors command permission flow).
        if self.permission_manager:
            summary = "; ".join(
                f"{a.get('action', '?')}"
                + (f" {a.get('x')},{a.get('y')}" if a.get('action') in ('move',) else
                   f" ({a.get('button', 'left')})" if a.get('action') == 'click' else
                   f" {a.get('keys')}" if a.get('action') in ('keys', 'combo', 'hotkey') else
                   f" {a.get('text')}" if a.get('action') in ('type',) else "")
                for a in actions[:4]
            ) + ("..." if len(actions) > 4 else "")
            ok = self.permission_manager.check_permission(
                "computer_input", {"summary": f"computer_control: {summary}",
                                   "action_count": len(actions),
                                   "tool": "computer_control"})
            if not ok:
                return _pp({"ok": False,
                            "error": "computer input batch was not approved",
                            "actions": [
                                {"action": a.get("action"), "ok": False,
                                 "error": "not approved (denied by permission policy)"}
                                for a in actions]})

        return _pp(self.controller.execute_batch(actions))


class ClipboardTool(Tool):
    name = "computer_clipboard"
    description = (
        "Read or write the system clipboard through the injected driver "
        "(xclip / xsel / wl-clipboard). `op=write` modifies the clipboard, so it "
        "requires approval under DEFAULT/SECURE; `op=read` is read-only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["read", "write"],
                   "description": "read = return clipboard text; write = set clipboard to `text`."},
            "text": {"type": "string", "description": "Text to write when op=write."},
        },
        "required": ["op"],
    }
    is_read_only = False
    action_type = "computer_input"

    def __init__(
        self,
        controller: Optional[ComputerController] = None,
        permission_manager: Optional[Any] = None,
    ):
        self.controller = controller or ComputerController()
        self.permission_manager = permission_manager

    def execute(self, op: str, text: str = "", **kwargs) -> str:
        if op == "read":
            return _pp(self.controller.clipboard_read())
        if self.permission_manager:
            ok = self.permission_manager.check_permission(
                "computer_input",
                {"summary": "computer_clipboard: write to clipboard", "op": "write"})
            if not ok:
                return _pp({"ok": False, "error": "clipboard write was not approved"})
        return _pp(self.controller.clipboard_write(text))


__tools__ = [
    ScreenCaptureTool,
    ScreenAnalyzeTool,
    ScreenDescribeViaVfbTool,
    ComputerControlTool,
    ClipboardTool,
]
def register_computer_tools(
    registry,
    controller: Optional[Any] = None,
    vision_describe: Optional[Any] = None,
    permission_manager: Optional[Any] = None,
) -> Any:
    """Register the hermetic computer tools onto a ToolRegistry.

    Hermetic invariant: never raises, never touches a real display. Each tool
    is built with *only the kwargs its own constructor declares*, so hermetic
    seams (controller/vision/perm) are passed per-class and registration is
    safe even when a seam is absent (hermetic default controller is used).
    """
    for cls in __tools__:
        init = cls.__init__
        params = set(inspect.signature(init).parameters) if init is not object.__init__ else set()
        kwargs = {}
        for name, value in (
            ("controller", controller),
            ("vision_describe", vision_describe),
            ("permission_manager", permission_manager),
        ):
            if name in params and value is not None:
                kwargs[name] = value
        registry.register(cls(**kwargs))
    return registry


