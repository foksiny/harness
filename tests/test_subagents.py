"""
Tests for Subagent Orchestration Engine.
"""
import unittest
from harness.core.subagents import SubagentOrchestrator, SubagentType

class TestSubagents(unittest.TestCase):

    def test_subagent_spawning(self):
        invocations = []

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns):
            invocations.append({
                "sys_prompt": sys_prompt,
                "prompt": prompt,
                "allowed_tools": allowed_tools,
            })
            return f"Synthesized research report for {prompt}", 2

        orchestrator = SubagentOrchestrator(mock_runner)

        # Spawn researcher
        res = orchestrator.spawn("researcher", "Find all auth decorators")
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.agent_type, "researcher")
        self.assertIn("Synthesized research report", res.output)
        self.assertEqual(len(invocations), 1)

        # Check allowed tools for researcher
        allowed = invocations[0]["allowed_tools"]
        self.assertIn("view_file", allowed)
        self.assertIn("grep_search", allowed)
        self.assertNotIn("edit_file", allowed)  # Researcher cannot edit files

        # Spawn coder
        res_coder = orchestrator.spawn("coder", "Implement JWT validation")
        self.assertEqual(res_coder.agent_type, "coder")
        allowed_coder = invocations[1]["allowed_tools"]
        self.assertIn("edit_file", allowed_coder) # Coder can edit files

if __name__ == "__main__":
    unittest.main()
