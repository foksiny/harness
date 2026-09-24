"""
Tests for proactive context-budget management in Harness.

Covers the ContextBudget pressure gauge, per-tool output caps, the updated tool
layers (view_file / run_command / grep_search / execute_python), reasoning-content
stripping during compaction, and the context-aware system-prompt assembly.
"""
import json
import unittest

from harness.config import HarnessConfig
from harness.core.context_budget import (
    ContextBudget,
    DEFAULT_OUTPUT_LIMITS,
    truncate_output,
    PRESSURE_HEALTHY,
    PRESSURE_WARNING,
    PRESSURE_CRITICAL,
    ACTION_COMPACT,
    ACTION_TRIM,
)
from harness.core.compaction import Compactor, calculate_history_tokens
from harness.core.prompt import SystemPromptBuilder
from harness.core.modes import Mode
from harness.core.permissions import PermissionLevel


def _msg(role, content, tool_calls=None, tool_call_id=None, reasoning=None):
    m = {"role": role, "content": content}
    if tool_calls is not None:
        m["tool_calls"] = tool_calls
    if tool_call_id is not None:
        m["tool_call_id"] = tool_call_id
    if reasoning is not None:
        m["reasoning_content"] = reasoning
    return m


def _tool_call(name, args):
    return {
        "id": f"tc_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class TestContextBudgetPressure(unittest.TestCase):

    def test_healthy_when_low(self):
        budget = ContextBudget(context_window=10000, threshold_ratio=0.75, cap_ratio=0.95)
        msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
        status = budget.check(msgs, "")
        self.assertIn(status.pressure, (PRESSURE_HEALTHY, PRESSURE_WARNING))
        self.assertFalse(status.is_critical)
        self.assertLessEqual(status.usage_ratio, 0.75)
        self.assertEqual(status.action, "none")

    def test_warning_action_is_compact(self):
        budget = ContextBudget(context_window=1000, threshold_ratio=0.5, cap_ratio=0.9)
        # Grow the transcript until pressure is in the warning band [0.5, 0.9).
        msgs = []
        status = None
        for _ in range(50):
            msgs.append(_msg("user", "u" * 600))
            msgs.append(_msg("assistant", "a" * 600))
            status = budget.check(msgs, "")
            if status.pressure == PRESSURE_WARNING:
                break
        self.assertEqual(status.pressure, PRESSURE_WARNING)
        self.assertFalse(status.is_critical)
        self.assertEqual(status.action, ACTION_COMPACT)

    def test_critical_action_is_trim(self):
        budget = ContextBudget(context_window=1000, threshold_ratio=0.5, cap_ratio=0.6)
        msgs = [_msg("user", "u" * 3000) for _ in range(3)]
        status = budget.check(msgs, "")
        self.assertEqual(status.pressure, PRESSURE_CRITICAL)
        self.assertEqual(status.action, ACTION_TRIM)
        self.assertTrue(status.is_critical)
        self.assertTrue(status.is_warning)

    def test_rebind_updates_thresholds(self):
        budget = ContextBudget(context_window=1000, cap_ratio=0.9)
        budget.rebind(context_window=2000, threshold_ratio=0.8, cap_ratio=0.99)
        self.assertEqual(budget.context_window, 2000)
        self.assertEqual(budget.threshold_ratio, 0.8)


class TestOutputLimits(unittest.TestCase):

    def test_default_limits_exist_for_highvolume_tools(self):
        for tool in ("view_file", "run_command", "grep_search", "find_files", "list_dir", "execute_python", "exa_search"):
            self.assertIn(tool, DEFAULT_OUTPUT_LIMITS)

    def test_get_and_set_output_limit(self):
        budget = ContextBudget(context_window=10000)
        self.assertGreater(budget.get_output_limit("run_command"), 0)
        budget.set_output_limit("run_command", 0)
        self.assertEqual(budget.get_output_limit("run_command"), 0)

    def test_truncate_output_caps_long_result(self):
        budget = ContextBudget(context_window=10000)
        big = "x" * 30000
        res = budget.truncate_output("run_command", big)
        self.assertLess(len(res), 30000)
        self.assertIn("output truncated by context budget", res)

    def test_truncate_output_explicit_override_wins(self):
        budget = ContextBudget(context_window=10000)
        big = "x" * 30000
        res = budget.truncate_output("run_command", big, max_chars=10 ** 7)
        self.assertEqual(res, big)

    def test_truncate_output_untouched_within_limit(self):
        budget = ContextBudget(context_window=10000)
        small = "y" * 100
        self.assertEqual(budget.truncate_output("view_file", small), small)

    def test_standalone_truncate_helper(self):
        big = "z" * 500
        res = truncate_output(big, 100)
        self.assertLess(len(res), 500)
        self.assertIn("truncated", res)


class TestToolLayerCaps(unittest.TestCase):
    """Integration: ensure the high-volume tools actually cap their output."""

    def test_view_file_caps_by_default(self):
        from harness.tools.filesystem import ViewFileTool
        import os
        import tempfile
        tool = ViewFileTool()
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("line_" + "x" * 2000 + "\n" * 100)  # ~ multiple lines but moderate
            f.flush()
            name = f.name
        try:
            # Write 3000 long lines so default output exceeds 8k chars.
            with open(name, "w", encoding="utf-8") as f:
                for i in range(3000):
                    f.write(f"L{i:05d}_abcdefghijklmnopqrstuvwxyz\n")
            res = tool.execute(name)
            self.assertIn("output truncated by context budget", res)
            self.assertNotIn("L02999_abcdefghijklmnopqrstuvwxyz", res)
            # Explicit max_chars recovers full output.
            full = tool.execute(name, max_chars=10 ** 7)
            self.assertIn("L02999_abcdefghijklmnopqrstuvwxyz", full)
        finally:
            os.unlink(name)

    def test_grep_search_caps_by_default(self):
        from harness.tools.search import GrepSearchTool
        tool = GrepSearchTool()
        # A huge query string isn't a file search; this just guards the helper.
        cap = DEFAULT_OUTPUT_LIMITS["grep_search"]
        result = "z" * (cap + 500)
        limited = tool._limit(result, None)
        self.assertIn("output truncated by context budget", limited)


class TestReasoningStrippingInCompaction(unittest.TestCase):

    def _six_turns_with_reasoning(self):
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"task {i}"))
            msgs.append(_msg("assistant", f"work {i}", tool_calls=[_tool_call("read_file", {"path": f"f{i}.py"})], reasoning="S" * 500))
            msgs.append(_msg("tool", f"result {i}", tool_call_id="tc_read_file"))
        return msgs

    def test_digest_strips_reasoning_from_summarized_block(self):
        # The core invariant: verbose reasoning blobs from old turns are never
        # carried into the compacted transcript — regardless of which sweep
        # (heuristic digest, LLM digest, or checkpoint consolidation) wins.
        compactor = Compactor(
            context_window=500, threshold_ratio=0.30,
            summarize_fn=lambda block: "LLM DIGEST delivered", summary_mode="auto",
        )
        messages = self._six_turns_with_reasoning()
        compacted, report = compactor.compact(messages, preserve_recent_turns=1)
        # A 500-token window is far below the ~800-token transcript, so it MUST
        # compact (down to the target).
        self.assertTrue(report["compacted"])
        text = " ".join(str(m.get("content") or "") for m in compacted)
        # NONE of the (six * 500-char) reasoning blobs survive into the history.
        self.assertNotIn("S" * 500, text)
        self.assertLess(text.count("S" * 100), 6)
        # And the signal the LLM digest / checkpoint carried is present.
        self.assertTrue("AUTOMATED CONTEXT MEMORY CHECKPOINT" in text or "LLM DIGEST" in text)

    def test_kept_recent_turns_keep_reasoning(self):
        # Only *old* summarized groups lose reasoning; the recent working set
        # keeps its reasoning verbatim so the model doesn't lose in-flight chain.
        compactor = Compactor(context_window=3000, threshold_ratio=0.70,
                              summarize_fn=lambda b: "digest")
        messages = self._six_turns_with_reasoning()
        compacted, _ = compactor.compact(messages, preserve_recent_turns=1)
        # The last group (user/turn) is preserved verbatim including reasoning.
        last_group = compacted[-3:]
        found_reasoning = any(m.get("reasoning_content") == "S" * 500 for m in last_group)
        self.assertTrue(found_reasoning)
        # Old summarized group has no raw reasoning blob.
        self.assertNotIn("S" * 500, str(compacted[0].get("content") or ""))

    def test_strip_reasoning_returns_copy(self):
        compactor = Compactor(context_window=10000)
        msg = _msg("user", "hello", reasoning="R" * 100)
        out = compactor._strip_reasoning(msg)
        self.assertIsNone(out["reasoning_content"])
        # Original untouched.
        self.assertEqual(msg["reasoning_content"], "R" * 100)


class TestPromptContextAwareness(unittest.TestCase):

    def test_mcp_elided_when_degreded(self):
        builder = SystemPromptBuilder()
        prompt = builder.build(mcp_tools_summary="LINE1\n- tool A: does x\n- tool B: does y")
        self.assertIn("tool A: does x", prompt)
        reduced = builder.build(
            mcp_tools_summary="LINE1\n- tool A: does x\n- tool B: does y",
            degrade_verbose=True,
        )
        self.assertIn("elided to conserve context budget", reduced)
        self.assertNotIn("tool A: does x", reduced)

    def test_builder_accepts_new_kwargs(self):
        builder = SystemPromptBuilder()
        prompt = builder.build(
            mcp_tools_summary="",
            active_todos="- [ ] x",
            learned_lessons="a prior lesson",
            degrade_verbose=False,
        )
        self.assertIn("- [ ] x", prompt)
        self.assertIn("a prior lesson", prompt)

    def test_git_info_cached_and_clearable(self):
        from harness.core.prompt import get_git_info, clear_git_info_cache
        clear_git_info_cache()
        a = get_git_info()
        b = get_git_info()
        self.assertEqual(a, b)
        clear_git_info_cache()

    def test_workspace_dir_loads_custom_rules(self):
        import tempfile
        from pathlib import Path
        from harness.core.prompt import SystemPromptBuilder, load_project_rules
        with tempfile.TemporaryDirectory() as td:
            rule_file = Path(td) / "AGENTS.md"
            rule_file.write_text("Custom workspace rule content")
            rules = load_project_rules(td)
            self.assertIn("Custom workspace rule content", rules)

            builder = SystemPromptBuilder()
            prompt = builder.build(workspace_dir=td)
            self.assertIn("Custom workspace rule content", prompt)
            self.assertIn(td, prompt)


class TestConfigKnobs(unittest.TestCase):

    def test_defaults_sane(self):
        cfg = HarnessConfig()
        self.assertGreater(cfg.compact_threshold, cfg.compact_target_ratio)
        self.assertGreater(cfg.compact_cap_ratio, cfg.compact_threshold)


if __name__ == "__main__":
    unittest.main()