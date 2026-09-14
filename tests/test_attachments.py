"""
Tests for media attachment support: path/paste/swipe detection, provider
multimodal serialization, and agent-side vision/video gating.
"""
import os
import json
import tempfile
import unittest

from harness.core.attachments import (
    parse_attachments,
    data_url_for_block,
    b64_payload_for_block,
    mime_type_for,
    media_kind_for_path,
    MAX_INLINE_BYTES,
)
from harness.providers.anthropic_provider import AnthropicProvider
from harness.providers.gemini_provider import GeminiProvider
from harness.providers.openai_compatible import OpenAICompatibleProvider
from harness.providers.mock_provider import MockProvider
from harness.providers.base import LLMChunk
from harness.providers.detector import ModelSpec, inspect_model
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent


def make_file(name: str, data: bytes = b"fake bytes") -> str:
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "wb") as f:
        f.write(data)
    return path


class TestParseAttachments(unittest.TestCase):

    def setUp(self):
        self.img = make_file("harness_test.png")
        self.vid = make_file("harness_test.mp4")
        self.cwd = tempfile.gettempdir()

    def tearDown(self):  # pragma: no cover - cleanup best effort
        for p in (self.img, self.vid):
            try:
                os.remove(p)
            except OSError:
                pass

    def test_detects_bare_and_relative_paths(self):
        text = f"look at {os.path.basename(self.img)} and the clip {os.path.basename(self.vid)}"
        clean, blocks, warnings = parse_attachments(text, self.cwd)
        types = [b["type"] for b in blocks]
        self.assertEqual(types, ["image", "video"])
        self.assertEqual(blocks[0]["path"], os.path.abspath(self.img))
        self.assertEqual(warnings, [])
        self.assertNotIn(".png", clean)
        self.assertNotIn(".mp4", clean)

    def test_absolute_path_and_trailing_punctuation(self):
        text = f"analyze {self.img}. thanks"
        clean, blocks, _ = parse_attachments(text, self.cwd)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "image")
        self.assertNotIn("harness_test.png", clean)
        # the glued period travels with the token, leaving normal spacing
        self.assertEqual(clean.strip(), "analyze  thanks")

    def test_file_uri_scheme(self):
        text = f"see file://{self.img}"
        clean, blocks, _ = parse_attachments(text, self.cwd)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["path"], os.path.abspath(self.img))
        self.assertNotIn("file://", clean)

    def test_missing_file_stays_as_plain_text(self):
        text = "find missing.png but keep as text"
        clean, blocks, _ = parse_attachments(text, self.cwd)
        self.assertEqual(blocks, [])
        self.assertEqual(clean, text)

    def test_data_uri_paste_supported(self):
        uri = "data:image/png;base64,aGVsbG8="
        clean, blocks, _ = parse_attachments(f"decode {uri}", self.cwd)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["data_uri"], uri)
        self.assertEqual(data_url_for_block(blocks[0]), uri)
        mime, b64 = b64_payload_for_block(blocks[0])
        self.assertEqual(mime, "image/png")
        self.assertEqual(b64, "aGVsbG8=")

    def test_oversized_file_flagged_and_kept_in_text(self):
        big = make_file("harness_big.png", b"x" * (MAX_INLINE_BYTES + 1))
        try:
            text = f"big {big}"
            clean, blocks, warnings = parse_attachments(text, self.cwd)
            self.assertEqual(blocks, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("over", warnings[0].lower())
            self.assertIn("harness_big.png", clean)
        finally:
            try:
                os.remove(big)
            except OSError:
                pass

    def test_spaced_filename_joined(self):
        spaced = make_file("harness spaced photo.jpg")
        try:
            text = f"see harness spaced photo.jpg now"
            clean, blocks, _ = parse_attachments(text, self.cwd)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["path"], os.path.abspath(spaced))
        finally:
            try:
                os.remove(spaced)
            except OSError:
                pass

    def test_repeated_path_deduplicated(self):
        text = f"see {self.img} again {self.img}"
        clean, blocks, _ = parse_attachments(text, self.cwd)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn(".png", clean)


class TestAttachmentDataHelpers(unittest.TestCase):

    def test_mime_and_kind(self):
        self.assertEqual(media_kind_for_path("a.PNG"), "image")
        self.assertEqual(media_kind_for_path("b.webm"), "video")
        self.assertIsNone(media_kind_for_path("c.txt"))
        self.assertEqual(mime_type_for("x.png"), "image/png")
        self.assertEqual(mime_type_for("x.mov"), "video/quicktime")

    def test_data_url_and_b64(self):
        path = make_file("harness_helper2.png", b"abcdef")
        try:
            blk = {"type": "image", "path": path}
            url = data_url_for_block(blk)
            self.assertTrue(url.startswith("data:image/png;base64,"))
            mime, b64 = b64_payload_for_block(blk)
            self.assertEqual(mime, "image/png")
            self.assertEqual(b64, "YWJjZGVm")
            self.assertEqual((None, None), b64_payload_for_block({"type": "image", "path": "/nope.png"}))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class TestProviderSerialization(unittest.TestCase):

    def setUp(self):
        self.img = make_file("harness_prov.png", b"img")
        self.vid = make_file("harness_prov.mp4", b"vid")
        self.blocks = [
            {"type": "text", "text": "describe"},
            {"type": "image", "path": self.img},
            {"type": "video", "path": self.vid},
        ]

    def tearDown(self):  # pragma: no cover
        for p in (self.img, self.vid):
            try:
                os.remove(p)
            except OSError:
                pass

    def test_anthropic_conversion(self):
        prov = AnthropicProvider()
        out = prov._convert_messages([{"role": "user", "content": self.blocks}])
        content = out[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["media_type"], "image/png")
        self.assertEqual(content[1]["source"]["type"], "base64")
        self.assertEqual(content[2]["type"], "video")
        self.assertEqual(content[2]["source"]["media_type"], "video/mp4")

    def test_anthropic_plain_string_passthrough(self):
        prov = AnthropicProvider()
        out = prov._convert_messages([{"role": "user", "content": "hello"}])
        self.assertEqual(out[0]["content"], "hello")

    def test_gemini_conversion(self):
        prov = GeminiProvider()
        out = prov._convert_contents([{"role": "user", "content": self.blocks}])
        parts = out[0]["parts"]
        self.assertEqual(parts[0], {"text": "describe"})
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[2]["inline_data"]["mime_type"], "video/mp4")

    def test_openai_compatible_conversion(self):
        prov = OpenAICompatibleProvider("t", "T", "m", "http://localhost")
        out = prov._build_messages_payload([{"role": "user", "content": self.blocks}])
        content = out[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(content[2]["type"], "input_video")


class TestAgentAttachmentFlow(unittest.TestCase):

    def _agent(self, model: str) -> HarnessAgent:
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.session.model = model
        agent.provider = MockProvider(responses=[LLMChunk(delta_text="ok", finish_reason="stop")])
        return agent

    def setUp(self):
        self.img = make_file("harness_flow.png", b"imgdata")

    def tearDown(self):  # pragma: no cover
        try:
            os.remove(self.img)
        except OSError:
            pass

    def test_vision_model_attaches_image_as_multimodal_content(self):
        agent = self._agent("gpt-4o")
        events = list(agent.step(f"analyze {self.img}"))
        attaches = [e for e in events if e.type == "attachment"]
        self.assertEqual(len(attaches), 1)
        self.assertEqual(attaches[0].data["files"][0]["type"], "image")
        user_msg = agent.session.messages[0]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        types = [b["type"] for b in user_msg["content"]]
        self.assertIn("image", types)
        self.assertIn("text", types)
        # Provider receives the canonical blocks.
        last_call = agent.provider.call_history[-1]["messages"]
        self.assertIsInstance(last_call[0]["content"], list)

    def test_non_vision_model_keeps_path_as_text_with_warning(self):
        agent = self._agent("deepseek-chat")
        events = list(agent.step(f"analyze {self.img}"))
        warnings = [e for e in events if e.type == "attachment_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("not detected as image-capable", warnings[0].data["message"])
        user_msg = agent.session.messages[0]
        self.assertIsInstance(user_msg["content"], str)
        self.assertIn(".png", user_msg["content"])

    def test_supported_image_plus_unsupported_video(self):
        vid = make_file("harness_flow.mp4", b"vid")
        try:
            agent = self._agent("gemini-2.5-pro")
            events = list(agent.step(f"see {self.img} and {vid}"))
            attaches = [e for e in events if e.type == "attachment"]
            warnings = [e for e in events if e.type == "attachment_warning"]
            self.assertEqual(len(attaches), 1)
            self.assertEqual(len(warnings), 1)
            self.assertIn("not detected as video-capable", warnings[0].data["message"])
            types = [b["type"] for b in agent.session.messages[0]["content"]]
            self.assertIn("image", types)
            self.assertNotIn("video", types)
        finally:
            try:
                os.remove(vid)
            except OSError:
                pass

    def test_no_media_in_prompt_unchanged(self):
        agent = self._agent("deepseek-chat")
        events = list(agent.step("just a plain request"))
        self.assertFalse(any(e.type in ("attachment", "attachment_warning") for e in events))
        self.assertEqual(agent.session.messages[0]["content"], "just a plain request")

    def test_force_media_attach_overrides_non_vision_model(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.force_media_attach = True
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.session.model = "deepseek-chat"
        agent.provider = MockProvider(responses=[LLMChunk(delta_text="ok", finish_reason="stop")])
        events = list(agent.step(f"analyze {self.img}"))
        self.assertTrue(any(e.type == "attachment" for e in events))
        self.assertFalse(any(e.type == "attachment_warning" for e in events))
        content = agent.session.messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("image", [b["type"] for b in content])


class VfbMockProvider(MockProvider):
    """Mock that is vision-capable as a specific model and replies with a
    canned precise description for that model (used as the fallback)."""

    VFB_MODEL = "gemma-3-27b-it"
    VFB_DESCRIPTION = "A red apple resting on a weathered wooden table under soft window lighting."

    def get_model_spec(self, model_name=None):
        m = (model_name or self.default_model).strip()
        if m == self.VFB_MODEL:
            return ModelSpec(
                name=m,
                provider="mock",
                context_window=128000,
                max_output_tokens=8192,
                supports_thinking=True,
                thinking_type="reasoning_effort",
                supports_vision=True,
                supports_video=True,
            )
        return inspect_model(m, self.name)

    def stream_chat(self, messages, model=None, **kwargs):
        if model == self.VFB_MODEL:
            yield LLMChunk(delta_text=self.VFB_DESCRIPTION)
            yield LLMChunk(finish_reason="stop")
            return
        return super().stream_chat(messages, model=model, **kwargs)


class BrokenVfbMockProvider(VfbMockProvider):
    def stream_chat(self, messages, model=None, **kwargs):
        if model == self.VFB_MODEL:
            raise RuntimeError("network down")
        return super().stream_chat(messages, model=model, **kwargs)


class TestVisionFallback(unittest.TestCase):

    def setUp(self):
        self.img = make_file("harness_vfb.png", b"imgdata")

    def tearDown(self):  # pragma: no cover
        try:
            os.remove(self.img)
        except OSError:
            pass

    def _agent(self, vfb_provider=None, vfb_model="", main_model="deepseek-chat"):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.vfb_provider = vfb_provider or ""
        cfg.vfb_model = vfb_model
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.session.model = main_model
        agent.provider = MockProvider(responses=[LLMChunk(delta_text="ok", finish_reason="stop")])
        if vfb_provider:
            agent._vfb_provider_override = VfbMockProvider()
        return agent

    def test_degraded_image_described_by_vfb_and_embedded_as_text(self):
        agent = self._agent(vfb_provider="mock", vfb_model=VfbMockProvider.VFB_MODEL)
        events = list(agent.step(f"analyze {self.img}"))
        etypes = [e.type for e in events]
        self.assertIn("vfb_notice", etypes)
        self.assertIn("vfb_result", etypes)
        # User told the fallback model is being used, with no scary degrade warning.
        self.assertNotIn("attachment_warning", etypes)
        # The non-vision model receives the description as plain text, no raw path leak.
        content = agent.session.messages[0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn(VfbMockProvider.VFB_DESCRIPTION, content)
        self.assertNotIn(".png", content)
        # And that text is what actually went to the main provider.
        self.assertIn(VfbMockProvider.VFB_DESCRIPTION, agent.provider.call_history[-1]["messages"][0]["content"])
        notice = [e for e in events if e.type == "vfb_notice"][0].data
        self.assertEqual(notice["labels"], "image")
        self.assertIn(self.img, notice["files"][0])

    def test_vfb_not_vision_capable_falls_back_to_text_path(self):
        agent = self._agent(vfb_provider="mock", vfb_model="deepseek-chat")
        events = list(agent.step(f"analyze {self.img}"))
        self.assertFalse(any(e.type == "vfb_notice" for e in events))
        warnings = [e for e in events if e.type == "attachment_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("not detected as image-capable", warnings[0].data["message"])
        content = agent.session.messages[0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn(".png", content)
        self.assertNotIn(VfbMockProvider.VFB_DESCRIPTION, content)

    def test_vfb_call_failure_falls_back_to_text_path(self):
        agent = self._agent(vfb_provider="mock", vfb_model=VfbMockProvider.VFB_MODEL)
        agent._vfb_provider_override = BrokenVfbMockProvider()
        events = list(agent.step(f"analyze {self.img}"))
        warnings = [e for e in events if e.type == "attachment_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("failed while describing", warnings[0].data["message"])
        content = agent.session.messages[0]["content"]
        self.assertIn(".png", content)

    def test_vfb_unused_when_main_model_is_vision_capable(self):
        agent = self._agent(vfb_provider="mock", vfb_model=VfbMockProvider.VFB_MODEL, main_model="gpt-4o")
        events = list(agent.step(f"analyze {self.img}"))
        self.assertFalse(any(e.type in ("vfb_notice", "vfb_result") for e in events))
        content = agent.session.messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("image", [b["type"] for b in content])
        self.assertNotIn(VfbMockProvider.VFB_DESCRIPTION, str(content))

    def test_vfb_unconfigured_keeps_legacy_degrade(self):
        agent = self._agent(vfb_provider="", vfb_model="")
        events = list(agent.step(f"analyze {self.img}"))
        self.assertFalse(any(e.type in ("vfb_notice", "vfb_result") for e in events))
        warnings = [e for e in events if e.type == "attachment_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("text path reference", warnings[0].data["message"])
        self.assertIn(".png", agent.session.messages[0]["content"])


class TestVfbConfigFields(unittest.TestCase):

    def test_vfb_fields_set_and_roundtrip(self):
        cfg = HarnessConfig()
        self.assertTrue(cfg.set_field("vfb_provider", "openrouter"))
        self.assertTrue(cfg.set_field("vfb_model", "openai/gpt-4o"))
        self.assertEqual(cfg.vfb_provider, "openrouter")
        self.assertEqual(cfg.vfb_model, "openai/gpt-4o")
        restored = HarnessConfig.from_dict(cfg.to_dict())
        self.assertEqual(restored.vfb_provider, "openrouter")
        self.assertEqual(restored.vfb_model, "openai/gpt-4o")


if __name__ == "__main__":
    unittest.main()