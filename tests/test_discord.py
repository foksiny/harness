"""
Unit tests for the Discord integration: config token lifecycle, event rendering
(thinking as > quotes, full response at end, tool notices), message chunking,
sync relay, and key store validation.
"""
import unittest
import tempfile
import json
import os
from pathlib import Path

from harness.config import HarnessConfig, save_config, load_config, mask_key
from harness import secure_store
from harness.discord.renderer import (
    DiscordRenderState,
    DiscordOutgoing,
    chunk_message,
    chunk_quote,
)
from harness.discord.sync import MessageRelay, get_relay


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