"""
Tests for Skills Discovery and Built-in Skills Catalog.
"""
import unittest
from harness.skills.loader import SkillsManager

class TestSkills(unittest.TestCase):

    def setUp(self):
        self.mgr = SkillsManager()

    def test_builtin_skills_count(self):
        # We created 10 builtin skills
        skills = self.mgr.list_skills()
        self.assertGreaterEqual(len(skills), 10)

    def test_required_specialized_skills_exist(self):
        # 1. skill_creator
        sk_creator = self.mgr.get("skill_creator")
        self.assertIsNotNone(sk_creator, "skill_creator must exist")
        self.assertTrue(sk_creator.matches("Please generate a new skill for kubernetes"))

        # 2. mcp_integrator
        mcp_integ = self.mgr.get("mcp_integrator")
        self.assertIsNotNone(mcp_integ, "mcp_integrator must exist")
        self.assertTrue(mcp_integ.matches("Add an MCP server for postgres"))

    def test_other_builtin_skills(self):
        expected = [
            "git_master",
            "test_architect",
            "code_refactor",
            "database_query",
            "api_designer",
            "docker_deploy",
            "performance_profiler",
            "documentation_writer",
        ]
        for name in expected:
            skill = self.mgr.get(name)
            self.assertIsNotNone(skill, f"Skill '{name}' should be loaded")

    def test_skills_summary_formatting(self):
        summary = self.mgr.format_summary()
        self.assertIn("Available Skills", summary)
        self.assertIn("skill_creator", summary)
        self.assertIn("mcp_integrator", summary)

if __name__ == "__main__":
    unittest.main()
