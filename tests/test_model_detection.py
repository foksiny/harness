"""
Tests for Model-Specific Detection and Dynamic Parameter Resolution.
"""
import unittest
from harness.providers.detector import (
    detect_context_window,
    detect_thinking_support,
    detect_vision_support,
    inspect_model,
)
from harness.providers.base import BaseProvider
from harness.providers.mock_provider import MockProvider

class TestModelDetection(unittest.TestCase):

    def test_vision_support_detection(self):
        # Core omni / VLM families are detected as vision-capable.
        self.assertTrue(detect_vision_support("gpt-4o", "openai"))
        self.assertTrue(detect_vision_support("gpt-4o-mini", "openai"))
        self.assertTrue(detect_vision_support("gpt-4.1-mini", "openai"))
        self.assertTrue(detect_vision_support("gpt-5", "openai"))
        self.assertTrue(detect_vision_support("gemini-2.5-pro", "gemini"))
        self.assertTrue(detect_vision_support("gemini-3-flash", "google"))
        self.assertTrue(detect_vision_support("claude-3-7-sonnet", "anthropic"))
        self.assertTrue(detect_vision_support("claude-sonnet-4-5", "anthropic"))
        self.assertTrue(detect_vision_support("pixtral-large-latest", "mistral"))
        self.assertTrue(detect_vision_support("grok-4.6", "xai"))
        self.assertTrue(detect_vision_support("qwen2.5-vl-72b", "openrouter"))
        self.assertTrue(detect_vision_support("meta-llama/llama-4-maverick-17b-128e-instruct", "openrouter"))
        self.assertTrue(detect_vision_support("command-a-plus", "cohere"))

        # Meta Muse / Gemma-3 / other explicit multimodal families.
        self.assertTrue(detect_vision_support("meta/muse-glimmer-30b", "nvidia"))
        self.assertTrue(detect_vision_support("meta/muse-gecko-25b", "nvidia"))
        self.assertTrue(detect_vision_support("google/gemma-3-27b-it", "openrouter"))
        self.assertTrue(detect_vision_support("nvidia/nvidia-neva-22b", "nvidia"))

        # Muse is a reasoning family with separate reasoning output.
        th, ttype = detect_thinking_support("meta/muse-glimmer-30b", "nvidia")
        self.assertTrue(th)
        self.assertEqual(ttype, "reasoning_effort")

        # Text-only models are not vision-capable.
        self.assertFalse(detect_vision_support("deepseek-chat", "deepseek"))
        self.assertFalse(detect_vision_support("llama-3.3-70b-instruct", "groq"))
        self.assertFalse(detect_vision_support("text-embedding-3-large", "openai"))
        self.assertFalse(detect_vision_support("nvidia/nemotron-3-ultra-550b-a55b", "nvidia"))
        self.assertFalse(detect_vision_support("deepseek-ai/deepseek-r1", "nvidia"))

        # Geminis are vision-capable even with no name hint (provider family default).
        self.assertTrue(detect_vision_support("future-gemini-x", "gemini"))

    def test_known_model_detection(self):
        spec_claude = inspect_model("claude-3-7-sonnet", "anthropic")
        self.assertEqual(spec_claude.context_window, 200000)
        self.assertTrue(spec_claude.supports_thinking)
        self.assertEqual(spec_claude.thinking_type, "budget_tokens")

        spec_gemini = inspect_model("gemini-2.5-pro", "gemini")
        self.assertEqual(spec_gemini.context_window, 1048576)
        self.assertTrue(spec_gemini.supports_thinking)
        self.assertEqual(spec_gemini.thinking_type, "thinking_budget")

        spec_o1 = inspect_model("o1", "openai")
        self.assertEqual(spec_o1.context_window, 200000)
        self.assertTrue(spec_o1.supports_thinking)
        self.assertEqual(spec_o1.thinking_type, "reasoning_effort")

    def test_future_model_context_detection(self):
        # 1M token suffix
        c1 = detect_context_window("custom-super-llm-1m")
        self.assertEqual(c1, 1000000)

        # 2M token suffix
        c2 = detect_context_window("agent-max-2m")
        self.assertEqual(c2, 2000000)

        # 256k token suffix
        c3 = detect_context_window("llama-4-256k-instruct")
        self.assertEqual(c3, 256000)

        # 512k token suffix
        c4 = detect_context_window("qwen-3-512k")
        self.assertEqual(c4, 512000)

        # Future Gemini model (no provider → conservative default)
        c5 = detect_context_window("gemini-3-pro")
        self.assertEqual(c5, 128000)

    def test_future_reasoning_detection(self):
        # r1 pattern
        is_th1, ttype1 = detect_thinking_support("my-company/deepseek-r1-custom")
        self.assertTrue(is_th1)
        self.assertEqual(ttype1, "reasoning_effort")

        # reasoning keyword
        is_th2, ttype2 = detect_thinking_support("llama-4-reasoning")
        self.assertTrue(is_th2)

        # Claude future thinking
        is_th3, ttype3 = detect_thinking_support("claude-3-7-opus", provider="anthropic")
        self.assertTrue(is_th3)
        self.assertEqual(ttype3, "budget_tokens")

    def test_normalize_thinking_effort_parameters(self):
        mock_prov = MockProvider()

        # Anthropic format
        spec_anth = inspect_model("claude-3-7-sonnet", "anthropic")
        p_anth = mock_prov.normalize_thinking_effort(spec_anth, "high")
        self.assertIn("thinking", p_anth)
        self.assertEqual(p_anth["thinking"]["type"], "enabled")
        self.assertEqual(p_anth["thinking"]["budget_tokens"], 16384)

        # Gemini format
        spec_gem = inspect_model("gemini-2.5-pro", "gemini")
        p_gem = mock_prov.normalize_thinking_effort(spec_gem, "medium")
        self.assertIn("thinking_config", p_gem)
        self.assertEqual(p_gem["thinking_config"]["thinking_budget"], 8192)

        # OpenAI / NIM / OpenRouter format
        spec_o1 = inspect_model("o1", "openai")
        p_o1 = mock_prov.normalize_thinking_effort(spec_o1, "low")
        self.assertEqual(p_o1, {"reasoning_effort": "low"})

class TestCurrentGenModelSpecs(unittest.TestCase):

    def test_openai_gpt5_family(self):
        spec = inspect_model("gpt-5", "openai")
        self.assertEqual(spec.context_window, 400000)
        self.assertEqual(spec.max_output_tokens, 128000)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "reasoning_effort")

        spec_mini = inspect_model("gpt-5-mini", "openai")
        self.assertEqual(spec_mini.context_window, 400000)

        spec_codex = inspect_model("gpt-5.1-codex", "openai")
        self.assertEqual(spec_codex.context_window, 400000)
        self.assertTrue(spec_codex.supports_thinking)

    def test_anthropic_claude46_family(self):
        spec = inspect_model("claude-sonnet-4-5", "anthropic")
        self.assertEqual(spec.context_window, 200000)
        self.assertEqual(spec.max_output_tokens, 64000)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "budget_tokens")

        spec_opus = inspect_model("claude-opus-4-5-20251101", "anthropic")
        self.assertEqual(spec_opus.max_output_tokens, 64000)

    def test_gemini_25_family_context(self):
        for model in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
            spec = inspect_model(model, "gemini")
            self.assertEqual(spec.context_window, 1048576, model)
            self.assertEqual(spec.max_output_tokens, 65536, model)
            self.assertEqual(spec.thinking_type, "thinking_budget", model)

    def test_grok4_context_and_thinking(self):
        spec = inspect_model("grok-4.6", "xai")
        self.assertEqual(spec.context_window, 500000)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "reasoning_effort")

        spec_multi = inspect_model("grok-4.20-0309-reasoning", "xai")
        self.assertEqual(spec_multi.context_window, 1048576)

    def test_cohere_reasoning_dialect(self):
        spec = inspect_model("command-a-reasoning-08-2025", "cohere")
        self.assertEqual(spec.context_window, 256000)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "thinking_token_budget")

    def test_mistral_newgen_context(self):
        spec = inspect_model("mistral-medium-3-5", "mistral")
        self.assertEqual(spec.context_window, 262144)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "reasoning_effort")

        spec_large = inspect_model("mistral-large-latest", "mistral")
        self.assertEqual(spec_large.context_window, 262144)
        self.assertFalse(spec_large.supports_thinking)

    def test_groq_gpt_oss(self):
        spec = inspect_model("openai/gpt-oss-120b", "groq")
        self.assertEqual(spec.context_window, 131072)
        self.assertEqual(spec.max_output_tokens, 65536)
        self.assertTrue(spec.supports_thinking)

    def test_deepseek_platform_flash(self):
        spec = inspect_model("deepseek-flash", "deepseek")
        self.assertEqual(spec.context_window, 1048576)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "reasoning_effort")

    def test_openrouter_normalizes_to_reasoning_object(self):
        spec = inspect_model("anthropic/claude-sonnet-4.5", "openrouter")
        self.assertEqual(spec.context_window, 200000)
        self.assertEqual(spec.thinking_type, "reasoning_object")

    def test_together_hybrid_reasoning_toggle(self):
        spec = inspect_model("together/deepseek-v4-pro", "together")
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, "reasoning_toggle")

        spec_oss = inspect_model("openai/gpt-oss-120b", "together")
        self.assertEqual(spec_oss.thinking_type, "reasoning_effort")


class TestThinkingDialectNormalization(unittest.TestCase):

    def test_dialect_shapes(self):
        prov = MockProvider()

        # Cohere
        p_cohere = prov.normalize_thinking_effort(
            inspect_model("command-a-reasoning-08-2025", "cohere"), "high")
        self.assertEqual(p_cohere, {"thinking": {"type": "enabled", "token_budget": 16384}})
        self.assertEqual(prov.normalize_thinking_effort(
            inspect_model("command-a-reasoning-08-2025", "cohere"), "off"),
            {"thinking": {"type": "disabled"}})

        # OpenRouter
        p_or = prov.normalize_thinking_effort(
            inspect_model("anthropic/claude-sonnet-4.5", "openrouter"), "medium")
        self.assertEqual(p_or, {"reasoning": {"effort": "medium"}})
        self.assertEqual(prov.normalize_thinking_effort(
            inspect_model("anthropic/claude-sonnet-4.5", "openrouter"), "off"),
            {"reasoning": {"enabled": False}})

        # NVIDIA NIM DeepSeek-V4
        p_nim = prov.normalize_thinking_effort(
            inspect_model("deepseek-ai/deepseek-v4-pro-0813", "nvidia"), "high")
        self.assertEqual(p_nim, {"chat_template_kwargs": {"thinking": True}, "reasoning_effort": "high"})
        self.assertEqual(prov.normalize_thinking_effort(
            inspect_model("deepseek-ai/deepseek-v4-pro-0813", "nvidia"), "off"),
            {"chat_template_kwargs": {"thinking": False}})

        # Together hybrid toggle
        p_tog = prov.normalize_thinking_effort(
            inspect_model("together/deepseek-v4-pro", "together"), "medium")
        self.assertEqual(p_tog, {"reasoning": {"enabled": True}})

        # Gemini surfaces thoughts
        p_gem = prov.normalize_thinking_effort(inspect_model("gemini-2.5-pro", "gemini"), "low")
        self.assertEqual(p_gem, {"thinking_config": {"thinking_budget": 2048, "include_thoughts": True}})

        # Anthropic budget clamps below 1024
        p_anth = prov.normalize_thinking_effort(inspect_model("claude-sonnet-4-5", "anthropic"), "1")
        self.assertGreaterEqual(p_anth["thinking"]["budget_tokens"], 1024)

        # Numeric reasoning effort maps to levels
        p_num = prov.normalize_thinking_effort(inspect_model("o3", "openai"), "15000")
        self.assertEqual(p_num, {"reasoning_effort": "high"})

    def test_off_is_noop_for_openai_style(self):
        prov = MockProvider()
        spec = inspect_model("o3", "openai")
        self.assertEqual(prov.normalize_thinking_effort(spec, "off"), {})
        self.assertEqual(prov.normalize_thinking_effort(spec, "none"), {})

if __name__ == "__main__":
    unittest.main()
