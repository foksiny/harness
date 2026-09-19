"""
Tests for provider-error retry behavior:

* The agent retries the WHOLE model call (with backoff) when a stream dies
  mid-way (error chunk or raised exception) — nothing from the failed attempt
  is committed, so a fresh request is always safe.
* Fatal errors (auth/bad-request) are not retried.
* Retries are bounded by provider_max_retries and reuse provider_retry_base_delay.
* The OpenAI-compatible layer classifies read timeouts as transient.
"""
import socket
import unittest

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.providers.base import LLMChunk, is_fatal_provider_error
from harness.providers.mock_provider import MockProvider
from harness.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _is_transient_exception,
)


class ScriptedProvider(MockProvider):
    """Pops one "script" per stream_chat call: a list of chunks to yield, or
    an exception instance to raise. Records every call."""

    def __init__(self, scripts):
        super().__init__(responses=[])
        self.scripts = list(scripts)
        self.calls = 0

    def stream_chat(self, messages, model=None, **kwargs):
        self.call_history.append({"messages": list(messages), "model": model})
        self.calls += 1
        script = self.scripts.pop(0) if self.scripts else [LLMChunk(delta_text="?", finish_reason="stop")]
        if isinstance(script, Exception):
            raise script
        for chunk in script:
            yield chunk


def _agent(provider, max_retries=3):
    cfg = HarnessConfig()
    cfg.provider = "mock"
    cfg.learning_enabled = False
    cfg.provider_max_retries = max_retries
    cfg.provider_retry_base_delay = 0.01  # keep the backoff instant in tests
    agent = HarnessAgent(cfg)
    agent.ensure_session()
    agent.provider = provider
    return agent


class TestFatalErrorClassifier(unittest.TestCase):

    def test_transient_errors_not_fatal(self):
        self.assertFalse(is_fatal_provider_error("The read operation timed out"))
        self.assertFalse(is_fatal_provider_error("[HTTP Error 503: overloaded]"))
        self.assertFalse(is_fatal_provider_error("rate limit exceeded"))

    def test_auth_and_request_errors_fatal(self):
        for msg in (
            "No API key for NVIDIA NIM. Run: harness keys set nvidia",
            "401 Unauthorized",
            "invalid request error: model id required",
            "context length exceeded",
            "model not found",
        ):
            self.assertTrue(is_fatal_provider_error(msg), msg)


class TestOpenAICompatibleTransientClassification(unittest.TestCase):

    def test_read_timeout_is_retryable_message(self):
        p = OpenAICompatibleProvider(name="t", display_name="T",
                                     default_model="m", base_url="http://x")
        self.assertTrue(p._is_retryable_error_message("The read operation timed out"))
        self.assertTrue(p._is_retryable_error_message("Connection reset by peer"))
        self.assertFalse(p._is_retryable_error_message("invalid request: bad json"))

    def test_is_transient_exception(self):
        self.assertTrue(_is_transient_exception(socket.timeout("The read operation timed out")))
        self.assertTrue(_is_transient_exception(ConnectionResetError("connection reset")))
        self.assertFalse(_is_transient_exception(RuntimeError("no api key configured")))


class TestAgentStreamRetry(unittest.TestCase):

    def test_midstream_error_chunk_retried_and_succeeds(self):
        scripts = [
            [  # first attempt: streams reasoning, then NIM-style read timeout
                LLMChunk(delta_reasoning="thinking hard..."),
                LLMChunk(delta_text="\n[Unexpected Error from NVIDIA NIM: The read operation timed out]\n",
                         finish_reason="error"),
            ],
            [  # second attempt: succeeds
                LLMChunk(delta_text="final answer"),
                LLMChunk(finish_reason="stop"),
            ],
        ]
        provider = ScriptedProvider(scripts)
        agent = _agent(provider)
        events = list(agent.step("hello"))

        retries = [e for e in events if e.type == "provider_retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].data["attempt"], 1)
        self.assertEqual(retries[0].data["max_retries"], 3)
        self.assertIn("timed out", retries[0].data["error"])
        self.assertEqual(provider.calls, 2)

        # The failed attempt was discarded; only the final answer is committed.
        finals = [m for m in agent.session.messages if m.get("role") == "assistant" and m.get("content")]
        self.assertEqual(finals[-1]["content"], "final answer")
        # Turn completed normally — no "returned an error; ending this turn".
        self.assertFalse(any("returned an error" in str(getattr(e, "data", ""))
                             for e in events if e.type == "text_delta"))
        self.assertTrue(any(e.type == "turn_complete" for e in events))

    def test_raised_exception_retried(self):
        scripts = [
            socket.timeout("The read operation timed out"),
            [LLMChunk(delta_text="recovered"), LLMChunk(finish_reason="stop")],
        ]
        provider = ScriptedProvider(scripts)
        agent = _agent(provider)
        events = list(agent.step("hello"))
        self.assertEqual(provider.calls, 2)
        self.assertTrue(any(e.type == "provider_retry" for e in events))
        finals = [m for m in agent.session.messages if m.get("role") == "assistant" and m.get("content")]
        self.assertEqual(finals[-1]["content"], "recovered")

    def test_fatal_error_not_retried(self):
        scripts = [
            [LLMChunk(delta_text="\n[Error from NVIDIA NIM: No API key for NVIDIA NIM]\n",
                      finish_reason="error")],
        ]
        provider = ScriptedProvider(scripts)
        agent = _agent(provider)
        events = list(agent.step("hello"))
        self.assertEqual(provider.calls, 1)
        self.assertFalse(any(e.type == "provider_retry" for e in events))
        self.assertTrue(any("returned an error" in str(getattr(e, "data", ""))
                            for e in events if e.type == "text_delta"))
        # No assistant content committed.
        finals = [m for m in agent.session.messages if m.get("role") == "assistant" and m.get("content")]
        self.assertEqual(finals, [])

    def test_retries_exhausted_then_ends_turn(self):
        err = [LLMChunk(delta_text="\n[Unexpected Error from NVIDIA NIM: The read operation timed out]\n",
                        finish_reason="error")]
        provider = ScriptedProvider([err, err, err, err])
        agent = _agent(provider, max_retries=3)
        events = list(agent.step("hello"))
        # 1 initial + 3 retries, then the turn ends with the error.
        self.assertEqual(provider.calls, 4)
        retries = [e for e in events if e.type == "provider_retry"]
        self.assertEqual(len(retries), 3)
        self.assertTrue(any("returned an error" in str(getattr(e, "data", ""))
                            for e in events if e.type == "text_delta"))

    def test_zero_retries_single_call(self):
        err = [LLMChunk(delta_text="\n[Unexpected Error from NVIDIA NIM: The read operation timed out]\n",
                        finish_reason="error")]
        provider = ScriptedProvider([err, err])
        agent = _agent(provider, max_retries=0)
        events = list(agent.step("hello"))
        self.assertEqual(provider.calls, 1)
        self.assertFalse(any(e.type == "provider_retry" for e in events))

    def test_partial_text_discarded_on_retry(self):
        scripts = [
            [  # partial text streams, then dies — must NOT be committed
                LLMChunk(delta_text="partial answer that should be"),
                LLMChunk(delta_text="\n[Unexpected Error from NVIDIA NIM: read timed out]\n",
                         finish_reason="error"),
            ],
            [LLMChunk(delta_text="clean answer"), LLMChunk(finish_reason="stop")],
        ]
        provider = ScriptedProvider(scripts)
        agent = _agent(provider)
        events = list(agent.step("hello"))
        finals = [m for m in agent.session.messages if m.get("role") == "assistant" and m.get("content")]
        self.assertEqual(finals[-1]["content"], "clean answer")
        self.assertFalse(any("partial answer" in str(m) for m in agent.session.messages))


if __name__ == "__main__":
    unittest.main()
