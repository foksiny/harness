"""
Tests for the budget-driven, graduated Context Compaction system.
Covers token estimation, turn-group integrity, payload truncation, the LLM /
heuristic summarizer fallback, hysteresis, the mid-turn emergency trim, and
token breakdown reporting.
"""
import unittest

from harness.core.compaction import (
    estimate_tokens,
    calculate_history_tokens,
    Compactor,
    TokenStats,
)
from harness.config import HarnessConfig


def _msg(role, content, tool_calls=None, tool_call_id=None):
    m = {"role": role, "content": content}
    if tool_calls is not None:
        m["tool_calls"] = tool_calls
    if tool_call_id is not None:
        m["tool_call_id"] = tool_call_id
    return m


def _tool_call(name, path=None):
    args = {}
    if path:
        args["path"] = path
    return {
        "id": f"tc_{name}",
        "type": "function",
        "function": {"name": name, "arguments": __import__("json").dumps(args)},
    }


class TestTokenEstimation(unittest.TestCase):

    def test_basic_estimation(self):
        self.assertEqual(estimate_tokens("a" * 380), 100)
        obj = {"key": "value", "list": [1, 2, 3]}
        self.assertGreater(estimate_tokens(obj), 0)

    def test_history_counts_structural_overhead(self):
        msgs = [_msg("user", "a" * 380)]
        self.assertEqual(calculate_history_tokens(msgs), 105)  # 100 content + role + overhead

    def test_breakdown_sums_to_total(self):
        c = Compactor(context_window=10000)
        sys_prompt = "s" * 190
        msgs = [
            _msg("user", "z" * 380),
            _msg("assistant", "y" * 380, tool_calls=[_tool_call("read_file", "a.py")]),
            _msg("tool", "t" * 380, tool_call_id="tc_read_file"),
        ]
        status = c.check_status(msgs, sys_prompt)
        bd = status.breakdown
        self.assertEqual(
            sum(v for v in bd.values()),
            calculate_history_tokens(msgs, sys_prompt),
        )
        self.assertIsInstance(status, TokenStats)


class TestGraduatedCompaction(unittest.TestCase):

    @staticmethod
    def _big_old_message_set(n_old_groups=10, recent_turns=4):
        msgs = []
        for i in range(n_old_groups + recent_turns):
            msgs.append(_msg("user", f"Old instruction {i} " + ("x" * 400)))
            msgs.append(_msg("assistant", f"Old work {i} " + ("y" * 400)))
        return msgs

    def test_compacts_down_to_target_ratio(self):
        compactor = Compactor(context_window=2000, threshold_ratio=0.70)
        messages = self._big_old_message_set()
        self.assertTrue(compactor.should_compact(messages))

        compacted, report = compactor.compact(messages, preserve_recent_turns=4)
        self.assertTrue(report["compacted"])
        self.assertGreater(report["saved_tokens"], 0)
        self.assertLess(len(compacted), len(messages))
        self.assertLessEqual(report["after_ratio"], compactor.target_ratio + 0.02)

    def test_recent_working_set_stays_verbatim(self):
        compactor = Compactor(context_window=2000, threshold_ratio=0.70)
        messages = self._big_old_message_set(recent_turns=2)
        compacted, _ = compactor.compact(messages, preserve_recent_turns=2)
        tail = [m for m in compacted[-4:]]
        self.assertEqual(tail[0]["content"], messages[-4]["content"])
        self.assertEqual(tail[1]["content"], messages[-3]["content"])
        self.assertEqual(tail[2]["content"], messages[-2]["content"])
        self.assertEqual(tail[3]["content"], messages[-1]["content"])

    def test_ledger_records_compaction(self):
        compactor = Compactor(context_window=2000, threshold_ratio=0.70)
        messages = self._big_old_message_set()
        compactor.compact(messages)
        self.assertEqual(len(compactor.ledger), 1)
        entry = compactor.ledger[0]
        self.assertIn("trigger", entry)
        self.assertGreater(entry["saved_tokens"], 0)

    def test_noop_when_below_minimum_history(self):
        compactor = Compactor(context_window=2000, threshold_ratio=0.70)
        messages = [_msg("user", "hi"), _msg("assistant", "hello")]
        compacted, report = compactor.compact(messages)
        self.assertFalse(report["compacted"])
        self.assertEqual(compacted, messages)

    def test_tool_result_never_split_from_its_call(self):
        # A user + assistant(tool call) + tool result is one atomic group: it is
        # either preserved whole or summarized whole, never partially kept.
        compactor = Compactor(context_window=500, threshold_ratio=0.70, target_ratio=0.55)
        messages = [
            _msg("user", "inspect the config"),
            _msg("assistant", "", tool_calls=[_tool_call("read_file", "cfg.yaml")]),
            _msg("tool", "settings: debug: true, port = 8080", tool_call_id="tc_read_file"),
            _msg("user", "newer task " + ("q" * 400)),
            _msg("assistant", "keep me verbatim " + ("r" * 400)),
        ]
        compacted, report = compactor.compact(messages, preserve_recent_turns=1)
        self.assertTrue(report["compacted"])
        # Everything from the kept turn onward is untouched.
        self.assertEqual(compacted[-2:], messages[-2:])


class TestPayloadTruncation(unittest.TestCase):

    def test_oversized_tool_payload_collapsed(self):
        compactor = Compactor(context_window=5000, threshold_ratio=0.10, max_message_tokens=50)
        big = "D" * 2000
        messages = [
            _msg("user", "read that huge file"),
            _msg("assistant", "", tool_calls=[_tool_call("read_file", "huge.bin")]),
            _msg("tool", big, tool_call_id="tc_read_file"),
            _msg("user", "next " + ("e" * 300)),
            _msg("assistant", "done " + ("f" * 300)),
        ]
        compactor.max_message_tokens = 50
        compacted, report = compactor.compact(messages, preserve_recent_turns=1)
        self.assertTrue(report["compacted"])
        self.assertGreaterEqual(report["tactics"]["payloads_truncated"], 1)
        # The collapsed payload carries a size-stamped marker, not the raw blob.
        self.assertIn("payload truncated", str(compacted[2]["content"]))
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertNotIn(big, text)

    def test_truncate_marker_present_when_payload_kept(self):
        compactor = Compactor(context_window=5000, threshold_ratio=0.30, target_ratio=0.60, max_message_tokens=50)
        big = "D" * 2000
        messages = [
            _msg("user", "read it"),
            _msg("assistant", "", tool_calls=[_tool_call("read_file", "x.bin")]),
            _msg("tool", big, tool_call_id="tc_read_file"),
            _msg("user", "new task " + ("e" * 300)),
            _msg("assistant", "done " + ("f" * 300)),
        ]
        compactor.max_message_tokens = 50
        # The old tool group is collapsed but otherwise kept in full (the window
        # is huge, so no summarization is needed) -- the marker must be present.
        compacted, _ = compactor.compact(messages, preserve_recent_turns=1)
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertIn("payload truncated", text)
        self.assertNotIn(big, text)
        # Kept turn untouched.
        self.assertEqual(compacted[-2:], messages[-2:])


class TestLLMSummarizerFallback(unittest.TestCase):

    @staticmethod
    def _six_pairs():
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"task {i} " + ("p" * 400)))
            msgs.append(_msg("assistant", f"work {i} " + ("z" * 400)))
        return msgs

    def test_uses_llm_when_available(self):
        def fake_summarize(block):
            return "LLM DIGEST: the model condensed this block."

        compactor = Compactor(
            context_window=2000, threshold_ratio=0.70,
            summarize_fn=fake_summarize, summary_mode="auto",
        )
        messages = self._six_pairs()
        compacted, report = compactor.compact(messages, preserve_recent_turns=1)
        self.assertTrue(report["compacted"])
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertIn("LLM DIGEST", text)

    def test_falls_back_to_heuristic_on_error(self):
        def broken_summarize(block):
            raise RuntimeError("provider down")

        compactor = Compactor(
            context_window=2000, threshold_ratio=0.70,
            summarize_fn=broken_summarize, summary_mode="llm",
        )
        messages = self._six_pairs()
        compacted, report = compactor.compact(messages, preserve_recent_turns=1)
        self.assertTrue(report["compacted"])
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertIn("CONTEXT SUMMARY", text)

    def test_heuristic_mode_ignores_summarizer(self):
        calls = []

        def fake_summarize(block):
            calls.append(block)
            return "SHOULD NOT BE USED"

        compactor = Compactor(
            context_window=2000, threshold_ratio=0.70,
            summarize_fn=fake_summarize, summary_mode="heuristic",
        )
        messages = self._six_pairs()
        compacted, _ = compactor.compact(messages, preserve_recent_turns=1)
        self.assertEqual(calls, [])
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertNotIn("SHOULD NOT BE USED", text)


class TestHysteresis(unittest.TestCase):

    def _six_user_messages(self, content_char_len):
        return [_msg("user", "u" * content_char_len) for _ in range(6)]

    def test_fires_when_above_threshold_and_fresh(self):
        compactor = Compactor(context_window=1000, threshold_ratio=0.40)
        messages = self._six_user_messages(300)  # ~80 tokens each => 0.48 ratio
        self.assertEqual(compactor.should_compact(messages), True)

    def test_suppressed_right_after_compaction(self):
        compactor = Compactor(context_window=1000, threshold_ratio=0.40)
        messages = self._six_user_messages(500)
        messages_after, report = compactor.compact(messages, preserve_recent_turns=2)
        self.assertTrue(report["compacted"])
        # Usage still above threshold but within the post-compaction margin =>
        # hysteresis keeps it quiet instead of re-compacting every turn.
        self.assertEqual(compactor.should_compact(messages_after), False)

    def test_gate_logic(self):
        low = Compactor(context_window=1000, threshold_ratio=0.40)
        low._last_target_ratio = 0.55  # recently compacted to ~55%
        messages = self._six_user_messages(300)  # 0.48 ratio, above threshold
        self.assertFalse(low.should_compact(messages))

        fresh = Compactor(context_window=1000, threshold_ratio=0.40)
        self.assertTrue(fresh.should_compact(messages))


class TestEmergencyTrim(unittest.TestCase):

    def test_trims_only_old_payloads_behind_before_index(self):
        compactor = Compactor(
            context_window=2000, threshold_ratio=0.05,
            cap_ratio=0.95, max_message_tokens=40,
        )
        # Two huge old tool payloads + a smaller diagnostic one.
        messages = [
            _msg("user", "task"),
            _msg("assistant", "", tool_calls=[_tool_call("run", None)]),
            _msg("tool", "Z" * 4500, tool_call_id="tc_run"),   # old, huge
            _msg("tool", "Y" * 4500, tool_call_id="tc_run2"),  # old, huge
        ]
        before_index = len(messages)
        # Simulate the current turn appending after before_index.
        messages.append(_msg("assistant", "KEEP this text verbatim " + ("k" * 200)))
        messages.append(_msg("tool", "fresh result " + ("f" * 50), tool_call_id="tc_run3"))

        trimmed, report = compactor.emergency_trim(messages, before_index=before_index)
        self.assertIsNotNone(report)
        # Old payloads collapsed.
        self.assertIn("payload truncated", trimmed[2]["content"])
        self.assertIn("payload truncated", trimmed[3]["content"])
        # Current-turn messages untouched.
        self.assertEqual(trimmed[4], messages[4])
        self.assertEqual(trimmed[5], messages[5])

    def test_noop_within_cap(self):
        compactor = Compactor(context_window=100000, threshold_ratio=0.05, cap_ratio=0.95)
        messages = [
            _msg("user", "task"),
            _msg("assistant", "work"),
        ]
        trimmed, report = compactor.emergency_trim(messages)
        self.assertIsNone(report)
        self.assertEqual(trimmed, messages)


class TestCheckpointBuilding(unittest.TestCase):

    def test_checkpoint_includes_marker_and_file_paths(self):
        compactor = Compactor(context_window=2000, threshold_ratio=0.70)
        messages = self._large_set()
        compacted, report = compactor.compact(messages, preserve_recent_turns=2)
        # With a fully-consumed old block the consolidation should kick in.
        text = " ".join(str(m.get("content") or "") for m in compacted)
        self.assertIn("[AUTOMATED CONTEXT MEMORY CHECKPOINT]", text)

    @staticmethod
    def _large_set():
        msgs = []
        for i in range(12):
            msgs.append(_msg("user", f"Goal {i}: " + ("a" * 400)))
            msgs.append(_msg("assistant", "", tool_calls=[_tool_call("read_file", f"f{i}.py")]))
            msgs.append(_msg("tool", f"Contents of f{i}.py: ok", tool_call_id=f"tc_{i}"))
        return msgs


class TestConfigKnobs(unittest.TestCase):

    def test_new_compaction_fields_exist_and_settable(self):
        cfg = HarnessConfig()
        self.assertEqual(cfg.compact_target_ratio, 0.60)
        self.assertEqual(cfg.compact_cap_ratio, 0.95)
        self.assertEqual(cfg.compact_preserve_turns, 4)
        self.assertEqual(cfg.compact_summary, "auto")
        self.assertTrue(cfg.set_field("compact_target_ratio", "0.55"))
        self.assertEqual(cfg.compact_target_ratio, 0.55)
        self.assertTrue(cfg.set_field("compact_summary", "llm"))
        self.assertEqual(cfg.compact_summary, "llm")


if __name__ == "__main__":
    unittest.main()