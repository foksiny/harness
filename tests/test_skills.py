"""
Tests for Skills Discovery and Built-in Skills Catalog.
"""
import unittest
from harness.skills.loader import SkillsManager
from harness.tools.skill_tools import ListSkillsTool, ReadSkillTool
from harness.tools import ToolRegistry
from harness.core.prompt import SystemPromptBuilder
from harness.core.modes import Mode
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.providers.base import LLMChunk
from harness.providers.mock_provider import MockProvider

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

    def test_step_seeds_list_skills_before_any_work(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        agent = HarnessAgent(cfg)
        events = list(agent.step("what is this project about?"))
        # Eager list_skills tool + result must be injected into the conversation.
        seeded = [m for m in agent.session.messages if m.get("role") == "tool" and m.get("name") == "list_skills"]
        self.assertEqual(len(seeded), 1)
        self.assertIn("skill_creator", seeded[0]["content"])
        self.assertTrue(any(ev.type == "tool_call_start" and ev.data.get("name") == "list_skills" for ev in events))
        # The very first provider request must already include the catalog.
        first_call = agent.provider.call_history[0]
        self.assertEqual(first_call["messages"][-1]["role"], "tool")
        self.assertEqual(first_call["messages"][-1]["name"], "list_skills")

    def test_step_does_not_re_seed_list_skills(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        agent = HarnessAgent(cfg)
        list(agent.step("static analysis task"))
        list(agent.step("second task"))
        seeded = [m for m in agent.session.messages if m.get("role") == "tool" and m.get("name") == "list_skills"]
        self.assertEqual(len(seeded), 1)

    def test_empty_response_never_ends_turn_until_model_answers(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.thinking_effort = "off"
        cfg.learning_enabled = False

        class EmptyThenTextProvider(MockProvider):
            def __init__(self, empties_first):
                super().__init__(responses=[])
                self._empties_left = empties_first

            def stream_chat(self, messages, model=None, thinking_effort="high", tools=None, system_prompt=None, **kwargs):
                self.call_history.append({
                    "messages": list(messages),
                    "model": model,
                    "tools": tools,
                    "system_prompt": system_prompt,
                })
                if self._empties_left:
                    self._empties_left -= 1
                    yield LLMChunk()
                else:
                    yield LLMChunk(delta_text="Here is my final answer.", finish_reason="stop")

        mock = EmptyThenTextProvider(empties_first=3)
        agent = HarnessAgent(cfg)
        agent.provider = mock
        events = list(agent.step("respond to me"))

        # An empty reply must not auto-stop the turn: all 4 provider calls ran
        # (3 empty + 1 answering) and the turn only ended once text arrived.
        self.assertEqual(len(mock.call_history), 4)
        nudges = [m for m in agent.session.messages if str(m.get("content", "")).startswith("[SYSTEM]: Your previous response was empty")]
        self.assertEqual(len(nudges), 3)
        self.assertTrue(any(ev.type == "text_delta" and "Here is my final answer." in str(ev.data) for ev in events))
        self.assertTrue(any(ev.type == "turn_complete" for ev in events))
        self.assertTrue(any(ev.type == "step_end" and ev.data.get("complete") for ev in events))

    def test_provider_receives_list_skills_result(self):
        from harness.providers.mock_provider import MockProvider
        mock = MockProvider(responses=[
            LLMChunk(delta_text="The project is an AI agent harness.", finish_reason="stop"),
        ])
        cfg = HarnessConfig()
        cfg.provider = "mock"
        cfg.learning_enabled = False
        agent = HarnessAgent(cfg)
        agent.provider = mock
        list(agent.step("task"))
        seen = [m for m in mock.call_history[0]["messages"] if m.get("name") == "list_skills"]
        self.assertEqual(len(seen), 1)

if __name__ == "__main__":
    unittest.main()
