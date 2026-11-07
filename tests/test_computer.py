"""Hermetic computer-tool tests (D1/D2/D3) using injectable seams.

No display, no tesseract, no subprocess: every backend is fake and OCR is
stubbed. The live backends are exercised manually against real desktop
sessions (GNOME/COSMIC/KDE Wayland + X11), not in unit tests.
"""
import os
import unittest
from unittest.mock import patch

from harness.tools.computer import (
    ComputerActTool,
    ComputerClickTool,
    ComputerScreenshotTool,
    ComputerTypeTool,
    ComputerWindowsTool,
    register_computer_tools,
)
from harness.tools import ToolRegistry
from harness.computer.grounding import (
    annotate_marks,
    denormalize_coords,
    find_mark,
    normalize_coords,
    parse_ocr_tsv,
    render_marks_text,
)


class _FakeCtrl:
    def __init__(self):
        self.actions = []
        self.focus_calls = []
        self.captures = 0

    def capture(self, monitor_index=0, with_marks=False, **kw):
        self.captures += 1
        return {"ok": True, "image_path": "/tmp/shot.png", "width": 1000,
                "height": 800, "monitors": [], "backend": "fake"}

    def execute_input(self, action):
        self.actions.append(action)
        return {"ok": True, "action": action.get("action")}

    def execute_batch(self, actions):
        out = []
        for a in actions:
            if a.get("action") in ("type", "text"):
                r = self.type_text(
                    str(a.get("text") or ""),
                    submit=bool(a.get("submit")),
                    expect=a.get("expect"),
                    verify=a.get("verify") is not False,
                    allow_unverified=bool(a.get("allow_unverified")),
                )
            else:
                r = self.execute_input(a)
            out.append(r)
            if not r.get("ok"):
                break
        return out

    def type_text(self, text, submit=False, expect=None, verify=True,
                  allow_unverified=False):
        self.actions.append({"action": "type", "text": text})
        if submit:
            self.actions.append({"action": "key", "key": "Return"})
        return {"ok": True, "delivered": True, "verified": bool(verify),
                "submitted": bool(submit)}

    def list_windows(self):
        return {"ok": True, "windows": [], "session": "wayland", "de": "gnome"}

    def focus_window(self, wid, app=""):
        self.focus_calls.append((wid, app))
        return {"ok": True, "window_id": wid, "app": app}

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

    def test_click_requires_a_target(self):
        ctrl = _FakeCtrl()
        t = ComputerClickTool(controller_factory=lambda: ctrl)
        out = t.execute()
        self.assertIn("needs a target", out)
        self.assertEqual(ctrl.actions, [])

    def test_click_accepts_mark(self):
        ctrl = _FakeCtrl()
        t = ComputerClickTool(controller_factory=lambda: ctrl)
        t.execute(mark=3)
        self.assertEqual(ctrl.actions[0]["mark"], 3)

    def test_type_submit_batches_return(self):
        ctrl = _FakeCtrl()
        t = ComputerTypeTool(controller_factory=lambda: ctrl)
        out = t.execute(text="hello", submit=True)
        self.assertIn('"submitted": true', out)
        self.assertEqual(ctrl.actions[0], {"action": "type", "text": "hello"})
        self.assertEqual(ctrl.actions[1], {"action": "key", "key": "Return"})

    def test_type_without_submit_is_one_action(self):
        ctrl = _FakeCtrl()
        t = ComputerTypeTool(controller_factory=lambda: ctrl)
        t.execute(text="hello")
        self.assertEqual(len(ctrl.actions), 1)
        self.assertEqual(ctrl.actions[0]["action"], "type")

    def test_type_failure_is_reported_as_tool_error(self):
        class _BlockedCtrl(_FakeCtrl):
            def type_text(self, text, submit=False, expect=None, verify=True,
                          allow_unverified=False):
                return {"ok": False, "delivered": False, "submitted": False,
                        "kind": "overview_open", "error": "app-search is open",
                        "next": "press Escape"}

        ctrl = _BlockedCtrl()
        t = ComputerTypeTool(controller_factory=lambda: ctrl)
        out = t.execute(text="hello", submit=True)
        self.assertTrue(out.startswith("Error:"))
        self.assertIn('"delivered": false', out)
        self.assertIn("app-search is open", out)
        self.assertEqual(ctrl.actions, [])

    def test_type_without_a_verified_path_refuses(self):
        class _LegacyCtrl:
            pass

        t = ComputerTypeTool(controller_factory=lambda: _LegacyCtrl())
        out = t.execute(text="hello")
        self.assertTrue(out.startswith("Error:"))
        self.assertIn("refusing", out)

    def test_windows_empty_list_is_not_a_failure(self):
        ctrl = _FakeCtrl()
        t = ComputerWindowsTool(controller_factory=lambda: ctrl)
        out = t.execute(op="list")
        self.assertIn('"ok": true', out)
        self.assertIn("Do NOT call computer_windows again", out)

    def test_windows_focus_passes_app(self):
        ctrl = _FakeCtrl()
        t = ComputerWindowsTool(controller_factory=lambda: ctrl)
        out = t.execute(op="focus", app="Discord")
        self.assertIn('"ok": true', out)
        self.assertEqual(ctrl.focus_calls, [("", "Discord")])

    def test_act_returns_fresh_screenshot(self):
        ctrl = _FakeCtrl()
        t = ComputerActTool(controller_factory=lambda: ctrl)
        out = t.execute(steps=[{"action": "click", "mark": 1},
                               {"action": "type", "text": "hi"}])
        self.assertIn("[harness:image:", out)
        self.assertIn('"failed_at_step": null', out)
        self.assertEqual(ctrl.captures, 1)
        self.assertEqual(len(ctrl.actions), 2)

    def test_act_reports_failed_step(self):
        class _FailCtrl(_FakeCtrl):
            def execute_batch(self, actions):
                return [{"ok": True, "action": "click"},
                        {"ok": False, "error": "no such window"}]

        ctrl = _FailCtrl()
        t = ComputerActTool(controller_factory=lambda: ctrl)
        out = t.execute(steps=[{"action": "click", "mark": 1},
                               {"action": "key", "key": "Return"}])
        self.assertIn('"failed_at_step": 1', out)
        self.assertTrue(out.startswith("Error:"))

    def test_act_surfaces_delivery_failure(self):
        class _NoDeliveryCtrl(_FakeCtrl):
            def type_text(self, text, submit=False, expect=None, verify=True,
                          allow_unverified=False):
                return {"ok": False, "delivered": False, "submitted": False,
                        "kind": "text_not_on_screen",
                        "error": "the text did not appear"}

        ctrl = _NoDeliveryCtrl()
        t = ComputerActTool(controller_factory=lambda: ctrl)
        out = t.execute(steps=[{"action": "click", "mark": 1},
                               {"action": "type", "text": "hello"},
                               {"action": "key", "key": "Return"}])
        self.assertTrue(out.startswith("Error:"))
        self.assertIn('"delivered": false', out)
        self.assertIn('"failed_at_step": 1', out)
        self.assertNotIn({"action": "key", "key": "Return"}, ctrl.actions)


class _ControllerScaffold:
    """Build a ComputerController with fully fake backends."""

    def make(self):
        from harness.computer.controller import ComputerController
        seen = []

        def _input(action):
            seen.append(action)
            return {"ok": True, "action": action.get("action")}

        def _capture(region, monitor_index, out_dir):
            return {"ok": True, "image_path": "/tmp/fake-shot.png",
                    "width": 1000, "height": 800, "backend": "fake"}

        ctrl = ComputerController(capture_backend=_capture, input_exec=_input,
                                  screenshot_dir="/tmp")
        return ctrl, seen


_FAKE_MARKS = [
    {"mark": 1, "role": "text", "name": "Search", "x": 120, "y": 340,
     "nx": 120, "ny": 425},
    {"mark": 2, "role": "text", "name": "Send", "x": 800, "y": 700,
     "nx": 800, "ny": 875},
]


class TestControllerCoordinates(unittest.TestCase):
    def test_capture_populates_geometry_and_marks(self):
        ctrl, _ = _ControllerScaffold().make()
        with patch("harness.computer.grounding.ocr_marks", return_value=list(_FAKE_MARKS)):
            shot = ctrl.capture(with_marks=True)
        self.assertTrue(shot["ok"])
        self.assertEqual(shot["width"], 1000)
        self.assertEqual(shot["height"], 800)
        self.assertEqual(ctl_geom := ctrl.latest_geometry(),
                         {"x": 0, "y": 0, "width": 1000, "height": 800})
        self.assertEqual(len(shot["marks"]), 2)
        self.assertIn("Set-of-Marks", shot["marks_text"])
        self.assertEqual(ctl_geom["width"], 1000)

    def test_mark_resolves_to_absolute_pixels(self):
        ctrl, seen = _ControllerScaffold().make()
        with patch("harness.computer.grounding.ocr_marks", return_value=list(_FAKE_MARKS)):
            ctrl.capture(with_marks=True)
        out = ctrl.execute_input({"action": "click", "mark": "2"})
        self.assertTrue(out["ok"])
        self.assertEqual(seen[-1]["x"], 800)
        self.assertEqual(seen[-1]["y"], 700)

    def test_unknown_mark_fails_before_input(self):
        ctrl, seen = _ControllerScaffold().make()
        out = ctrl.execute_input({"action": "click", "mark": 99})
        self.assertFalse(out["ok"])
        self.assertIn("Unknown mark 99", out["error"])
        self.assertEqual(seen, [])

    def test_normalized_coords_need_a_screenshot(self):
        ctrl, seen = _ControllerScaffold().make()
        out = ctrl.execute_input({"action": "click", "nx": 500, "ny": 500})
        self.assertFalse(out["ok"])
        self.assertIn("need a screenshot", out["error"])
        self.assertEqual(seen, [])

    def test_normalized_coords_scale_to_geometry(self):
        ctrl, seen = _ControllerScaffold().make()
        with patch("harness.computer.grounding.ocr_marks", return_value=[]):
            ctrl.capture(with_marks=True)
        ctrl.execute_input({"action": "click", "nx": 500, "ny": 500})
        self.assertEqual((seen[-1]["x"], seen[-1]["y"]), (500, 400))

    def test_absolute_coords_pass_through(self):
        ctrl, seen = _ControllerScaffold().make()
        ctrl.execute_input({"action": "click", "x": 42, "y": 43})
        self.assertEqual((seen[-1]["x"], seen[-1]["y"]), (42, 43))

    def test_batch_stops_at_first_failure(self):
        ctrl, seen = _ControllerScaffold().make()
        ctrl.input_exec = lambda a: (
            {"ok": a.get("action") != "type", "action": a.get("action")}
        )
        results = ctrl.execute_batch([
            {"action": "click", "x": 1, "y": 2},
            {"action": "type", "text": "hi"},
            {"action": "key", "key": "Return"},
        ])
        self.assertEqual(len(results), 2)
        self.assertFalse(results[-1]["ok"])

    def test_batch_supports_focus_and_wait(self):
        from harness.computer.controller import ComputerController
        focus = []

        def _window(op, **kw):
            focus.append((op, kw))
            return {"ok": True}

        ctrl = ComputerController(
            capture_backend=lambda *a, **k: {"ok": True, "image_path": "/tmp/x.png",
                                             "width": 10, "height": 10},
            input_exec=lambda a: {"ok": True},
            window_backend=_window,
        )
        results = ctrl.execute_batch([
            {"action": "focus", "app": "Discord"},
            {"action": "wait", "ms": 1},
        ])
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(focus, [("focus", {"window_id": "", "app": "Discord"})])


class TestGuardedTyping(unittest.TestCase):
    """The wrong-app incident: type + Return must never run blind.

    A fresh capture is inspected before typing; after typing, the text must be
    visible near the clicked field, otherwise Return is never pressed.
    """

    def _ctrl(self):
        from harness.computer.controller import ComputerController
        seen = []
        shots = {"n": 0}

        def _capture(region, monitor_index, out_dir):
            shots["n"] += 1
            return {"ok": True, "image_path": f"/tmp/guard-{shots['n']}.png",
                    "width": 1000, "height": 800, "backend": "fake"}

        def _input(action):
            seen.append(action)
            return {"ok": True, "action": action.get("action")}

        return ComputerController(capture_backend=_capture, input_exec=_input,
                                  screenshot_dir="/tmp"), seen

    @staticmethod
    def _ocr(pre, post):
        calls = {"n": 0}

        def _prep(marks):
            out = []
            for m in marks:
                m = dict(m)
                m.setdefault("nx", int(m.get("x", 0) / 10))
                m.setdefault("ny", int(m.get("y", 0) / 8))
                out.append(m)
            return out

        def _fn(image_path, max_marks=70, **kw):
            calls["n"] += 1
            return _prep(pre if calls["n"] == 1 else post)

        return patch("harness.computer.grounding.ocr_marks", side_effect=_fn)

    def _click_input(self, ctrl, y=700):
        out = ctrl.execute_input({"action": "click", "x": 500, "y": y})
        self.assertTrue(out["ok"])

    def test_overview_blocks_typing_before_any_keystroke(self):
        ctrl, seen = self._ctrl()
        pre = [{"mark": 1, "name": "Digite para pesquisar", "x": 500, "y": 40}]
        with self._ocr(pre, pre):
            out = ctrl.type_text("PARTE 2", submit=True)
        self.assertFalse(out["ok"])
        self.assertFalse(out["delivered"])
        self.assertFalse(out["submitted"])
        self.assertEqual(out["kind"], "overview_open")
        self.assertEqual(seen, [])

    def test_expect_missing_blocks_typing(self):
        ctrl, seen = self._ctrl()
        pre = [{"mark": 1, "name": "Slack", "x": 500, "y": 300}]
        with self._ocr(pre, pre):
            out = ctrl.type_text("hello", submit=True, expect="Discord")
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "target_not_visible")
        self.assertEqual(seen, [])

    def test_verified_text_near_click_submits(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        pre = [{"mark": 1, "name": "Discord", "x": 100, "y": 100},
               {"mark": 2, "name": "Message input", "x": 500, "y": 700}]
        post = pre + [{"mark": 3, "name": "hello world", "x": 500, "y": 700}]
        with self._ocr(pre, post):
            out = ctrl.type_text("hello world", submit=True, expect="Discord")
        self.assertTrue(out["ok"])
        self.assertTrue(out["delivered"])
        self.assertTrue(out["verified"])
        self.assertTrue(out["submitted"])
        self.assertTrue(out["anchor_checked"])
        self.assertEqual(seen[-1], {"action": "key", "key": "Return"})

    def test_text_not_on_screen_never_presses_return(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        pre = [{"mark": 2, "name": "Message input", "x": 500, "y": 700}]
        with self._ocr(pre, pre):
            out = ctrl.type_text("hello world", submit=True)
        self.assertFalse(out["ok"])
        self.assertFalse(out["delivered"])
        self.assertEqual(out["kind"], "text_not_on_screen")
        self.assertIn({"action": "type", "text": "hello world"}, seen)
        self.assertFalse(any(a.get("key") == "Return" for a in seen))

    def test_text_far_from_click_never_presses_return(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        pre = [{"mark": 1, "name": "Message input", "x": 500, "y": 700}]
        post = pre + [{"mark": 2, "name": "hello world", "x": 500, "y": 40}]
        with self._ocr(pre, post):
            out = ctrl.type_text("hello world", submit=True)
        self.assertFalse(out["ok"])
        self.assertFalse(out["delivered"])
        self.assertEqual(out["kind"], "text_landed_wrong_place")
        self.assertFalse(any(a.get("key") == "Return" for a in seen))

    def test_no_ocr_never_presses_return_without_opt_in(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        with patch("harness.computer.grounding.ocr_marks", return_value=[]):
            out = ctrl.type_text("hello world", submit=True)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["delivered"])
        self.assertFalse(any(a.get("key") == "Return" for a in seen))

    def test_manual_return_blocked_after_failed_delivery(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        pre = [{"mark": 1, "name": "Message input", "x": 500, "y": 700}]
        with self._ocr(pre, pre):
            out = ctrl.type_text("hello", submit=False)
        self.assertFalse(out["ok"])
        before = len(seen)
        key = ctrl.execute_input({"action": "key", "key": "Return"})
        self.assertFalse(key["ok"])
        self.assertEqual(key["kind"], "typing_unverified")
        self.assertEqual(len(seen), before)

    def test_escape_clears_the_typing_lock(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        pre = [{"mark": 1, "name": "Message input", "x": 500, "y": 700}]
        with self._ocr(pre, pre):
            ctrl.type_text("hello")
        esc = ctrl.execute_input({"action": "key", "key": "Escape"})
        self.assertTrue(esc["ok"])
        ret = ctrl.execute_input({"action": "key", "key": "Return"})
        self.assertTrue(ret["ok"])
        self.assertIn({"action": "key", "key": "Escape"}, seen)
        self.assertIn({"action": "key", "key": "Return"}, seen)

    def test_blind_submit_needs_explicit_opt_in(self):
        ctrl, seen = self._ctrl()
        self._click_input(ctrl, y=700)
        out = ctrl.type_text("hello", submit=True, verify=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "submit_unverified")
        self.assertFalse(any(a.get("key") == "Return" for a in seen))


class TestImageSize(unittest.TestCase):
    def test_png_header_without_pillow(self):
        import struct
        import tempfile
        from harness.computer.controller import _image_size
        with tempfile.NamedTemporaryFile(suffix=".png") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                     + struct.pack(">II", 1920, 1200))
            fh.flush()
            self.assertEqual(_image_size(fh.name), (1920, 1200))

    def test_bmp_header(self):
        import struct
        import tempfile
        from harness.computer.controller import _image_size
        with tempfile.NamedTemporaryFile(suffix=".bmp") as fh:
            fh.write(b"BM" + b"\x00" * 16 + struct.pack("<ii", 800, 600))
            fh.flush()
            self.assertEqual(_image_size(fh.name), (800, 600))

    def test_unknown_file_degrades_to_zero(self):
        import tempfile
        from harness.computer.controller import _image_size
        with tempfile.NamedTemporaryFile(suffix=".dat") as fh:
            fh.write(b"not an image")
            fh.flush()
            self.assertEqual(_image_size(fh.name), (0, 0))


class TestOcrMarks(unittest.TestCase):
    HEADER = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
              "left\ttop\twidth\theight\tconf\ttext")

    def _tsv(self):
        return "\n".join([
            self.HEADER,
            "5\t1\t1\t1\t1\t1\t100\t50\t40\t10\t90\tHello",
            "5\t1\t1\t1\t1\t2\t145\t50\t45\t10\t85\tWorld",
            "5\t1\t1\t1\t2\t1\t500\t700\t40\t20\t80\tSend",
            "5\t1\t1\t1\t3\t1\t10\t10\t20\t10\t10\tnoise",
        ])

    def test_groups_words_into_line_marks(self):
        marks = parse_ocr_tsv(self._tsv(), 1000, 800)
        self.assertEqual(len(marks), 2)
        first = marks[0]
        self.assertEqual(first["mark"], 1)
        self.assertEqual(first["name"], "Hello World")
        self.assertEqual((first["x"], first["y"]), (145, 55))
        self.assertEqual((first["w"], first["h"]), (90, 10))
        self.assertEqual(first["nx"], denormalize_coords(145, 55, 1000, 800)["x"])
        self.assertEqual(first["ny"], denormalize_coords(145, 55, 1000, 800)["y"])

        second = marks[1]
        self.assertEqual(second["mark"], 2)
        self.assertEqual(second["name"], "Send")
        self.assertEqual((second["x"], second["y"]), (520, 710))

    def test_low_confidence_words_are_skipped(self):
        marks = parse_ocr_tsv(self._tsv(), 1000, 800, min_confidence=80)
        self.assertEqual([m["name"] for m in marks], ["Hello World", "Send"])

    def test_max_marks_caps_output(self):
        marks = parse_ocr_tsv(self._tsv(), 1000, 800, max_marks=1)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["name"], "Hello World")

    def test_page_row_provides_dimensions(self):
        tsv = "\n".join([
            self.HEADER,
            "1\t1\t0\t0\t0\t0\t0\t0\t1000\t800\t-1\t",
            "5\t1\t1\t1\t1\t1\t100\t50\t40\t10\t90\tHello",
        ])
        marks = parse_ocr_tsv(tsv)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["nx"], denormalize_coords(120, 55, 1000, 800)["x"])

    def test_bad_input_degrades_to_empty(self):
        self.assertEqual(parse_ocr_tsv("", 100, 100), [])
        self.assertEqual(parse_ocr_tsv("nonsense", 100, 100), [])

    def test_find_mark_accepts_int_and_str(self):
        marks = [{"mark": 1, "x": 1, "y": 2}, {"mark": 2, "x": 3, "y": 4}]
        self.assertEqual(find_mark(marks, 1)["x"], 1)
        self.assertEqual(find_mark(marks, "2")["x"], 3)
        self.assertIsNone(find_mark(marks, "nope"))
        self.assertIsNone(find_mark([], 1))

    def test_render_marks_text(self):
        self.assertEqual(render_marks_text([]), "")
        text = render_marks_text([
            {"mark": 1, "name": "Search", "nx": 10, "ny": 20},
        ])
        self.assertIn("Set-of-Marks", text)
        self.assertIn("[1] 'Search' nx=10 ny=20", text)


class TestPlatformDetection(unittest.TestCase):
    def test_linux_session_types(self):
        import harness.computer.wayland_utils as wu
        with patch.dict(os.environ,
                        {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
                        clear=True):
            self.assertEqual(wu.detect_session_type(), "xwayland")
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertEqual(wu.detect_session_type(), "wayland")
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertEqual(wu.detect_session_type(), "x11")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(wu.detect_session_type(), "headless")

    def test_de_markers(self):
        import harness.computer.wayland_utils as wu
        cases = {
            "ubuntu:GNOME": "gnome",
            "GNOME-Classic:GNOME": "gnome",
            "KDE": "kde",
            "COSMIC": "cosmic",
            "XFCE": "xfce",
            "sway": "sway",
            "Hyprland": "hyprland",
        }
        for raw, expected in cases.items():
            with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": raw},
                            clear=True):
                with patch.object(wu, "_which", return_value=False):
                    self.assertEqual(wu.detect_de(), expected, raw)

    def test_unknown_de_keeps_first_token(self):
        import harness.computer.wayland_utils as wu
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "MyDE:Other"},
                        clear=True):
            with patch.object(wu, "_which", return_value=False):
                self.assertEqual(wu.detect_de(), "myde")

    def test_platform_names(self):
        import harness.computer.wayland_utils as wu
        with patch.object(wu.sys, "platform", "darwin"):
            self.assertEqual(wu.detect_platform(), "macos")
            self.assertEqual(wu.detect_session_type(), "macos")
            self.assertEqual(wu.detect_de(), "macos")
        with patch.object(wu.sys, "platform", "win32"):
            self.assertEqual(wu.detect_platform(), "windows")
            self.assertEqual(wu.detect_session_type(), "windows")
            self.assertEqual(wu.detect_de(), "windows")


class TestKeyNameTranslation(unittest.TestCase):
    """Regression: ydotool 0.1.8 silently maps unknown names to their first letter.

    ``Return`` pressed R, ``Page_Down`` pressed P, ``space`` pressed S. Every
    backend must normalize names before sending them, and unsupported names
    must fail loudly instead of pressing the wrong key.
    """

    def test_normalization(self):
        import harness.computer.wayland_utils as wu
        cases = {
            "Return": "Return", "return": "Return", "enter": "Return",
            "Page_Down": "Page_Down", "pagedown": "Page_Down",
            "page-up": "Page_Up", "pgdn": "Page_Down",
            "Esc": "Escape", "space": "space", "spacebar": "space",
            "BackSpace": "BackSpace", "caps_lock": "Caps_Lock",
            "Control": "ctrl", "cmd": "super", "option": "alt",
            "f12": "F12", "a": "a", "!": "!",
        }
        for raw, expected in cases.items():
            self.assertEqual(wu._normalize_key_name(raw), expected, raw)
        self.assertEqual(wu._normalize_key_name("Hyper"), "Hyper")

    def test_ydotool_tokens(self):
        import harness.computer.wayland_utils as wu
        cases = {
            "Return": "ENTER", "Escape": "ESC", "Page_Down": "PAGEDOWN",
            "Page_Up": "PAGEUP", "space": " ", "Up": "UP",
            "ctrl": "CTRL", "F12": "F12", "a": "a",
        }
        for raw, expected in cases.items():
            self.assertEqual(wu._ydotool_key_token(raw), expected, raw)

    def test_ydotool_rejects_unsafe_names(self):
        import harness.computer.wayland_utils as wu
        with self.assertRaises(ValueError):
            wu._ydotool_key_token("Hyper")
        with self.assertRaises(ValueError) as ctx:
            wu._ydotool_key_token("F13")
        self.assertIn("F1-F12", str(ctx.exception))
        with self.assertRaises(ValueError):
            wu._ydotool_key_token("Menu")
        with self.assertRaises(ValueError) as ctx:
            wu._ydotool_key_token("!")
        self.assertIn("computer_type", str(ctx.exception))

    def test_ydotool_sequences(self):
        import harness.computer.wayland_utils as wu
        self.assertEqual(wu._ydotool_key_sequence(["ctrl+c"]), "CTRL+c")
        self.assertEqual(
            wu._ydotool_key_sequence(["ctrl", "Page_Down"]),
            "CTRL+PAGEDOWN")
        with self.assertRaises(ValueError):
            wu._ydotool_key_sequence([])

    def test_ydotool_key_command(self):
        import harness.computer.wayland_utils as wu
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return {"rc": 0, "stdout": "", "stderr": ""}

        with patch.object(wu, "_run", side_effect=fake_run):
            out = wu._ydotool_input({"action": "key", "key": "Return"})
            combo = wu._ydotool_input(
                {"action": "combo", "keys": ["ctrl", "Page_Down"]})
            typed = wu._ydotool_input({"action": "type", "text": "-hello"})
        self.assertTrue(out["ok"])
        self.assertTrue(combo["ok"])
        self.assertTrue(typed["ok"])
        self.assertEqual(calls[0], ["ydotool", "key", "ENTER"])
        self.assertEqual(calls[1], ["ydotool", "key", "CTRL+PAGEDOWN"])
        self.assertEqual(calls[2], ["ydotool", "type", "--", "-hello"])

    def test_ydotool_unknown_key_never_reaches_cli(self):
        import harness.computer.wayland_utils as wu
        calls = []
        with patch.object(wu, "_run", side_effect=lambda *a, **k: calls.append(a)):
            out = wu._ydotool_input({"action": "key", "key": "Hyper"})
        self.assertFalse(out["ok"])
        self.assertIn("unknown key", out["error"])
        self.assertEqual(calls, [])

    def test_ydotool_scroll_uses_real_page_keys(self):
        import harness.computer.wayland_utils as wu
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return {"rc": 0, "stdout": "", "stderr": ""}

        with patch.object(wu, "_run", side_effect=fake_run):
            out = wu._ydotool_input(
                {"action": "scroll", "direction": "up", "amount": 2})
        self.assertTrue(out["ok"])
        self.assertEqual(calls, [["ydotool", "key", "PAGEUP"],
                                 ["ydotool", "key", "PAGEUP"]])

    def test_macos_key_codes(self):
        import harness.computer.wayland_utils as wu
        self.assertEqual(wu._mac_key_code("Return"), 36)
        self.assertEqual(wu._mac_key_code("Page_Down"), 121)
        self.assertEqual(wu._mac_key_code("F5"), 96)
        self.assertEqual(wu._mac_key_code("forwarddelete"), 117)
        self.assertIsNone(wu._mac_key_code("Hyper"))

    def test_windows_sendkeys_names(self):
        import harness.computer.wayland_utils as wu
        self.assertEqual(wu._win_sendkeys_key("Return"), "{ENTER}")
        self.assertEqual(wu._win_sendkeys_key("Page_Down"), "{PGDN}")
        self.assertEqual(wu._win_sendkeys_key("a"), "a")
        self.assertIsNone(wu._win_sendkeys_key("Hyper"))

    def test_windows_super_does_not_silently_become_ctrl(self):
        import harness.computer.wayland_utils as wu
        with patch.object(wu, "_powershell", return_value="powershell"):
            out = wu._windows_input(
                {"action": "combo", "keys": ["win", "e"]})
        self.assertFalse(out["ok"])
        self.assertIn("Super", out["error"])


class _FakeXTestSession:
    """Records XTEST calls instead of touching a real X display."""

    def __init__(self, *args, **kwargs):
        self.calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def move(self, x, y):
        self.calls.append(("move", int(x), int(y)))

    def click(self, name="left"):
        self.calls.append(("click", str(name)))

    def double_click(self, name="left"):
        self.calls.append(("double_click", str(name)))

    def drag(self, x1, y1, x2, y2, name="left"):
        self.calls.append(("drag", int(x1), int(y1), int(x2), int(y2), str(name)))

    def scroll(self, x_steps=0, y_steps=0):
        self.calls.append(("scroll", int(x_steps), int(y_steps)))

    def combo(self, keys):
        self.calls.append(("combo", list(keys)))

    def key(self, name, down=True):
        self.calls.append(("key", str(name), bool(down)))

    def type_text(self, text):
        self.calls.append(("type_text", str(text)))


class TestPointerPositioning(unittest.TestCase):
    """Regression: the cursor used to land in a screen corner.

    XTEST click/double ignored x/y and clicked the current position, and
    ``ydotool 0.1.8 mousemove <x> <y>`` is a relative move (warp to the
    origin, then apply a delta). Pointer actions must move first, on every
    GUI platform, and tools must never default a missing target to (0, 0).
    """

    def _session(self):
        session = _FakeXTestSession()
        return session

    def test_click_moves_before_pressing(self):
        from harness.computer.controller import _apply_xtest
        session = self._session()
        out = _apply_xtest(
            session, {"action": "click", "x": 500, "y": 400, "button": "left"})
        self.assertTrue(out["ok"])
        self.assertEqual(session.calls[0], ("move", 500, 400))
        self.assertEqual(session.calls[1], ("click", "left"))
        self.assertEqual(out["backend"], "xtest")

    def test_double_moves_before_pressing(self):
        from harness.computer.controller import _apply_xtest
        session = self._session()
        out = _apply_xtest(
            session, {"action": "double", "x": 1200, "y": 800})
        self.assertTrue(out["ok"])
        self.assertEqual(session.calls[0], ("move", 1200, 800))
        self.assertEqual(session.calls[1], ("double_click", "left"))

    def test_drag_uses_absolute_endpoints(self):
        from harness.computer.controller import _apply_xtest
        session = self._session()
        out = _apply_xtest(
            session,
            {"action": "drag", "x1": 100, "y1": 100, "x2": 800, "y2": 600})
        self.assertTrue(out["ok"])
        self.assertEqual(session.calls, [("drag", 100, 100, 800, 600, "left")])

    def test_scroll_moves_before_wheel(self):
        from harness.computer.controller import _apply_xtest
        session = self._session()
        out = _apply_xtest(
            session,
            {"action": "scroll", "x": 500, "y": 400,
             "direction": "down", "amount": 3})
        self.assertTrue(out["ok"])
        self.assertEqual(session.calls[0], ("move", 500, 400))
        self.assertEqual(session.calls[1], ("scroll", 0, -3))

    def test_move_without_coords_fails_loudly(self):
        from harness.computer.controller import _apply_xtest
        session = self._session()
        out = _apply_xtest(session, {"action": "move"})
        self.assertFalse(out["ok"])
        self.assertIn("needs x and y", out["error"])
        self.assertEqual(session.calls, [])

    def test_hybrid_click_positions_with_xtest_injects_with_ydotool(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _apply_xtest
        session = self._session()
        cli_calls = []

        def fake_cli(action):
            cli_calls.append(action)
            return {"ok": True}

        with patch.object(ctrl_mod, "cli_input", side_effect=fake_cli):
            out = _apply_xtest(
                session,
                {"action": "click", "x": 500, "y": 400, "button": "left"},
                hybrid_clicks=True,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "xtest+ydotool")
        # XTEST did the positioning, ydotool only pressed the button
        # at the compositor's current pointer (no coords of its own).
        self.assertEqual(session.calls, [("move", 500, 400)])
        self.assertEqual(
            cli_calls, [{"action": "click", "button": "left"}])

    def test_default_input_exec_prefers_xtest_for_pointer_on_xwayland(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _default_input_exec
        session = self._session()
        cli_calls = []

        def fake_cli(action):
            cli_calls.append(action)
            return {"ok": True, "backend": "ydotool"}

        with patch.object(ctrl_mod, "detect_platform", return_value="linux"), \
             patch.object(ctrl_mod, "detect_session_type", return_value="xwayland"), \
             patch.object(ctrl_mod, "XTestSession", return_value=session), \
             patch.object(ctrl_mod, "cli_input", side_effect=fake_cli), \
             patch.object(ctrl_mod, "pyautogui_available", return_value=False):
            out = _default_input_exec({"action": "move", "x": 123, "y": 456})
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "xtest")
        self.assertEqual(session.calls, [("move", 123, 456)])
        # Pointer movement must not fall back to the relative ydotool move.
        self.assertEqual(cli_calls, [])

    def test_default_input_exec_hybrid_click_on_xwayland(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _default_input_exec
        session = self._session()
        cli_calls = []

        def fake_cli(action):
            cli_calls.append(action)
            return {"ok": True}

        with patch.object(ctrl_mod, "detect_platform", return_value="linux"), \
             patch.object(ctrl_mod, "detect_session_type", return_value="xwayland"), \
             patch.object(ctrl_mod, "XTestSession", return_value=session), \
             patch.object(ctrl_mod, "cli_input", side_effect=fake_cli), \
             patch.object(ctrl_mod, "pyautogui_available", return_value=False):
            out = _default_input_exec(
                {"action": "click", "x": 500, "y": 400})
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "xtest+ydotool")
        self.assertEqual(session.calls[0], ("move", 500, 400))
        self.assertEqual(cli_calls, [{"action": "click", "button": "left"}])

    def test_default_input_exec_keyboard_stays_on_cli_on_xwayland(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _default_input_exec
        session = self._session()
        cli_calls = []

        def fake_cli(action):
            cli_calls.append(action)
            return {"ok": True, "backend": "ydotool"}

        with patch.object(ctrl_mod, "detect_platform", return_value="linux"), \
             patch.object(ctrl_mod, "detect_session_type", return_value="xwayland"), \
             patch.object(ctrl_mod, "XTestSession", return_value=session), \
             patch.object(ctrl_mod, "cli_input", side_effect=fake_cli), \
             patch.object(ctrl_mod, "is_wayland", return_value=True), \
             patch.object(ctrl_mod, "pyautogui_available", return_value=False):
            out = _default_input_exec({"action": "key", "key": "Return"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "ydotool")
        self.assertEqual(session.calls, [])
        self.assertEqual(cli_calls, [{"action": "key", "key": "Return"}])

    def test_ydotool_move_uses_absolute_syntax_when_supported(self):
        import harness.computer.wayland_utils as wu
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return {"rc": 0, "stdout": "", "stderr": ""}

        with patch.object(wu, "_YDOTOOL_ABS_CACHE", True), \
             patch.object(wu, "_run", side_effect=fake_run):
            out = wu._ydotool_input({"action": "move", "x": 500, "y": 400})
        self.assertTrue(out["ok"])
        self.assertNotIn("warning", out)
        self.assertEqual(
            calls,
            [["ydotool", "mousemove", "--absolute",
              "--xpos", "500", "--ypos", "400"]])

    def test_ydotool_move_legacy_syntax_warns(self):
        import harness.computer.wayland_utils as wu
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return {"rc": 0, "stdout": "", "stderr": ""}

        with patch.object(wu, "_YDOTOOL_ABS_CACHE", False), \
             patch.object(wu, "_run", side_effect=fake_run):
            out = wu._ydotool_input({"action": "move", "x": 500, "y": 400})
        self.assertTrue(out["ok"])
        self.assertIn("warning", out)
        self.assertEqual(
            calls, [["ydotool", "mousemove", "500", "400"]])

    def test_ydotool_absolute_detection_from_help(self):
        import harness.computer.wayland_utils as wu

        def located(stdout):
            def fake_run(cmd, **kwargs):
                return {"rc": 0, "stdout": stdout, "stderr": ""}
            with patch.object(wu, "_run", side_effect=fake_run):
                return wu._ydotool_supports_absolute(force=True)

        try:
            self.assertTrue(located("Usage: mousemove --absolute --xpos X"))
            self.assertFalse(
                located("Usage: mousemove [--delay <ms>] <x> <y>"))
        finally:
            wu._YDOTOOL_ABS_CACHE = None

    def test_move_tool_never_defaults_to_origin(self):
        from harness.tools.computer import ComputerMoveTool
        ctrl = _FakeCtrl()
        tool = ComputerMoveTool(controller_factory=lambda: ctrl)
        out = tool.execute()
        self.assertIn("needs a target", out)
        self.assertEqual(ctrl.actions, [])
        out = tool.execute(x=800, y=600)
        self.assertIn('"ok": true', out)
        self.assertEqual(
            ctrl.actions[-1], {"action": "move", "x": 800, "y": 600})

    def test_scroll_tool_never_defaults_to_origin(self):
        from harness.tools.computer import ComputerScrollTool
        ctrl = _FakeCtrl()
        tool = ComputerScrollTool(controller_factory=lambda: ctrl)
        out = tool.execute()
        self.assertIn('"ok": true', out)
        self.assertEqual(len(ctrl.actions), 1)
        action = ctrl.actions[-1]
        self.assertEqual(action["action"], "scroll")
        self.assertNotIn("x", action)
        self.assertNotIn("y", action)
        tool.execute(x=500, y=400)
        self.assertEqual(ctrl.actions[-1]["x"], 500)
        self.assertEqual(ctrl.actions[-1]["y"], 400)


class TestUnicodeTyping(unittest.TestCase):
    """Unicode typing is clipboard+paste, never key synthesis.

    XTEST, ydotool ``type`` and pyautogui ``typewrite`` only type characters
    bound in the current keymap. Anything else (accents, CJK, emoji) must go
    through the clipboard so it is not silently dropped or mangled.
    """

    def test_is_ascii_text(self):
        import harness.computer.wayland_utils as wu
        self.assertTrue(wu.is_ascii_text("hello, world!"))
        self.assertTrue(wu.is_ascii_text(""))
        self.assertTrue(wu.is_ascii_text(None))
        self.assertTrue(wu.is_ascii_text("123 !@#\n\t"))
        self.assertFalse(wu.is_ascii_text("olá mundo"))
        self.assertFalse(wu.is_ascii_text("café"))
        self.assertFalse(wu.is_ascii_text("中文测试"))
        self.assertFalse(wu.is_ascii_text("hello 🎉"))
        self.assertFalse(wu.is_ascii_text("naïve ñ"))

    def test_paste_combo_is_platform_specific(self):
        import harness.computer.wayland_utils as wu
        with patch.object(wu, "detect_platform", return_value="macos"):
            self.assertEqual(wu.paste_combo_keys(), ["super", "v"])
        for plat in ("linux", "windows"):
            with patch.object(wu, "detect_platform", return_value=plat):
                self.assertEqual(wu.paste_combo_keys(), ["ctrl", "v"])

    def test_clipboard_paste_writes_then_pastes_then_restores(self):
        import harness.computer.wayland_utils as wu
        clip_calls = []

        def fake_clip(op, text=None):
            clip_calls.append((op, text))
            if op == "read":
                return {"ok": True, "text": "previous"}
            return {"ok": True, "backend": "x11clipboard"}

        combos = []

        def fake_input(action):
            combos.append(action)
            return {"ok": True}

        with patch.object(wu, "cli_clipboard", side_effect=fake_clip), \
             patch.object(wu, "cli_input", side_effect=fake_input):
            out = wu.clipboard_paste("olá 🎉")
        self.assertTrue(out["ok"])
        self.assertEqual(out["via"], "clipboard-paste")
        self.assertIn("x11clipboard", out["backend"])
        self.assertTrue(out["restored_clipboard"])
        self.assertEqual(
            clip_calls,
            [("read", None), ("write", "olá 🎉"), ("write", "previous")])
        self.assertEqual(combos, [{"action": "combo", "keys": ["ctrl", "v"]}])

    def test_clipboard_paste_fails_loudly_without_clipboard(self):
        import harness.computer.wayland_utils as wu
        combos = []
        with patch.object(
                wu, "cli_clipboard",
                return_value={"ok": False,
                              "error": "no clipboard writer (wl-copy/xclip/xsel)",
                              "hints": ["install wl-clipboard"]}), \
             patch.object(
                wu, "cli_input",
                side_effect=lambda a: combos.append(a) or {"ok": True}):
            out = wu.clipboard_paste("中文")
        self.assertFalse(out["ok"])
        self.assertIn("needs a clipboard", out["error"])
        self.assertEqual(combos, [])

    def test_cli_input_routes_unicode_to_paste(self):
        import harness.computer.wayland_utils as wu
        with patch.object(
                wu, "clipboard_paste", return_value={"ok": True, "via": "clipboard-paste"}) as fake:
            out = wu.cli_input({"action": "type", "text": "olá"})
        self.assertTrue(out["ok"])
        fake.assert_called_once_with("olá")

    def test_cli_input_keeps_ascii_on_direct_path(self):
        import harness.computer.wayland_utils as wu
        with patch.object(wu, "clipboard_paste") as fake_paste, \
             patch.object(wu, "detect_platform", return_value="linux"), \
             patch.object(wu, "_linux_input",
                          return_value={"ok": True, "backend": "ydotool"}) as fake_linux:
            out = wu.cli_input({"action": "type", "text": "hello"})
        self.assertTrue(out["ok"])
        fake_paste.assert_not_called()
        fake_linux.assert_called_once_with({"action": "type", "text": "hello"})

    def test_default_input_exec_routes_unicode_to_paste(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _default_input_exec
        with patch.object(
                ctrl_mod, "clipboard_paste",
                return_value={"ok": True, "via": "clipboard-paste"}) as fake:
            out = _default_input_exec({"action": "type", "text": "café ☕"})
        self.assertTrue(out["ok"])
        fake.assert_called_once_with("café ☕")

    def test_default_input_exec_keeps_ascii_off_paste(self):
        import harness.computer.controller as ctrl_mod
        from harness.computer.controller import _default_input_exec
        with patch.object(ctrl_mod, "clipboard_paste") as fake_paste, \
             patch.object(ctrl_mod, "detect_platform", return_value="linux"), \
             patch.object(ctrl_mod, "detect_session_type", return_value="xwayland"), \
             patch.object(ctrl_mod, "is_wayland", return_value=True), \
             patch.object(ctrl_mod, "cli_input",
                          return_value={"ok": True, "backend": "ydotool"}) as fake_cli, \
             patch.object(ctrl_mod, "pyautogui_available", return_value=False):
            out = _default_input_exec({"action": "type", "text": "hello"})
        self.assertTrue(out["ok"])
        fake_paste.assert_not_called()
        fake_cli.assert_called_once()

    def test_apply_xtest_rejects_unicode_loudly(self):
        from harness.computer.controller import _apply_xtest

        class _S:
            def __init__(self):
                self.calls = []

            def type_text(self, text):
                self.calls.append(text)

        session = _S()
        out = _apply_xtest(session, {"action": "type", "text": "olá"})
        self.assertFalse(out["ok"])
        self.assertIn("cannot type non-ASCII", out["error"])
        self.assertEqual(session.calls, [])
        out = _apply_xtest(session, {"action": "type", "text": "hello"})
        self.assertTrue(out["ok"])
        self.assertEqual(session.calls, ["hello"])

    def test_xtest_session_rejects_unicode_without_display(self):
        from harness.computer.xtest import XTestSession
        session = XTestSession.__new__(XTestSession)
        with self.assertRaises(ValueError) as ctx:
            session.type_text("olá")
        self.assertIn("cannot type non-ASCII", str(ctx.exception))
        with self.assertRaises(ValueError):
            session.type_text("中文")

    def test_ydotool_type_rejects_unicode_without_running_cli(self):
        import harness.computer.wayland_utils as wu
        calls = []
        with patch.object(wu, "_run", side_effect=lambda *a, **k: calls.append(a)):
            out = wu._ydotool_input({"action": "type", "text": "café"})
        self.assertFalse(out["ok"])
        self.assertIn("cannot type non-ASCII", out["error"])
        self.assertEqual(calls, [])

    def test_pyautogui_unicode_uses_clipboard_paste(self):
        import harness.computer.pyautogui_backend as pb

        class _FakePA:
            FAILSAFE = False

            def __init__(self):
                self.hotkeys = []
                self.typed = []

            def typewrite(self, text, interval=0.02):
                self.typed.append(text)

            def hotkey(self, *keys):
                self.hotkeys.append(keys)

        fake = _FakePA()
        clips = []

        def fake_write(text):
            clips.append(text)
            return {"ok": True}

        with patch.object(pb, "_pyautogui", fake), \
             patch.object(pb, "_imports_done", True), \
             patch.object(pb, "clipboard_read",
                          return_value={"ok": False, "error": "none"}), \
             patch.object(pb, "clipboard_write", side_effect=fake_write):
            out = pb.execute_input({"action": "type", "text": "café ☕"})
            ascii_out = pb.execute_input({"action": "type", "text": "hello"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["via"], "clipboard-paste")
        self.assertEqual(clips, ["café ☕"])
        self.assertEqual(fake.hotkeys, [("ctrl", "v")])
        self.assertEqual(fake.typed, ["hello"])
        self.assertTrue(ascii_out["ok"])

    def test_locate_short_unicode_token_boundaries(self):
        from harness.computer.guards import locate_typed_text
        # "olá" folds to "ola"; it must not match inside "Solá".
        inside = locate_typed_text(
            [{"mark": 1, "name": "Solá", "x": 500, "y": 700}], "olá")
        self.assertFalse(inside["present"])
        standalone = locate_typed_text(
            [{"mark": 1, "name": "olá mundo", "x": 500, "y": 700}], "olá")
        self.assertTrue(standalone["present"])
        # CJK: a two-character needle must not match inside a longer word.
        cjk_inside = locate_typed_text(
            [{"mark": 1, "name": "你好世界", "x": 500, "y": 700}], "你好")
        self.assertFalse(cjk_inside["present"])
        cjk_token = locate_typed_text(
            [{"mark": 1, "name": "你好 世界", "x": 500, "y": 700}], "你好")
        self.assertTrue(cjk_token["present"])


class TestComputerPrompt(unittest.TestCase):
    def test_protocol_only_when_computer_tools_are_enabled(self):
        from harness.core.prompt import SystemPromptBuilder
        with_computer = SystemPromptBuilder().build(computer_enabled=True)
        without = SystemPromptBuilder().build()
        self.assertIn("COMPUTER USE (desktop GUI)", with_computer)
        self.assertIn("delivered=false", with_computer)
        self.assertIn("overview", with_computer.lower())
        self.assertNotIn("COMPUTER USE (desktop GUI)", without)


class TestComputerWatchdogHelpers(unittest.TestCase):
    def test_observation_classification(self):
        from harness.core.agent import HarnessAgent
        self.assertTrue(HarnessAgent._is_computer_observation_call(
            "computer_screenshot", {}))
        self.assertTrue(HarnessAgent._is_computer_observation_call(
            "computer_ocr", {}))
        self.assertTrue(HarnessAgent._is_computer_observation_call(
            "computer_env", {}))
        self.assertTrue(HarnessAgent._is_computer_observation_call(
            "computer_windows", {"op": "list"}))
        self.assertTrue(HarnessAgent._is_computer_observation_call(
            "computer_windows", {"op": "active"}))
        self.assertFalse(HarnessAgent._is_computer_observation_call(
            "computer_windows", {"op": "focus"}))
        self.assertFalse(HarnessAgent._is_computer_observation_call(
            "computer_click", {}))

    def test_delivery_status_parsing(self):
        from harness.core.agent import HarnessAgent
        failed = HarnessAgent._delivery_status(
            "computer_type",
            'Error: {"ok": false, "delivered": false, "kind": "overview_open", '
            '"error": "app-search is open"}')
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["kind"], "overview_open")
        delivered = HarnessAgent._delivery_status(
            "computer_type", '{"ok": true, "delivered": true, "submitted": true}')
        self.assertEqual(delivered["status"], "delivered")
        unverified = HarnessAgent._delivery_status(
            "computer_type",
            '{"ok": false, "delivered": null, "submitted": false, '
            '"kind": "no_ocr", "error": "OCR unavailable"}')
        self.assertEqual(unverified["status"], "failed")
        submit_failed = HarnessAgent._delivery_status(
            "computer_type",
            '{"ok": false, "delivered": true, "submitted": false, '
            '"kind": "submit_failed", "error": "Return key failed"}')
        self.assertEqual(submit_failed["status"], "failed")
        self.assertIsNone(HarnessAgent._delivery_status(
            "computer_click", '{"ok": true}'))
        act = HarnessAgent._delivery_status(
            "computer_act",
            'Error: {"results": [{"ok": true}, {"ok": false, '
            '"delivered": false, "error": "not on screen"}], '
            '"failed_at_step": 1, "delivered": false} [harness:image:/tmp/x.png]')
        self.assertEqual(act["status"], "failed")
        self.assertEqual(act["error"], "not on screen")
        self.assertEqual(act["failed_at_step"], 1)
        act_key = HarnessAgent._delivery_status(
            "computer_act",
            'Error: {"results": [{"ok": true, "delivered": true}, '
            '{"ok": false, "error": "Return key failed"}], '
            '"failed_at_step": 1, "delivered": true, '
            '"failed_step_action": "key", "error": "Return key failed"}')
        self.assertEqual(act_key["status"], "failed")
        self.assertIn("Return key failed", act_key["error"])

    def test_action_label(self):
        from harness.core.agent import HarnessAgent
        self.assertEqual(
            HarnessAgent._computer_action_label("computer_click", {"mark": 3}),
            "computer_click(mark=3)")
        self.assertEqual(
            HarnessAgent._computer_action_label("computer_click", {}),
            "computer_click")

    def test_macro_labels_include_steps(self):
        from harness.core.agent import HarnessAgent
        first = HarnessAgent._computer_action_label(
            "computer_act", {"steps": [{"action": "click", "mark": 1}]})
        second = HarnessAgent._computer_action_label(
            "computer_act", {"steps": [{"action": "click", "mark": 2}]})
        self.assertNotEqual(first, second)
        self.assertIn("click:1", first)


if __name__ == "__main__":
    unittest.main()
