"""
Tests for Model-Specific Detection and Dynamic Parameter Resolution.
"""
import unittest
from harness.providers.detector import (
    detect_context_window,
    detect_thinking_support,
    inspect_model,
)
from harness.providers.base import BaseProvider
from harness.providers.mock_provider import MockProvider

class TestModelDetection(unittest.TestCase):

    def test_known_model_detection(self):
        spec_claude = inspect_model("claude-3-7-sonnet", "anthropic")
        self.assertEqual(spec_claude.context_window, 200000)
        self.assertTrue(spec_claude.supports_thinking)
        self.assertEqual(spec_claude.thinking_type, "budget_tokens")

        spec_gemini = inspect_model("gemini-2.5-pro", "gemini")
        self.assertEqual(spec_gemini.context_window, 2097152)
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

        # Future Gemini model
        c5 = detect_context_window("gemini-3-pro")
        self.assertEqual(c5, 2097152)

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

if __name__ == "__main__":
    unittest.main()
