"""
Tests for LLM Providers and Factory Integration.
"""
import unittest
from harness.providers import get_provider, list_providers, PROVIDER_CONFIGS
from harness.config import HarnessConfig

class TestProviders(unittest.TestCase):

    def test_provider_count_exceeds_twelve(self):
        provs = list_providers()
        self.assertGreaterEqual(len(provs), 16)

    def test_required_specific_providers(self):
        # opencode zen/go
        self.assertIn("opencode", PROVIDER_CONFIGS)
        prov_opencode = get_provider("opencode")
        self.assertIn("opencode", prov_opencode.base_url)

        # openrouter
        self.assertIn("openrouter", PROVIDER_CONFIGS)
        prov_openrouter = get_provider("openrouter")
        self.assertIn("openrouter", prov_openrouter.base_url)

        # nvidia nim
        self.assertIn("nvidia", PROVIDER_CONFIGS)
        prov_nvidia = get_provider("nvidia")
        self.assertIn("nvidia", prov_nvidia.base_url)

    def test_all_provider_instantiations(self):
        cfg = HarnessConfig()
        cfg.api_keys["openai"] = "test-key"
        cfg.api_keys["anthropic"] = "test-key"
        cfg.api_keys["gemini"] = "test-key"

        for pname in PROVIDER_CONFIGS:
            prov = get_provider(pname, cfg)
            self.assertIsNotNone(prov)
            self.assertIsNotNone(prov.default_model)
            # Verify model spec resolution works
            spec = prov.get_model_spec()
            self.assertGreater(spec.context_window, 0)

    def test_base_provider_has_stream_chat_abstract_method(self):
        from harness.providers.base import BaseProvider
        self.assertTrue(hasattr(BaseProvider, "stream_chat"))
        self.assertIn("stream_chat", BaseProvider.__abstractmethods__)

    def test_gemini_consecutive_tool_results_merged_in_single_turn(self):
        prov = get_provider("gemini")
        messages = [
            {"role": "user", "content": "run checks"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "tool1", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "tool2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "tool1", "content": "res1"},
            {"role": "tool", "tool_call_id": "c2", "name": "tool2", "content": "res2"},
        ]
        contents = prov._convert_contents(messages)
        # Should be: user turn, model turn, single user turn with 2 functionResponse parts
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[2]["role"], "user")
        self.assertEqual(len(contents[2]["parts"]), 2)
        self.assertIn("functionResponse", contents[2]["parts"][0])
        self.assertEqual(contents[2]["parts"][0]["functionResponse"]["name"], "tool1")
        self.assertIn("functionResponse", contents[2]["parts"][1])
        self.assertEqual(contents[2]["parts"][1]["functionResponse"]["name"], "tool2")

    def test_anthropic_consecutive_tool_results_merged_in_single_turn(self):
        prov = get_provider("anthropic")
        messages = [
            {"role": "user", "content": "run checks"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "tool1", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "tool2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "tool1", "content": "res1"},
            {"role": "tool", "tool_call_id": "c2", "name": "tool2", "content": "res2"},
        ]
        msgs = prov._convert_messages(messages)
        # Should be: user turn, assistant turn, single user turn with 2 tool_result blocks
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[2]["role"], "user")
        self.assertIsInstance(msgs[2]["content"], list)
        self.assertEqual(len(msgs[2]["content"]), 2)
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")
        self.assertEqual(msgs[2]["content"][0]["tool_use_id"], "c1")
        self.assertEqual(msgs[2]["content"][1]["type"], "tool_result")
        self.assertEqual(msgs[2]["content"][1]["tool_use_id"], "c2")

if __name__ == "__main__":
    unittest.main()
