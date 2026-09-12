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

if __name__ == "__main__":
    unittest.main()
