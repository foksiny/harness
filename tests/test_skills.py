"""
Tests for Skills Discovery and Built-in Skills Catalog.
"""
import os
import unittest
from pathlib import Path
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
        # 12 core/engineering + 54 vendored motion skills + motion_verify + motion_graphics removed
        skills = self.mgr.list_skills()
        self.assertGreaterEqual(len(skills), 66)

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

    def test_motion_graphics_builtin_is_removed(self):
        # Replaced wholesale by the 54 vendored iart-ai motion skills.
        self.assertIsNone(self.mgr.get("motion_graphics"))
        from harness.tools.skill_tools import ReadSkillTool
        out = ReadSkillTool(self.mgr).execute(name="motion_graphics")
        self.assertIn("not found", out)

    def test_vendored_motion_skills_are_registered(self):
        expected = [
            # motion design & craft
            "animation_principles", "beat_sync_editing", "color_motion",
            "shot_composition", "motion_art_direction", "motion_background",
            "after_effects", "remotion_video", "logo_animation",
            # web motion
            "gsap_web", "svg_animation", "lottie_animation", "micro_interaction",
            "glassmorphism", "page_transition_animation", "accessible_animation",
            "fps_60_animation", "ascii_animation",
            # webgl
            "threejs_animation", "shader_glsl", "particle_system",
            # type / code-drawn / generative / audio
            "kinetic_typography", "javascript_animation", "generative_illustration",
            "soundtrack",
            # data & explainers
            "chart_animation", "animated_infographic", "presentation_video",
            "explainer_video", "diagram_animation", "isometric_animation",
            "whiteboard_animation", "wrapped_video", "manim",
            # platform & social video
            "short_form_video", "caption_animation", "countdown_video",
            "audiogram", "youtube_intro_outro", "text_message_animation",
            "photo_slideshow", "product_demo_video", "promo_video", "launch_video",
            "ad_creative_video", "testimonial_video",
            # lower thirds (two disambiguated upstream packs)
            "lower_thirds", "lower_thirds_overlay",
            # maps
            "map_animation",
            # freelance / studio business
            "creative_brief", "motion_pricing", "client_revisions",
            "video_delivery_specs", "brand_motion_guidelines",
            # shared verify toolkit
            "motion_verify",
        ]
        self.assertEqual(len(expected), 55)
        for name in expected:
            skill = self.mgr.get(name)
            self.assertIsNotNone(skill, f"Motion skill '{name}' should be loaded")
            self.assertTrue(skill.is_builtin, f"'{name}' must be a built-in")
            self.assertTrue(skill.description.strip(), f"'{name}' needs a description")
            self.assertTrue(skill.triggers, f"'{name}' needs triggers")

    def test_motion_skill_bodies_and_references_present(self):
        # Bodies are vendored verbatim, so they must be substantial, not stubs.
        for name in ("chart_animation", "manim", "threejs_animation", "lower_thirds_overlay"):
            skill = self.mgr.get(name)
            body = skill.load_instructions()
            self.assertGreater(len(body), 3000, f"'{name}' body was not vendored")
            self.assertNotIn("---", body.split("\n")[0], f"'{name}' body still has frontmatter")
        # Reference material ships alongside the skills.
        builtin = Path(self.mgr.get("manim").source_path).parent
        self.assertTrue((builtin / "references").is_dir())
        self.assertTrue(any((builtin / "references").iterdir()))

    def test_verify_toolkit_scripts_are_executable_and_shared(self):
        toolkit = Path(self.mgr.get("motion_verify").source_path).parent / "scripts"
        for script in ("seek-shot.sh", "contact-sheet.sh", "probe-mp4.sh"):
            path = toolkit / script
            self.assertTrue(path.exists(), f"{script} must ship with motion_verify")
            self.assertTrue(os.access(path, os.X_OK), f"{script} must be executable")

    def test_motion_skill_trigger_matching_is_discriminative(self):
        cases = [
            ("make a bar chart race video from my csv", "chart_animation"),
            ("animate this flowchart step by step", "diagram_animation"),
            ("write a remotion component", "remotion_video"),
            ("help with a gsap scrolltrigger timeline", "gsap_web"),
            ("build a 3blue1brown style equation animation", "manim"),
            ("write an after effects expression for a wiggle", "after_effects"),
            ("make a spotify wrapped style recap video", "wrapped_video"),
            ("what should I charge for a 30 second explainer", "motion_pricing"),
            ("make an isometric infographic of our stack", "isometric_animation"),
            ("render a photo montage with ken burns pan and zoom", "photo_slideshow"),
        ]
        for prompt, expected in cases:
            matched = {s.name for s in self.mgr.match_skills(prompt)}
            self.assertIn(expected, matched, f"'{prompt}' should match '{expected}'")

    def test_skill_instructions_are_loaded_lazily(self):
        from harness.skills.loader import parse_skill_markdown
        skill = self.mgr.get("chart_animation")
        self.assertIsNone(skill._instructions, "body must not be read at discovery time")
        first = skill.load_instructions()
        self.assertIsNotNone(skill._instructions)
        self.assertEqual(first, skill.load_instructions(), "body is cached after first read")
        # The property must stay equivalent for existing callers.
        self.assertEqual(skill.instructions, first)
        # A skill parsed from memory (no source_path) still exposes its body.
        inline = parse_skill_markdown("---\nname: x\ndescription: d\ntriggers: [a]\n---\nBODY")
        self.assertEqual(inline.instructions, "BODY")

    def test_motion_skills_are_verbatim_except_name_and_triggers(self):
        """Vendoring must not rewrite upstream instruction bodies or descriptions."""
        import re
        fm_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
        desc_re = re.compile(r"^description:[ \t]*(.*)$", re.M)
        for name in ("chart_animation", "manim", "after_effects", "gsap_web",
                     "lower_thirds", "lower_thirds_overlay", "soundtrack"):
            skill = self.mgr.get(name)
            raw = Path(skill.source_path).read_text(encoding="utf-8")
            m = fm_re.match(raw)
            self.assertIsNotNone(m, f"{name} lost its frontmatter")
            self.assertEqual(skill.description, desc_re.search(m.group(1)).group(1).strip())
            # Body must be non-empty and must not retain the frontmatter block.
            self.assertTrue(skill.load_instructions().startswith("#"), f"{name} body malformed")
            self.assertNotIn("\nname: ", skill.load_instructions()[:40])

    def test_builtin_skill_files_are_declared_as_package_data(self):
        """Regression: skills ship no __init__.py, so they must be package data."""
        import re
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if not pyproject.exists():  # installed-only layout
            self.skipTest("pyproject.toml not available")
        text = pyproject.read_text(encoding="utf-8")
        self.assertRegex(text, r"namespaces\s*=\s*true")
        pkg_data = re.search(r"\[tool\.setuptools\.package-data\](.*?)(\n\[|\Z)", text, re.DOTALL)
        self.assertIsNotNone(pkg_data, "package-data table must exist")
        self.assertIn("builtin/**/*.md", pkg_data.group(1))
        self.assertIn("builtin/**/*.sh", pkg_data.group(1))

    def test_motion_skills_attribution_is_present(self):
        builtin = Path(self.mgr.get("manim").source_path).parent.parent
        license_file = builtin / "MOTION_SKILLS_LICENSE"
        provenance = builtin / "MOTION_SKILLS_PROVENANCE.md"
        self.assertTrue(license_file.exists(), "vendored MIT license must ship")
        self.assertIn("iart.ai", license_file.read_text(encoding="utf-8"))
        self.assertTrue(provenance.exists(), "provenance record must ship")
        self.assertIn("motion-skills", provenance.read_text(encoding="utf-8"))

    def test_every_builtin_skill_is_well_formed(self):
        """No junk entries: docs/provenance files must not register as skills."""
        builtin_skills = [s for s in self.mgr.list_skills() if s.is_builtin]
        self.assertTrue(builtin_skills)
        for s in builtin_skills:
            self.assertTrue(s.name.strip(), "skill has an empty name")
            self.assertNotIn(" ", s.name, f"skill name '{s.name}' should be snake_case")
            self.assertTrue(s.description.strip(), f"'{s.name}' has an empty description")
            self.assertTrue(s.triggers, f"'{s.name}' has no triggers")
            self.assertTrue(s.source_path and Path(s.source_path).exists())
        names = [s.name for s in builtin_skills]
        self.assertEqual(len(names), len(set(names)), "duplicate built-in skill names")

    def test_plain_markdown_in_skills_dir_is_not_a_skill(self):
        from harness.skills.loader import parse_skill_markdown
        # The attribution record lives in the builtin dir and has no frontmatter.
        prose = "# Vendored Motion Skills\n\nJust documentation.\n"
        self.assertIsNone(parse_skill_markdown(prose, "/x/builtin/MOTION_SKILLS_PROVENANCE.md"))
        self.assertIsNone(self.mgr.get("MOTION_SKILLS_PROVENANCE"))
        self.assertIsNone(self.mgr.get("MOTION_SKILLS_PROVENANCE.md"))
        # Frontmatter always wins, regardless of scope.
        self.assertIsNotNone(parse_skill_markdown(
            "---\nname: doc_skill\ndescription: d\n---\nBody", "/x/builtin/doc_skill.md"))
        # A bare <name>.md is still accepted in user skill dirs (back-compat).
        self.assertIsNotNone(parse_skill_markdown(
            prose, "/x/user_skill.md", is_builtin=False, allow_bare_markdown=True))

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
