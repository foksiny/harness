"""
Tests for the /ultra-goal command: mission brief assembly, ULTRA-GOAL system
prompt injection, and command wiring (queue + direct step paths).

The requirements interview is performed by the AGENT (via ask_user) inside its
turn, so the harness command itself never blocks on structured questioning.
"""
import unittest
from unittest import mock

from harness.commands.registry import CommandRegistry
from harness.commands.ultra_goal import build_ultra_goal_brief
from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.core.prompt import (
    SystemPromptBuilder,
    ULTRA_GOAL_MARKER,
    ULTRA_GOAL_PROTOCOL,
)
from harness.tui.terminal import TerminalRenderer
from harness.tui.queue import ExecutionQueue


class TestUltraGoal(unittest.TestCase):

    def setUp(self):
        self.registry = CommandRegistry()
        self.renderer = TerminalRenderer("cyberpunk")

    # ── Registration ────────────────────────────────────────────────

    def test_command_registered(self):
        self.assertIn("ultra-goal", self.registry.commands)
        self.assertIn("ultragoal", self.registry.commands)

    # ── Mission brief ───────────────────────────────────────────────

    def test_brief_includes_marker_goal_and_mission(self):
        brief = build_ultra_goal_brief("generate me the BEST minecraft clone")
        self.assertTrue(
            brief.startswith(f"{ULTRA_GOAL_MARKER} generate me the BEST minecraft clone")
        )
        self.assertIn("interview", brief.lower())
        self.assertIn("spawn_swarm", brief)
        self.assertIn("ULTRA-GOAL PROTOCOL", brief)
        self.assertIn("LAUNCH", brief.upper())

    def test_brief_contains_no_hardcoded_requirements_block(self):
        # Requirement gathering belongs to the agent's ask_user interview,
        # never a pre-baked harness form.
        brief = build_ultra_goal_brief("make a snake game")
        self.assertNotIn("## PRE-FLIGHT REQUIREMENTS", brief)

    # ── System prompt injection ─────────────────────────────────────

    def test_prompt_builder_appends_protocol_only_when_ultra_goal(self):
        builder = SystemPromptBuilder(mode=Mode.SUPER)
        full = builder.build(ultra_goal=True)
        self.assertIn("ULTRA-GOAL PROTOCOL", full)
        self.assertIn("FINISH CONTRACT", full.upper())
        self.assertIn("YOU are the one who asks", full)
        regular = SystemPromptBuilder(mode=Mode.SUPER).build(ultra_goal=False)
        self.assertNotIn("ULTRA-GOAL PROTOCOL", regular)
        default_prompt = SystemPromptBuilder(mode=Mode.BUILD).build()
        self.assertNotIn("ULTRA-GOAL PROTOCOL", default_prompt)

    def test_agent_detects_ultra_goal_marker_in_query(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        ultra = agent._build_system_prompt(f"{ULTRA_GOAL_MARKER} build me a full app")
        self.assertIn(ULTRA_GOAL_PROTOCOL, ultra)
        plain = agent._build_system_prompt("build me a full app")
        self.assertNotIn(ULTRA_GOAL_PROTOCOL, plain)

    # ── Command wiring ──────────────────────────────────────────────

    def _mock_agent(self):
        return mock.Mock()

    def test_command_requires_goal(self):
        agent = self._mock_agent()
        model = mock.Mock()
        model.cfg = HarnessConfig()
        agent.config = model.cfg
        q = ExecutionQueue()
        self.registry.handle("/ultra-goal", agent, self.renderer, queue=q)
        agent.set_mode.assert_not_called()
        self.assertEqual(q.size(), 0)

    def test_command_kicks_off_mission_without_harness_interview(self):
        # The whole point: the harness must NOT run its own question modal.
        agent = self._mock_agent()
        agent.config = HarnessConfig()
        q = ExecutionQueue()
        self.registry.handle("/ultra-goal build a metroidvania", agent, self.renderer, queue=q)
        agent.set_mode.assert_called_once_with(Mode.SUPER)
        self.assertEqual(q.size(), 1)
        item = q.peek()
        self.assertEqual(item.mode, "super")
        self.assertTrue(item.prompt.startswith(ULTRA_GOAL_MARKER))
        # The agent is tasked with doing the asking itself.
        self.assertIn("interview", item.prompt.lower())

    def test_command_accepts_ultragoal_alias(self):
        agent = self._mock_agent()
        agent.config = HarnessConfig()
        q = ExecutionQueue()
        self.registry.handle("/ultragoal make a quiz app", agent, self.renderer, queue=q)
        self.assertEqual(q.size(), 1)
        self.assertTrue(q.peek().prompt.startswith(ULTRA_GOAL_MARKER))

    def test_command_steps_directly_when_no_queue(self):
        agent = self._mock_agent()
        agent.config = HarnessConfig()
        captured = {}

        def fake_step(prompt):
            captured["prompt"] = prompt
            return iter([])

        agent.step = fake_step
        self.registry.handle("/ultra-goal make a game", agent, self.renderer, queue=None)
        self.assertTrue(captured["prompt"].startswith(ULTRA_GOAL_MARKER))


if __name__ == "__main__":
    unittest.main()