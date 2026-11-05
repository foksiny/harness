"""Computer-use tools (D1): expose ComputerController to the model.

Hermetic: controller is injectable. Screenshot emits [harness:image:path] so
the existing vision/VFB routing in the agent loop works unchanged.
"""
import json
from typing import Optional, Any
from harness.tools.base import Tool
from harness.core.permissions import PermissionManager


class ComputerManager:
    """One shared ComputerController per registry (mirrors BrowserManager)."""

    def __init__(self, controller=None):
        self._controller = controller

    @property
    def controller(self):
        if self._controller is None:
            from harness.computer.controller import ComputerController
            self._controller = ComputerController()
        return self._controller

    def controller_factory(self):
        return self.controller


class _ComputerBase(Tool):
    action_type = "computer_input"
    is_read_only = False

    def __init__(self, controller_factory=None, permission_manager: Optional[PermissionManager] = None):
        self._factory = controller_factory
        self.permission_manager = permission_manager

    def _controller(self):
        return self._factory() if self._factory else None

    def _allowed(self, summary: str, read_only: bool = False) -> Optional[str]:
        if self.permission_manager is None:
            return None
        at = "computer_read" if read_only else "computer_input"
        ok = self.permission_manager.check_permission(at, {"tool": self.name, "summary": summary})
        if not ok:
            return f"Error: {self.name} rejected by security policy or user: {summary}"
        return None

    def _input(self, action: dict) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed(action.get("action", self.name))
        if denied:
            return denied
        try:
            # D3: normalize 0-1000 coords if latest capture geometry known.
            return json.dumps(ctrl.execute_input(action), default=str)
        except Exception as e:
            return f"Error: {e}"


class ComputerEnvTool(_ComputerBase):
    name = "computer_env"
    description = (
        "Probe desktop environment (DE, session, screenshot/input backends, vision support). "
        "Call this FIRST on a new machine/session — it tells you which tools will actually work "
        "(COSMIC vs GNOME, X11 vs Wayland, ydotool vs xdotool, wmctrl X-only limitation)."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed("Computer env probe", read_only=True)
        if denied:
            return denied
        try:
            probe = ctrl.probe() if hasattr(ctrl, "probe") else {}
            from harness.computer.wayland_utils import detect_de, detect_session_type
            probe["de"] = detect_de()
            probe["session"] = detect_session_type()
            probe["screenshot"] = "cosmic-screenshot > grim > scrot/import (auto)"
            probe["input"] = "ydotool (Wayland) | xdotool/XTEST (X11/XWayland)"
            probe["windows"] = "wmctrl = X11/XWayland only; empty on COSMIC/Wayland-native apps"
            probe["perception"] = (
                "Vision models see screenshots directly; non-vision models get a path only — "
                "use computer_ocr (tesseract) or configure vfb_provider/vfb_model. "
                "Never use view_file on a .png (binary garbage)."
            )
            return json.dumps(probe, default=str, indent=2)
        except Exception as e:
            return f"Error: {e}"


class ComputerOcrTool(_ComputerBase):
    name = "computer_ocr"
    description = (
        "OCR the latest screenshot (or a given PNG path) with tesseract. "
        "Use this when you are a NON-vision model: computer_screenshot only gives you a path, "
        "not pixels. OCR returns visible text so you can identify windows/apps."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {
        "image_path": {"type": "string", "description": "PNG path (defaults to latest screenshot)."},
        "max_chars": {"type": "integer", "description": "Max chars to return."}}, "required": []}

    def execute(self, image_path: str = "", max_chars: int = 8000, **kwargs) -> str:
        denied = self._allowed("Computer OCR", read_only=True)
        if denied:
            return denied
        ctrl = self._controller()
        path = (image_path or "").strip()
        if not path and ctrl is not None and hasattr(ctrl, "latest_capture"):
            path = ctrl.latest_capture() or ""
        if not path:
            return "Error: No screenshot yet — call computer_screenshot first, or pass image_path."
        import subprocess
        try:
            res = subprocess.run(["tesseract", path, "stdout"], capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                return f"Error: tesseract failed: {res.stderr[:500]} (install tesseract-ocr)"
            text = (res.stdout or "").strip()
            cap = max(500, min(int(max_chars), 64000))
            if len(text) > cap:
                text = text[: cap // 2] + "\n...[middle elided]\n" + text[-cap // 2:]
            return f"OCR of {path}:\n\n{text or '(no text detected)'}"
        except FileNotFoundError:
            return "Error: tesseract not installed (apt install tesseract-ocr)."
        except Exception as e:
            return f"Error: {e}"


class ComputerScreenshotTool(_ComputerBase):
    name = "computer_screenshot"
    description = (
        "Capture the desktop screen. VISION models see the image directly; NON-vision models get "
        "ONLY a file path (no pixels) — then use computer_ocr on that path or ask the admin to "
        "configure vfb_provider. NEVER use view_file on the .png path (binary, not text). "
        "Coords for clicking are 0-1000 normalized. backend is DE-aware (cosmic-screenshot on COSMIC)."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {"monitor_index": {"type": "integer"}}, "required": []}

    def execute(self, monitor_index: int = 0, **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed("Computer screenshot", read_only=True)
        if denied:
            return denied
        try:
            res = ctrl.capture(monitor_index=int(monitor_index or 0))
            if not res.get("ok"):
                return f"Error: {res.get('error', 'capture failed')} hints={res.get('hints', [])}"
            path = res.get("image_path") or res.get("path") or ""
            meta = {k: res.get(k) for k in ("width", "height", "monitors", "backend")}
            return (
                f"Screenshot saved to {path} [harness:image:{path}] " + json.dumps(meta, default=str) +
                "\nIf you did NOT receive image content, you are a non-vision model: "
                "call computer_ocr on this path (do NOT use view_file on the PNG)."
            )
        except Exception as e:
            return f"Error: {e}"


class ComputerClickTool(_ComputerBase):
    name = "computer_click"
    description = "Click at 0-1000 normalized coords (nx/ny) or absolute x/y. Coordinates come from computer_screenshot."
    parameters = {"type": "object", "properties": {
        "nx": {"type": "number"}, "ny": {"type": "number"},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "button": {"type": "string", "default": "left"},
        "double": {"type": "boolean"}}, "required": []}

    def execute(self, nx=None, ny=None, x=None, y=None, button="left", double=False, **kwargs) -> str:
        action: dict = {"action": "double" if double else "click", "button": button}
        if nx is not None and ny is not None:
            action["nx"] = nx
            action["ny"] = ny
            ctrl = self._controller()
            # Try to scale via last capture geometry if available.
            if ctrl is not None and getattr(ctrl, "_latest_geometry", None):
                try:
                    from harness.computer.grounding import normalize_coords
                    geom = ctrl._latest_geometry  # type: ignore[attr-defined]
                    px = normalize_coords(nx, ny, geom.get("width", 0), geom.get("height", 0))
                    action["x"], action["y"] = px["x"], px["y"]
                except Exception:
                    pass
        else:
            action["x"], action["y"] = x or 0, y or 0
        return self._input(action)


class ComputerMoveTool(_ComputerBase):
    name = "computer_move"
    description = "Move the mouse to 0-1000 normalized coords (nx/ny)."
    parameters = {"type": "object", "properties": {"nx": {"type": "number"}, "ny": {"type": "number"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": []}

    def execute(self, nx=None, ny=None, x=None, y=None, **kwargs) -> str:
        action = {"action": "move", "x": x or 0, "y": y or 0, "nx": nx, "ny": ny}
        return self._input(action)


class ComputerDragTool(_ComputerBase):
    name = "computer_drag"
    description = "Drag from (x1,y1) to (x2,y2) absolute coords."
    parameters = {"type": "object", "properties": {"x1": {"type": "integer"}, "y1": {"type": "integer"}, "x2": {"type": "integer"}, "y2": {"type": "integer"}}, "required": ["x1", "y1", "x2", "y2"]}

    def execute(self, x1, y1, x2, y2, **kwargs) -> str:
        return self._input({"action": "drag", "x1": x1, "y1": y1, "x2": x2, "y2": y2})


class ComputerScrollTool(_ComputerBase):
    name = "computer_scroll"
    description = "Scroll at screen. direction up/down, amount steps."
    parameters = {"type": "object", "properties": {"direction": {"type": "string", "default": "down"}, "amount": {"type": "integer", "default": 3}, "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": []}

    def execute(self, direction="down", amount=3, x=0, y=0, **kwargs) -> str:
        return self._input({"action": "scroll", "direction": direction, "amount": amount, "x": x, "y": y})


class ComputerTypeTool(_ComputerBase):
    name = "computer_type"
    description = "Type text at the focused input."
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def execute(self, text: str, **kwargs) -> str:
        return self._input({"action": "type", "text": text})


class ComputerKeyTool(_ComputerBase):
    name = "computer_key"
    description = "Press a key (e.g. Enter, Tab, Escape)."
    parameters = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}

    def execute(self, key: str, **kwargs) -> str:
        return self._input({"action": "key", "key": key})


class ComputerComboTool(_ComputerBase):
    name = "computer_combo"
    description = "Press a key combo (e.g. Ctrl+C)."
    parameters = {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"]}

    def execute(self, keys, **kwargs) -> str:
        return self._input({"action": "combo", "keys": list(keys or [])})


class ComputerWindowsTool(_ComputerBase):
    name = "computer_windows"
    description = "List/focus/active windows (op=list|focus|active)."
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {"op": {"type": "string", "default": "list"}, "window_id": {"type": "string"}}, "required": []}

    def execute(self, op="list", window_id="", **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed("Computer windows", read_only=True)
        if denied:
            return denied
        try:
            if op == "list":
                return json.dumps(ctrl.list_windows(), default=str)
            if op == "focus":
                return json.dumps(ctrl.focus_window(str(window_id)), default=str)
            if op == "active":
                return json.dumps(ctrl.active_window(), default=str)
            return f"Error: unknown op '{op}'"
        except Exception as e:
            return f"Error: {e}"


class ComputerClipboardTool(_ComputerBase):
    name = "computer_clipboard"
    description = "Read/write the system clipboard (op=read|write)."
    parameters = {"type": "object", "properties": {"op": {"type": "string", "default": "read"}, "text": {"type": "string"}}, "required": []}

    def execute(self, op="read", text="", **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        if op == "write":
            denied = self._allowed("Clipboard write")
            if denied:
                return denied
            return json.dumps(ctrl.clipboard_write(str(text or "")), default=str)
        denied = self._allowed("Clipboard read", read_only=True)
        if denied:
            return denied
        return json.dumps(ctrl.clipboard_read(), default=str)


class ComputerActTool(_ComputerBase):
    name = "computer_act"
    description = "Execute a sequence of computer actions server-side (D7 macro), then one screenshot. Steps: [{action, x, y, text, key, keys...}]."
    parameters = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object"}}}, "required": ["steps"]}

    def execute(self, steps, **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed("Computer macro")
        if denied:
            return denied
        steps = list(steps or [])
        if not steps:
            return "Error: steps must be a non-empty list."
        try:
            results = ctrl.execute_batch(steps)
            shot = ctrl.capture()
            shot_note = ""
            if shot.get("ok"):
                p = shot.get("image_path") or shot.get("path") or ""
                shot_note = f" [harness:image:{p}]"
            return json.dumps({"results": results, "screenshot_ok": bool(shot.get("ok"))}, default=str) + shot_note
        except Exception as e:
            return f"Error: {e}"


def register_computer_tools(registry, controller_factory=None, permission_manager=None):
    registry.register(ComputerEnvTool(controller_factory, permission_manager))
    registry.register(ComputerOcrTool(controller_factory, permission_manager))
    registry.register(ComputerScreenshotTool(controller_factory, permission_manager))
    registry.register(ComputerClickTool(controller_factory, permission_manager))
    registry.register(ComputerMoveTool(controller_factory, permission_manager))
    registry.register(ComputerDragTool(controller_factory, permission_manager))
    registry.register(ComputerScrollTool(controller_factory, permission_manager))
    registry.register(ComputerTypeTool(controller_factory, permission_manager))
    registry.register(ComputerKeyTool(controller_factory, permission_manager))
    registry.register(ComputerComboTool(controller_factory, permission_manager))
    registry.register(ComputerWindowsTool(controller_factory, permission_manager))
    registry.register(ComputerClipboardTool(controller_factory, permission_manager))
    registry.register(ComputerActTool(controller_factory, permission_manager))
