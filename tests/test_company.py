"""Hermetic /company tests: no display, no LLM, no keyboard.

CompanyController seams (step/render/announce/sleep) are faked, the stop
file lives in a temp dir, and registry tests patch CompanyController.
"""
import os
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import harness.config as config_mod
from harness.commands.registry import CommandRegistry
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent


class DummyRenderer:
    def __init__(self):
        self.messages = []

    def print_success(self, msg: str):
        self.messages.append(("success", msg))

    def print_warning(self, msg: str):
        self.messages.append(("warning", msg))

    def print_info(self, msg: str):
        self.messages.append(("info", msg))

    def print_error(self, msg: str):
        self.messages.append(("error", msg))

    def render_agent_event(self, ev):
        self.messages.append(("event", str(getattr(ev, "type", ev))))

    def finish_markdown(self):
        self.messages.append(("finish_markdown", ""))

    def finish_thinking(self):
        self.messages.append(("finish_thinking", ""))


class TestCompanyHelpers(unittest.TestCase):
    def test_tick_prompt_carries_role_and_quiet_default(self):
        from harness.company.controller import build_tick_prompt
        prompt = build_tick_prompt("side-chat on Discord", 3)
        self.assertIn("side-chat on Discord", prompt)
        self.assertIn("#3", prompt)
        self.assertIn("Default to silence", prompt)
        quiet = build_tick_prompt("role", 9, last_speech="hi", quiet_streak=0)
        self.assertIn("Do not repeat yourself", quiet)
        nudge = build_tick_prompt("role", 9, quiet_streak=4)
        self.assertIn("stayed quiet for 4 beats", nudge)

    def test_voice_is_opt_in(self):
        from harness.company.controller import voice_requested
        self.assertTrue(voice_requested("speak out loud"))
        self.assertFalse(voice_requested("chat on Discord"))

    def test_parse_evdev_chord(self):
        from harness.company.controller import parse_evdev_events

        def ev(code, value):
            return struct.pack("<qqHHi", 0, 0, 0x01, code, value)

        # Super(L) + Shift(L) down, Esc down -> one hit.
        blob = ev(125, 1) + ev(42, 1) + ev(1, 1)
        self.assertEqual(parse_evdev_events(blob), [True])
        # Esc alone is not the chord.
        self.assertEqual(parse_evdev_events(ev(1, 1)), [])
        # Right-side modifiers count too.
        blob2 = ev(126, 1) + ev(54, 1) + ev(1, 1)
        self.assertEqual(parse_evdev_events(blob2), [True])
        # A release then Esc does not.
        blob3 = ev(125, 1) + ev(125, 0) + ev(42, 1) + ev(1, 1)
        self.assertEqual(parse_evdev_events(blob3), [])
        # Garbage buffers never raise.
        self.assertEqual(parse_evdev_events(b"short"), [])
        self.assertEqual(parse_evdev_events(b""), [])


class TestCompanyController(unittest.TestCase):
    def _controller(self, tmp, ticks=3, role="test companion"):
        from harness.company.controller import CompanyController
        prompts = []
        rendered = []
        announced = []

        def fake_step(prompt):
            prompts.append(prompt)
            yield SimpleNamespace(type="text_delta", data="hello from tick")

        class _Finish:
            pass

        ctl = CompanyController(
            step_fn=fake_step,
            render_fn=rendered.append,
            announce_fn=announced.append,
            speak_fn=lambda t: False,
            tick_interval=0.1,
            stop_path=str(Path(tmp) / "company.stop"),
        )

        def sleep_fn(seconds, stop_event):
            # Fast quiet period: one short tick sleep, then stop after
            # ``ticks`` beats by pre-setting the event from the tick side.
            if ctl.tick_no >= ticks:
                ctl.stop("test stop")
            else:
                stop_event.wait(timeout=0.01)

        ctl._sleep_fn = sleep_fn
        return ctl, prompts, rendered, announced

    def test_loop_runs_takes_beats_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl, prompts, rendered, announced = self._controller(tmp, ticks=3)
            self.assertTrue(ctl.start("test companion"))
            ctl._thread.join(timeout=5)
            self.assertFalse(ctl.is_running)
            self.assertEqual(len(prompts), 3)
            self.assertEqual(ctl.tick_no, 3)
            self.assertTrue(any("beat #1" in p for p in prompts))
            self.assertTrue(any("Company stopped" in a for a in announced))

    def test_double_start_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl, _p, _r, _a = self._controller(tmp, ticks=99)
            self.assertTrue(ctl.start("first"))
            self.assertFalse(ctl.start("second is a duplicate"))
            ctl.stop("join me")
            ctl._thread.join(timeout=5)
            self.assertFalse(ctl.is_running)

    def test_stop_file_triggers_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl, prompts, _r, announced = self._controller(tmp, ticks=99)
            stop_file = Path(tmp) / "company.stop"
            self.assertTrue(ctl.start("watcher test"))
            stop_file.touch()
            ctl._thread.join(timeout=5)
            self.assertFalse(ctl.is_running)
            self.assertTrue(any("stop-file" in a for a in announced))

    def test_speech_resets_quiet_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl, _p, _r, _a = self._controller(tmp, ticks=2)
            ctl.quiet_streak = 5
            self.assertTrue(ctl.start("quiet test"))
            ctl._thread.join(timeout=5)
            # fake_step always speaks "hello from tick"
            self.assertEqual(ctl.quiet_streak, 0)

    def test_status_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl, _p, _r, _a = self._controller(tmp)
            st = ctl.status()
            self.assertIn("running", st)
            self.assertIn("role", st)
            self.assertIn("stop_file", st)
            self.assertIn("evdev_files", st)


class TestCompanyCommand(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_user = config_mod.USER_CONFIG_PATH
        self._orig_ws = config_mod.WORKSPACE_CONFIG_PATH
        config_mod.USER_CONFIG_PATH = Path(self._tmp_dir.name) / "config.json"
        config_mod.WORKSPACE_CONFIG_PATH = Path(self._tmp_dir.name) / "ws" / "config.json"
        self.cfg = HarnessConfig()
        self.cfg.provider = "mock"
        self.agent = HarnessAgent(self.cfg)
        self.renderer = DummyRenderer()
        self.registry = CommandRegistry()

    def tearDown(self):
        config_mod.USER_CONFIG_PATH = self._orig_user
        config_mod.WORKSPACE_CONFIG_PATH = self._orig_ws
        try:
            company = getattr(self.agent, "_company_controller", None)
            if company is not None and hasattr(company, "stop"):
                company.stop("test teardown")
        except Exception:
            pass
        self._tmp_dir.cleanup()

    def _fake_controller(self, started):
        fake = unittest.mock.Mock()
        fake.is_running = False
        started_flag = {"running": False}
        def _start(role):
            started.append(role)
            started_flag["running"] = True
            fake.is_running = True
            return True
        fake.start = _start
        fake.role = ""
        fake.stop = unittest.mock.Mock()
        fake.status = lambda: {
            "running": True, "role": "fake role", "tick": 1,
            "interval": 25.0, "quiet_streak": 0,
            "stop_file": "~/.harness/company.stop", "evdev_files": [],
            "stopped_reason": "",
        }
        return fake

    def test_company_starts_with_role(self):
        started = []
        with patch("harness.company.controller.CompanyController",
                   side_effect=lambda *a, **k: self._fake_controller(started)):
            self.registry.handle("/company chatting on Discord", self.agent, self.renderer)
        self.assertEqual(started, ["chatting on Discord"])
        successes = [m for tag, m in self.renderer.messages if tag == "success"]
        self.assertTrue(any("Company started" in m for m in successes))

    def test_company_bare_uses_default_role(self):
        from harness.company.controller import DEFAULT_ROLE
        started = []
        with patch("harness.company.controller.CompanyController",
                   side_effect=lambda *a, **k: self._fake_controller(started)):
            self.registry.handle("/company", self.agent, self.renderer)
        self.assertEqual(started, [DEFAULT_ROLE])

    def test_company_role_containing_stop_word_is_a_role(self):
        started = []
        with patch("harness.company.controller.CompanyController",
                   side_effect=lambda *a, **k: self._fake_controller(started)):
            self.registry.handle("/company stop chatting in Discord", self.agent, self.renderer)
        self.assertEqual(started, ["stop chatting in Discord"])

    def test_company_stop_status(self):
        fake = self._fake_controller([])
        fake.is_running = True
        self.agent._company_controller = fake
        self.registry.handle("/company status", self.agent, self.renderer)
        infos = [m for tag, m in self.renderer.messages if tag == "info"]
        self.assertTrue(any("Company RUNNING" in m for m in infos))
        self.registry.handle("/company stop", self.agent, self.renderer)
        fake.stop.assert_called()

    def test_stop_command_stops_company(self):
        fake = self._fake_controller([])
        fake.is_running = True
        self.agent._company_controller = fake
        self.agent.is_running = False
        self.registry.handle("/stop", self.agent, self.renderer)
        fake.stop.assert_called()

    def test_input_handler_lists_company(self):
        from harness.tui.input_handler import SLASH_COMMANDS, COMMAND_DESCRIPTIONS
        self.assertIn("/company", SLASH_COMMANDS)
        self.assertIn("/company", COMMAND_DESCRIPTIONS)

    def test_turn_lock_serializes_steps(self):
        import threading
        import time
        current = 0
        peak = 0
        lock = threading.Lock()

        def fake_inner(prompt):
            nonlocal current, peak
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with lock:
                current -= 1
            yield SimpleNamespace(type="text_delta", data="x")

        self.agent._step_inner = fake_inner
        threads = [threading.Thread(target=lambda: list(self.agent.step("x")))
                   for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
