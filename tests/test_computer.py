"""Hermetic computer-tool tests (D1/D2) using injectable seams."""
import unittest
from harness.tools.computer import ComputerClickTool, ComputerScreenshotTool, register_computer_tools
from harness.tools import ToolRegistry
from harness.computer.grounding import normalize_coords, denormalize_coords, annotate_marks


class _FakeCtrl:
    def __init__(self):
        self.actions = []
    def capture(self, monitor_index=0, **kw):
        return {"ok": True, "image_path": "/tmp/shot.png", "width": 1000, "height": 800, "monitors": [], "backend": "fake"}
    def execute_input(self, action):
        self.actions.append(action)
        return {"ok": True, "action": action.get("action")}
    def execute_batch(self, actions):
        out = []
        for a in actions:
            r = self.execute_input(a)
            out.append(r)
            if not r.get("ok"):
                break
        return out
    def list_windows(self):
        return {"ok": True, "windows": []}
    def focus_window(self, wid):
        return {"ok": True, "window_id": wid}
    def active_window(self):
        return {"ok": True, "window_id": "1"}
    def clipboard_read(self):
        return {"ok": True, "text": "hi"}
    def clipboard_write(self, text):
        return {"ok": True, "text": text}


class TestComputerTools(unittest.TestCase):
    def _tool(self, cls):
        ctrl = _FakeCtrl()
        return cls(controller_factory=lambda: ctrl)

    def test_registry_has_computer_tools(self):
        r = ToolRegistry()
        names = {t.name for t in r.list_tools()}
        for n in ("computer_env", "computer_ocr", "computer_screenshot", "computer_click", "computer_move", "computer_type",
                  "computer_act", "computer_windows", "computer_clipboard"):
            self.assertIn(n, names)

    def test_screenshot_emits_image_marker(self):
        t = self._tool(ComputerScreenshotTool)
        out = t.execute()
        self.assertIn("[harness:image:", out)

    def test_click_records_action(self):
        ctrl = _FakeCtrl()
        t = ComputerClickTool(controller_factory=lambda: ctrl)
        out = t.execute(nx=500, ny=250)
        self.assertIn('"ok": true', out)
        self.assertEqual(len(ctrl.actions), 1)


class TestGrounding(unittest.TestCase):
    def test_normalize_center(self):
        self.assertEqual(normalize_coords(500, 500, 1000, 800), {"x": 500, "y": 400})

    def test_denormalize_roundtrip(self):
        px = normalize_coords(250, 750, 1920, 1080)
        back = denormalize_coords(px["x"], px["y"], 1920, 1080)
        self.assertAlmostEqual(back["x"], 250, delta=1)

    def test_annotate_marks(self):
        marks = annotate_marks([{"role": "button", "name": "OK", "x": 500, "y": 400}], 1000, 800)
        self.assertEqual(marks[0]["mark"], 1)
        self.assertEqual(marks[0]["nx"], 500)


if __name__ == "__main__":
    unittest.main()
