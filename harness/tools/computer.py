"""Computer-use tools: expose the cross-platform ComputerController to the model.

Hermetic: the controller is injectable. Screenshots emit
``[harness:image:path]`` so the existing vision/VFB routing in the agent loop
works unchanged. Every screenshot also carries OCR-derived Set-of-Marks when
tesseract is available, so the model clicks ``mark=N`` instead of guessing
pixels.
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
            return json.dumps(ctrl.execute_input(action), default=str)
        except Exception as e:
            return f"Error: {e}"


class ComputerEnvTool(_ComputerBase):
    name = "computer_env"
    description = (
        "Probe the desktop environment once per session: platform (Linux/macOS/Windows), "
        "DE (GNOME/COSMIC/KDE/Sway/...), session (Wayland/X11/XWayland), the preferred "
        "capture/input backends, and available helpers. Call this FIRST on a new machine. "
        "On Wayland, computer_windows returning [] is normal — use screenshots + marks."
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
            session = str(probe.get("session") or "")
            cli = probe.get("cli") or {}
            probe["perception"] = (
                "Screenshots include numbered OCR marks when tesseract is installed. "
                "Click with computer_click(mark=N); normalized nx/ny is the fallback. "
                "On Wayland, empty window lists are normal — never loop on computer_windows."
            )
            probe["workflow"] = (
                "computer_screenshot -> computer_click(mark=N) -> "
                "computer_type(text, expect='<text visible in target>', submit=true) -> "
                "look at the new screenshot. If the screen did not change, change strategy."
            )
            probe["typing_guard"] = (
                "computer_type captures before and after typing: it refuses to type over "
                "an overview/app launcher, checks that the text appeared near the input "
                "you clicked, and only then presses Return. delivered=false means NOT sent."
            )
            if session in ("wayland", "xwayland"):
                probe["wayland_note"] = (
                    "Native Wayland windows are invisible to wmctrl/xdotool. Act on pixels. "
                    "ydotool reaches native windows; pyautogui only reaches XWayland."
                )
            if not cli.get("tesseract"):
                probe["ocr_note"] = (
                    "tesseract is not installed — no OCR marks. Use the screenshot image "
                    "(vision model) or install tesseract-ocr for marks."
                )
            return json.dumps(probe, default=str, indent=2)
        except Exception as e:
            return f"Error: {e}"


class ComputerOcrTool(_ComputerBase):
    name = "computer_ocr"
    description = (
        "OCR the latest screenshot (or a given image path) and return visible text plus "
        "numbered marks with coordinates. Use this for non-vision models, or when you "
        "need exact labels/coordinates. Then click with computer_click(mark=N)."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {
        "image_path": {"type": "string", "description": "Image path (defaults to the latest screenshot)."},
        "max_chars": {"type": "integer", "description": "Max OCR text characters to return."}}, "required": []}

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
        except FileNotFoundError:
            return "Error: tesseract not installed (apt install tesseract-ocr)."
        except Exception as e:
            return f"Error: {e}"
        if res.returncode != 0:
            return f"Error: tesseract failed: {res.stderr[:500]} (install tesseract-ocr)"
        text = (res.stdout or "").strip()
        cap = max(500, min(int(max_chars), 64000))
        if len(text) > cap:
            text = text[: cap // 2] + "\n...[middle elided]\n" + text[-cap // 2:]

        marks_text = ""
        try:
            from harness.computer.grounding import ocr_marks, render_marks_text
            if ctrl is not None and hasattr(ctrl, "compute_marks") and path == getattr(ctrl, "latest_capture", lambda: None)():
                marks = ctrl.compute_marks(path)
            else:
                marks = ocr_marks(path)
            marks_text = render_marks_text(marks) if marks else ""
        except Exception:
            marks_text = ""

        parts = [f"OCR of {path}:", "", text or "(no text detected)"]
        if marks_text:
            parts += ["", marks_text, "",
                      "Click a mark with computer_click(mark=N). Marks belong to this screenshot."]
        else:
            parts += ["", "(No OCR marks. Click by 0-1000 nx/ny from the screenshot, or install tesseract.)"]
        return "\n".join(parts)


class ComputerScreenshotTool(_ComputerBase):
    name = "computer_screenshot"
    description = (
        "Capture the screen. Returns the image for vision models AND numbered OCR marks "
        "(text -> mark number + 0-1000 coords) when tesseract is installed. START HERE, "
        "then act with computer_click(mark=N) or one computer_act batch. Do not guess pixels. "
        "Do not call this repeatedly without acting in between. Pass marks=false to skip "
        "OCR (~3s saved) when you only need the raw image."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {
        "monitor_index": {"type": "integer"},
        "marks": {"type": "boolean", "description": "Include OCR marks (default true)."}},
        "required": []}

    def execute(self, monitor_index: int = 0, marks: bool = True, **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed("Computer screenshot", read_only=True)
        if denied:
            return denied
        try:
            try:
                res = ctrl.capture(monitor_index=int(monitor_index or 0), with_marks=bool(marks))
            except TypeError:
                res = ctrl.capture(monitor_index=int(monitor_index or 0))
            if not res.get("ok"):
                return f"Error: {res.get('error', 'capture failed')} hints={res.get('hints', [])}"
            path = res.get("image_path") or res.get("path") or ""
            meta = {k: res.get(k) for k in ("width", "height", "monitors", "backend", "warning") if res.get(k)}
            lines = [
                f"Screenshot saved to {path} [harness:image:{path}]",
                json.dumps(meta, default=str),
            ]
            if res.get("warning"):
                lines.append(f"WARNING: {res['warning']}")
            if res.get("overview"):
                lines.append(
                    "DO NOT TYPE OR PRESS RETURN HERE. Press Escape or click the "
                    "real app window, take a new screenshot, then click the input."
                )
            marks_text = res.get("marks_text") or ""
            if marks_text:
                lines.append(marks_text)
                lines.append(
                    "NEXT: computer_click(mark=N) to focus the input, then "
                    "computer_type(text, expect='<text visible in target>', submit=true) — "
                    "or one computer_act batch. The type tool re-checks delivery before "
                    "Return. Marks belong to THIS screenshot; take a new one after the "
                    "screen changes."
                )
            else:
                lines.append(
                    "No OCR marks available. Click by 0-1000 normalized nx/ny from the image "
                    "(never raw pixel guesses), or install tesseract-ocr for marks."
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"


class ComputerClickTool(_ComputerBase):
    name = "computer_click"
    description = (
        "Click a target. PREFERRED: mark=N from the latest computer_screenshot. "
        "Fallbacks: nx/ny (0-1000 normalized) or absolute x/y. Include coordinates — "
        "a click without a target hits whatever is under the current cursor."
    )
    parameters = {"type": "object", "properties": {
        "mark": {"type": "integer", "description": "Mark number from the latest screenshot (preferred)."},
        "nx": {"type": "number"}, "ny": {"type": "number"},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "button": {"type": "string", "default": "left"},
        "double": {"type": "boolean"}}, "required": []}

    def execute(self, mark=None, nx=None, ny=None, x=None, y=None, button="left", double=False, **kwargs) -> str:
        action: dict = {"action": "double" if double else "click", "button": button}
        if mark is not None:
            action["mark"] = mark
        elif nx is not None and ny is not None:
            action["nx"], action["ny"] = nx, ny
        elif x is not None and y is not None:
            action["x"], action["y"] = x, y
        else:
            return ("Error: click needs a target. Pass mark=N from the latest "
                    "computer_screenshot (preferred), or nx/ny, or x/y.")
        return self._input(action)


class ComputerMoveTool(_ComputerBase):
    name = "computer_move"
    description = "Move the pointer to a mark from the latest screenshot (preferred) or 0-1000 nx/ny."
    parameters = {"type": "object", "properties": {
        "mark": {"type": "integer"},
        "nx": {"type": "number"}, "ny": {"type": "number"},
        "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": []}

    def execute(self, mark=None, nx=None, ny=None, x=None, y=None, **kwargs) -> str:
        action = {"action": "move"}
        if mark is not None:
            action["mark"] = mark
        elif nx is not None and ny is not None:
            action["nx"], action["ny"] = nx, ny
        elif x is not None and y is not None:
            action["x"], action["y"] = x, y
        else:
            return ("Error: move needs a target. Pass mark=N from the latest "
                    "computer_screenshot (preferred), or nx/ny, or x/y.")
        return self._input(action)


class ComputerDragTool(_ComputerBase):
    name = "computer_drag"
    description = "Drag from (x1,y1) to (x2,y2) in absolute screen pixels."
    parameters = {"type": "object", "properties": {
        "x1": {"type": "integer"}, "y1": {"type": "integer"},
        "x2": {"type": "integer"}, "y2": {"type": "integer"}},
        "required": ["x1", "y1", "x2", "y2"]}

    def execute(self, x1, y1, x2, y2, **kwargs) -> str:
        return self._input({"action": "drag", "x1": x1, "y1": y1, "x2": x2, "y2": y2})


class ComputerScrollTool(_ComputerBase):
    name = "computer_scroll"
    description = (
        "Scroll at a mark (preferred) or x/y. direction=up|down, amount=steps. "
        "On Wayland this may fall back to PageUp/PageDown."
    )
    parameters = {"type": "object", "properties": {
        "direction": {"type": "string", "default": "down"},
        "amount": {"type": "integer", "default": 3},
        "mark": {"type": "integer"},
        "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": []}

    def execute(self, direction="down", amount=3, mark=None, x=None, y=None, **kwargs) -> str:
        action = {"action": "scroll", "direction": direction, "amount": amount}
        if mark is not None:
            action["mark"] = mark
        elif x is not None and y is not None:
            # Only move the pointer when a target was actually given; the old
            # x=0/y=0 defaults yanked the cursor to the top-left corner.
            action["x"], action["y"] = x, y
        return self._input(action)


class ComputerTypeTool(_ComputerBase):
    name = "computer_type"
    description = (
        "Type text into the focused input with delivery verification. It captures a "
        "fresh screenshot first, REFUSES to type over an overview/app-search/launcher, "
        "types, captures again, and proves the text appeared near the input you clicked. "
        "submit=true presses Return ONLY after that proof; otherwise it returns "
        "delivered=false and presses nothing (so it cannot send into the wrong app). "
        "Pass expect='Discord' (any text visible in the intended window) to also prove "
        "the right app is in front. verify=false is blind typing (delivered=null) and "
        "still refuses to press Return unless allow_unverified=true — use it only when "
        "you deliberately accept that risk. Any Unicode is supported (accents, CJK, "
        "emoji): ASCII is typed directly, non-ASCII is pasted via the clipboard. "
        "If OCR cannot read the script, verification may fail; confirm visually and "
        "retry with verify=false + allow_unverified=true."
    )
    parameters = {"type": "object", "properties": {
        "text": {"type": "string"},
        "submit": {"type": "boolean", "description": "Press Return after delivery is verified."},
        "expect": {"type": "string",
                   "description": "Text that must be visible in the intended window (e.g. app name or channel)."},
        "verify": {"type": "boolean",
                   "description": "Capture + verify delivery (default true). false = blind typing."},
        "allow_unverified": {"type": "boolean",
                             "description": "Allow submit even when OCR cannot confirm the text. Default false."}},
        "required": ["text"]}

    def execute(self, text: str, submit: bool = False, expect: str = "",
                verify: bool = True, allow_unverified: bool = False, **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        denied = self._allowed(f"Type {len(str(text))} characters")
        if denied:
            return denied
        type_text = getattr(ctrl, "type_text", None)
        if type_text is None:
            return ("Error: this controller has no verified typing path; refusing to "
                    "type blind. Upgrade the computer controller or type manually.")
        try:
            payload = type_text(
                str(text),
                submit=bool(submit),
                expect=(str(expect).strip() or None),
                verify=bool(verify),
                allow_unverified=bool(allow_unverified),
            )
        except Exception as e:
            return f"Error: {e}"
        body = json.dumps(payload, default=str)
        if payload.get("ok"):
            return body
        # Non-ok results are tool errors so the agent's error tracking sees them.
        return f"Error: {body}"


class ComputerKeyTool(_ComputerBase):
    name = "computer_key"
    description = "Press one key on the focused window (e.g. Return, Tab, Escape, Page_Down)."
    parameters = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}

    def execute(self, key: str, **kwargs) -> str:
        return self._input({"action": "key", "key": key})


class ComputerComboTool(_ComputerBase):
    name = "computer_combo"
    description = "Press a key combo (e.g. keys=['ctrl','c'])."
    parameters = {"type": "object", "properties": {
        "keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"]}

    def execute(self, keys, **kwargs) -> str:
        return self._input({"action": "combo", "keys": list(keys or [])})


class ComputerWindowsTool(_ComputerBase):
    name = "computer_windows"
    description = (
        "List/focus/active windows. On Wayland, list may be empty because native windows "
        "are not exposed — that is NORMAL; do not retry. It also returns running GUI apps "
        "and a screenshot-based fallback. Use op=focus with app='Discord' or a window_id."
    )
    action_type = "computer_read"
    is_read_only = True
    parameters = {"type": "object", "properties": {
        "op": {"type": "string", "default": "list"},
        "window_id": {"type": "string"},
        "app": {"type": "string", "description": "App/process name for op=focus."}},
        "required": []}

    def execute(self, op="list", window_id="", app="", **kwargs) -> str:
        ctrl = self._controller()
        if ctrl is None:
            return "Error: No computer controller available"
        if op == "focus":
            denied = self._allowed("Computer focus window")
            if denied:
                return denied
        else:
            denied = self._allowed("Computer windows", read_only=True)
            if denied:
                return denied
        try:
            if op == "list":
                res = ctrl.list_windows()
                if res.get("ok") and not res.get("windows"):
                    session = str(res.get("session") or "")
                    if session in ("wayland", "xwayland"):
                        res["next"] = (
                            "Empty window list is expected on Wayland-native desktops. "
                            "Do NOT call computer_windows again this turn. Take a screenshot "
                            "and act on its marks instead."
                        )
                    else:
                        res["next"] = (
                            "No windows were reported. Verify a desktop session is running, "
                            "or use computer_screenshot and act on the image/marks."
                        )
                return json.dumps(res, default=str)
            if op == "focus":
                if not window_id and not app:
                    return "Error: focus requires window_id or app (e.g. app='Discord')."
                return json.dumps(ctrl.focus_window(str(window_id), app=str(app)), default=str)
            if op == "active":
                return json.dumps(ctrl.active_window(), default=str)
            return f"Error: unknown op '{op}'"
        except Exception as e:
            return f"Error: {e}"


class ComputerClipboardTool(_ComputerBase):
    name = "computer_clipboard"
    description = "Read/write the system clipboard (op=read|write)."
    parameters = {"type": "object", "properties": {
        "op": {"type": "string", "default": "read"},
        "text": {"type": "string"}}, "required": []}

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
    description = (
        "PREFERRED macro: run several GUI steps in one call, then return one screenshot "
        "with fresh marks. Steps: "
        "{action:'click', mark:3}, {action:'type', text:'hi', expect:'Discord'}, "
        "{action:'key', key:'Return'}, {action:'focus', app:'Discord'}, {action:'wait', ms:300}. "
        "A type step is verified before the next step runs, so Return is skipped when the "
        "text did not reach the clicked input. Stops at the first failed step."
    )
    parameters = {"type": "object", "properties": {
        "steps": {"type": "array", "items": {"type": "object"}},
        "marks": {"type": "boolean",
                  "description": "Return OCR marks on the fresh screenshot (default true; false saves ~3s)."}},
        "required": ["steps"]}

    def execute(self, steps, marks: bool = True, **kwargs) -> str:
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
            shot = ctrl.capture(with_marks=bool(marks))
            shot_note = ""
            marks_text = ""
            if shot.get("ok"):
                p = shot.get("image_path") or shot.get("path") or ""
                shot_note = f" [harness:image:{p}]"
                marks_text = shot.get("marks_text") or ""
            failed_at = None
            for i, res in enumerate(results):
                if not res.get("ok"):
                    failed_at = i
                    break
            # Roll up text delivery: False if any type step failed to land,
            # True only when a type step was verified and none failed.
            delivered = None
            for res in results:
                if res.get("delivered") is False:
                    delivered = False
                    break
                if res.get("delivered") is True and delivered is None:
                    delivered = True
            payload = {
                "results": results,
                "screenshot_ok": bool(shot.get("ok")),
                "failed_at_step": failed_at,
                "delivered": delivered,
            }
            if failed_at is not None:
                failed = results[failed_at] if failed_at < len(results) else {}
                payload["error"] = failed.get("error") or "step failed"
                if failed.get("next"):
                    payload["next"] = failed.get("next")
                if failed_at < len(steps):
                    payload["failed_step_action"] = str(
                        (steps[failed_at] or {}).get("action") or "").lower()
            prefix = "Error: " if failed_at is not None else ""
            out = prefix + json.dumps(payload, default=str) + shot_note
            if marks_text:
                out += "\n" + marks_text + "\nNEXT: use these fresh marks for the next action."
            elif shot.get("ok"):
                out += ("\nNo OCR marks available; use nx/ny from the screenshot. "
                        "Marks belong to this new screenshot.")
            return out
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
