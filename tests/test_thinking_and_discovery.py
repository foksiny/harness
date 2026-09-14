"""
Tests for Thinking Display Streaming, ThinkTagParser, and Dynamic Discovery.
"""
import unittest
from harness.providers.detector import inspect_model, detect_context_window, detect_thinking_support
from harness.providers.openai_compatible import ThinkTagParser
from harness.providers.discovery import (
    resolve_model_spec_dynamic,
    load_cached_models,
    save_cached_models,
    _extract_media_capabilities,
)
from harness.tui.terminal import TerminalRenderer
from harness.core.agent import AgentEvent

class TestThinkingAndDiscovery(unittest.TestCase):

    def test_deepseek_v4_and_nim_models_detection(self):
        spec_v4 = inspect_model('deepseek-ai/deepseek-v4-pro-0813', 'nvidia')
        self.assertEqual(spec_v4.context_window, 1048576)
        # DeepSeek-V4 on NVIDIA NIM reasons via chat-template kwargs
        self.assertTrue(spec_v4.supports_thinking)
        self.assertEqual(spec_v4.thinking_type, 'chat_template_kwargs')

        spec_v4_flash = inspect_model('deepseek-ai/deepseek-v4-flash-0731', 'nvidia')
        self.assertEqual(spec_v4_flash.context_window, 1310720)
        self.assertTrue(spec_v4_flash.supports_thinking)

        # MockProvider dynamic detection test
        from harness.providers.mock_provider import MockProvider
        mock_prov = MockProvider()
        spec_mock_claude = mock_prov.get_model_spec('claude-3-7-sonnet')
        self.assertEqual(spec_mock_claude.context_window, 200000)
        self.assertTrue(spec_mock_claude.supports_thinking)

        spec_r1 = inspect_model('deepseek-ai/deepseek-r1', 'nvidia')
        self.assertEqual(spec_r1.context_window, 128000)
        self.assertTrue(spec_r1.supports_thinking)
        self.assertEqual(spec_r1.thinking_type, 'reasoning_effort')

        spec_nemo = inspect_model('nvidia/llama-3.1-nemotron-ultra-253b-v1', 'nvidia')
        self.assertEqual(spec_nemo.context_window, 131072)
        self.assertTrue(spec_nemo.supports_thinking)

        spec_llama4 = inspect_model('meta-llama/llama-4-maverick-17b-128e-instruct', 'openrouter')
        self.assertEqual(spec_llama4.context_window, 1048576)

        spec_gpt41 = inspect_model('gpt-4.1', 'openai')
        self.assertEqual(spec_gpt41.context_window, 1047576)

    def test_openrouter_tag_stripping(self):
        c_free = detect_context_window('deepseek-ai/deepseek-r1:free')
        self.assertEqual(c_free, 128000)
        th_free, ttype = detect_thinking_support('deepseek-ai/deepseek-r1:free')
        self.assertTrue(th_free)

    def test_think_tag_parser_standard(self):
        parser = ThinkTagParser()
        t1, r1 = parser.process('Hello world! ')
        self.assertEqual(t1, 'Hello world! ')
        self.assertEqual(r1, '')

        t2, r2 = parser.process('<think>Analyzing user query.')
        self.assertEqual(t2, '')
        self.assertEqual(r2, 'Analyzing user query.')
        self.assertTrue(parser.in_think)

        t3, r3 = parser.process(' Formulating plan.')
        self.assertEqual(t3, '')
        self.assertEqual(r3, ' Formulating plan.')

        t4, r4 = parser.process(' Done.</think>Here is the solution:')
        self.assertEqual(r4, ' Done.')
        self.assertEqual(t4, 'Here is the solution:')
        self.assertFalse(parser.in_think)

    def test_think_tag_parser_split_boundary(self):
        parser = ThinkTagParser()
        t1, r1 = parser.process('Intro <th')
        self.assertEqual(t1, 'Intro ')
        self.assertEqual(r1, '')

        t2, r2 = parser.process('ink>Thinking step...')
        self.assertEqual(t2, '')
        self.assertEqual(r2, 'Thinking step...')
        self.assertTrue(parser.in_think)

        t3, r3 = parser.process(' Step complete </th')
        self.assertEqual(r3, ' Step complete ')
        self.assertEqual(t3, '')

        t4, r4 = parser.process('ink>Final response.')
        self.assertEqual(r4, '')
        self.assertEqual(t4, 'Final response.')
        self.assertFalse(parser.in_think)

    def test_dynamic_discovery_resolution(self):
        cache = load_cached_models()
        cache['test_nim'] = {
            'timestamp': 9999999999,
            'models': [
                {'id': 'custom-org/future-model-x', 'context_length': 327680, 'supports_thinking': True, 'thinking_type': 'reasoning_effort'}
            ]
        }
        save_cached_models(cache)

        spec = resolve_model_spec_dynamic('custom-org/future-model-x', 'test_nim')
        self.assertEqual(spec.context_window, 327680)
        self.assertTrue(spec.supports_thinking)
        self.assertEqual(spec.thinking_type, 'reasoning_effort')

    def test_extract_media_capabilities_schemas(self):
        # OpenRouter architecture.input_modalities
        self.assertEqual(
            _extract_media_capabilities(
                {"architecture": {"input_modalities": ["text", "image"]}}),
            (True, False),
        )
        self.assertEqual(
            _extract_media_capabilities(
                {"architecture": {"input_modalities": ["text", "image", "video"]}}),
            (True, True),
        )
        # OpenAI lm input_modalities
        self.assertEqual(
            _extract_media_capabilities({"lm": {"input_modalities": ["text", "image"]}}),
            (True, False),
        )
        # Groq direct vision flag
        self.assertEqual(_extract_media_capabilities({"vision": True}), (True, None))
        self.assertEqual(_extract_media_capabilities({"vision": False}), (False, None))
        # No schema advertised
        self.assertEqual(_extract_media_capabilities({"id": "deepseek-chat"}), (None, None))
        self.assertEqual(_extract_media_capabilities(None), (None, None))

    def test_server_reported_vision_overrides_heuristics(self):
        cache = load_cached_models()
        # Heuristic flags nemotron as non-vision; server says it accepts images.
        cache['test_nim_v2'] = {
            'timestamp': 9999999999,
            'models': [
                {'id': 'nvidia/nemotron-check', 'context_length': 128000,
                 'supports_vision': True, 'supports_video': True}
            ]
        }
        save_cached_models(cache)
        spec = resolve_model_spec_dynamic('nvidia/nemotron-check', 'test_nim_v2')
        self.assertTrue(spec.supports_vision)
        self.assertTrue(spec.supports_video)

        # Server says text-only for a name that heuristic would call multimodal.
        cache['test_v3'] = {
            'timestamp': 9999999999,
            'models': [
                {'id': 'gpt-4o-textonly', 'context_length': 128000,
                 'supports_vision': False, 'supports_video': False}
            ]
        }
        save_cached_models(cache)
        spec2 = resolve_model_spec_dynamic('gpt-4o-textonly', 'test_v3')
        self.assertFalse(spec2.supports_vision)

    def test_terminal_thinking_event_lifecycle(self):
        renderer = TerminalRenderer('cyberpunk')
        self.assertFalse(renderer._is_thinking_visible)

        renderer.render_agent_event(AgentEvent('reasoning_delta', 'Step 1: check architecture\n'))
        self.assertTrue(renderer._is_thinking_visible)
        self.assertIn('Step 1', renderer._current_thinking)

        renderer.render_agent_event(AgentEvent('reasoning_delta', 'Step 2: execute verification\n'))
        self.assertIn('Step 2', renderer._current_thinking)

        renderer.render_agent_event(AgentEvent('text_delta', 'The result is verified.'))
        self.assertFalse(renderer._is_thinking_visible)
        self.assertEqual(renderer._current_thinking, '')

    def test_markdown_stream_flushes_each_segment_once(self):
        renderer = TerminalRenderer('cyberpunk')
        chunks = [
            'First paragraph of the response.\n',
            '\n',
            '## A Heading\n',
            '\n',
            'Second paragraph with **bold** text.\n',
        ]
        for c in chunks:
            renderer.render_agent_event(AgentEvent('text_delta', c))
        full = ''.join(chunks)
        self.assertEqual(renderer._md_buffer, full)

        renderer._finish_markdown()
        self.assertEqual(renderer._md_buffer, '')
        self.assertIsNone(renderer._md_live)

        renderer._finish_markdown()
        self.assertEqual(renderer._md_buffer, '')

    def test_markdown_preview_is_bounded(self):
        renderer = TerminalRenderer('cyberpunk')
        line = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n'
        renderer._md_buffer = line * 100
        preview = renderer._md_preview()
        self.assertLessEqual(len(preview), len(line) * 40)
        self.assertTrue(preview.startswith('Lorem ipsum'))

if __name__ == '__main__':
    unittest.main()
