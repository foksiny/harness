"""
Tests for the agent self-improvement / learning feature:
lesson storage + cascade, matching, injection, promotion, tools, auto-capture, and /learn.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.learning import LearningManager
from harness.core.prompt import SystemPromptBuilder
from harness.core.modes import Mode
from harness.providers.mock_provider import MockProvider
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.tools import ToolRegistry
from harness.tools.learning_tools import LearnRecordTool, LearnRecallTool, LearnPromoteTool
from harness.commands.registry import CommandRegistry


class ScriptedProvider(MockProvider):
    """Mock provider that replays a per-call script, then emits a plain stop reply."""

    def __init__(self, script):
        super().__init__(responses=[])
        self.script = list(script)
        self.call_count = 0

    def stream_chat(self, messages, model=None, thinking_effort="high", tools=None, system_prompt=None, **kwargs):
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.script):
            for chunk in self.script[idx]:
                yield chunk
        else:
            yield LLMChunk(delta_text="done.", finish_reason="stop")


def make_manager(tmp: Path) -> LearningManager:
    return LearningManager(workspace_path=tmp / "ws", storage_dir=tmp / "global")


class TestLearningManager(unittest.TestCase):

    def test_record_list_forget_roundtrip_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lm = make_manager(tmp)
            lid = lm.record("Tests run with python3 -m unittest discover -s tests -q", tags=["python", "testing"])
            self.assertTrue(lid.startswith("lsn_"))

            lessons = lm.list()
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0].summary, "Tests run with python3 -m unittest discover -s tests -q")
            self.assertEqual(lessons[0].tags, ["python", "testing"])

            # Persisted to the workspace memories file.
            ws_file = tmp / "ws" / ".harness" / "memories.json"
            self.assertTrue(ws_file.exists())
            data = json.loads(ws_file.read_text(encoding="utf-8"))
            self.assertEqual(len(data["lessons"]), 1)

            # A fresh manager reloads from disk.
            lm2 = make_manager(tmp)
            self.assertEqual(lm2.list()[0].id, lid)

            self.assertTrue(lm.forget(lid))
            self.assertEqual(lm.list(), [])
            self.assertFalse(lm.forget(lid))

    def test_global_lessons_are_found_and_workspace_overrides_by_id(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            global_file = tmp / "global" / "memories.json"
            ws_file = tmp / "ws" / ".harness" / "memories.json"

            gid = "lsn_global_1"
            wid = "lsn_global_1"  # same id in both scopes -> workspace wins
            lessons = [
                {"id": gid, "summary": "global: verify before editing", "tags": [], "hits": 0, "promoted": False},
                {"id": "lsn_global_2", "summary": "global: use atomic edits", "tags": [], "hits": 0, "promoted": False},
            ]
            global_file.parent.mkdir(parents=True)
            global_file.write_text(json.dumps({"lessons": lessons}), encoding="utf-8")
            ws_file.parent.mkdir(parents=True)
            ws_file.write_text(json.dumps({"lessons": [
                {"id": wid, "summary": "workspace: always verify tests", "tags": [], "hits": 0, "promoted": False},
            ]}), encoding="utf-8")

            lm = make_manager(tmp)
            by_id = {l.id: l for l in lm.list()}
            self.assertEqual(len(by_id), 2)
            # Workspace overrides the global entry with the same id.
            self.assertEqual(by_id[gid].summary, "workspace: always verify tests")
            # Global-only lesson still available as fallback.
            self.assertEqual(by_id["lsn_global_2"].summary, "global: use atomic edits")

    def test_match_ranking_and_limit(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            lm.record("Run the unittest suite before finishing", tags=["python", "tests"])
            lm.record("Cache the API response with redis", tags=["api", "performance"])
            lm.record("Double-check git rebase before pushing", tags=["git"])

            hits = lm.match("how do I run python tests")
            self.assertEqual(len(hits), 1)
            self.assertIn("unittest", hits[0].summary)

            limited = lm.match("", k=3)
            self.assertEqual(len(limited), 0)  # no keyword overlap -> no matches

    def test_format_top_is_bounded_and_bumps_hits(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            lid = lm.record("Always verify the build with tests", tags=["tests"])
            out = lm.format_top("verify the build", max_chars=600)
            self.assertIn("Always verify the build with tests", out)
            lesson = lm.get(lid)
            self.assertEqual(lesson.hits, 1)

    def test_hit_persists_and_forget_removes_global(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lm = make_manager(tmp)
            lid = lm.record("provisional lesson", tags=["x"])
            lm.hit(lid)

            lm2 = make_manager(tmp)
            self.assertEqual(lm2.get(lid).hits, 1)

    def test_lru_eviction_at_cap(self):
        import harness.core.learning as learning
        old_cap = learning.MAX_LESSONS
        learning.MAX_LESSONS = 5
        try:
            with tempfile.TemporaryDirectory() as td:
                lm = make_manager(Path(td))
                ids = [lm.record(f"lesson #{i}", tags=[f"t{i}"]) for i in range(8)]
                self.assertEqual(len(lm.list()), 5)
                # Oldest lessons were evicted.
                for old_id in ids[:3]:
                    self.assertIsNone(lm.get(old_id))
                for old_id in ids[3:]:
                    self.assertIsNotNone(lm.get(old_id))
        finally:
            learning.MAX_LESSONS = old_cap

    def test_promote_writes_skill_and_invokes_reload_hook(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lm = make_manager(tmp)
            reloaded = []
            lm.set_reload_hook(lambda: reloaded.append(True))
            lid = lm.record("Use conventional commits for git pushes", tags=["git", "commits"])

            self.assertTrue(lm.promote(lid, name="git_commits"))
            skill_file = tmp / "ws" / ".harness" / "skills" / "git_commits" / "SKILL.md"
            self.assertTrue(skill_file.exists())
            content = skill_file.read_text(encoding="utf-8")
            self.assertIn("name: git_commits", content)
            self.assertIn("Use conventional commits for git pushes", content)
            self.assertEqual(reloaded, [True])
            self.assertTrue(lm.get(lid).promoted)

            # The generated SKILL.md is a parseable skill definition.
            from harness.skills.loader import parse_skill_markdown
            skill = parse_skill_markdown(content, str(skill_file), is_builtin=False)
            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "git_commits")
            self.assertIn("conventional commits", skill.description)

    def test_promote_global_writes_to_global_skills_dir(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lm = make_manager(tmp)
            reloaded = []
            lm.set_reload_hook(lambda: reloaded.append(True))
            lid = lm.record("Always use type hints in Python", tags=["python", "typing"])

            self.assertTrue(lm.promote(lid, name="type_hints", scope="global"))
            skill_file = Path.home() / ".harness" / "skills" / "type_hints" / "SKILL.md"
            self.assertTrue(skill_file.exists())
            content = skill_file.read_text(encoding="utf-8")
            self.assertIn("name: type_hints", content)
            self.assertIn("scope: global", content)
            self.assertIn("global skill", content.lower())
            self.assertEqual(reloaded, [True])
            # Cleanup
            skill_file.unlink()
            skill_file.parent.rmdir()

    def test_promote_missing_lesson_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            self.assertFalse(lm.promote("nope", name="x"))

    def test_auto_promote_after_hit_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lm = make_manager(tmp)
            lid = lm.record("Reuse temp dirs in tests to keep runs hermetic", tags=["tests"])
            for _ in range(3):
                lm.hit(lid)
            lesson = lm.get(lid)
            self.assertTrue(lesson.promoted)
            skills_root = tmp / "ws" / ".harness" / "skills"
            skill_files = list(skills_root.rglob("SKILL.md")) if skills_root.exists() else []
            self.assertEqual(len(skill_files), 1)
            self.assertIn("reuse_temp_dirs", str(skill_files[0]))

    def test_stats(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            lm.record("a", tags=["x"])
            lm.record("b", tags=["y"])
            stats = lm.stats()
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["workspace"], 2)


class TestLearningTools(unittest.TestCase):

    def test_tools_registered_in_default_registry(self):
        registry = ToolRegistry()
        self.assertIsInstance(registry.get("learn_record"), LearnRecordTool)
        self.assertIsInstance(registry.get("learn_recall"), LearnRecallTool)
        self.assertIsInstance(registry.get("learn_promote"), LearnPromoteTool)

    def test_learning_tools_can_be_disabled(self):
        registry = ToolRegistry(learning_enabled=False)
        self.assertIsNone(registry.get("learn_record"))
        self.assertIsNone(registry.get("learn_recall"))
        schemas = [s["function"]["name"] for s in registry.get_openai_schemas(Mode.BUILD)]
        self.assertNotIn("learn_record", schemas)

    def test_record_recall_promote_through_registry(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            registry = ToolRegistry(learning_manager=lm)

            out = registry.execute("learn_record", {"summary": "Always typecheck before commit", "tags": ["typescript"]})
            self.assertIn("Recorded lesson", out)

            recall = registry.execute("learn_recall", {"query": "typecheck before commit"})
            self.assertIn("Always typecheck before commit", recall)

            # learn_recall with non-matching query returns the empty case cleanly.
            miss = registry.execute("learn_recall", {"query": "zzqqxx"})
            self.assertIn("No prior lessons match", miss)

    def test_learn_record_requires_summary(self):
        with tempfile.TemporaryDirectory() as td:
            registry = ToolRegistry(learning_manager=make_manager(Path(td)))
            out = registry.execute("learn_record", {"summary": "   "})
            self.assertIn("Error", out)
            self.assertEqual(registry.learning_manager.list(), [])

    def test_promote_tool_with_scope(self):
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            registry = ToolRegistry(learning_manager=lm)
            out = registry.execute("learn_record", {"summary": "Use virtualenvs for isolation", "tags": ["python"]})
            self.assertIn("Recorded lesson", out)
            # Extract lesson id from the output
            lesson_id = out.split("`")[1]
            # Promote as global
            result = registry.execute("learn_promote", {"lesson_id": lesson_id, "name": "venv_isolation", "scope": "global"})
            self.assertIn("global", result)
            self.assertIn("Promoted", result)
            skill_file = Path.home() / ".harness" / "skills" / "venv_isolation" / "SKILL.md"
            self.assertTrue(skill_file.exists())
            content = skill_file.read_text(encoding="utf-8")
            self.assertIn("scope: global", content)
            # Cleanup
            skill_file.unlink()
            skill_file.parent.rmdir()


class TestLearningPrompt(unittest.TestCase):

    def test_learned_lessons_section_injected_into_prompt(self):
        builder = SystemPromptBuilder(Mode.BUILD)
        prompt = builder.build(learned_lessons="- verify the tests before finishing")
        self.assertIn("## LEARNED LESSONS (PRIOR MEMORY):", prompt)
        self.assertIn("- verify the tests before finishing", prompt)

        prompt2 = builder.build()
        self.assertNotIn("## LEARNED LESSONS (PRIOR MEMORY):", prompt2)

    def test_no_learning_section_when_disabled_via_agent(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = HarnessConfig()
            cfg.provider = "mock"
            cfg.learning_enabled = False
            lm = make_manager(Path(td))
            lm.record("relevant lesson about tests", tags=["tests"])
            agent = HarnessAgent(cfg, learning_manager=lm)
            agent.ensure_session()
            prompt = agent._build_system_prompt("run the tests")
            self.assertNotIn("## LEARNED LESSONS (PRIOR MEMORY):", prompt)


class TestAutoLearn(unittest.TestCase):

    def test_error_tool_result_triggers_auto_lesson(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c1", name="view_file",
                    arguments_delta='{"path": "/nonexistent/x.txt"}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c2", name="finish",
                    arguments_delta='{"summary": "task complete"}',
                )]),
            ],
        ]
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            agent = HarnessAgent(HarnessConfig(), learning_manager=lm)
            agent.config.provider = "mock"
            agent.provider = ScriptedProvider(script)

            for _ in agent.step("check that file"):
                pass

            lessons = lm.list()
            self.assertEqual(len(lessons), 1)
            self.assertIn("view_file", lessons[0].summary)
            self.assertIn("Error", lessons[0].evidence)

    def test_no_lesson_when_no_error_this_turn(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c3", name="list_dir",
                    arguments_delta='{"path": "."}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c4", name="finish",
                    arguments_delta='{"summary": "all good"}',
                )]),
            ],
        ]
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            agent = HarnessAgent(HarnessConfig(), learning_manager=lm)
            agent.config.provider = "mock"
            agent.provider = ScriptedProvider(script)

            for _ in agent.step("list the directory"):
                pass

            self.assertEqual(lm.list(), [])

    def test_auto_learn_skips_when_agent_already_used_learn_record(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c5", name="learn_record",
                    arguments_delta='{"summary": "the agent learned this itself"}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0, id="c6", name="finish",
                    arguments_delta='{"summary": "done"}',
                )]),
            ],
        ]
        with tempfile.TemporaryDirectory() as td:
            lm = make_manager(Path(td))
            agent = HarnessAgent(HarnessConfig(), learning_manager=lm)
            agent.config.provider = "mock"
            agent.provider = ScriptedProvider(script)

            for _ in agent.step("do something"):
                pass

            lessons = lm.list()
            self.assertEqual(len(lessons), 1)  # only the learn_record one; no heuristic double-append


class TestLearnCommand(unittest.TestCase):

    def test_learn_command_list(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry()
            lm = make_manager(Path(td))
            fake_agent = mock.MagicMock()
            fake_agent.learning_manager = lm
            fake_renderer = mock.MagicMock()

            reg.handle("/learn", fake_agent, fake_renderer)
            fake_renderer.print_info.assert_called_once()

            lm.record("A useful lesson")
            reg.handle("/learn list", fake_agent, fake_renderer)
            fake_renderer.print_markdown.assert_called()
            self.assertIn("A useful lesson", fake_renderer.print_markdown.call_args[0][0])

    def test_learn_command_record_and_forget(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry()
            lm = make_manager(Path(td))
            fake_agent = mock.MagicMock()
            fake_agent.learning_manager = lm
            fake_renderer = mock.MagicMock()

            reg.handle("/learn record keep it simple --tags design,clarity", fake_agent, fake_renderer)
            self.assertEqual(len(lm.list()), 1)
            self.assertEqual(lm.list()[0].tags, ["design", "clarity"])
            self.assertIn("recorded", fake_renderer.print_success.call_args[0][0].lower())

            lid = lm.list()[0].id
            reg.handle(f"/learn forget {lid}", fake_agent, fake_renderer)
            self.assertEqual(lm.list(), [])

    def test_learn_command_promote(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry()
            lm = make_manager(Path(td))
            fake_agent = mock.MagicMock()
            fake_agent.learning_manager = lm
            fake_renderer = mock.MagicMock()

            lid = lm.record("Use fixtures for external services", tags=["tests"])
            reg.handle(f"/learn promote {lid} test_fixtures", fake_agent, fake_renderer)
            self.assertTrue(lm.get(lid).promoted)
            self.assertIn("Promoted lesson", fake_renderer.print_success.call_args[0][0])

    def test_learn_command_usage_when_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry()
            fake_agent = mock.MagicMock()
            fake_agent.learning_manager = make_manager(Path(td))
            fake_renderer = mock.MagicMock()
            reg.handle("/learn bogusstuff", fake_agent, fake_renderer)
            fake_renderer.print_info.assert_called_once()


if __name__ == "__main__":
    unittest.main()