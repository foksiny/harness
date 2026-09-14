"""
Hermetic tests for the computer-use toolset.

These tests NEVER touch a real display, X server, or Wayland compositor.
They inject fake controllers and verify:
- Bare ToolRegistry registers ZERO computer tools (hermetic invariant)
- Seam-injected ToolRegistry registers all 5 computer tools
- Each tool executes hermetically (returns structured dicts, never raises)
- Permission gating routes through PermissionManager
- PLAN mode blocks computer_control
"""
import unittest
from unittest.mock import MagicMock

from harness.tools import ToolRegistry
from harness.tools.computer import (
    ScreenCaptureTool,
    ScreenAnalyzeTool,
    ScreenDescribeViaVfbTool,
    ComputerControlTool,
    ClipboardTool,
    register_computer_tools,
)
from harness.computer.controller import ComputerController
from harness.core.modes import Mode, is_tool_allowed_in_mode
from harness.core.permissions import PermissionManager, PermissionLevel


class FakeController:
    """Hermetic fake controller — never touches a display."""

    def probe(self):
        return {"ok": True, "backend": "fake", "session": "hermetic-test"}

    def capture(self, region=None, monitor_index=0, out_dir=None):
        return {
            "ok": True,
            "path": "/tmp/hermetic-test-shot.png",
            "geometry": {"width": 64, "height": 64, "x": 0, "y": 0},
            "monitors": [{"index": 0, "x": 0, "y": 0, "w": 64, "h": 64}],
        }

    def execute_input(self, action):
        return {"ok": True, "action": action.get("action", "unknown")}

    def execute_batch(self, actions):
        return [self.execute_input(a) for a in actions or []]

    def list_windows(self):
        return {"ok": True, "windows": []}

    def focus_window(self, window_id):
        return {"ok": True, "window": window_id}

    def active_window(self):
        return {"ok": True, "window": {"id": "test"}}

    def clipboard_read(self):
        return {"ok": True, "text": "hermetic-clip"}

    def clipboard_write(self, text):
        return {"ok": True, "wrote": text}


def fake_vision_describe(media_blocks, question=None, system_prompt=None):
    return ("hermetic vision description of the screen", "")


class TestBareRegistryHermetic(unittest.TestCase):
    """Bare ToolRegistry must register ZERO computer tools — hermetic invariant."""

    def test_bare_registry_no_computer_tools(self):
        r = ToolRegistry()
        computer_tools = [t.name for t in r.list_tools() if t.name.startswith(("screen_", "computer_"))]
        self.assertEqual(computer_tools, [])


class TestSeamInjection(unittest.TestCase):
    """Seam-injected registry registers all 5 computer tools."""

    def setUp(self):
        self.registry = ToolRegistry(
            computer_controller_factory=lambda: FakeController(),
            vision_describe=fake_vision_describe,
            permission_manager=PermissionManager(level=PermissionLevel.FULL),
        )

    def test_all_five_tools_registered(self):
        names = sorted(t.name for t in self.registry.list_tools() if t.name.startswith(("screen_", "computer_")))
        self.assertEqual(names, [
            "computer_clipboard",
            "computer_control",
            "screen_analyze",
            "screen_capture",
            "screen_describe_via_vfb",
        ])

    def test_screen_capture_hermetic(self):
        result = self.registry.execute("screen_capture", {})
        self.assertIn('"ok": true', result)
        self.assertIn("/tmp/hermetic-test-shot.png", result)

    def test_screen_analyze_hermetic(self):
        result = self.registry.execute("screen_analyze", {"question": "what is here?"})
        self.assertIn("hermetic vision description", result)

    def test_computer_control_hermetic(self):
        result = self.registry.execute("computer_control", {
            "actions": [{"action": "move", "x": 10, "y": 10}]
        })
        self.assertIn('"ok": true', result)

    def test_clipboard_read_hermetic(self):
        result = self.registry.execute("computer_clipboard", {"op": "read"})
        self.assertIn("hermetic-clip", result)

    def test_clipboard_write_hermetic(self):
        result = self.registry.execute("computer_clipboard", {"op": "write", "text": "test"})
        self.assertIn('"ok": true', result)


class TestModeGating(unittest.TestCase):
    """Computer tools respect mode gating."""

    def test_screen_capture_allowed_in_plan(self):
        self.assertTrue(is_tool_allowed_in_mode("screen_capture", Mode.PLAN))

    def test_screen_analyze_allowed_in_plan(self):
        self.assertTrue(is_tool_allowed_in_mode("screen_analyze", Mode.PLAN))

    def test_computer_control_blocked_in_plan(self):
        self.assertFalse(is_tool_allowed_in_mode("computer_control", Mode.PLAN))

    def test_computer_control_allowed_in_build(self):
        self.assertTrue(is_tool_allowed_in_mode("computer_control", Mode.BUILD))

    def test_computer_control_allowed_in_super(self):
        self.assertTrue(is_tool_allowed_in_mode("computer_control", Mode.SUPER))


class TestPermissionGating(unittest.TestCase):
    """Computer tools route through PermissionManager."""

    def test_screen_read_auto_approved(self):
        """Read-only tools should be auto-approved."""
        pm = PermissionManager()
        self.assertTrue(pm.check_permission("screen_read", {"tool": "screen_capture"}))

    def test_computer_input_requires_approval_default(self):
        """computer_input should require approval under DEFAULT."""
        pm = PermissionManager()
        pm.interactive_deny = True  # simulate non-interactive
        self.assertFalse(pm.check_permission("computer_input", {
            "tool": "computer_control",
            "summary": "click at 10,10",
        }))

    def test_computer_input_auto_approved_full(self):
        """computer_input auto-approved under FULL."""
        from harness.core.permissions import PermissionLevel
        pm = PermissionManager(level=PermissionLevel.FULL)
        self.assertTrue(pm.check_permission("computer_input", {
            "tool": "computer_control",
            "summary": "click at 10,10",
        }))


class TestRegistrationFunction(unittest.TestCase):
    """The register_computer_tools function works correctly."""

    def test_register_with_controller(self):
        r = ToolRegistry()
        self.assertEqual(len([t for t in r.list_tools() if t.name.startswith(("screen_", "computer_"))]), 0)
        register_computer_tools(r, controller=FakeController(), vision_describe=fake_vision_describe)
        names = sorted(t.name for t in r.list_tools() if t.name.startswith(("screen_", "computer_")))
        self.assertEqual(len(names), 5)

    def test_register_without_controller(self):
        """Registration with controller=None still works (tools use default)."""
        r = ToolRegistry()
        register_computer_tools(r, controller=None, vision_describe=fake_vision_describe)
        names = [t.name for t in r.list_tools() if t.name.startswith(("screen_", "computer_"))]
        self.assertEqual(len(names), 5)


class TestControllerHermetic(unittest.TestCase):
    """ComputerController hermetic: probe/capture never raise."""

    def test_probe_hermetic(self):
        c = ComputerController()
        result = c.probe()
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)

    def test_capture_hermetic(self):
        c = ComputerController()
        result = c.capture()
        self.assertIsInstance(result, dict)
        # Without a real display, capture returns ok=False with hints
        self.assertIn("ok", result)
