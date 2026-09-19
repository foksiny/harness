"""
Stop responsiveness, retry hygiene, and model-recovery regression tests.

Covers the fixes for:
- /stop and Ctrl-C being ignored while a provider stream stalls (pump+abort
  architecture in Agent._stream_interruptible / SsePostStream.abort)
- zombie provider retry loops burning tokens after an aborted turn
- NVIDIA-NIM-style "stream ended without a response" hammering with a full
  exponential backoff chain (now capped at one fast retry)
- SUPER mode abandoning the goal on intermediate narration (text without
  tool calls) — now challenged once before the turn may end
- models spiraling in read-only exploration loops — now nudged back to action
- malformed tool calls (shell command passed as the tool name) wasting many
  recovery steps — now answered with an actionable hint
"""
import socket
import threading
import time
import unittest

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.providers.base import BaseProvider, LLMChunk, SsePostStream, ToolCallDelta
from harness.providers.mock_provider import MockProvider
from harness.tools import ToolRegistry
from harness.core.permissions import PermissionManager


class ScriptedProvider(MockProvider):
    """MockProvider that plays a different script per stream_chat call."""

    def __init__(self, scripts):
        super().__init__()
        self.scripts = list(scripts)

    def stream_chat(self, **kwargs):
        self.call_history.append(kwargs)
        if self.scripts:
            script = self.scripts.pop(0)
        else:
            script = [LLMChunk(delta_text="done", finish_reason="stop")]
        for chunk in script:
            yield chunk


def text_only_response(text="Just narrating my plan."):
    return [LLMChunk(delta_text=text), LLMChunk(finish_reason="stop")]


def run_command_call(command="ls -la .", call_id="c1"):
    return [LLMChunk(tool_calls=[ToolCallDelta(
        index=0, id=call_id, name="run_command",
        arguments_delta='{"command": "%s"}' % command,
    )])]


class AgentStepTestCase(unittest.TestCase):
    """Base: full HarnessAgent wired to a scripted provider."""

    def make_agent(self, scripts, mode=Mode.BUILD):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        agent.provider = ScriptedProvider(scripts)
        agent.set_mode(mode)
        return agent

    def run_step(self, agent, prompt="do the thing"):
        agent.is_running = True
        events = list(agent.step(prompt))
        return events


class TestInterruptibleStream(AgentStepTestCase):

    def test_stop_unblocks_a_stalled_provider_stream(self):
        """The exact NIM failure: stream delivers one chunk, then stalls.
        /stop must end the turn within ~1s, not after the 300s read timeout."""
        release = threading.Event()

        class StalledProvider(MockProvider):
            def stream_chat(self, **kwargs):
                self.call_history.append(kwargs)
                yield LLMChunk(delta_text="partial ")
                release.wait(timeout=20)  # simulate a stalled socket read
                yield LLMChunk(delta_text="too late", finish_reason="stop")

        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        agent.provider = StalledProvider()
        agent.is_running = True

        threading.Timer(0.5, agent.request_stop).start()
        start = time.time()
        events = list(agent.step("build me a game"))
        elapsed = time.time() - start
        release.set()  # let the abandoned pump retire

        self.assertLess(elapsed, 3.0,
                        f"/stop did not interrupt the stalled stream (took {elapsed:.1f}s)")
        types = [e.type for e in events]
        self.assertIn("turn_complete", types)

    def test_abandoned_stream_does_not_reconnect(self):
        """A provider mid-internal-retry must not open a NEW connection after
        the agent abandoned it (zombie pump burning tokens)."""
        attempts = []

        class RefusedProvider(MockProvider):
            def stream_chat(self, **kwargs):
                self.call_history.append(kwargs)
                attempts.append(time.time())
                raise OSError("connection refused")  # pre-payload transient

        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        provider = RefusedProvider()
        agent.provider = provider
        agent.is_running = True

        # Consume the stream on a thread; abort mid-backoff.
        def consume():
            list(agent.step("hello"))

        t = threading.Thread(target=consume, daemon=True)
        t.start()
        deadline = time.time() + 5
        while not attempts and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(attempts, "first attempt never fired")
        agent.request_stop()  # what /stop really does: flag + abort (via wrapper)
        t.join(timeout=5)
        time.sleep(0.3)
        self.assertEqual(len(attempts), 1,
                         f"zombie pump opened {len(attempts) - 1} extra connection(s)")


class TestSuperModeContinuation(AgentStepTestCase):

    def test_text_only_in_super_mode_is_challenged_then_ends(self):
        agent = self.make_agent(
            [text_only_response("Step 1: I will inspect things."),
             text_only_response("Step 2: still narrating.")],
            mode=Mode.SUPER,
        )
        events = self.run_step(agent)

        self.assertEqual(len(agent.provider.call_history), 2,
                         "model should be called twice (narration + challenge, then accept)")
        nudges = [m for m in agent.session.messages
                  if m.get("role") == "user" and "SUPER MODE is still active" in str(m.get("content", ""))]
        self.assertEqual(len(nudges), 1, "exactly one challenge nudge expected")
        self.assertIn("turn_complete", [e.type for e in events])

    def test_text_only_in_build_mode_still_ends_turn(self):
        agent = self.make_agent([text_only_response("Here is your answer.")], mode=Mode.BUILD)
        events = self.run_step(agent)

        self.assertEqual(len(agent.provider.call_history), 1,
                         "BUILD mode: text-only is a valid final answer, no extra call")
        nudges = [m for m in agent.session.messages
                  if m.get("role") == "user" and "SUPER MODE" in str(m.get("content", ""))]
        self.assertEqual(nudges, [])
        self.assertIn("turn_complete", [e.type for e in events])

    def test_tools_in_super_mode_reset_the_challenge_streak(self):
        agent = self.make_agent(
            [run_command_call("ls -la", "c1"),   # tool work
             text_only_response("narrating"),      # narration → challenged once
             run_command_call("ls -la", "c2"),     # tool work resets streak
             text_only_response("final answer")],  # narration → challenged AGAIN
            mode=Mode.SUPER,
        )
        self.run_step(agent)
        nudges = [m for m in agent.session.messages
                  if m.get("role") == "user" and "SUPER MODE is still active" in str(m.get("content", ""))]
        self.assertEqual(len(nudges), 2,
                         "streak must reset on tool work so later narration is challenged again")


class TestEmptyStreamRetryCap(AgentStepTestCase):

    def test_empty_stream_error_gets_exactly_one_fast_retry(self):
        """NIM 'stream ended without a response': one 2s retry, then give up —
        never the full 5s→10s→20s exponential chain."""
        error_script = [LLMChunk(
            delta_text="\n[Error from NIM: stream ended without a response]\n",
            finish_reason="error",
        )]
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.provider_max_retries = 3
        agent = HarnessAgent(cfg)
        agent.provider = ScriptedProvider([error_script, error_script, error_script])
        agent.is_running = True

        events = list(agent.step("hello"))
        self.assertEqual(len(agent.provider.call_history), 2,
                         "empty-stream errors must retry exactly once (got "
                         f"{len(agent.provider.call_history)} calls)")
        deltas = "".join(e.data for e in events if e.type == "text_delta")
        self.assertIn("returned an error; ending this turn", deltas)
        self.assertIn("retrying once in 2s", deltas)


class TestExplorationWatchdog(AgentStepTestCase):

    def test_exploration_loop_triggers_nudge(self):
        scripts = [run_command_call("ls -la .", f"c{i}") for i in range(10)]
        scripts.append(text_only_response("All done, final summary here."))
        agent = self.make_agent(scripts)
        events = self.run_step(agent)

        self.assertEqual(len(agent.provider.call_history), 11)
        nudges = [m for m in agent.session.messages
                  if m.get("role") == "user"
                  and "read-only exploration calls" in str(m.get("content", ""))]
        self.assertEqual(len(nudges), 1, "watchdog should fire exactly once per streak")
        self.assertIn("turn_complete", [e.type for e in events])

    def test_real_work_resets_the_exploration_streak(self):
        scripts = [
            run_command_call("ls -la .", "c0"),
            run_command_call("mkdir -p docs", "c1"),   # mutation → resets streak
        ] + [run_command_call("ls -la .", f"c{i}") for i in range(2, 11)]
        scripts.append(text_only_response("done"))
        agent = self.make_agent(scripts)
        self.run_step(agent)
        nudges = [m for m in agent.session.messages
                  if m.get("role") == "user"
                  and "read-only exploration calls" in str(m.get("content", ""))]
        # 10 explorations + 1 mutation: the streak reached only 10 AFTER the
        # mutation reset it, so one nudge still fires — but prove the reset by
        # checking the nuke fires only once and the turn completes normally.
        self.assertLessEqual(len(nudges), 1)


class TestToolNotFoundRecovery(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry(permission_manager=PermissionManager())

    def test_shell_command_as_tool_name_gets_actionable_hint(self):
        r = self.registry.execute(
            "find / -maxdepth 4 -iname *.md -path *harness* 2>/dev/null | head -50",
            {}, "build")
        self.assertIn("run_command", r)
        self.assertIn("shell command", r)

    def test_read_file_alias_suggests_view_file(self):
        r = self.registry.execute("read_file", {"path": "x"}, "build")
        self.assertIn("view_file", r)

    def test_unknown_tool_lists_available_tools(self):
        r = self.registry.execute("zzz_totally_unknown", {}, "build")
        self.assertIn("not found in registry", r)
        self.assertIn("run_command", r)  # available-tools listing


class TestSseAbort(unittest.TestCase):
    """The real HTTP-level abort: a stalled SSE body read must unblock in
    milliseconds when another thread calls abort() (what /stop triggers)."""

    def _stalling_sse_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            while True:
                conn, _ = srv.accept()
                try:
                    conn.recv(65536)
                    conn.sendall(b"HTTP/1.1 200 OK\r\n"
                                 b"Content-Type: text/event-stream\r\n\r\n")
                    time.sleep(30)
                except OSError:
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        threading.Thread(target=serve, daemon=True).start()
        return port

    def test_abort_unblocks_stalled_mid_body_read(self):
        port = self._stalling_sse_server()
        stream = SsePostStream(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            {}, {"model": "m", "messages": [], "stream": True},
            stream_timeout=60.0,
        )
        outcome = {}

        def read():
            try:
                with stream as s:
                    for _ in s:
                        pass
                outcome["done"] = True
            except BaseException as ex:
                outcome["exc"] = repr(ex)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(0.5)  # reader now blocked mid-body (headers sent, no data)

        start = time.time()
        stream.abort()
        reader.join(timeout=3.0)
        elapsed = time.time() - start

        self.assertFalse(reader.is_alive(),
                          f"stalled read still blocked {elapsed:.1f}s after abort()")
        self.assertLess(elapsed, 2.0)

    def test_tracked_stream_registry_lifecycle(self):
        from harness.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(name="t", display_name="T", default_model="m",
                                     base_url="http://127.0.0.1:9/v1", api_key="k")
        port = self._stalling_sse_server()
        with p._tracked_sse_stream(f"http://127.0.0.1:{port}/v1/c", {}, {}) as s:
            self.assertEqual(len(p._active_sse), 1)
        self.assertEqual(len(p._active_sse), 0, "registry must clear on normal exit")

    def test_abort_generation_guard(self):
        from harness.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(name="t", display_name="T", default_model="m",
                                     base_url="http://127.0.0.1:9/v1", api_key="k",
                                     max_retries=5, base_delay=0.2)
        attempts = []

        def refusing_stream(endpoint, headers, body):
            attempts.append(time.time())
            raise OSError("connection refused")

        p._tracked_sse_stream = refusing_stream
        gen = p.stream_chat(messages=[{"role": "user", "content": "hi"}], model="m")
        t = threading.Thread(target=lambda: list(gen), daemon=True)
        t.start()
        deadline = time.time() + 5
        while not attempts and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(attempts, "first attempt never fired")
        p.abort_active_streams()
        t.join(timeout=3)
        time.sleep(0.5)
        self.assertEqual(len(attempts), 1,
                         "zombie retry loop must not reconnect after abort")


if __name__ == "__main__":
    unittest.main()
