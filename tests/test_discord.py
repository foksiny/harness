"""
Unit tests for the Discord integration: config token lifecycle, event rendering
(thinking as > quotes, full response at end, tool notices), message chunking,
sync relay, and key store validation.
"""
import unittest
import tempfile
import json
import os
import time
from pathlib import Path

from harness.config import HarnessConfig, save_config, load_config, mask_key
from harness import secure_store
from harness.core.agent import HarnessAgent
from harness.discord.renderer import (
    DiscordRenderState,
    DiscordOutgoing,
    chunk_message,
    chunk_quote,
)
from harness.discord.sync import MessageRelay, get_relay, SyncBus, MESSAGE, OUTPUT, STATE, STOP


class _EV:
    """Minimal stand-in for AgentEvent."""

    def __init__(self, type, data=None):
        self.type = type
        self.data = data


class TestDiscordConfig(unittest.TestCase):

    def setUp(self):
        secure_store.remove_key("discord")
        self._old_token = os.environ.get("DISCORD_BOT_TOKEN")
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        self._old_discord = os.environ.get("DISCORD_TOKEN")
        os.environ.pop("DISCORD_TOKEN", None)

    def tearDown(self):
        secure_store.remove_key("discord")
        if self._old_token is None:
            os.environ.pop("DISCORD_BOT_TOKEN", None)
        else:
            os.environ["DISCORD_BOT_TOKEN"] = self._old_token
        if self._old_discord is None:
            os.environ.pop("DISCORD_TOKEN", None)
        else:
            os.environ["DISCORD_TOKEN"] = self._old_discord

    def test_token_lifecycle_secure_store(self):
        cfg = HarnessConfig()
        self.assertIsNone(cfg.get_discord_token())
        cfg.set_discord_token("my-bot-token-12345")
        self.assertEqual(cfg.get_discord_token(), "my-bot-token-12345")
        self.assertEqual(cfg.discord_bot_token, "my-bot-token-12345")
        self.assertTrue(cfg.remove_discord_token())
        self.assertIsNone(cfg.get_discord_token())

    def test_token_env_fallback(self):
        cfg = HarnessConfig()
        os.environ["DISCORD_BOT_TOKEN"] = "env-token-value"
        self.assertEqual(cfg.get_discord_token(), "env-token-value")

    def test_set_field_routes_token_to_secure_store(self):
        cfg = HarnessConfig()
        self.assertTrue(cfg.set_field("discord_bot_token", "field-token"))
        self.assertEqual(cfg.get_discord_token(), "field-token")

    def test_channel_ids_normalization(self):
        cfg = HarnessConfig()
        cfg.set_discord_channel_ids("111, 222 ,333")
        self.assertEqual(cfg.discord_channel_ids, "111,222,333")
        self.assertTrue(cfg.set_field("discord_channel_ids", " a , b "))
        self.assertEqual(cfg.discord_channel_ids, "a,b")

    def test_save_config_strips_token(self):
        with tempfile.TemporaryDirectory() as td:
            # Point config paths at a temp dir via monkeypatching the module constants
            import harness.config as config_mod
            old_user = config_mod.USER_CONFIG_PATH
            config_mod.USER_CONFIG_PATH = Path(td) / "config.json"
            try:
                cfg = HarnessConfig()
                cfg.set_discord_token("secret-token-abc")
                cfg.set_field("theme", "nord")
                save_config(cfg)
                raw = json.loads((Path(td) / "config.json").read_text())
                self.assertNotIn("discord_bot_token", raw)
                self.assertEqual(raw.get("theme"), "nord")
                # Token survives a reload from the secure store
                reloaded = load_config()
                self.assertEqual(reloaded.get_discord_token(), "secret-token-abc")
            finally:
                config_mod.USER_CONFIG_PATH = old_user

    def test_default_discord_fields(self):
        cfg = HarnessConfig()
        self.assertEqual(cfg.discord_permission, "full")
        self.assertEqual(cfg.discord_channel_ids, "")
        self.assertEqual(cfg.discord_workspace, "")


class TestDiscordMessageChunking(unittest.TestCase):

    def test_chunk_message_small(self):
        self.assertEqual(chunk_message("hi"), ["hi"])
        self.assertEqual(chunk_message(""), [])

    def test_chunk_message_splits_long(self):
        text = "\n".join(f"line {i}" + "x" * 40 for i in range(80))
        chunks = chunk_message(text, 400)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 400)
        self.assertEqual("".join(chunks), text)

    def test_chunk_quote_prefixes_lines(self):
        chunks = chunk_quote("one\ntwo\nthree")
        self.assertEqual(chunks, ["> one\n> two\n> three"])

    def test_chunk_quote_respects_limit_with_prefix(self):
        lines = [f"line-{i} " + "y" * 50 for i in range(60)]
        text = "\n".join(lines)
        chunks = chunk_quote(text, 300)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 300)
            for ln in c.split("\n"):
                self.assertTrue(ln.startswith("> "))
        # Concatenating the un-prefixed lines reproduces the original text
        joined = "\n".join(
            line[2:] if line.startswith("> ") else line
            for c in chunks
            for line in c.split("\n")
        )
        self.assertEqual(joined, text)


class TestDiscordRenderState(unittest.TestCase):

    def _collect(self, events):
        s = DiscordRenderState()
        out = []
        for ev in events:
            out.extend(s.add_event(ev))
        out.extend(s.finish())
        return out

    def test_thinking_as_quote_and_response_at_end(self):
        events = [
            _EV("reasoning_delta", "Analyzing the request carefully..."),
            _EV("reasoning_delta", " More detail."),
            _EV("text_delta", "Here is the final"),
            _EV("text_delta", " answer."),
            _EV("turn_complete", None),
        ]
        out = self._collect(events)
        kinds = [o.kind for o in out]
        self.assertIn("thinking", kinds)
        self.assertIn("response", kinds)
        thinking = next(o for o in out if o.kind == "thinking")
        self.assertIn("Analyzing the request carefully...", thinking.text)
        response = next(o for o in out if o.kind == "response")
        self.assertEqual(response.text, "Here is the final answer.")

    def test_response_not_streamed(self):
        # text_delta events should NOT each produce a message; the full response
        # is only flushed at the end.
        events = [
            _EV("text_delta", "part1 "),
            _EV("text_delta", "part2 "),
            _EV("text_delta", "part3"),
        ]
        s = DiscordRenderState()
        out = []
        for ev in events:
            out.extend(s.add_event(ev))
        self.assertEqual(out, [])  # nothing sent while streaming
        out.extend(s.finish())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, "response")
        self.assertEqual(out[0].text, "part1 part2 part3")

    def test_tool_usage_notice(self):
        events = [
            _EV("tool_call_start", {"name": "read_file", "arguments": {"path": "a.py"}}),
            _EV("tool_call_result", {"name": "read_file", "result": "file ok\nmore lines"}),
        ]
        out = self._collect(events)
        notices = [o for o in out if o.kind == "notice"]
        self.assertTrue(any("read_file" in n.text for n in notices))
        self.assertTrue(any(n.text.startswith("✔ file ok") for n in notices))

    def test_mention_and_attachment_events(self):
        events = [
            _EV("mention", {"original": "@a.py", "resolved": "/abs/a.py", "kind": "file", "success": True}),
            _EV("attachment", {"files": [{"type": "image", "path": "/abs/img.png"}]}),
        ]
        out = self._collect(events)
        self.assertTrue(any(o.kind == "attachment" and "a.py" in o.text for o in out))
        self.assertTrue(any(o.kind == "attachment" and "image" in o.text for o in out))

    def test_multi_phase_thinking(self):
        events = [
            _EV("reasoning_delta", "think one"),
            _EV("tool_call_start", {"name": "list_dir", "arguments": {}}),
            _EV("reasoning_delta", "think two"),
            _EV("text_delta", "done"),
        ]
        out = self._collect(events)
        thinkings = [o for o in out if o.kind == "thinking"]
        self.assertEqual(len(thinkings), 2)
        self.assertIn("think one", thinkings[0].text)
        self.assertIn("think two", thinkings[1].text)

    def test_compaction_notice(self):
        events = [
            _EV("compaction", {"before_tokens": 1000, "after_tokens": 500}),
        ]
        out = self._collect(events)
        notices = [o for o in out if o.kind == "notice"]
        self.assertTrue(any("compacted" in n.text for n in notices))


class TestDiscordModuleGuard(unittest.TestCase):
    """The bot module must degrade gracefully when discord.py is not installed."""

    def test_import_does_not_require_discord(self):
        try:
            import discord  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        # This test only asserts meaningful behaviour when discord.py is absent.
        if not installed:
            from harness.discord.bot import run_discord_bot
            from harness.config import HarnessConfig
            self.assertEqual(run_discord_bot(HarnessConfig()), 1)


try:
    import discord  # noqa: F401
    from harness.discord.bot import HarnessDiscordBot
    _HAS_DISCORD = True
except ImportError:
    _HAS_DISCORD = False


class TestDiscordBotFlow(unittest.IsolatedAsyncioTestCase):
    """End-to-end flow test: drive /ask through a mocked interaction with the
    offline MockProvider and assert Discord-native output is produced."""

    def setUp(self):
        self._messages = []

    def _build_interaction(self):
        sink = self._messages

        class _Response:
            def __init__(self):
                self._done = False

            def is_done(self):
                return self._done

            async def defer(self, thinking=False):
                self._done = True

            async def send_message(self, content, ephemeral=False):
                self._done = True
                sink.append(content)

        class _Followup:
            async def send(self, content, ephemeral=False):
                sink.append(content)

        class _User:
            def __init__(self):
                self.id = 9999

        class _Interaction:
            def __init__(self):
                self.channel_id = 4242
                self.data = {}
                self.response = _Response()
                self.channel = None
                self.followup = _Followup()
                self.user = _User()

        return _Interaction()

    async def test_ask_produces_thinking_quote_and_response(self):
        if not _HAS_DISCORD:
            self.skipTest("discord.py not installed")
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.model = "mock-harness-model"
        cfg.mode = "build"
        cfg.discord_permission = "full"

        interaction = self._build_interaction()
        bot = HarnessDiscordBot(cfg, token="fake")
        await bot._cmd_ask(interaction, "explain the system")

        self.assertTrue(self._messages, "expected at least one Discord message")
        joined = "\n".join(self._messages)
        # Thinking is delivered as a > quote block
        self.assertTrue(any(line.startswith("> ") for line in joined.splitlines()), joined)
        # The mock provider's final response is present verbatim
        self.assertIn("Finished executing tool.", joined)

    async def test_ask_rejects_empty_prompt(self):
        if not _HAS_DISCORD:
            self.skipTest("discord.py not installed")
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.discord_permission = "full"
        interaction = self._build_interaction()
        bot = HarnessDiscordBot(cfg, token="fake")
        await bot._cmd_ask(interaction, "   ")
        self.assertTrue(any("prompt" in m for m in self._messages))

    async def test_channel_restriction(self):
        if not _HAS_DISCORD:
            self.skipTest("discord.py not installed")
        cfg = HarnessConfig()
        cfg.set_field("discord_channel_ids", "111,222")
        cfg.provider = "mock"
        interaction = self._build_interaction()
        bot = HarnessDiscordBot(cfg, token="fake")
        await bot._cmd_ask(interaction, "hello")  # channel 4242 is not allowed
        self.assertTrue(any("not allowed" in m for m in self._messages))


class TestMessageRelay(unittest.TestCase):

    def test_bidirectional_relay(self):
        relay = MessageRelay()
        cli_msgs = []
        discord_msgs = []
        relay.register_cli(lambda text: cli_msgs.append(text))
        relay.register_discord(lambda text, cid: discord_msgs.append((text, cid)))
        relay.relay_from_discord("hello from discord", channel_id=123)
        relay.relay_from_cli("hello from CLI")
        self.assertEqual(cli_msgs, ["hello from discord"])
        self.assertEqual(discord_msgs, [("hello from CLI", None)])

    def test_history(self):
        relay = MessageRelay()
        relay.relay_from_cli("msg1")
        relay.relay_from_discord("msg2", 99)
        h = relay.get_history()
        self.assertEqual(len(h), 2)
        self.assertEqual(h[0]["source"], "cli")
        self.assertEqual(h[1]["source"], "discord")

    def test_global_relay_singleton(self):
        r1 = get_relay()
        r2 = get_relay()
        self.assertIs(r1, r2)

    def test_state_publish_reaches_listeners(self):
        relay = MessageRelay()
        seen = []
        relay.register_state_listener(lambda payload, origin: seen.append((payload, origin)))
        relay.publish_state({"mode": "super"}, origin="discord")
        self.assertEqual(seen, [({"mode": "super"}, "discord")])
        # Also mirrored onto the bus
        cursor = relay.bus.new_cursor()
        # cursor starts at end-of-file; poll from a fresh cursor reading all
        events = SyncBus(path=relay.bus.path).poll(SyncBus.__new__(SyncBus) if False else _FreshCursor(relay.bus.path))
        self.assertTrue(any(ev.get("kind") == STATE and ev.get("payload", {}).get("mode") == "super" for ev in events))

    def test_relay_output_published_to_bus(self):
        relay = MessageRelay()
        relay.relay_output("agent answer", origin="discord")
        events = _all_bus_events(relay.bus.path)
        self.assertTrue(any(
            ev.get("kind") == OUTPUT and ev.get("text") == "agent answer" and ev.get("origin") == "discord"
            for ev in events
        ))

    def test_publish_stop(self):
        relay = MessageRelay()
        relay.publish_stop(origin="discord")
        events = _all_bus_events(relay.bus.path)
        self.assertTrue(any(ev.get("kind") == STOP and ev.get("origin") == "discord" for ev in events))

    def test_publish_state_recorded_in_history(self):
        relay = MessageRelay()
        relay.publish_state({"provider": "mock"}, origin="cli")
        h = relay.get_history()
        self.assertTrue(any(e.get("kind") == "state" and e.get("payload", {}).get("provider") == "mock" for e in h))


def _all_bus_events(path):
    """Read every (fresh) event from a bus file for assertions."""
    bus = SyncBus(path=path)
    cursor = SyncBus.SyncCursor(offset=0, start_ts=0) if hasattr(SyncBus, "SyncCursor") else None
    # Simpler: craft a cursor that reads from the beginning with no TTL filter.
    from harness.discord.sync import SyncCursor
    cur = SyncCursor(offset=0, start_ts=time.time() - 3600)
    return bus.poll(cur, limit=1000)


class _FreshCursor:
    def __init__(self, path):
        from harness.discord.sync import SyncCursor
        self._cur = SyncCursor(offset=0, start_ts=time.time() - 3600)

    def __getattr__(self, item):
        return getattr(self._cur, item)


class TestSyncBus(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bus = SyncBus(path=Path(self._tmp.name) / "bus.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def _fresh_cursor(self):
        from harness.discord.sync import SyncCursor
        return SyncCursor(offset=0, start_ts=time.time() - 3600)

    def test_publish_then_poll(self):
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "ping"})
        events = self.bus.poll(self._fresh_cursor())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "ping")
        self.assertTrue(events[0].get("id"))  # auto-assigned UUID

    def test_cursor_advances_no_replay(self):
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "one"})
        cur = self.bus.new_cursor()  # end-of-file cursor
        self.assertEqual(self.bus.poll(cur), [])  # nothing new yet
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "two"})
        events = self.bus.poll(cur)
        self.assertEqual([e["text"] for e in events], ["two"])
        # second poll with same cursor → no duplicates
        self.assertEqual(self.bus.poll(cur), [])

    def test_stale_events_dropped(self):
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "old", "ts": time.time() - 10_000})
        self.assertEqual(self.bus.poll(self._fresh_cursor()), [])

    def test_new_cursor_ignores_backlog(self):
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "backlog"})
        cur = self.bus.new_cursor()
        self.assertEqual(self.bus.poll(cur), [])

    def test_corrupt_lines_ignored(self):
        with open(self.bus.path, "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        self.bus.publish({"kind": MESSAGE, "origin": "cli", "text": "good"})
        events = self.bus.poll(self._fresh_cursor())
        self.assertEqual([e["text"] for e in events], ["good"])

    def test_missing_file_poll_safe(self):
        bus = SyncBus(path=Path(self._tmp.name) / "missing.jsonl")
        self.assertEqual(bus.poll(self._fresh_cursor()), [])

    def test_publish_never_raises_on_bad_path(self):
        bus = SyncBus(path=Path("/proc/definitely/not/writable/bus.jsonl"))
        bus.publish({"kind": MESSAGE, "origin": "cli", "text": "x"})  # must not raise


class TestAgentCooperativeStop(unittest.TestCase):

    def _agent(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        return HarnessAgent(cfg)

    def test_request_stop_flags_and_clear(self):
        agent = self._agent()
        self.assertFalse(agent.stop_requested())
        agent.request_stop()
        self.assertTrue(agent.stop_requested())
        agent.clear_stop()
        self.assertFalse(agent.stop_requested())

    def test_step_clears_stale_stop_at_start(self):
        agent = self._agent()
        agent.request_stop()
        events = list(agent.step("say hi"))
        # The stale stop must NOT abort the fresh turn.
        self.assertTrue(any(ev.type == "turn_complete" for ev in events))
        self.assertFalse(agent.stop_requested())

    def test_stop_between_tool_calls_interrupts_turn(self):
        from harness.providers.mock_provider import MockProvider
        from harness.providers.base import LLMChunk, ToolCallDelta

        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        agent = HarnessAgent(cfg)

        class _Scripted(MockProvider):
            def __init__(self):
                super().__init__(responses=[])
                self.calls = 0

            def stream_chat(self, messages, model=None, thinking_effort="high", tools=None, system_prompt=None, **kw):
                self.calls += 1
                if self.calls == 1:
                    # Two tool calls in one assistant turn.
                    yield LLMChunk(tool_calls=[
                        ToolCallDelta(index=0, id="c1", name="list_dir", arguments_delta='{"path": "."}'),
                        ToolCallDelta(index=1, id="c2", name="list_dir", arguments_delta='{"path": ".."}'),
                    ])
                else:
                    yield LLMChunk(delta_text="never reached", finish_reason="stop")

        agent.provider = _Scripted()
        # Request the stop AFTER the provider's first call (so the mock turn is
        # already being consumed) but before the second tool runs. The seam:
        # arm the stop only once the model's own tool calls start executing.
        original_execute = agent.tool_registry.execute
        state = {"model_tools": 0}

        def _execute_then_stop(name, args, mode):
            if name == "list_dir":
                state["model_tools"] += 1
                if state["model_tools"] == 1:
                    agent.request_stop()  # interrupt after the first real tool runs
            return original_execute(name, args, mode)

        agent.tool_registry.execute = _execute_then_stop
        events = list(agent.step("do two things"))

        # Turn ends via the interrupted path, not a second provider call.
        self.assertEqual(agent.provider.calls, 1)
        self.assertTrue(any(ev.type == "step_end" and ev.data.get("complete") for ev in events))
        interrupted_msgs = [
            m for m in agent.session.messages
            if m.get("role") == "tool" and "interrupted by user" in str(m.get("content", ""))
        ]
        # The first list_dir ran; the second got the synthetic interrupted result.
        self.assertEqual(len(interrupted_msgs), 1)
        self.assertFalse(agent.stop_requested())


class TestCliDiscordStateSync(unittest.TestCase):

    def _agent(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        return HarnessAgent(cfg)

    def test_apply_remote_state_mode_and_permission(self):
        from harness.tui.interactive import _apply_remote_state
        agent = self._agent()
        _apply_remote_state(agent, {"mode": "plan", "permission": "secure"}, origin="discord")
        self.assertEqual(agent.mode.value, "plan")
        self.assertEqual(agent.permission_manager.level.value, "secure")

    def test_apply_remote_state_ignores_cli_origin(self):
        from harness.tui.interactive import _apply_remote_state
        agent = self._agent()
        _apply_remote_state(agent, {"mode": "plan"}, origin="cli")
        self.assertEqual(agent.mode.value, "build")  # unchanged

    def test_apply_remote_state_bad_payload_safe(self):
        from harness.tui.interactive import _apply_remote_state
        agent = self._agent()
        _apply_remote_state(agent, {"mode": 12345, "provider": {"weird": True}}, origin="discord")
        self.assertEqual(agent.mode.value, "build")

    def test_cli_commands_publish_state_to_bus(self):
        import harness.config as config_mod
        from harness.commands.registry import CommandRegistry
        from harness.tui.terminal import TerminalRenderer
        from harness.discord.sync import MessageRelay

        with tempfile.TemporaryDirectory() as td:
            old_user = config_mod.USER_CONFIG_PATH
            config_mod.USER_CONFIG_PATH = Path(td) / "config.json"
            relay = MessageRelay(bus_path=Path(td) / "bus.jsonl")
            try:
                # Point the global relay at the temp bus for this test.
                import harness.discord.sync as sync_mod
                old_relay = sync_mod._global_relay
                sync_mod._global_relay = relay
                agent = self._agent()
                registry = CommandRegistry()
                registry.handle("/mode super", agent, TerminalRenderer("cyberpunk"))
                events = _all_bus_events(relay.bus.path)
                self.assertTrue(any(
                    ev.get("kind") == STATE and ev.get("payload", {}).get("mode") == "super"
                    for ev in events
                ))
            finally:
                sync_mod._global_relay = old_relay
                config_mod.USER_CONFIG_PATH = old_user

    def test_goal_command_publishes_state(self):
        import harness.config as config_mod
        from harness.commands.registry import CommandRegistry
        from harness.tui.terminal import TerminalRenderer
        import harness.discord.sync as sync_mod
        from harness.discord.sync import MessageRelay

        with tempfile.TemporaryDirectory() as td:
            old_user = config_mod.USER_CONFIG_PATH
            config_mod.USER_CONFIG_PATH = Path(td) / "config.json"
            old_relay = sync_mod._global_relay
            relay = MessageRelay(bus_path=Path(td) / "bus.jsonl")
            sync_mod._global_relay = relay
            try:
                agent = self._agent()
                registry = CommandRegistry()
                registry.handle("/goal make all tests pass", agent, TerminalRenderer("cyberpunk"))
                events = _all_bus_events(relay.bus.path)
                self.assertTrue(any(
                    ev.get("kind") == STATE and ev.get("payload", {}).get("goal") == "make all tests pass"
                    for ev in events
                ))
                self.assertEqual(agent.mode.value, "super")
            finally:
                sync_mod._global_relay = old_relay
                config_mod.USER_CONFIG_PATH = old_user

    def test_stop_command_registered(self):
        from harness.commands.registry import CommandRegistry
        registry = CommandRegistry()
        self.assertIn("stop", registry.commands)


try:
    import discord  # noqa: F401
    _HAS_DISCORD_LIB = True
except ImportError:
    _HAS_DISCORD_LIB = False


@unittest.skipUnless(_HAS_DISCORD_LIB, "discord.py not installed")
class TestDiscordBotGoalStopAsk(unittest.IsolatedAsyncioTestCase):
    """New bot features: /goal flow, /stop semantics, and the ask_user view."""

    def setUp(self):
        pass

    def _cfg(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.model = "mock-harness-model"
        cfg.discord_permission = "full"
        return cfg

    def test_ask_user_view_resolves_via_callback(self):
        # Production semantics: the handler blocks the agent's *worker thread*,
        # while the Discord event loop resolves the future (simulated instantly
        # by a stubbed view).
        import asyncio
        import concurrent.futures
        import threading
        from harness.discord.bot import HarnessDiscordBot

        bot = HarnessDiscordBot(self._cfg(), token="fake")
        loop = asyncio.new_event_loop()

        class _Interaction:
            class _Followup:
                async def send(self, *a, **kw):
                    return None

            def __init__(self):
                self.followup = _Interaction._Followup()
                self.channel_id = 1

        from harness.discord import bot as bot_mod
        real_view = bot_mod._AskUserView

        class _FakeView:
            def __init__(self, future, question, options, allow_custom, recommended):
                future.set_result("my custom answer")

            def build_children(self):
                pass

        def run_case(stub_view):
            bot_mod._AskUserView = stub_view
            handler = bot._make_ask_handler(_Interaction(), loop)
            result = {}

            def worker():
                result["answer"] = handler("Pick one?", ["a", "b"], True, "a")

            t = threading.Thread(target=worker)
            t.start()
            try:
                loop.run_until_complete(
                    asyncio.wait_for(asyncio.sleep(0.5), timeout=2)
                )
            finally:
                t.join(timeout=10)
            return result.get("answer")

        try:
            asyncio.set_event_loop(loop)
            answer = run_case(_FakeView)
        finally:
            bot_mod._AskUserView = real_view
            asyncio.set_event_loop(None)
            loop.close()
        self.assertEqual(answer, "User replied: my custom answer")

    async def test_cmd_goal_runs_super_turn_and_publishes(self):
        from harness.discord.bot import HarnessDiscordBot

        cfg = self._cfg()
        bot = HarnessDiscordBot(cfg, token="fake")

        sent = []

        class _Response:
            def __init__(self):
                self._done = False

            def is_done(self):
                return self._done

            async def defer(self, thinking=False):
                self._done = True

            async def send_message(self, content, ephemeral=False):
                self._done = True
                sent.append(content)

        class _Followup:
            async def send(self, content, ephemeral=False):
                sent.append(content)

        class _User:
            id = 1

        class _Interaction:
            def __init__(self):
                self.channel_id = 77
                self.data = {}
                self.response = _Response()
                self.channel = None
                self.followup = _Followup()
                self.user = _User()

        interaction = _Interaction()
        import harness.discord.sync as sync_mod
        from harness.discord.sync import MessageRelay, STATE
        old_relay = sync_mod._global_relay
        relay = MessageRelay()
        sync_mod._global_relay = relay
        try:
            await bot._cmd_goal(interaction, "achieve the objective")
        finally:
            sync_mod._global_relay = old_relay

        self.assertEqual(bot.config.mode, "super")
        joined = "\n".join(sent)
        self.assertIn("SUPER MODE ACTIVATED", joined)
        events = _all_bus_events(relay.bus.path)
        self.assertTrue(any(
            ev.get("kind") == STATE and ev.get("payload", {}).get("mode") == "super"
            for ev in events
        ))

    def test_stop_requests_agent_interrupt(self):
        from harness.discord.bot import HarnessDiscordBot
        from harness.core.agent import HarnessAgent

        cfg = self._cfg()
        cfg.learning_enabled = False
        bot = HarnessDiscordBot(cfg, token="fake")
        agent = HarnessAgent(cfg)
        agent.is_running = True
        agent.request_stop()
        self.assertTrue(agent.stop_requested())

    async def test_stop_command_interrupts_and_publishes(self):
        from harness.discord.bot import HarnessDiscordBot

        cfg = self._cfg()
        bot = HarnessDiscordBot(cfg, token="fake")

        sent = []

        class _Response:
            def __init__(self):
                self._done = False

            def is_done(self):
                return self._done

            async def defer(self, thinking=False, ephemeral=False):
                self._done = True

            async def send_message(self, content, ephemeral=False):
                self._done = True
                sent.append(content)

        class _Followup:
            async def send(self, content, ephemeral=False):
                sent.append(content)

        class _User:
            id = 1

        class _Interaction:
            def __init__(self):
                self.channel_id = 77
                self.data = {}
                self.response = _Response()
                self.channel = None
                self.followup = _Followup()
                self.user = _User()

        interaction = _Interaction()
        import harness.discord.sync as sync_mod
        from harness.discord.sync import MessageRelay, STOP
        old_relay = sync_mod._global_relay
        relay = MessageRelay()
        sync_mod._global_relay = relay
        try:
            # Invoke the registered /stop command through the command tree.
            cmd = next(
                (c for c in bot.tree.get_commands() if c.name == "stop"), None
            )
            self.assertIsNotNone(cmd, "expected a /stop command")
            await cmd._callback(interaction)
        finally:
            sync_mod._global_relay = old_relay
        self.assertTrue(any("Stop requested" in m or "No agent" in m for m in sent))
        events = _all_bus_events(relay.bus.path)
        self.assertTrue(any(ev.get("kind") == STOP for ev in events))

    def test_goal_command_registered_in_tree(self):
        from harness.discord.bot import HarnessDiscordBot
        bot = HarnessDiscordBot(self._cfg(), token="fake")
        names = {c.name for c in bot.tree.get_commands()}
        self.assertIn("goal", names)
        self.assertIn("stop", names)

    def test_ask_user_handler_timeout_returns_skip(self):
        # Timeout path: a 1-second ask timeout + a never-resolving view.
        # The worker thread (a real thread, like production) waits, times out,
        # and reports the skip string — no global monkeypatching involved.
        import asyncio
        import threading
        from harness.discord.bot import HarnessDiscordBot

        cfg = self._cfg()
        cfg.discord_ask_timeout = 1
        bot = HarnessDiscordBot(cfg, token="fake")

        class _Interaction:
            class _Followup:
                async def send(self, *a, **kw):
                    return None

            def __init__(self):
                self.followup = _Interaction._Followup()
                self.channel_id = 1

        from harness.discord import bot as bot_mod
        real_view = bot_mod._AskUserView

        class _NeverResolvingView:
            def __init__(self, future, question, options, allow_custom, recommended):
                self._f = future  # never resolved: simulates no user clicking

            def build_children(self):
                pass

        loop = asyncio.new_event_loop()
        try:
            bot_mod._AskUserView = _NeverResolvingView
            handler = bot._make_ask_handler(_Interaction(), loop)
            result = {}

            def worker():
                result["answer"] = handler("Q?", [], False, None)

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            # Run the loop so the scheduled _post coroutine executes.
            end = time.time() + 1.5
            while time.time() < end:
                loop.run_until_complete(asyncio.sleep(0.1))
            t.join(timeout=10)
        finally:
            bot_mod._AskUserView = real_view
            asyncio.set_event_loop(None)
            loop.close()
        self.assertIn("skipped", result.get("answer", ""))


class TestSecureStoreValidation(unittest.TestCase):

    def test_validate_keys_store_fixes_permissions(self):
        import stat
        with tempfile.TemporaryDirectory() as td:
            ks = Path(td) / "api_keys.json"
            ks.parent.mkdir(parents=True, exist_ok=True)
            ks.write_text("{}")
            ks.chmod(0o644)
            old_keys_file = secure_store.KEYS_FILE
            secure_store.KEYS_FILE = ks
            try:
                result = secure_store.validate_keys_store()
                self.assertTrue(result["ok"])
                current = stat.S_IMODE(os.stat(str(ks)).st_mode)
                self.assertFalse(current & 0o077)
            finally:
                secure_store.KEYS_FILE = old_keys_file

    def test_validate_migrates_plaintext_keys(self):
        with tempfile.TemporaryDirectory() as td:
            fake_keys = Path(td) / "api_keys.json"
            fake_keys.parent.mkdir(parents=True, exist_ok=True)
            fake_keys.write_text("{}")

            old_keys_file = secure_store.KEYS_FILE
            old_keyring = secure_store._keyring
            old_keyring_checked = secure_store._keyring_checked
            secure_store.KEYS_FILE = fake_keys
            secure_store._keyring = None
            secure_store._keyring_checked = True
            try:
                config_path = Path(td) / "config.json"
                data = {"provider": "anthropic", "api_keys": {"migratetest": "sk-test-migrate-12345"}}
                config_path.write_text(json.dumps(data))

                result = secure_store.validate_keys_store()
                self.assertIn("migrated", result)

                stored_data = json.loads(fake_keys.read_text())
                self.assertIn("migratetest", stored_data)
                self.assertEqual(stored_data["migratetest"], "sk-test-migrate-12345")
            finally:
                secure_store.KEYS_FILE = old_keys_file
                secure_store._keyring = old_keyring
                secure_store._keyring_checked = old_keyring_checked


class TestAutoStartConfig(unittest.TestCase):

    def test_discord_auto_start_default(self):
        cfg = HarnessConfig()
        self.assertFalse(cfg.discord_auto_start)

    def test_discord_auto_start_settable(self):
        cfg = HarnessConfig()
        self.assertTrue(cfg.set_field("discord_auto_start", "true"))
        self.assertTrue(cfg.discord_auto_start)
        self.assertTrue(cfg.set_field("discord_auto_start", "false"))
        self.assertFalse(cfg.discord_auto_start)


class TestChunkMessageSafety(unittest.TestCase):

    def test_chunks_stay_under_limit(self):
        text = "x" * 5000
        chunks = chunk_message(text, 2000)
        for c in chunks:
            self.assertLessEqual(len(c), 2000)
        self.assertEqual("".join(chunks), text)

    def test_chunks_prefer_newlines(self):
        text = "line1\nline2\nline3\n" + "x" * 3000
        chunks = chunk_message(text, 2000)
        self.assertGreater(len(chunks), 1)
        # First chunk should end at a newline
        self.assertTrue(chunks[0].endswith("\n") or len(chunks[0]) < 2000)


if __name__ == "__main__":
    unittest.main()