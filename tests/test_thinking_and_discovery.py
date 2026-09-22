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

    def test_thinking_newlineless_stream_paints_via_fallback(self):
        """A newline-less thinking line must still paint mid-stream.

        Regression test: previously a thinking line with no ``\\n`` was held
        back for minutes and dumped as one huge chunk. The fallback prints
        up to the last word boundary once the hold exceeds the age limit.
        The age gate is forced to zero here for a deterministic (sleepless)
        test; production uses _MD_FLUSH_MAX_AGE seconds.
        """
        import io
        from rich.console import Console
        import harness.tui.terminal as term_mod
        old_age = term_mod._MD_FLUSH_MAX_AGE
        term_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer = TerminalRenderer('cyberpunk', show_thinking=True)
            renderer.console = Console(file=io.StringIO(), force_terminal=True, width=80)
            renderer.render_agent_event(AgentEvent('reasoning_delta', 'alpha beta '))
            out1 = renderer.console.file.getvalue()
            # First sight only stamps the hold window — header shows, text held.
            self.assertIn('Thought', out1)
            self.assertNotIn('alpha', out1)
            self.assertEqual(renderer._thinking_buffer, 'alpha beta ')

            renderer.render_agent_event(AgentEvent('reasoning_delta', 'gamma delta'))
            out2 = renderer.console.file.getvalue()
            # Fallback cut at the last word boundary: 'delta' stays buffered.
            self.assertIn('alpha beta gamma', out2)
            self.assertNotIn('delta', out2.split('alpha beta gamma')[-1])
            self.assertEqual(renderer._thinking_buffer, 'delta')

            # Back to the real age gate: a fresh remainder waits again.
            term_mod._MD_FLUSH_MAX_AGE = old_age
            renderer.render_agent_event(AgentEvent('reasoning_delta', ' epsilon\ntail'))
            out3 = renderer.console.file.getvalue()
            # Newline flushes the completed line; 'tail' stays buffered.
            self.assertIn('delta epsilon', out3)
            self.assertEqual(renderer._thinking_buffer, 'tail')
        finally:
            term_mod._MD_FLUSH_MAX_AGE = old_age

    def test_thinking_single_huge_word_hard_cuts(self):
        """A spaceless thinking blob must paint rather than stall forever."""
        import io
        from rich.console import Console
        import harness.tui.terminal as term_mod
        old_age = term_mod._MD_FLUSH_MAX_AGE
        term_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer = TerminalRenderer('cyberpunk', show_thinking=True)
            renderer.console = Console(file=io.StringIO(), force_terminal=True, width=80)
            renderer.render_agent_event(AgentEvent('reasoning_delta', 'supercali'))
            renderer.render_agent_event(AgentEvent('reasoning_delta', 'fragilistic'))
            out = renderer.console.file.getvalue()
            self.assertIn('supercalifragilistic', out)
            self.assertEqual(renderer._thinking_buffer, '')
        finally:
            term_mod._MD_FLUSH_MAX_AGE = old_age

    def test_thinking_hidden_mode_shows_indicator_not_content(self):
        """Default (show_thinking=False) prints a compact indicator and the
        final token count, never the raw reasoning text."""
        import io
        from rich.console import Console

        renderer = TerminalRenderer('cyberpunk', show_thinking=False)
        renderer.console = Console(file=io.StringIO(), force_terminal=True, width=80)

        renderer.render_agent_event(AgentEvent('reasoning_delta', 'secret reasoning alpha '))
        renderer.render_agent_event(AgentEvent('reasoning_delta', 'gamma\n'))
        out1 = renderer.console.file.getvalue()
        self.assertIn('Thinking', out1)
        self.assertNotIn('alpha', out1)
        self.assertNotIn('gamma', out1)
        # Reasoning text is still tallied for the final token count.
        self.assertEqual(renderer._current_thinking, 'secret reasoning alpha gamma\n')
        self.assertEqual(renderer._thinking_buffer, '')

        renderer.render_agent_event(AgentEvent('text_delta', 'Final answer.'))
        renderer.finish_markdown()
        out2 = renderer.console.file.getvalue()
        self.assertIn('Final answer.', out2)
        self.assertNotIn('alpha', out2)
        self.assertNotIn('gamma', out2)
        # Closing stats report the thinking duration and token count.
        self.assertIn('Thought', out2)
        self.assertIn('tokens', out2)

    def test_thinking_hidden_mode_is_default(self):
        """TerminalRenderer() matches the product default: reasoning is hidden."""
        renderer = TerminalRenderer('cyberpunk')
        self.assertFalse(renderer._show_thinking)

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

        # Append-only streaming: every completed block is printed as it
        # arrives, so only the trailing (unfinished) block may remain buffered.
        self.assertEqual(renderer._md_buffer, 'Second paragraph with **bold** text.\n')

        renderer._finish_markdown()
        self.assertEqual(renderer._md_buffer, '')

        renderer._finish_markdown()
        self.assertEqual(renderer._md_buffer, '')

    def test_markdown_stream_is_append_only_no_cursor_repositioning(self):
        """Regression test for the Windows 'Oi! 👋' repeated-greeting bug.

        The old implementation repainted a rich.live.Live region every delta;
        on Windows ConPTY/conhost the immediate-wrap quirk made every repaint
        leak the previous frame's first line, printing the greeting once per
        refresh down the left margin. The streamed output must therefore be
        append-only: no cursor-up / erase-line / carriage-return repositioning
        and no hidden-cursor escape sequences.
        """
        import io
        from rich.console import Console

        renderer = TerminalRenderer('cyberpunk')
        renderer.console = Console(
            file=io.StringIO(), force_terminal=True, width=80
        )

        chunks = [
            'Oi! 👋\n',
            '\n',
            'Sou o Harness, seu assistente de engenharia.\n',
            '\n',
            '- Código – escrever, debugar\n',
            '- DevOps\n',
        ]
        for c in chunks:
            renderer.render_agent_event(AgentEvent('text_delta', c))
        renderer._finish_markdown()

        out = renderer.console.file.getvalue()
        self.assertIn('Oi!', out)
        self.assertNotIn('\x1b[1A', out)      # cursor up (Live repaint climb)
        self.assertNotIn('\x1b[2K', out)      # erase line
        self.assertNotIn('\x1b[?25l', out)    # hide cursor
        self.assertNotIn('\r', out.replace('\r\n', '\n'))  # bare carriage return
        # The greeting must appear exactly once.
        self.assertEqual(out.count('Oi!'), 1)

    def test_markdown_stream_never_opens_a_live_region(self):
        """No Live region may be created while streaming (Windows-safe by design)."""
        renderer = TerminalRenderer('cyberpunk')
        self.assertFalse(hasattr(renderer, '_md_live'))
        for c in ('Oi! 👋\n', '\n', 'Sou o Harness.\n'):
            renderer.render_agent_event(AgentEvent('text_delta', c))
        renderer._finish_markdown()
        self.assertFalse(hasattr(renderer, '_md_live'))

    def test_markdown_stream_holds_back_open_code_fence(self):
        """Text inside an unterminated code fence must stay buffered.

        A flush at a blank line inside an open fence would split the fence
        into two renders and print garbled code blocks.
        """
        renderer = TerminalRenderer('cyberpunk')
        chunks = [
            'Intro paragraph.\n',
            '\n',
            '```python\n',
            'x = 1\n',
            '\n',       # blank line INSIDE the fence — must not trigger a flush
            'y = 2\n',
        ]
        for c in chunks:
            renderer.render_agent_event(AgentEvent('text_delta', c))
        self.assertEqual(renderer._md_buffer, '```python\nx = 1\n\ny = 2\n')

        # Closing the fence and adding a blank line flushes the whole fence.
        renderer.render_agent_event(AgentEvent('text_delta', '```\n\n'))
        self.assertEqual(renderer._md_buffer, '')
        renderer._finish_markdown()
        self.assertEqual(renderer._md_buffer, '')

    def test_md_completed_end_boundary_cases(self):
        end = TerminalRenderer._md_completed_end
        # Nothing complete yet.
        self.assertEqual(end('Oi! partial greet'), 0)
        # Blank line ends the first block.
        self.assertEqual(end('P1\n\nP2 partial'), len('P1\n\n'))
        # Blank lines inside a closed fence only bound AFTER the fence.
        buf = 'Intro\n\n```python\nx = 1\n\ny = 2\n```\n\nTail'
        self.assertEqual(end(buf), len('Intro\n\n```python\nx = 1\n\ny = 2\n```\n\n'))
        # Unterminated fence: nothing after its opening may be flushed.
        self.assertEqual(end('Intro\n\n```python\nx = 1\n\ny = 2'), len('Intro\n\n'))
        # Tilde fences too.
        buf5 = 'P\n\n~~~\ncode\n~~~\n\nX'
        self.assertEqual(end(buf5), len('P\n\n~~~\ncode\n~~~\n\n'))

    def test_streamed_markdown_matches_whole_document_render(self):
        """Streaming must be visually identical to rendering the full markdown
        at once, no matter where the SSE deltas happen to split the text.

        This is the core quality contract of the append-only streamer that
        replaced the Windows-buggy rich.live.Live repaint.
        """
        import io
        import itertools
        from rich.console import Console
        from rich.markdown import Markdown

        blocks = {
            'para': 'Oi! Sou o Harness, seu assistente.',
            'head': '## A Heading',
            'list': '- Código\n- Arquitetura\n- DevOps',
            'olist': '1. Um\n2. Dois',
            'quote': '> Nota do agente.',
            'fence': '```python\nx = 1\n```',
            'table': '| A | B |\n|---|---|\n| 1 | 2 |',
            'hr': '---',
        }

        def whole(md):
            buf = io.StringIO()
            c = Console(file=buf, force_terminal=True, width=60)
            c.print(Markdown(md, code_theme='monokai'))
            return buf.getvalue()

        def streamed(md, cuts):
            r = TerminalRenderer('cyberpunk')
            r.console = Console(file=io.StringIO(), force_terminal=True, width=60)
            prev = 0
            for cut in list(cuts) + [len(md)]:
                if cut > prev:
                    r.render_agent_event(AgentEvent('text_delta', md[prev:cut]))
                    prev = cut
            r._finish_markdown()
            return r.console.file.getvalue()

        combos = list(itertools.permutations(blocks.keys(), 2)) + [
            ('para', 'list', 'para'),
            ('para', 'para', 'para'),
            ('para', 'fence', 'para'),
            ('hr', 'para', 'hr'),
        ]
        for combo in combos:
            md = '\n\n'.join(blocks[k] for k in combo)
            expected = whole(md)
            # Split the stream at assorted offsets, including mid-word and
            # mid-fence positions, to simulate arbitrary SSE chunking.
            for cuts in (range(1, len(md), 5), range(2, len(md), 11), [len(md) // 2]):
                self.assertEqual(
                    streamed(md, cuts), expected,
                    f'stream != whole for {combo} with cuts {cuts}',
                )

if __name__ == '__main__':
    unittest.main()
