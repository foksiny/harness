"""
Agent-level tests for screenshot routing: vision models receive image blocks
in the tool result, non-vision models get a VFB text description when a
vision-fallback model is configured, else a plain path reference. Plus
provider payload shapes for multimodal tool results.
"""
import os
import base64
import tempfile
import unittest
from unittest.mock import MagicMock

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.core.permissions import PermissionManager, PermissionLevel
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.providers.mock_provider import MockProvider
from harness.providers.detector import ModelSpec, inspect_model
from harness.providers.anthropic_provider import AnthropicProvider
from harness.providers.gemini_provider import GeminiProvider
from harness.providers.openai_compatible import OpenAICompatibleProvider
from harness.tools.browser import BrowserScreenshotTool


TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwAD"
    "hgGAWjR9awAAAABJRU5ErkJggg=="
)


class VfbMockProvider(MockProvider):
    """Vision-capable mock used as the fallback describer."""

    VFB_MODEL = "gemma-3-27b-it"
    VFB_DESCRIPTION = "A red apple resting on a weathered wooden table under soft window lighting."

    def get_model_spec(self, model_name=None):
        m = (model_name or self.default_model).strip()
        if m == self.VFB_MODEL:
            return ModelSpec(
                name=m, provider="mock", context_window=128000,
                max_output_tokens=8192, supports_thinking=True,
                thinking_type="reasoning_effort",
                supports_vision=True, supports_video=True,
            )
        return inspect_model(m, self.name)

    def stream_chat(self, messages, model=None, **kwargs):
        if model == self.VFB_MODEL:
            yield LLMChunk(delta_text=self.VFB_DESCRIPTION)
            yield LLMChunk(finish_reason="stop")
            return
        return super().stream_chat(messages, model=model, **kwargs)


def _screenshot_responses():
    return [
        LLMChunk(tool_calls=[ToolCallDelta(
            index=0, id="call_shot_1", name="browser_screenshot", arguments_delta="{}")]),
        LLMChunk(delta_text="done", finish_reason="stop"),
    ]


class OneShotToolMockProvider(MockProvider):
    """Emits one browser_screenshot tool call, then a final text answer.

    (MockProvider replays preset ``responses`` on *every* stream_chat call,
    which would re-trigger the tool call forever — this override fires once.)
    """

    def __init__(self):
        super().__init__(responses=[])
        self._streams = 0

    def stream_chat(self, messages, model=None, **kwargs):
        self.call_history.append({
            "messages": list(messages),
            "model": model,
            "tools": kwargs.get("tools"),
            "system_prompt": kwargs.get("system_prompt"),
        })
        self._streams += 1
        if self._streams == 1:
            yield LLMChunk(tool_calls=[ToolCallDelta(
                index=0, id="call_shot_1", name="browser_screenshot",
                arguments_delta="{}")])
            return
        yield LLMChunk(delta_text="done", finish_reason="stop")


def _agent(model, png_path, vfb=False):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.learning_enabled = False
    cfg.vfb_provider = "mock" if vfb else ""
    cfg.vfb_model = VfbMockProvider.VFB_MODEL if vfb else ""
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    agent.session.model = model
    agent.provider = OneShotToolMockProvider()
    if vfb:
        agent._vfb_provider_override = VfbMockProvider()

    ctrl = MagicMock()
    ctrl.screenshot_to_file.return_value = png_path
    ctrl.get_url.return_value = "https://example.com"
    ctrl.get_title.return_value = "Example Domain"
    agent.tool_registry.register(BrowserScreenshotTool(
        controller_factory=lambda **kw: ctrl,
        permission_manager=agent.permission_manager,
    ))
    return agent


class TestScreenshotRouting(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.png = os.path.join(self.td.name, "shot.png")
        with open(self.png, "wb") as f:
            f.write(base64.b64decode(TINY_PNG_B64))

    def tearDown(self):
        self.td.cleanup()

    def _shot_messages(self, agent):
        return [m for m in agent.session.messages
                if m.get("role") == "tool" and m.get("name") == "browser_screenshot"]

    def test_vision_model_gets_image_blocks(self):
        agent = _agent("gpt-4o", self.png)
        events = list(agent.step("take a screenshot"))
        shots = self._shot_messages(agent)
        self.assertEqual(len(shots), 1)
        content = shots[0]["content"]
        self.assertIsInstance(content, list)
        by_type = {b["type"]: b for b in content}
        self.assertIn("text", by_type)
        self.assertIn("image", by_type)
        self.assertEqual(by_type["image"]["path"], self.png)
        # UX event announced the attached image; no VFB involved.
        self.assertTrue(any(e.type == "attachment" for e in events))
        self.assertFalse(any(e.type in ("vfb_notice", "vfb_result") for e in events))
        # The displayed tool result carries no raw marker.
        results = [e for e in events if e.type == "tool_call_result" and e.data.get("name") == "browser_screenshot"]
        self.assertEqual(len(results), 1)
        self.assertNotIn("[harness:image:", results[0].data["result"])
        self.assertIn("Screenshot saved to", results[0].data["result"])

    def test_non_vision_model_uses_vfb_description(self):
        agent = _agent("deepseek-chat", self.png, vfb=True)
        events = list(agent.step("take a screenshot"))
        shots = self._shot_messages(agent)
        self.assertEqual(len(shots), 1)
        content = shots[0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn(VfbMockProvider.VFB_DESCRIPTION, content)
        etypes = [e.type for e in events]
        self.assertIn("vfb_notice", etypes)
        self.assertIn("vfb_result", etypes)
        self.assertFalse(any(e.type == "attachment" for e in events))

    def test_non_vision_without_vfb_gets_plain_path_reference(self):
        agent = _agent("deepseek-chat", self.png, vfb=False)
        events = list(agent.step("take a screenshot"))
        shots = self._shot_messages(agent)
        self.assertEqual(len(shots), 1)
        content = shots[0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn(".png", content)
        self.assertNotIn(VfbMockProvider.VFB_DESCRIPTION, content)
        self.assertFalse(any(e.type in ("vfb_notice", "vfb_result", "attachment") for e in events))

    def test_force_media_attach_overrides_non_vision(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        cfg.force_media_attach = True
        agent = HarnessAgent(cfg)
        agent.ensure_session()
        agent.session.model = "deepseek-chat"
        agent.provider = OneShotToolMockProvider()
        ctrl = MagicMock()
        ctrl.screenshot_to_file.return_value = self.png
        ctrl.get_url.return_value = "https://example.com"
        ctrl.get_title.return_value = "Example Domain"
        agent.tool_registry.register(BrowserScreenshotTool(
            controller_factory=lambda **kw: ctrl,
            permission_manager=agent.permission_manager,
        ))
        list(agent.step("take a screenshot"))
        shots = [m for m in agent.session.messages
                 if m.get("role") == "tool" and m.get("name") == "browser_screenshot"]
        self.assertEqual(len(shots), 1)
        self.assertIsInstance(shots[0]["content"], list)
        self.assertIn("image", [b["type"] for b in shots[0]["content"]])


def _tool_conversation(png):
    assistant = {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_shot_1", "type": "function",
                        "function": {"name": "browser_screenshot", "arguments": "{}"}}],
    }
    tool = {
        "role": "tool", "tool_call_id": "call_shot_1", "name": "browser_screenshot",
        "content": [{"type": "text", "text": "Screenshot saved to: X"},
                    {"type": "image", "path": png}],
    }
    return [{"role": "user", "content": "take a screenshot"}, assistant, tool]


class TestMultimodalToolResultPayloads(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.png = os.path.join(self.td.name, "shot.png")
        with open(self.png, "wb") as f:
            f.write(base64.b64decode(TINY_PNG_B64))

    def tearDown(self):
        self.td.cleanup()

    def test_anthropic_tool_result_carries_image_block(self):
        p = AnthropicProvider(api_key="x")
        msgs = p._convert_messages(_tool_conversation(self.png))
        tr = msgs[-1]["content"][0]
        self.assertEqual(tr["type"], "tool_result")
        self.assertEqual(tr["tool_use_id"], "call_shot_1")
        by_type = {b["type"]: b for b in tr["content"]}
        self.assertIn("text", by_type)
        self.assertIn("image", by_type)
        self.assertEqual(by_type["image"]["source"]["media_type"], "image/png")
        self.assertTrue(by_type["image"]["source"]["data"].startswith("iVBOR"))

    def test_anthropic_missing_image_degrades_to_text(self):
        p = AnthropicProvider(api_key="x")
        msgs = p._convert_messages(_tool_conversation("/no/such/shot.png"))
        tr = msgs[-1]["content"][0]
        texts = [b for b in tr["content"] if b["type"] == "text"]
        self.assertTrue(any("unavailable" in b["text"] for b in texts))

    def test_anthropic_plain_string_tool_result_unchanged(self):
        p = AnthropicProvider(api_key="x")
        msgs = p._convert_messages([
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "name": "t", "content": "plain"},
        ])
        self.assertEqual(msgs[-1]["content"][0]["content"], "plain")

    def test_openai_tool_result_splits_media_to_followup_user_msg(self):
        p = OpenAICompatibleProvider(name="t", display_name="T",
                                     default_model="m", base_url="http://x")
        payload = p._build_messages_payload(_tool_conversation(self.png))
        tool_msg = [m for m in payload if m.get("role") == "tool"][0]
        self.assertIsInstance(tool_msg["content"], str)
        self.assertIn("Screenshot saved to", tool_msg["content"])
        followups = [m for m in payload
                     if m.get("role") == "user" and isinstance(m.get("content"), list)]
        self.assertEqual(len(followups), 1)
        imgs = [b for b in followups[0]["content"] if b.get("type") == "image_url"]
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0]["image_url"]["url"].startswith("data:image/png;base64,iVBOR"))

    def test_openai_plain_string_tool_result_unchanged(self):
        p = OpenAICompatibleProvider(name="t", display_name="T",
                                     default_model="m", base_url="http://x")
        payload = p._build_messages_payload([
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "name": "t", "content": "plain"},
        ])
        self.assertEqual(payload[-1]["content"], "plain")
        self.assertEqual(len(payload), 2)

    def test_gemini_tool_result_splits_media_to_followup(self):
        p = GeminiProvider(api_key="x")
        contents = p._convert_contents(_tool_conversation(self.png))
        fn = [c for c in contents if any("functionResponse" in part for part in c["parts"])][0]
        out = fn["parts"][0]["functionResponse"]["response"]["output"]
        self.assertIn("Screenshot saved to", out)
        followups = [c for c in contents
                     if c["role"] == "user"
                     and any("inline_data" in part for part in c["parts"])]
        self.assertEqual(len(followups), 1)
        inline = [part["inline_data"] for part in followups[0]["parts"] if "inline_data" in part][0]
        self.assertEqual(inline["mime_type"], "image/png")
        self.assertTrue(inline["data"].startswith("iVBOR"))

    def test_gemini_plain_string_tool_result_unchanged(self):
        p = GeminiProvider(api_key="x")
        contents = p._convert_contents([
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "name": "t", "content": "plain"},
        ])
        self.assertEqual(contents[-1]["parts"][0]["functionResponse"]["response"]["output"], "plain")


if __name__ == "__main__":
    unittest.main()
