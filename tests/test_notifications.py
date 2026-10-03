"""
Tests for cross-platform end-of-turn desktop notifications.

The agent fires an OS notification when a turn finishes (text answer or
explicit `finish` tool) and when it stops because of an error (fatal
provider error, input-security gate). User interrupts stay silent — the
user is already at the keyboard.

All OS interaction is mocked; the hermetic test sandbox additionally
blocks real dispatch as a safety net.
"""
import unittest
from unittest import mock

from harness.config import HarnessConfig
from harness.core import notifications as notif
from harness.core.agent import HarnessAgent
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.providers.mock_provider import MockProvider


class ScriptedProvider(MockProvider):
    """Replays one per-call script (chunk list or exception), then plain stop."""

    def __init__(self, script):
        super().__init__(responses=[])
        self.script = list(script)
        self.call_count = 0

    def stream_chat(self, messages, model=None, **kwargs):
        self.call_history.append({"messages": list(messages), "model": model})
        self.call_count += 1
        idx = self.call_count - 1
        if idx < len(self.script):
            item = self.script[idx]
            if isinstance(item, Exception):
                raise item
            for chunk in item:
                yield chunk
        else:
            yield LLMChunk(delta_text="done.", finish_reason="stop")


def _agent(provider=None):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.learning_enabled = False
    cfg.provider_retry_base_delay = 0.01  # keep retry backoff instant in tests
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    if provider is not None:
        agent.provider = provider
    return agent


class TestNotificationDispatch(unittest.TestCase):

    def test_macos_args_quote_and_choose_sound(self):
        args = notif._macos_args('Harness: "Task"', 'He said \\"hi\\"', urgent=False)
        self.assertEqual(args[0], "osascript")
        script = args[2]
        self.assertIn('with title "Harness: \\"Task\\""', script)
        self.assertIn('sound name "Glass"', script)
        self.assertNotIn('"Basso"', script)

        urgent_args = notif._macos_args("T", "M", urgent=True)
        self.assertIn('"Basso"', urgent_args[2])

    def test_notify_duration_is_five_seconds(self):
        self.assertEqual(notif._NOTIFY_DURATION, 5)

    def test_linux_args_use_notify_send_and_urgency(self):
        args = notif._linux_args("Title", "Message", urgent=True)
        self.assertEqual(args[0], "notify-send")
        self.assertIn("--app-name", args)
        self.assertIn("Harness", args)
        self.assertIn("critical", args)

    def test_linux_args_pin_expire_time_to_five_seconds(self):
        args = notif._linux_args("Title", "Message", urgent=False)
        self.assertIn("--expire-time", args)
        expire = args[args.index("--expire-time") + 1]
        self.assertEqual(int(expire), notif._NOTIFY_DURATION * 1000)
        self.assertEqual(expire, "5000")

    def test_windows_args_build_balloon_tip_script(self):
        args = notif._windows_args("It's done", "a 'quoted' thing", urgent=False)
        joined = " ".join(args)
        self.assertIn("powershell", joined.lower())
        self.assertIn("NotifyIcon", joined)
        self.assertIn("It''s done", joined)  # single quotes doubled for PS
        self.assertIn("a ''quoted'' thing", joined)
        self.assertIn("Asterisk", joined)
        self.assertIn("ShowBalloonTip(5000)", joined)  # stays on screen 5s

    def test_windows_args_urgent_uses_error_icon_and_sound(self):
        args = notif._windows_args("T", "M", urgent=True)
        joined = " ".join(args)
        self.assertIn("Error", joined)
        self.assertIn("Exclamation", joined)

    def test_send_notification_falls_back_to_bell(self):
        with mock.patch.object(notif.sys, "platform", "linux"), \
             mock.patch.object(notif.shutil, "which", return_value=None), \
             mock.patch.object(notif, "_bell", return_value=True) as bell, \
             mock.patch.object(notif, "_in_test_sandbox", return_value=False):
            ok = notif.send_notification("Title", "Body")
        self.assertTrue(ok)
        bell.assert_called_once()

    def test_send_notification_suppressed_in_test_sandbox(self):
        with mock.patch.object(notif, "_in_test_sandbox", return_value=True), \
             mock.patch.object(notif, "_bell") as bell:
            ok = notif.send_notification("Title", "Body")
        self.assertFalse(ok)
        bell.assert_not_called()

    def test_send_notification_never_raises(self):
        with mock.patch.object(notif, "_in_test_sandbox", return_value=False), \
             mock.patch.object(notif.sys, "platform", "linux"), \
             mock.patch.object(notif.shutil, "which", return_value="/usr/bin/notify-send"), \
             mock.patch.object(notif.subprocess, "run", side_effect=OSError("boom")), \
             mock.patch.object(notif, "_bell", return_value=False):
            self.assertFalse(notif.send_notification("T", "M"))

    def test_truncate_long_messages(self):
        long = "word " * 200
        out = notif._truncate(long)
        self.assertLessEqual(len(out), notif._MAX_MESSAGE)
        self.assertTrue(out.endswith("..."))

    def test_notify_task_complete_and_failed_disabled(self):
        with mock.patch.object(notif, "send_notification") as sn:
            self.assertFalse(notif.notify_task_complete("s", enabled=False))
            self.assertFalse(notif.notify_task_failed("e", enabled=False))
        sn.assert_not_called()

    def test_notify_task_complete_and_failed_pass_through(self):
        with mock.patch.object(notif, "_in_test_sandbox", return_value=False), \
             mock.patch.object(notif, "send_notification", return_value=True) as sn:
            notif.notify_task_complete("All good")
            notif.notify_task_failed("Bad thing")
        self.assertEqual(sn.call_count, 2)
        self.assertIn("Task Complete", sn.call_args_list[0][0][0])
        self.assertIn("Task Failed", sn.call_args_list[1][0][0])
        self.assertTrue(sn.call_args_list[1][1]["urgent"])


class TestAgentTurnNotifications(unittest.TestCase):

    def _patch_send(self, agent):
        # Route the agent's notification through a mock; keep sandbox guard.
        return mock.patch("harness.core.notifications.send_notification")

    def test_text_answer_turn_notifies_success(self):
        provider = ScriptedProvider([[LLMChunk(delta_text="all done here"), LLMChunk(finish_reason="stop")]])
        agent = _agent(provider)
        with mock.patch("harness.core.notifications.send_notification") as sn:
            events = list(agent.step("do the thing"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        sn.assert_called_once()
        args, kwargs = sn.call_args
        self.assertIn("Task Complete", args[0])
        self.assertIn("all done here", args[1])

    def test_finish_tool_turn_notifies_success_with_summary(self):
        provider = ScriptedProvider([[
            LLMChunk(tool_calls=[ToolCallDelta(index=0, id="c1", name="finish",
                                              arguments_delta='{"summary": "Shipped the feature"}')]),
        ]])
        agent = _agent(provider)
        with mock.patch("harness.core.notifications.send_notification") as sn:
            events = list(agent.step("ship it"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        sn.assert_called_once()
        args, kwargs = sn.call_args
        self.assertIn("Shipped the feature", args[1])
        self.assertFalse(kwargs.get("urgent", False))

    def test_fatal_provider_error_notifies_failure(self):
        # Four attempts, all transient 503s: 1 initial + 3 retries (the default
        # provider_max_retries), then the retry budget is exhausted and the
        # empty-with-error response ends the turn with an error notification.
        provider = ScriptedProvider([
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
        ])
        agent = _agent(provider)
        with mock.patch("harness.core.notifications.send_notification") as sn:
            events = list(agent.step("hello"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        sn.assert_called_once()
        args, kwargs = sn.call_args
        self.assertIn("Task Failed", args[0])
        self.assertTrue(kwargs.get("urgent", False))
        self.assertIn("503", args[1])

    def test_config_disable_stops_notification(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.notifications_enabled = False
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.provider = ScriptedProvider([[LLMChunk(delta_text="quiet"), LLMChunk(finish_reason="stop")]])
        with mock.patch("harness.core.notifications.send_notification") as sn:
            events = list(agent.step("quiet run"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        sn.assert_not_called()

    def test_interrupted_turn_does_not_notify(self):
        provider = ScriptedProvider([
            [LLMChunk(delta_reasoning="thinking forever and ever")],
        ])
        agent = _agent(provider)
        with mock.patch("harness.core.notifications.send_notification") as sn:
            events = []
            gen = agent.step("long task")
            next(gen)  # step_start / early events
            agent.request_stop()
            for ev in gen:
                events.append(ev)
        self.assertTrue(any(e.type == "turn_complete" for e in events))
        sn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
