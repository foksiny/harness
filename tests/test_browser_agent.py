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
from harness.tools.browser import (
    ALL_BROWSER_TOOLS,
    BrowserConsoleTool,
    BrowserNetworkTool,
    BrowserWaitTool,
)
from harness.browser.controller import BrowserController


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


class TestCdpEventBuffer(unittest.TestCase):
    """CDP event recording is pure formatting — no browser needed."""

    def _ctrl(self):
        return BrowserController(port=19222)

    def test_console_api_called_with_values(self):
        c = self._ctrl()
        c._record_event({"method": "Runtime.consoleAPICalled", "params": {
            "type": "error",
            "args": [{"type": "string", "value": "boom"},
                     {"type": "number", "value": 42}],
        }})
        entries = c.get_console_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "console")
        self.assertEqual(entries[0]["level"], "error")
        self.assertIn("boom", entries[0]["text"])
        self.assertIn("42", entries[0]["text"])

    def test_console_object_arg_uses_description(self):
        c = self._ctrl()
        c._record_event({"method": "Runtime.consoleAPICalled", "params": {
            "type": "log",
            "args": [{"type": "object", "subtype": "error",
                      "description": "TypeError: x is not a function"}],
        }})
        self.assertIn("TypeError", c.get_console_entries()[0]["text"])

    def test_exception_thrown_recorded_as_error(self):
        c = self._ctrl()
        c._record_event({"method": "Runtime.exceptionThrown", "params": {
            "exceptionDetails": {
                "text": "Uncaught",
                "url": "http://localhost:3000/src/main.js",
                "exception": {"type": "object", "description": "Error: mount failed"},
            },
        }})
        entries = c.get_console_entries()
        self.assertEqual(entries[0]["kind"], "exception")
        self.assertEqual(entries[0]["level"], "error")
        self.assertIn("mount failed", entries[0]["text"])

    def test_log_entry_recorded(self):
        c = self._ctrl()
        c._record_event({"method": "Log.entryAdded", "params": {
            "entry": {"level": "warning", "text": "Slow network", "url": "http://x/"},
        }})
        self.assertEqual(c.get_console_entries()[0]["kind"], "log")

    def test_level_filter(self):
        c = self._ctrl()
        c._record_event({"method": "Log.entryAdded", "params": {
            "entry": {"level": "info", "text": "hi"}}})
        c._record_event({"method": "Log.entryAdded", "params": {
            "entry": {"level": "error", "text": "bad"}}})
        self.assertEqual(len(c.get_console_entries(level="error")), 1)
        self.assertEqual(len(c.get_console_entries()), 2)

    def test_network_failures_and_bad_status(self):
        c = self._ctrl()
        c._record_event({"method": "Network.loadingFailed",
                         "params": {"errorText": "net::ERR_CONNECTION_REFUSED"}})
        c._record_event({"method": "Network.responseReceived", "params": {
            "response": {"status": 404, "statusText": "Not Found",
                         "url": "http://x/missing.js"}}})
        c._record_event({"method": "Network.responseReceived", "params": {
            "response": {"status": 200, "url": "http://x/ok.js"}}})
        nets = c.get_network_entries()
        self.assertEqual(len(nets), 2)  # the 200 is ignored
        self.assertIn("ERR_CONNECTION_REFUSED", nets[0]["text"])
        self.assertIn("404", nets[1]["text"])
        # Network entries never leak into the console view.
        self.assertEqual(c.get_console_entries(), [])

    def test_unknown_method_ignored(self):
        c = self._ctrl()
        c._record_event({"method": "Page.loadEventFired", "params": {}})
        self.assertEqual(c.get_console_entries(), [])
        self.assertEqual(c.get_network_entries(), [])

    def test_ring_buffer_bounded(self):
        c = self._ctrl()
        for i in range(320):
            c._record_event({"method": "Log.entryAdded", "params": {
                "entry": {"level": "info", "text": f"m{i}"}}})
        self.assertEqual(len(c._event_log), 300)
        self.assertEqual(c.clear_event_log(), 300)
        self.assertEqual(c.get_console_entries(), [])


class TestBrowserDebugTools(unittest.TestCase):
    """browser_console / browser_network / browser_wait via mock controllers."""

    def _entries(self):
        return [
            {"kind": "console", "level": "error", "ts": "10:00:01",
             "text": "TypeError: x is not a function", "url": "http://x/app.js"},
            {"kind": "exception", "level": "error", "ts": "10:00:02",
             "text": "Uncaught: mount failed", "url": ""},
        ]

    def test_console_get_formats_entries(self):
        ctrl = MagicMock()
        ctrl.get_console_entries.return_value = self._entries()
        tool = BrowserConsoleTool(controller_factory=lambda **kw: ctrl)
        out = tool.execute(action="get")
        self.assertIn("TypeError", out)
        self.assertIn("mount failed", out)
        self.assertIn("10:00:01", out)
        self.assertIn("http://x/app.js", out)

    def test_console_level_filter_forwarded(self):
        ctrl = MagicMock()
        ctrl.get_console_entries.return_value = []
        tool = BrowserConsoleTool(controller_factory=lambda **kw: ctrl)
        out = tool.execute(level="error", limit=10)
        ctrl.get_console_entries.assert_called_once_with(level="error", limit=10)
        self.assertIn("clean", out)

    def test_console_all_maps_to_no_filter(self):
        ctrl = MagicMock()
        ctrl.get_console_entries.return_value = []
        tool = BrowserConsoleTool(controller_factory=lambda **kw: ctrl)
        tool.execute(level="all")
        ctrl.get_console_entries.assert_called_once_with(level=None, limit=50)

    def test_console_clear(self):
        ctrl = MagicMock()
        ctrl.clear_event_log.return_value = 7
        tool = BrowserConsoleTool(controller_factory=lambda **kw: ctrl)
        out = tool.execute(action="clear")
        self.assertIn("7", out)
        ctrl.clear_event_log.assert_called_once_with()

    def test_console_requires_browser(self):
        tool = BrowserConsoleTool(controller_factory=lambda **kw: None)
        self.assertIn("browser_launch", tool.execute())

    def test_network_get_and_empty(self):
        ctrl = MagicMock()
        ctrl.get_network_entries.return_value = [
            {"kind": "network", "level": "error", "ts": "10:01:00",
             "text": "HTTP 404 Not Found", "url": "http://x/missing.js"},
        ]
        tool = BrowserNetworkTool(controller_factory=lambda **kw: ctrl)
        out = tool.execute()
        self.assertIn("404", out)
        self.assertIn("missing.js", out)

        ctrl.get_network_entries.return_value = []
        self.assertIn("No failed requests", tool.execute())

    def test_wait_selector_satisfied_and_timeout(self):
        ctrl = MagicMock()
        ctrl.wait_for.return_value = True
        tool = BrowserWaitTool(controller_factory=lambda **kw: ctrl)
        self.assertIn("satisfied", tool.execute(selector="#app"))
        ctrl.wait_for.assert_called_once_with("#app", 10000)

        ctrl.wait_for.return_value = False
        self.assertIn("Timed out", tool.execute(selector="#app", timeout_ms=1500))

    def test_wait_text_and_missing_condition(self):
        ctrl = MagicMock()
        ctrl.wait_for_text.return_value = True
        tool = BrowserWaitTool(controller_factory=lambda **kw: ctrl)
        self.assertIn("satisfied", tool.execute(text="Dashboard"))
        ctrl.wait_for_text.assert_called_once_with("Dashboard", 10000)
        self.assertIn("Provide", tool.execute())

    def test_debug_tools_registered_and_read_only(self):
        names = [t.name for t in ALL_BROWSER_TOOLS]
        for expected in ("browser_console", "browser_network", "browser_wait"):
            self.assertIn(expected, names)
        for tool_cls in (BrowserConsoleTool, BrowserNetworkTool, BrowserWaitTool):
            tool = tool_cls(controller_factory=lambda **kw: MagicMock())
            self.assertTrue(tool.is_read_only)
            self.assertEqual(tool.action_type, "browser_read")
            schema = tool.to_openai_schema()
            self.assertEqual(schema["function"]["name"], tool.name)


if __name__ == "__main__":
    unittest.main()
