"""
Tests for Context Window Tracking and Auto-Compaction.
"""
import unittest
from harness.core.compaction import (
    estimate_tokens,
    calculate_history_tokens,
    Compactor,
)

class TestCompaction(unittest.TestCase):

    def test_token_estimation(self):
        txt = "a" * 380
        toks = estimate_tokens(txt)
        self.assertEqual(toks, 100)

        # Structure estimation
        obj = {"key": "value", "list": [1, 2, 3]}
        self.assertGreater(estimate_tokens(obj), 0)

    def test_compactor_threshold_and_checkpoint(self):
        compactor = Compactor(context_window=1000, threshold_ratio=0.70)

        # Generate large conversation history
        messages = []
        for i in range(12):
            messages.append({"role": "user", "content": f"User instruction #{i}: " + ("x" * 200)})
            messages.append({"role": "assistant", "content": f"Assistant response #{i}: " + ("y" * 200)})

        # Verify threshold is reached
        status = compactor.check_status(messages)
        self.assertTrue(status.is_warning)
        self.assertTrue(compactor.should_compact(messages))

        # Perform compaction
        compacted_msgs, report = compactor.compact(messages, preserve_recent_turns=4)
        self.assertTrue(report["compacted"])
        self.assertGreater(report["saved_tokens"], 0)
        self.assertLess(len(compacted_msgs), len(messages))

        # Verify structured checkpoint in first message
        first_msg = compacted_msgs[0]
        self.assertEqual(first_msg["role"], "system")
        self.assertIn("[AUTOMATED CONTEXT MEMORY CHECKPOINT]", first_msg["content"])

if __name__ == "__main__":
    unittest.main()
