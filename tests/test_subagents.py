"""
Tests for Subagent Orchestration Engine.
"""
import unittest
from harness.core.subagents import SubagentOrchestrator, SubagentType

class TestSubagents(unittest.TestCase):

    def test_subagent_spawning(self):
        invocations = []

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            invocations.append({
                "sys_prompt": sys_prompt,
                "prompt": prompt,
                "allowed_tools": allowed_tools,
                "agent_id": kwargs.get("agent_id"),
            })
            return f"Synthesized research report for {prompt}", 2

        orchestrator = SubagentOrchestrator(mock_runner)

        # Spawn researcher
        res = orchestrator.spawn("researcher", "Find all auth decorators")
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.agent_type, "researcher")
        self.assertIn("Synthesized research report", res.output)
        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0]["agent_id"], "researcher_1")

        # Check allowed tools for researcher
        allowed = invocations[0]["allowed_tools"]
        self.assertIn("view_file", allowed)
        self.assertIn("grep_search", allowed)
        self.assertNotIn("edit_file", allowed)  # Researcher cannot edit files

        # Spawn coder
        res_coder = orchestrator.spawn("coder", "Implement JWT validation")
        self.assertEqual(res_coder.agent_type, "coder")
        self.assertEqual(invocations[1]["agent_id"], "coder_2")
        allowed_coder = invocations[1]["allowed_tools"]
        self.assertIn("edit_file", allowed_coder) # Coder can edit files

        # Orchestrator keeps a durable activity record per spawned agent
        # (sorted most-recent-first for the board view).
        records = orchestrator.list_records()
        self.assertEqual({r.agent_id for r in records}, {"researcher_1", "coder_2"})
        self.assertEqual(records[0].agent_id, "coder_2")
        self.assertEqual(records[0].status, "completed")
        self.assertIn("Synthesized research report", records[0].output)

if __name__ == "__main__":
    unittest.main()
