"""
Tests for Skills Discovery and Built-in Skills Catalog.
"""
import unittest
from harness.skills.loader import SkillsManager
from harness.tools.skill_tools import ListSkillsTool, ReadSkillTool
from harness.tools import ToolRegistry
from harness.core.prompt import SystemPromptBuilder
from harness.core.modes import Mode

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

    def test_list_skills_tool(self):
        tool = ListSkillsTool(self.mgr)
        out = tool.execute()
        self.assertTrue(tool.is_read_only)
        self.assertIn("Available Skills", out)
        self.assertIn("skill_creator", out)
        self.assertIn("mcp_integrator", out)
        self.assertIn("read_skill", out)

    def test_read_skill_tool(self):
        tool = ReadSkillTool(self.mgr)
        out = tool.execute(name="skill_creator")
        self.assertTrue(tool.is_read_only)
        self.assertIn("=== SKILL: skill_creator ===", out)
        self.assertIn("INSTRUCTIONS", out)

    def test_read_skill_unknown(self):
        tool = ReadSkillTool(self.mgr)
        out = tool.execute(name="does_not_exist")
        self.assertIn("not found", out)

    def test_skill_tools_registered_read_only(self):
        registry = ToolRegistry(skills_manager=self.mgr)
        lst = registry.get("list_skills")
        rd = registry.get("read_skill")
        self.assertIsNotNone(lst, "list_skills must be registered")
        self.assertIsNotNone(rd, "read_skill must be registered")
        self.assertTrue(lst.is_read_only)
        self.assertTrue(rd.is_read_only)
        self.assertIn("list_skills", [s["function"]["name"] for s in registry.get_openai_schemas(Mode.PLAN)])

    def test_prompt_uses_skill_tools_directive(self):
        prompt = SystemPromptBuilder().build()
        self.assertIn("list_skills", prompt)
        self.assertIn("read_skill", prompt)

    def test_prompt_no_longer_injects_all_skills(self):
        prompt = SystemPromptBuilder().build()
        self.assertNotIn("LOADED SKILLS", prompt)
        self.assertNotIn("skill_creator", prompt)

if __name__ == "__main__":
    unittest.main()
