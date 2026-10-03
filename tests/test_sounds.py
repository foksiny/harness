"""
Tests for end-of-turn completion sound effects.

The agent plays a synthesized chime when a turn succeeds and a low minor
fall when it errors. Audio is synthesized in-process (stdlib `wave` + `math`)
and dispatched to a detached player process, so every OS interaction is mocked
and the hermetic test sandbox blocks real playback as a safety net.
"""
import os
import unittest
import wave
from unittest import mock

from harness.config import HarnessConfig
from harness.core import sounds
from harness.core.agent import HarnessAgent
from harness.providers.base import LLMChunk
from harness.providers.mock_provider import MockProvider


class ScriptedProvider(MockProvider):
    """Replays one per-call script, then a plain stop."""

    def __init__(self, script):
        super().__init__(responses=[])
        self.script = list(script)
        self.call_count = 0

    def stream_chat(self, messages, model=None, **kwargs):
        self.call_history.append({"messages": list(messages), "model": model})
        self.call_count += 1
        idx = self.call_count - 1
        if idx < len(self.script):
            for chunk in self.script[idx]:
                yield chunk
        else:
            yield LLMChunk(delta_text="done.", finish_reason="stop")


def _agent(provider=None):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.learning_enabled = False
    cfg.provider_retry_base_delay = 0.01
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    if provider is not None:
        agent.provider = provider
    return agent


class TestSynthesis(unittest.TestCase):

    def test_synthesize_produces_valid_mono_16bit_wav(self):
        pcm = sounds.synthesize(sounds.TONES["complete"])
        self.assertGreater(len(pcm), 1000)
        self.assertTrue(all(-32768 <= v <= 32767 for v in pcm))

        with mock.patch.object(sounds, "_temp_dir", return_value=os.path.dirname(__file__)), \
             mock.patch.dict(sounds._CACHE, clear=True):
            path = sounds.tone_path("complete")
        try:
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
            with wave.open(path, "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), sounds._RATE)
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    def test_success_tone_ascends_and_error_tone_is_lower(self):
        success_freqs = sorted(f for _s, f, _d in sounds.TONES["complete"])
        error_freqs = sorted(f for _s, f, _d in sounds.TONES["error"])
        self.assertEqual(success_freqs, sorted(success_freqs))
        self.assertGreater(success_freqs[0], error_freqs[-1])  # chime sits above the fall

    def test_tone_path_caches_between_calls(self):
        with mock.patch.object(sounds, "_temp_dir", return_value=os.path.dirname(__file__)), \
             mock.patch.dict(sounds._CACHE, clear=True):
            first = sounds.tone_path("complete")
            second = sounds.tone_path("complete")
            # Unknown tone names degrade to the success chime rather than raising.
            fallback = sounds.tone_path("nonsense")
        try:
            self.assertEqual(first, second)
            self.assertEqual(fallback, first)
        finally:
            for p in {first, fallback}:
                if p and os.path.exists(p):
                    os.unlink(p)

    def test_tone_path_returns_none_when_temp_dir_unavailable(self):
        with mock.patch.object(sounds, "_temp_dir", return_value=None), \
             mock.patch.dict(sounds._CACHE, clear=True):
            self.assertIsNone(sounds.tone_path("complete"))


class TestPlayerDispatch(unittest.TestCase):

    def test_player_argv_prefers_pulse_then_pipewire_then_alsa(self):
        with mock.patch.object(sounds.sys, "platform", "linux"), \
             mock.patch.object(sounds.shutil, "which", side_effect=lambda e: e == "pw-play"):
            self.assertEqual(sounds._player_argv(), ["pw-play"])
        with mock.patch.object(sounds.sys, "platform", "linux"), \
             mock.patch.object(sounds.shutil, "which", side_effect=lambda e: e == "aplay"):
            self.assertEqual(sounds._player_argv(), ["aplay"])
        with mock.patch.object(sounds.sys, "platform", "linux"), \
             mock.patch.object(sounds.shutil, "which", return_value=None):
            self.assertIsNone(sounds._player_argv())

    def test_macos_and_windows_player_argv(self):
        with mock.patch.object(sounds.sys, "platform", "darwin"), \
             mock.patch.object(sounds.shutil, "which", return_value="/usr/bin/afplay"):
            self.assertEqual(sounds._player_argv(), ["afplay"])
        with mock.patch.object(sounds.sys, "platform", "win32"), \
             mock.patch.object(sounds.shutil, "which", return_value="C:\\powershell.exe"):
            argv = sounds._player_argv()
            self.assertIn("-NoProfile", argv)
            self.assertIn("-Command", argv)

    def test_windows_play_script_uses_soundplayer(self):
        script = sounds._windows_play_script("/tmp/harness-complete.wav")
        self.assertIn("System.Media.SoundPlayer", script)
        self.assertIn("/tmp/harness-complete.wav", script)
        self.assertIn(".Play()", script)

    def test_play_spawns_player_detached(self):
        with mock.patch.object(sounds, "_in_test_sandbox", return_value=False), \
             mock.patch.object(sounds, "tone_path", return_value="/tmp/x.wav"), \
             mock.patch.object(sounds, "_player_argv", return_value=["aplay"]), \
             mock.patch.object(sounds.sys, "platform", "linux"), \
             mock.patch.object(sounds.subprocess, "Popen") as popen:
            self.assertTrue(sounds.play("complete"))
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], ["aplay", "/tmp/x.wav"])

    def test_play_falls_back_to_bell_without_player(self):
        with mock.patch.object(sounds, "_in_test_sandbox", return_value=False), \
             mock.patch.object(sounds, "_player_argv", return_value=None), \
             mock.patch.object(sounds, "_bell", return_value=True) as bell:
            self.assertTrue(sounds.play("complete"))
        bell.assert_called_once()

    def test_play_suppressed_in_test_sandbox(self):
        with mock.patch.object(sounds, "_in_test_sandbox", return_value=True), \
             mock.patch.object(sounds.subprocess, "Popen") as popen, \
             mock.patch.object(sounds, "_bell") as bell:
            self.assertFalse(sounds.play("complete"))
        popen.assert_not_called()
        bell.assert_not_called()

    def test_play_never_raises(self):
        with mock.patch.object(sounds, "_in_test_sandbox", return_value=False), \
             mock.patch.object(sounds, "tone_path", side_effect=RuntimeError("boom")), \
             mock.patch.object(sounds, "_bell", return_value=False):
            self.assertFalse(sounds.play("complete"))

    def test_play_disabled_is_noop(self):
        with mock.patch.object(sounds, "_in_test_sandbox", return_value=False), \
             mock.patch.object(sounds, "_bell") as bell:
            self.assertFalse(sounds.play("complete", enabled=False))
        bell.assert_not_called()


class TestAgentTurnSounds(unittest.TestCase):

    def test_success_turn_plays_chime(self):
        provider = ScriptedProvider([[LLMChunk(delta_text="all done"), LLMChunk(finish_reason="stop")]])
        agent = _agent(provider)
        with mock.patch("harness.core.sounds.play") as play:
            list(agent.step("do the thing"))
        play.assert_called_once_with("complete", enabled=True)

    def test_error_turn_plays_error_tone(self):
        provider = ScriptedProvider([
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
            [LLMChunk(delta_text="\n[HTTP Error 503: overloaded]\n", finish_reason="error")],
        ])
        agent = _agent(provider)
        with mock.patch("harness.core.sounds.play") as play:
            list(agent.step("hello"))
        play.assert_called_once_with("error", enabled=True)

    def test_config_disable_stops_sound(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.sound_effects_enabled = False
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.provider = ScriptedProvider([[LLMChunk(delta_text="quiet"), LLMChunk(finish_reason="stop")]])
        with mock.patch("harness.core.sounds.play") as play:
            list(agent.step("quiet run"))
        play.assert_not_called()

    def test_interrupted_turn_does_not_play(self):
        provider = ScriptedProvider([[LLMChunk(delta_reasoning="thinking forever and ever")]])
        agent = _agent(provider)
        with mock.patch("harness.core.sounds.play") as play:
            gen = agent.step("long task")
            next(gen)
            agent.request_stop()
            list(gen)
        play.assert_not_called()

    def test_sound_flag_independent_from_notifications(self):
        # Turning off the desktop toast must NOT mute the chime.
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.notifications_enabled = False
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.provider = ScriptedProvider([[LLMChunk(delta_text="hi"), LLMChunk(finish_reason="stop")]])
        with mock.patch("harness.core.notifications.send_notification") as sn, \
             mock.patch("harness.core.sounds.play") as play:
            list(agent.step("toastless but audible"))
        sn.assert_not_called()
        play.assert_called_once_with("complete", enabled=True)


if __name__ == "__main__":
    unittest.main()