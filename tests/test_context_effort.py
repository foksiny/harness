"""Tests for context-window, auto-compact, and thinking-effort upgrades:
registry index tiers, discovery memoization, media-aware token counting,
reasoning stripping (+ keep-block overflow fix), /effort validation,
/compact preview, and hysteresis rebind on model switches.
"""
import copy
import json
import os
import unittest
from unittest.mock import patch

from harness.commands.registry import CommandRegistry, parse_effort_arg
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.compaction import (
    Compactor,
    calculate_history_tokens,
)
from harness.providers.detector import inspect_model


class FakeRenderer:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []
        self.successes = []
        self.markdown = []

    def print_info(self, msg):
        self.infos.append(str(msg))

    def print_warning(self, msg):
        self.warnings.append(str(msg))

    def print_error(self, msg):
        self.errors.append(str(msg))

    def print_success(self, msg):
        self.successes.append(str(msg))

    def print_markdown(self, msg):
        self.markdown.append(str(msg))


def _agent(model="gpt-4o-mini"):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.model = model
    cfg.learning_enabled = False
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    return agent


def _msg(role, content, reasoning=None):
    m = {"role": role, "content": content}
    if reasoning is not None:
        m["reasoning_content"] = reasoning
    return m


class TestRegistryIndex(unittest.TestCase):
    def test_catalog_source(self):
        spec = inspect_model("claude-sonnet-4-5", "anthropic")
        self.assertEqual(spec.source, "catalog")
        self.assertEqual(spec.context_window, 200000)

    def test_dotted_alias_matches_dashed(self):
        a = inspect_model("anthropic/claude-sonnet-4.5", "openrouter")
        b = inspect_model("anthropic/claude-sonnet-4-5", "openrouter")
        self.assertEqual(a.context_window, b.context_window)

    def test_literal_suffix_source(self):
        spec = inspect_model("some-future-model-1m", "openrouter")
        self.assertEqual(spec.source, "literal")
        self.assertEqual(spec.context_window, 1_000_000)

    def test_provider_fallback_source(self):
        spec = inspect_model("unknown-model-zz", "gemini")
        self.assertEqual(spec.source, "provider")
        self.assertEqual(spec.context_window, 1048576)

    def test_default_source(self):
        spec = inspect_model("unknown-model-zz", "nosuchprovider")
        self.assertEqual(spec.source, "default")
        self.assertEqual(spec.context_window, 128000)

    def test_suffix_match(self):
        # OpenRouter-style prefixed id resolves to the bare catalog entry.
        spec = inspect_model("together/llama-3.3-70b-instruct", "together")
        self.assertEqual(spec.context_window, 128000)


class TestDiscoveryMemoization(unittest.TestCase):
    def setUp(self):
        import harness.providers.discovery as d
        self.d = d
        self.saved_univ = dict(d._universal_mem)
        self.saved_file = dict(d._models_file_cache)

    def tearDown(self):
        self.d._universal_mem.update(self.saved_univ)
        self.d._models_file_cache.update(self.saved_file)

    def test_universal_network_hit_once(self):
        self.d._universal_mem.update({"state": "cold", "data": {}, "mtime": None})
        with patch("urllib.request.urlopen", side_effect=Exception("no net")) as m, \
             patch.object(self.d, "UNIVERSAL_CACHE_FILE") as f:
            f.exists.return_value = False
            for _ in range(5):
                self.assertEqual(self.d.load_universal_models(), {})
            self.assertEqual(m.call_count, 1)

    def test_universal_force_refresh(self):
        self.d._universal_mem.update({"state": "ready", "data": {"a": 1}, "mtime": None})
        with patch("urllib.request.urlopen", side_effect=Exception("no net")) as m, \
             patch.object(self.d, "UNIVERSAL_CACHE_FILE") as f:
            f.exists.return_value = False
            # Ready state serves memory without touching the network...
            self.d.load_universal_models()
            self.assertEqual(m.call_count, 0)
            # ...unless forced.
            self.d.load_universal_models(force=True)
            self.assertEqual(m.call_count, 1)

    def test_cached_models_mtime_guard(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "models_cache.json"
            p.write_text(json.dumps({"mock": {"timestamp": 0, "models": []}}))
            with patch.object(self.d, "CACHE_FILE", p):
                self.d._models_file_cache.update({"mtime": None, "data": {}})
                first = self.d.load_cached_models()
                self.assertIn("mock", first)
                with patch("builtins.open", side_effect=AssertionError("should be cached")):
                    # json.load path must not run; open() for read must not run.
                    # (stat() still runs — that is the whole point of the guard.)
                    second = self.d.load_cached_models()
                self.assertEqual(first, second)
                # Touch with new content + new mtime -> re-read picks it up.
                os.utime(p, (0, 0))
                p.write_text(json.dumps({"mock": {"timestamp": 0, "models": [{"id": "x"}]}}))
                third = self.d.load_cached_models()
                self.assertEqual(third["mock"]["models"], [{"id": "x"}])


class TestMediaAwareTokens(unittest.TestCase):
    def test_image_block_counted(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image", "path": "/tmp/a.png"},
        ]}]
        self.assertGreaterEqual(calculate_history_tokens(msgs), 1500)

    def test_plain_strings_unchanged(self):
        msgs = [{"role": "user", "content": "a" * 380}]
        self.assertEqual(calculate_history_tokens(msgs), 105)

    def test_breakdown_sums_to_total_with_blocks(self):
        c = Compactor(context_window=100000)
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image", "path": "/tmp/a.png"},
            ]},
            {"role": "assistant", "content": "seen"},
        ]
        status = c.check_status(msgs)
        self.assertEqual(sum(status.breakdown.values()), status.total_tokens)


class TestReasoningStripping(unittest.TestCase):
    @staticmethod
    def _history(n_turns=8):
        m = []
        for i in range(n_turns):
            m.append(_msg("user", "instruction " + "x" * 500))
            m.append(_msg("assistant", "work " + "y" * 200, reasoning="thought " * 2000))
        return m

    def test_old_reasoning_stripped_recent_kept(self):
        # Window sized so the preserved recent block fits under target:
        # old reasoning is stripped, recent reasoning stays verbatim.
        c = Compactor(context_window=60000, threshold_ratio=0.5, target_ratio=0.4)
        out, rep = c.compact(self._history())
        self.assertGreater(rep["tactics"]["reasoning_stripped"], 0)
        self.assertFalse(any(x.get("reasoning_content") for x in out[:-8]))
        self.assertTrue(any(x.get("reasoning_content") for x in out[-8:]))

    def test_keep_block_overflow_fixed(self):
        # Keep block alone exceeds the target: the old code returned ~6x target.
        c = Compactor(context_window=3000, threshold_ratio=0.5, target_ratio=0.4)
        out, rep = c.compact(self._history())
        self.assertLessEqual(rep["after_ratio"], 0.4)


class TestEffortValidation(unittest.TestCase):
    def test_parse_effort(self):
        self.assertEqual(parse_effort_arg("high"), "high")
        self.assertEqual(parse_effort_arg("HIGH"), "high")
        self.assertEqual(parse_effort_arg("minimal"), "low")
        self.assertEqual(parse_effort_arg("med"), "medium")
        self.assertEqual(parse_effort_arg("max"), "max")
        self.assertEqual(parse_effort_arg("none"), "off")
        self.assertEqual(parse_effort_arg("16000"), "16000")
        self.assertIsNone(parse_effort_arg("banana"))
        self.assertIsNone(parse_effort_arg(""))
        self.assertIsNone(parse_effort_arg("ultra-mega"))

    def test_invalid_rejected_config_unchanged(self):
        agent = _agent()
        reg = CommandRegistry()
        renderer = FakeRenderer()
        before = agent.config.thinking_effort
        with patch("harness.commands.registry.save_config"):
            reg.handle("/effort banana", agent, renderer)
        self.assertEqual(agent.config.thinking_effort, before)
        self.assertTrue(renderer.errors)

    def test_synonym_canonicalized(self):
        agent = _agent()
        reg = CommandRegistry()
        renderer = FakeRenderer()
        with patch("harness.commands.registry.save_config"):
            reg.handle("/effort minimal", agent, renderer)
        self.assertEqual(agent.config.thinking_effort, "low")
        self.assertTrue(renderer.successes)

    def test_unsupported_model_says_ignored(self):
        agent = _agent(model="gpt-4o-mini")  # mock provider: no thinking support
        agent.session.model = "gpt-4o-mini"
        reg = CommandRegistry()
        renderer = FakeRenderer()
        reg.handle("/effort", agent, renderer)
        self.assertTrue(any("does not support thinking" in i for i in renderer.infos))

    def test_show_includes_params(self):
        agent = _agent(model="o3")  # reasoning marker -> thinking supported
        agent.session.model = "o3"
        reg = CommandRegistry()
        renderer = FakeRenderer()
        reg.handle("/effort", agent, renderer)
        self.assertTrue(any("dialect" in i for i in renderer.infos))


class TestCompactPreview(unittest.TestCase):
    def test_preview_changes_nothing(self):
        agent = _agent()
        for i in range(8):
            agent.session.messages.append(_msg("user", "instruction " + "x" * 500))
            agent.session.messages.append(_msg("assistant", "work " + "y" * 200,
                                               reasoning="thought " * 2000))
        before = copy.deepcopy(agent.session.messages)
        ledger_before = list(agent.compactor.ledger)
        hyst_before = agent.compactor._last_target_ratio
        reg = CommandRegistry()
        renderer = FakeRenderer()
        reg.handle("/compact preview", agent, renderer)
        self.assertEqual(agent.session.messages, before)
        self.assertEqual(agent.compactor.ledger, ledger_before)
        self.assertEqual(agent.compactor._last_target_ratio, hyst_before)
        self.assertTrue(any("Preview" in i for i in renderer.infos))

    def test_preview_empty_history(self):
        agent = _agent()
        reg = CommandRegistry()
        renderer = FakeRenderer()
        reg.handle("/compact preview", agent, renderer)
        self.assertTrue(any("nothing to compact" in i.lower() for i in renderer.infos))


class TestModelWrittenCheckpoint(unittest.TestCase):
    """The global checkpoint (sweep 4) must be model-written; heuristic is
    strictly the fallback."""

    @staticmethod
    def _history(n_turns=10):
        m = []
        for i in range(n_turns):
            m.append({"role": "user", "content": f"task {i} " + "p" * 400})
            m.append({"role": "assistant", "content": f"work {i} " + "z" * 400})
        return m

    @staticmethod
    def _tiny_window():
        # Target (300t) fits the keep group plus one consolidated checkpoint
        # but not nine per-group digests plus keep, forcing sweep 4 with a
        # fitting LLM win.
        return Compactor(context_window=2000, threshold_ratio=0.05,
                         target_ratio=0.15, preserve_min_turns=1)

    def test_checkpoint_prefers_llm(self):
        calls = []

        def fake_summarize(block):
            calls.append(block)
            if len(block) > 3:
                return "LLM GLOBAL checkpoint of everything."
            return "LLM DIGEST condensed."

        c = self._tiny_window()
        c.summarize_fn = fake_summarize
        c.summary_mode = "auto"
        out, rep = c.compact(self._history())
        self.assertTrue(rep["tactics"]["checkpointed"])
        self.assertTrue(rep["tactics"]["checkpoint_llm"])
        text = " ".join(str(m.get("content") or "") for m in out)
        self.assertIn("LLM GLOBAL", text)
        self.assertEqual(len(calls), 10)  # 9 group digests + 1 consolidation

    def test_checkpoint_falls_back_to_heuristic(self):
        def flaky_summarize(block):
            if len(block) > 3:
                return None  # model fails exactly on the consolidation call
            return "LLM DIGEST condensed."

        c = self._tiny_window()
        c.summarize_fn = flaky_summarize
        c.summary_mode = "auto"
        out, rep = c.compact(self._history())
        self.assertTrue(rep["tactics"]["checkpointed"])
        self.assertFalse(rep["tactics"]["checkpoint_llm"])
        text = " ".join(str(m.get("content") or "") for m in out)
        self.assertIn("[AUTOMATED CONTEXT MEMORY CHECKPOINT]", text)
        self.assertNotIn("LLM GLOBAL", text)

    def test_heuristic_mode_never_calls_model(self):
        calls = []

        def fake_summarize(block):
            calls.append(block)
            return "SHOULD NOT BE USED"

        c = self._tiny_window()
        c.summarize_fn = fake_summarize
        c.summary_mode = "heuristic"
        out, rep = c.compact(self._history())
        self.assertEqual(calls, [])
        self.assertTrue(rep["tactics"]["checkpointed"])
        text = " ".join(str(m.get("content") or "") for m in out)
        self.assertNotIn("SHOULD NOT BE USED", text)


class TestHysteresisRebind(unittest.TestCase):
    def test_set_provider_resets_hysteresis(self):
        agent = _agent()
        agent.compactor._last_target_ratio = 0.9
        agent.set_provider("mock")
        self.assertIsNone(agent.compactor._last_target_ratio)
        self.assertGreater(agent.compactor.context_window, 0)

    def test_rebind_auto_cap(self):
        c = Compactor(context_window=100000)
        c.rebind(200000, max_message_tokens=0)
        self.assertEqual(c.max_message_tokens, int(200000 * 0.15))
        c.rebind(200000, max_message_tokens=5000)
        self.assertEqual(c.max_message_tokens, 5000)


if __name__ == "__main__":
    unittest.main()
