"""
Tests for the /temperature command: parsing, persistence, provider plumbing,
CLI↔Discord state sync, and end-to-end propagation to the LLM request body.
"""
import json
import tempfile
import unittest
from pathlib import Path

import harness.config as config_mod
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.commands.registry import (
    DEFAULT_TEMPERATURE,
    CommandRegistry,
    parse_temperature_arg,
)
from harness.providers.base import BaseProvider
from harness.tui.input_handler import SLASH_COMMANDS, COMMAND_DESCRIPTIONS
from harness.tui.terminal import TerminalRenderer


# Dummy credential for provider constructors (never leaves the test process).
_TEST_KEY = {"api_key": "unit-test-credential"}


class DummyRenderer(TerminalRenderer):
    def __init__(self):
        super().__init__("cyberpunk")
        self.messages = []

    def print_success(self, msg: str):
        self.messages.append(("success", msg))

    def print_info(self, msg: str):
        self.messages.append(("info", msg))

    def print_error(self, msg: str):
        self.messages.append(("error", msg))

    def print_warning(self, msg: str):
        self.messages.append(("warning", msg))

    def print_markdown(self, md: str):
        self.messages.append(("markdown", md))


class TestTemperatureParsing(unittest.TestCase):
    def test_plain_range_values(self):
        self.assertEqual(parse_temperature_arg("0"), 0.0)
        self.assertEqual(parse_temperature_arg("0.0"), 0.0)
        self.assertEqual(parse_temperature_arg("0.35"), 0.35)
        self.assertEqual(parse_temperature_arg("1"), 1.0)
        self.assertEqual(parse_temperature_arg("1.0"), 1.0)
        self.assertEqual(parse_temperature_arg(" 0.7 "), 0.7)

    def test_percentage_shorthand(self):
        self.assertEqual(parse_temperature_arg("50%"), 0.5)
        self.assertEqual(parse_temperature_arg("50"), 0.5)
        self.assertEqual(parse_temperature_arg("20"), 0.2)
        self.assertEqual(parse_temperature_arg("100"), 1.0)

    def test_reset_keywords(self):
        for kw in ("default", "DEFAULT", "reset", "auto", "off", "none"):
            self.assertEqual(parse_temperature_arg(kw), DEFAULT_TEMPERATURE)

    def test_invalid_inputs_rejected(self):
        for bad in ("", "abc", "-0.5", "1.5", "150", "nan", "inf", "0.5.5"):
            self.assertIsNone(parse_temperature_arg(bad), bad)

    def test_default_temperature_is_one(self):
        self.assertEqual(DEFAULT_TEMPERATURE, 1.0)
        self.assertEqual(HarnessConfig().temperature, 1.0)


class TestClampTemperature(unittest.TestCase):
    def test_clamps_into_range(self):
        self.assertEqual(BaseProvider.clamp_temperature(0.4), 0.4)
        self.assertEqual(BaseProvider.clamp_temperature(-3), 0.0)
        self.assertEqual(BaseProvider.clamp_temperature(9), 1.0)
        self.assertEqual(BaseProvider.clamp_temperature("0.25"), 0.25)

    def test_unset_or_bogus_yields_none(self):
        self.assertIsNone(BaseProvider.clamp_temperature(None))
        self.assertIsNone(BaseProvider.clamp_temperature(""))
        self.assertIsNone(BaseProvider.clamp_temperature("hot"))
        self.assertIsNone(BaseProvider.clamp_temperature(True))
        self.assertIsNone(BaseProvider.clamp_temperature(float("nan")))


class _FakeRelay:
    def __init__(self, sink):
        self.sink = sink

    def publish_state(self, payload, origin=None):
        self.sink.append((dict(payload), origin))

    def relay_output(self, text, origin=None):
        pass


class TestTemperatureCommand(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_user_path = config_mod.USER_CONFIG_PATH
        self._orig_workspace_path = config_mod.WORKSPACE_CONFIG_PATH
        config_mod.USER_CONFIG_PATH = Path(self._tmp_dir.name) / "config.json"
        config_mod.WORKSPACE_CONFIG_PATH = Path(self._tmp_dir.name) / "ws" / "config.json"
        self.cfg = HarnessConfig()
        self.cfg.provider = "mock"
        self.agent = HarnessAgent(self.cfg)
        self.renderer = DummyRenderer()
        self.registry = CommandRegistry()

    def tearDown(self):
        config_mod.USER_CONFIG_PATH = self._orig_user_path
        config_mod.WORKSPACE_CONFIG_PATH = self._orig_workspace_path
        self._tmp_dir.cleanup()

    def _saved_config_json(self):
        path = config_mod.USER_CONFIG_PATH
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _last(self, level):
        for kind, msg in reversed(self.renderer.messages):
            if kind == level:
                return msg
        return ""

    def test_registered(self):
        self.assertIn("temperature", self.registry.commands)
        self.assertIn("/temperature", SLASH_COMMANDS)
        self.assertIn("/temperature", COMMAND_DESCRIPTIONS)

    def test_show_without_args(self):
        self.cfg.temperature = 0.4
        self.registry.handle("/temperature", self.agent, self.renderer)
        self.assertIn("0.40", self._last("info"))

    def test_set_persists_to_config(self):
        self.registry.handle("/temperature 0.3", self.agent, self.renderer)
        self.assertAlmostEqual(self.agent.config.temperature, 0.3, places=6)
        self.assertEqual(self._saved_config_json()["temperature"], 0.3)

    def test_set_zero_and_one(self):
        self.registry.handle("/temperature 0.0", self.agent, self.renderer)
        self.assertEqual(self.agent.config.temperature, 0.0)
        self.registry.handle("/temperature 1.0", self.agent, self.renderer)
        self.assertEqual(self.agent.config.temperature, 1.0)

    def test_reset_keyword(self):
        self.cfg.temperature = 0.1
        self.registry.handle("/temperature default", self.agent, self.renderer)
        self.assertEqual(self.agent.config.temperature, DEFAULT_TEMPERATURE)

    def test_invalid_value_rejected_and_config_untouched(self):
        self.cfg.temperature = 0.7
        self.registry.handle("/temperature 1.5", self.agent, self.renderer)
        self.assertEqual(self.agent.config.temperature, 0.7)
        self.assertIn("Invalid temperature", self._last("error"))

    def test_extra_words_are_ignored(self):
        self.registry.handle("/temperature 0.25 please", self.agent, self.renderer)
        self.assertAlmostEqual(self.agent.config.temperature, 0.25, places=6)

    def test_state_is_published_for_discord_sync(self):
        published = []
        relay = _FakeRelay(published)
        import harness.discord.sync as sync_mod

        orig = sync_mod._global_relay
        sync_mod._global_relay = relay
        try:
            self.registry.handle("/temperature 0.2", self.agent, self.renderer)
        finally:
            sync_mod._global_relay = orig
        self.assertTrue(any(
            p.get("temperature") == 0.2 and origin == "cli" for p, origin in published
        ))


class _FakeStream:
    def __enter__(self):
        return ["data: [DONE]\n"]

    def __exit__(self, *a):
        return False


class TestTemperatureProviderPlumbing(unittest.TestCase):
    def test_mock_provider_records_temperature(self):
        from harness.providers.mock_provider import MockProvider
        prov = MockProvider()
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=0.25))
        self.assertEqual(prov.call_history[-1]["temperature"], 0.25)

    def test_agent_forwards_configured_temperature(self):
        """The agent must forward config.temperature into every main turn request."""
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.temperature = 0.15
        agent = HarnessAgent(cfg)
        events = list(agent.step("hello"))
        self.assertTrue(events)  # sanity: the generator produced a turn
        self.assertEqual(agent.provider.call_history[-1]["temperature"], 0.15)

    def test_openai_compatible_body_includes_temperature(self):
        from harness.providers.openai_compatible import OpenAICompatibleProvider
        captured = {}

        class _Prov(OpenAICompatibleProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(
            name="openai", display_name="OpenAI", default_model="gpt-4o",
            api_key="x", base_url="https://api.example.com/v1",
        )
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=0.0))
        self.assertEqual(captured["body"].get("temperature"), 0.0)

    def test_openai_compatible_omits_unset_temperature(self):
        from harness.providers.openai_compatible import OpenAICompatibleProvider
        captured = {}

        class _Prov(OpenAICompatibleProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(
            name="openai", display_name="OpenAI", default_model="gpt-4o",
            api_key="x", base_url="https://api.example.com/v1",
        )
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}]))
        self.assertNotIn("temperature", captured["body"])

    def test_openai_compatible_clamps_out_of_range(self):
        from harness.providers.openai_compatible import OpenAICompatibleProvider
        captured = {}

        class _Prov(OpenAICompatibleProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(
            name="openai", display_name="OpenAI", default_model="gpt-4o",
            api_key="x", base_url="https://api.example.com/v1",
        )
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=4.2))
        self.assertEqual(captured["body"].get("temperature"), 1.0)

    def test_anthropic_body_includes_temperature(self):
        from harness.providers.anthropic_provider import AnthropicProvider
        captured = {}

        class _Prov(AnthropicProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(**dict(_TEST_KEY, base_url="https://api.anthropic.com/v1"))
        list(prov.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking_effort="off", temperature=0.6,
        ))
        self.assertEqual(captured["body"].get("temperature"), 0.6)

    def test_anthropic_omits_temperature_when_thinking_enabled(self):
        """Anthropic 400s on temperature != 1 while thinking is on, so it must be omitted."""
        from harness.providers.anthropic_provider import AnthropicProvider
        captured = {}

        class _Prov(AnthropicProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(**dict(_TEST_KEY, base_url="https://api.anthropic.com/v1"))
        list(prov.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking_effort="high", temperature=0.6,
        ))
        self.assertIn("thinking", captured["body"])
        self.assertNotIn("temperature", captured["body"])

    def test_gemini_generation_config_includes_temperature(self):
        from harness.providers.gemini_provider import GeminiProvider
        captured = {}

        class _Prov(GeminiProvider):
            def _tracked_sse_stream(self, endpoint, headers, body):
                captured["body"] = dict(body)
                return _FakeStream()

        prov = _Prov(**_TEST_KEY)
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=0.2))
        self.assertEqual(captured["body"].get("generationConfig", {}).get("temperature"), 0.2)


class TestTemperatureCapabilityDetection(unittest.TestCase):
    """Models that reject a custom temperature must be detected from the model id."""

    def test_reasoning_models_reject_temperature(self):
        from harness.providers.detector import detect_temperature_support
        for model in (
            "o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o4-mini",
            "gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-5-codex", "gpt-5.2",
            "deepseek-reasoner", "deepseek-r1", "DeepSeek-R1",
            "qwq-32b", "magistral-medium",
        ):
            self.assertFalse(detect_temperature_support(model), model)

    def test_regular_models_accept_temperature(self):
        from harness.providers.detector import detect_temperature_support
        for model in (
            "gpt-4o", "gpt-4.1", "gpt-4o-mini", "claude-3-7-sonnet",
            "claude-opus-4-5", "gemini-2.5-flash", "deepseek-chat",
            "llama-3.3-70b", "mistral-large", "some-unknown-model",
        ):
            self.assertTrue(detect_temperature_support(model), model)

    def test_prefix_does_not_confuse_gpt5_with_gpt4(self):
        """The gpt-5 family pattern must not swallow gpt-4* ids."""
        from harness.providers.detector import detect_temperature_support
        self.assertTrue(detect_temperature_support("gpt-4o"))
        self.assertTrue(detect_temperature_support("gpt-40-turbo"))
        self.assertFalse(detect_temperature_support("gpt-50"))

    def test_aggregator_prefixed_ids_are_still_detected(self):
        """OpenRouter/Groq-style "vendor/model" ids resolve on the base name."""
        from harness.providers.detector import detect_temperature_support
        self.assertFalse(detect_temperature_support("openai/o3-mini"))
        self.assertFalse(detect_temperature_support("deepseek/deepseek-r1:free"))
        self.assertTrue(detect_temperature_support("openai/gpt-4o"))

    def test_spec_exposes_capability_through_inspect_model(self):
        from harness.providers.detector import inspect_model
        self.assertFalse(inspect_model("o3-mini", "openai").supports_temperature)
        self.assertTrue(inspect_model("gpt-4o", "openai").supports_temperature)

    def test_resolve_temperature_gates_on_capability(self):
        from harness.providers.detector import inspect_model
        reasoning = inspect_model("o3-mini", "openai")
        normal = inspect_model("gpt-4o", "openai")
        self.assertIsNone(BaseProvider.resolve_temperature(reasoning, 0.3))
        self.assertEqual(BaseProvider.resolve_temperature(normal, 0.3), 0.3)
        # 0.0 must still reach the wire for non-reasoning models (greedy decoding).
        self.assertEqual(BaseProvider.resolve_temperature(normal, 0.0), 0.0)
        # Unset/garbage stays unset regardless of model.
        self.assertIsNone(BaseProvider.resolve_temperature(normal, None))
        self.assertIsNone(BaseProvider.resolve_temperature(normal, "hot"))


class _FakeSSE:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return list(self.lines) + ["data: [DONE]\n"]

    def __exit__(self, *a):
        return False


class _RejectThenOk:
    """Stream factory that rejects the first attempt, then succeeds.

    Mirrors a provider which only objects while the offending parameter is still
    on the request body -- exactly the strip-and-retry contract under test.
    """

    def __init__(self, message):
        self.message = message
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == 1:
            return _FakeSSE(['data: {"error": {"message": "%s"}}\n' % self.message])
        return _FakeSSE(['data: {"choices": [{"delta": {"content": "ok"}}]}\n'])


class _AlwaysReject:
    """Stream factory that rejects every attempt."""

    def __init__(self, message):
        self.message = message

    def __call__(self):
        return _FakeSSE(['data: {"error": {"message": "%s"}}\n' % self.message])


class _OkStream:
    def __enter__(self):
        return ['data: {"choices": [{"delta": {"content": "ok"}}]}\n', "data: [DONE]\n"]

    def __exit__(self, *a):
        return False


class TestTemperatureStripAndRetry(unittest.TestCase):
    """Safety net for reasoning models the detector does not know about."""

    def _provider(self, responder):
        from harness.providers.openai_compatible import OpenAICompatibleProvider

        class _Prov(OpenAICompatibleProvider):
            def __init__(self):
                super().__init__(
                    name="openai", display_name="OpenAI", default_model="gpt-4o",
                    **_TEST_KEY, base_url="https://api.example.com/v1",
                )
                self.bodies = []

            def _tracked_sse_stream(self, endpoint, headers, body):
                self.bodies.append(dict(body))
                return responder()

        return _Prov()

    def test_retries_without_temperature_when_provider_rejects_it(self):
        msg = ("Unsupported parameter: 'temperature' is not supported with this model. "
               "Only the default (1) value is supported.")
        prov = self._provider(_RejectThenOk(msg))
        chunks = list(prov.stream_chat(
            messages=[{"role": "user", "content": "hi"}], temperature=0.4,
        ))
        self.assertEqual(len(prov.bodies), 2, "expected one strip-and-retry")
        self.assertEqual(prov.bodies[0].get("temperature"), 0.4)
        self.assertNotIn("temperature", prov.bodies[1])
        # The retried request must succeed.
        self.assertTrue(any((c.delta_text or "").strip() == "ok" for c in chunks),
                        [(c.delta_text, c.finish_reason) for c in chunks])

    def test_deepseek_wording_is_recognised(self):
        msg = "deepseek-reasoner does not support the parameter temperature"
        prov = self._provider(_RejectThenOk(msg))
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=0.4))
        self.assertEqual(len(prov.bodies), 2)
        self.assertNotIn("temperature", prov.bodies[1])

    def test_strips_only_once_then_surfaces_the_error(self):
        """A provider that never accepts the param must not loop forever."""
        msg = "Unsupported parameter: 'temperature' is not supported with this model."

        prov = self._provider(_AlwaysReject(msg))
        chunks = list(prov.stream_chat(
            messages=[{"role": "user", "content": "hi"}], temperature=0.4,
        ))
        self.assertEqual(len(prov.bodies), 2, "must retry exactly once, then give up")
        self.assertNotIn("temperature", prov.bodies[1])
        self.assertTrue(any(c.finish_reason == "error" for c in chunks))

    def test_unrelated_error_is_not_mistaken_for_temperature_rejection(self):
        prov = self._provider(lambda: _OkStream())
        list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}], temperature=0.4))
        self.assertEqual(len(prov.bodies), 1)
        self.assertEqual(prov.bodies[0].get("temperature"), 0.4)


class TestTemperatureDiscordSync(unittest.TestCase):
    def test_remote_state_applies_temperature_to_agent(self):
        from harness.tui.interactive import _apply_remote_state

        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        _apply_remote_state(agent, {"temperature": 0.33}, origin="discord")
        self.assertEqual(agent.config.temperature, 0.33)

    def test_remote_state_ignores_own_cli_echo(self):
        from harness.tui.interactive import _apply_remote_state

        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        _apply_remote_state(agent, {"temperature": 0.11}, origin="cli")
        self.assertNotEqual(agent.config.temperature, 0.11)

    def test_bot_state_payload_mirrors_config_and_agents(self):
        """The Discord bot must mirror temperature in config + live agents."""
        import harness.discord.bot as bot_mod

        source = Path(bot_mod.__file__).read_text(encoding="utf-8")
        # Mirroring into self.config (in-process and cross-process paths).
        self.assertEqual(
            source.count(
                'for key in ("mode", "permission", "provider", "model", "thinking_effort", "temperature")'
            ),
            2,
        )
        # Applying onto live channel agents.
        self.assertIn('if "temperature" in payload:', source)

    def test_discord_help_documents_temperature(self):
        from harness.discord.help_text import DISCORD_HELP_TEXT, GENERAL_HELP_TEXT
        self.assertIn("/temperature", DISCORD_HELP_TEXT)
        self.assertIn("/temperature", GENERAL_HELP_TEXT)

    def test_discord_command_registered(self):
        try:
            import discord  # noqa: F401
        except ImportError:
            self.skipTest("discord.py not installed")
        from harness.discord.bot import HarnessDiscordBot
        bot = HarnessDiscordBot(HarnessConfig(), token="fake")
        names = {c.name for c in bot.tree.get_commands()}
        self.assertIn("temperature", names)


if __name__ == "__main__":
    unittest.main()