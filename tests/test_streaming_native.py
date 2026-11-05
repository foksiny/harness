"""
Provider-native streaming and live-render regression tests.

Covers the streaming overhaul:
- each provider SSE event maps to exactly one LLMChunk carrying the native
  decoded event payload (``LLMChunk.native``);
- OpenAI usage-only events are preserved and ``[DONE]`` never appends a
  duplicate synthetic stop chunk;
- Anthropic announces a tool the moment ``content_block_start`` arrives and
  ``message_stop`` does not duplicate ``message_delta``'s stop reason;
- Gemini merges all parts of one native SSE event into one LLMChunk;
- Discord's live buffer edits one message in place and only rolls over at the
  platform's hard limit;
- piped CLI output passes provider deltas through verbatim;
- newline-less markdown paints before completion without inserting line breaks;
- only a provider ``\\n`` ends a streamed line, and blank lines survive;
- interactive playback holds line fragments until a real newline, so
  prompt_toolkit's repaint cannot overwrite them;
- streamed lists stay tight instead of becoming one list per bullet.
"""
import io
import json
import unittest

from harness.core.agent import AgentEvent
from harness.discord.renderer import DiscordRenderState, DiscordStreamBuffer
from harness.providers.anthropic_provider import AnthropicProvider
from harness.providers.gemini_provider import GeminiProvider
from harness.providers.openai_compatible import OpenAICompatibleProvider
from harness.tui.terminal import TerminalRenderer
import harness.tui.terminal as terminal_mod


class _FakeSse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self.lines

    def __exit__(self, *args):
        return False


def _openai_chunks(lines):
    class _Prov(OpenAICompatibleProvider):
        def _tracked_sse_stream(self, endpoint, headers, body):
            return _FakeSse(lines)

    prov = _Prov(
        name="openai", display_name="OpenAI", default_model="gpt-4o",
        api_key="x", base_url="https://api.example.com/v1",
    )
    return list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}]))


def _anthropic_chunks(lines):
    class _Prov(AnthropicProvider):
        def _tracked_sse_stream(self, endpoint, headers, body):
            return _FakeSse(lines)

    prov = _Prov(api_key="x", base_url="https://api.anthropic.com/v1")
    return list(prov.stream_chat(
        messages=[{"role": "user", "content": "hi"}], thinking_effort="off",
    ))


def _gemini_chunks(lines):
    class _Prov(GeminiProvider):
        def _tracked_sse_stream(self, endpoint, headers, body):
            return _FakeSse(lines)

    prov = _Prov(api_key="x")
    return list(prov.stream_chat(messages=[{"role": "user", "content": "hi"}]))


def _data(payload):
    return "data: " + json.dumps(payload) + "\n"


class TestOpenAINativeStream(unittest.TestCase):
    def test_one_chunk_per_event_with_native_payload(self):
        lines = [
            _data({"id": "c1", "choices": [{"delta": {"content": "Hel"}}]}),
            _data({"id": "c1", "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
            _data({"id": "c1", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}),
            "data: [DONE]\n",
        ]
        chunks = _openai_chunks(lines)
        self.assertEqual(len(chunks), 3)
        self.assertEqual("".join(c.delta_text for c in chunks), "Hello")
        self.assertEqual(chunks[0].native["id"], "c1")
        self.assertEqual(chunks[1].native["choices"][0]["finish_reason"], "stop")
        # The usage-only event is a real stream event, not a dropped tail.
        self.assertEqual(chunks[2].usage["total_tokens"], 5)
        self.assertEqual(chunks[2].native["usage"]["total_tokens"], 5)
        # [DONE] must not synthesize a second stop after the native finish.
        self.assertEqual([c.finish_reason for c in chunks if c.finish_reason], ["stop"])

    def test_done_still_terminates_stream_without_a_finish_event(self):
        chunks = _openai_chunks([
            _data({"choices": [{"delta": {"content": "hi"}}]}),
            "data: [DONE]\n",
        ])
        self.assertEqual(chunks[0].delta_text, "hi")
        self.assertEqual(chunks[-1].finish_reason, "stop")
        self.assertEqual([c.finish_reason for c in chunks if c.finish_reason], ["stop"])


class TestAnthropicNativeStream(unittest.TestCase):
    def test_tool_start_announced_and_stop_not_duplicated(self):
        lines = [
            _data({
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "bash"},
            }),
            _data({
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"cmd": "ls"}'},
            }),
            _data({
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 12},
            }),
            _data({"type": "message_stop"}),
        ]
        chunks = _anthropic_chunks(lines)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].tool_calls[0].name, "bash")
        self.assertEqual(chunks[0].tool_calls[0].id, "toolu_1")
        self.assertEqual(chunks[1].tool_calls[0].arguments_delta, '{"cmd": "ls"}')
        self.assertEqual(chunks[2].finish_reason, "tool_use")
        self.assertEqual(chunks[2].usage["output_tokens"], 12)
        self.assertEqual(chunks[0].native["type"], "content_block_start")
        # message_delta already closed the stream; message_stop is not a chunk.
        self.assertNotIn("stop", [c.finish_reason for c in chunks])


class TestGeminiNativeStream(unittest.TestCase):
    def test_all_parts_of_one_event_merge_into_one_chunk(self):
        payload = {
            "candidates": [{
                "content": {"parts": [
                    {"text": "reasoning ", "thought": True},
                    {"text": "Hello "},
                    {"text": "world"},
                ]},
                "finishReason": "STOP",
            }],
        }
        chunks = _gemini_chunks([_data(payload)])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].delta_reasoning, "reasoning ")
        self.assertEqual(chunks[0].delta_text, "Hello world")
        self.assertEqual(chunks[0].finish_reason, "STOP")
        # The decoded native event is retained for consumers that need it.
        self.assertEqual(chunks[0].native, payload)


class TestDiscordStreamBuffer(unittest.TestCase):
    def test_small_stream_stays_in_one_part(self):
        buf = DiscordStreamBuffer(max_len=100)
        self.assertFalse(buf.append("Hello "))
        self.assertFalse(buf.append("world"))
        self.assertEqual(buf.parts, ["Hello world"])
        self.assertEqual(buf.full_text, "Hello world")

    def test_rollover_only_at_hard_limit_and_text_is_preserved(self):
        text = "word " * 60
        buf = DiscordStreamBuffer(max_len=100)
        for piece in (text[i:i + 17] for i in range(0, len(text), 17)):
            buf.append(piece)
        self.assertGreater(len(buf.parts), 1)
        for part in buf.parts:
            self.assertLessEqual(len(part), 90)  # max_len minus safety margin
        self.assertEqual(buf.full_text, text)

    def test_newline_rollover(self):
        buf = DiscordStreamBuffer(max_len=60)
        buf.append("a" * 40 + "\n" + "b" * 40)
        self.assertEqual(len(buf.parts), 2)
        self.assertTrue(buf.parts[0].endswith("a" * 40))
        self.assertEqual(buf.parts[1], "b" * 40)

    def test_mark_streamed_suppresses_final_response_only(self):
        state = DiscordRenderState()
        out = []
        out.extend(state.add_event(AgentEvent("reasoning_delta", "thinking...")))
        out.extend(state.add_event(AgentEvent("text_delta", "streamed live")))
        state.mark_response_streamed()
        out.extend(state.finish())
        self.assertTrue(any(o.kind == "thinking" for o in out))
        self.assertFalse(any(o.kind == "response" for o in out))


class TestCliStreaming(unittest.TestCase):
    def _renderer(self, raw=False):
        renderer = TerminalRenderer("cyberpunk", raw_stream=raw)
        buf = io.StringIO()
        from rich.console import Console
        renderer.console = Console(file=buf, force_terminal=False, color_system=None)
        return renderer, buf

    def test_raw_stream_writes_provider_deltas_verbatim(self):
        renderer, buf = self._renderer(raw=True)
        renderer.render_agent_event(AgentEvent("text_delta", "Hel"))
        renderer.render_agent_event(AgentEvent("text_delta", "lo **world**"))
        renderer.render_agent_event(AgentEvent("turn_complete", None))
        out = buf.getvalue()
        self.assertIn("Hello **world**", out)
        self.assertEqual(renderer._md_buffer, "")

    def test_newline_less_markdown_paints_before_completion(self):
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            renderer.render_agent_event(AgentEvent("text_delta", "Hello world this is a stream"))
            renderer.render_agent_event(AgentEvent("text_delta", " continuing"))
            before = buf.getvalue()
            self.assertIn("Hello", before)
            self.assertTrue(renderer._md_buffer)  # tail is still held
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            after = buf.getvalue()
            self.assertIn("continuing", after)
            # Text may be split across fallback paints but must never vanish.
            self.assertGreaterEqual(after.count("stream"), 1)
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_partial_inline_marker_is_not_split(self):
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            renderer.render_agent_event(AgentEvent("text_delta", "This is **bold"))
            renderer.render_agent_event(AgentEvent("text_delta", " text**"))
            out = buf.getvalue()
            self.assertNotIn("**bold", out)  # opener was held with its closer
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_newline_less_deltas_concatenate_inline(self):
        """Provider deltas without ``\\n`` must never become separate lines.

        Regression test for the greeting that streamed as ``Hi! I'm`` /
        ``Harness, your`` / ``agentic`` on consecutive lines: the fallback used
        to render each partial as its own Markdown block, and Rich appends a
        newline to every block render.
        """
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            for delta in ("Hi! I'm ", "Harness, your ", "agentic ",
                          "coding assistant."):
                renderer.render_agent_event(AgentEvent("text_delta", delta))
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            # A single logical line, with no auto-newline between deltas.
            self.assertEqual(
                buf.getvalue().strip(),
                "Hi! I'm Harness, your agentic coding assistant.",
            )
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_only_real_newline_breaks_an_inline_stream(self):
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            for delta in ("First part ", "still same ", "line\n",
                          "Second line ", "continues"):
                renderer.render_agent_event(AgentEvent("text_delta", delta))
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            self.assertEqual(
                buf.getvalue().strip(),
                "First part still same line\nSecond line continues",
            )
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_inline_stream_preserves_a_real_blank_line(self):
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            for delta in ("Hello ", "world", " again\n\nNext"):
                renderer.render_agent_event(AgentEvent("text_delta", delta))
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            self.assertEqual(
                buf.getvalue().strip(),
                "Hello world again\n\nNext",
            )
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_inline_stream_does_not_double_blank_before_a_list(self):
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            for delta in ("Intro continues ", "here.", "\n\n", "- item one\n",
                          "- item two\n"):
                renderer.render_agent_event(AgentEvent("text_delta", delta))
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            out = buf.getvalue()
            self.assertIn("Intro continues here.\n\n", out)
            self.assertNotIn("Intro continues here.\n\n\n", out)
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_fallback_list_items_stay_tight(self):
        """A streamed list must not become one Rich list per physical line.

        Each fresh Markdown list render carries its own leading blank line, so
        splitting a list across fallback paints used to put an empty line
        between every bullet.
        """
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            for interactive in (False, True):
                with self.subTest(interactive=interactive):
                    renderer, buf = self._renderer()
                    renderer.interactive_prompt = interactive
                    for delta in ("Intro.\n\n", "I can do:\n\n", "- one\n",
                                  "- two\n", "- three\n\n", "After."):
                        renderer.render_agent_event(AgentEvent("text_delta", delta))
                    renderer.render_agent_event(AgentEvent("turn_complete", None))
                    lines = [ln.strip() for ln in buf.getvalue().splitlines()]
                    first = lines.index("• one")
                    self.assertEqual(lines[first:first + 3], ["• one", "• two", "• three"])
                    # Exactly one separator between the list and the paragraph.
                    self.assertEqual(lines[first + 3:first + 5], ["", "After."])
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age

    def test_interactive_prompt_holds_partial_lines_until_a_real_newline(self):
        """Never flush a line fragment through prompt_toolkit's patch_stdout.

        patch_stdout erases and repaints the prompt around each write. A
        fragment without a trailing newline leaves the cursor mid-line and the
        repaint overwrites it from the left, which made responses appear to
        start mid-sentence.
        """
        old_age = terminal_mod._MD_FLUSH_MAX_AGE
        terminal_mod._MD_FLUSH_MAX_AGE = 0.0
        try:
            renderer, buf = self._renderer()
            renderer.interactive_prompt = True
            renderer.render_agent_event(AgentEvent("text_delta", "Hello "))
            renderer.render_agent_event(AgentEvent("text_delta", "world"))
            self.assertEqual(buf.getvalue(), "")
            renderer.render_agent_event(AgentEvent("text_delta", "\n"))
            out = buf.getvalue()
            self.assertEqual(out.strip(), "Hello world")
            renderer.render_agent_event(AgentEvent("turn_complete", None))
            self.assertEqual(buf.getvalue().strip(), "Hello world")
        finally:
            terminal_mod._MD_FLUSH_MAX_AGE = old_age


if __name__ == "__main__":
    unittest.main()
