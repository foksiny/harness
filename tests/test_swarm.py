"""
Tests for Agent Swarm support: message bus, concurrent orchestration,
swarm tools, mode propagation, and worker messaging.
"""
import threading
import time
import unittest
from unittest import mock

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.modes import Mode
from harness.core.permissions import PermissionManager, PermissionLevel
from harness.core.subagents import SubagentOrchestrator, SwarmMessageBus
from harness.core.prompt import SystemPromptBuilder
from harness.providers.mock_provider import MockProvider
from harness.providers.base import LLMChunk, ToolCallDelta
from harness.tools import ToolRegistry
from harness.tools.swarm_tools import SpawnSwarmTool, SwarmSendMessageTool, SwarmReadMessagesTool
from harness.tools.finish import FinishTool
from harness.tui.input_handler import SLASH_COMMANDS, SENTINEL_OPEN_AGENTS, SENTINEL_BACK
from harness.commands.registry import CommandRegistry


class ScriptedProvider(MockProvider):
    """Mock provider that replays a per-call script, then emits a plain stop reply."""

    def __init__(self, script):
        super().__init__(responses=[])
        self.script = list(script)
        self.call_count = 0

    def stream_chat(self, messages, model=None, thinking_effort="high", tools=None, system_prompt=None, **kwargs):
        self.call_history.append({
            "messages": list(messages),
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        })
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.script):
            for chunk in self.script[idx]:
                yield chunk
        else:
            yield LLMChunk(delta_text="done.", finish_reason="stop")


class TestSwarmMessageBus(unittest.TestCase):

    def test_post_and_read_ordering(self):
        bus = SwarmMessageBus()
        bus.post("main", "all", "hello")
        bus.post("researcher_1", "coder_2", "handoff data")

        msgs = bus.read()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].id, 1)
        self.assertEqual(msgs[0].sender, "main")
        self.assertEqual(msgs[1].sender, "researcher_1")

        later = bus.read(since_index=1)
        self.assertEqual([m.sender for m in later], ["researcher_1"])

    def test_sender_and_recipient_filters(self):
        bus = SwarmMessageBus()
        bus.post("researcher_1", "coder_2", "a")
        bus.post("researcher_2", "all", "b")
        bus.post("researcher_1", "all", "c")

        by_sender = bus.read(sender="researcher_1")
        self.assertEqual([m.body for m in by_sender], ["a", "c"])

        by_recipient = bus.read(recipient="coder_2")
        # Broadcasts to "all" are visible to the filtered recipient too.
        self.assertEqual([m.body for m in by_recipient], ["a", "b", "c"])

        # Broadcasts ("all") are visible to everyone on an unfiltered read.
        self.assertEqual(len(bus.read()), 3)

    def test_concurrent_posting_is_safe_and_contiguous(self):
        bus = SwarmMessageBus()

        def worker(n):
            for i in range(20):
                bus.post(f"w{n}", "all", f"m{n}-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(bus.length, 60)
        ids = [m.id for m in bus.read()]
        self.assertEqual(sorted(ids), list(range(1, 61)))


class TestSwarmOrchestrator(unittest.TestCase):

    def test_does_not_add_messaging_tools_to_sequential_spawn(self):
        calls = []
        kw_calls = []

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            calls.append(allowed_tools)
            kw_calls.append(kwargs)
            return "ok", 1

        orch = SubagentOrchestrator(mock_runner)
        orch.spawn("researcher", "explore")
        self.assertNotIn("swarm_send_message", calls[0])
        self.assertNotIn("swarm_read_messages", calls[0])
        # Spawned agents get a stable agent id for TUI record tracking.
        self.assertEqual(kw_calls[0]["agent_id"], "researcher_1")
        self.assertEqual(kw_calls[0]["agent_type"], "researcher")

    def test_launch_swarm_runs_workers_concurrently_with_swarm_tools(self):
        calls = []
        timestamps = []
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            with lock:
                calls.append((prompt, allowed_tools, kwargs.get("provider_factory")))
            barrier.wait(timeout=5)
            timestamps.append(time.time())
            time.sleep(0.02)
            return f"Done {prompt}", 2

        orch = SubagentOrchestrator(mock_runner)
        orch.set_swarm_runtime(
            provider_factory=lambda: object(),
            model="test-model",
            parent_mode=Mode.BUILD,
        )
        result = orch.launch_swarm([
            {"agent_type": "researcher", "task": "search auth"},
            {"agent_type": "coder", "task": "implement"},
            {"agent_type": "tester", "task": "write tests"},
        ])

        self.assertEqual(len(result.workers), 3)
        self.assertTrue(all(w.status == "completed" for w in result.workers))

        # All three were running at the same time (barrier-gated).
        self.assertLess(max(timestamps) - min(timestamps), 0.5)

        for prompt, allowed_tools, provider_factory in calls:
            self.assertIn("swarm_send_message", allowed_tools)
            self.assertIn("swarm_read_messages", allowed_tools)
            self.assertIn("finish", allowed_tools)
            self.assertIsNotNone(provider_factory)

    def test_workers_get_unique_agent_ids_and_stats_kwargs(self):
        seen_senders = []

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            orch.post_message("all", f"update from {prompt}")
            seen_senders.append(kwargs)
            return "ok", 1

        orch = SubagentOrchestrator(mock_runner)
        orch.set_swarm_runtime(
            provider_factory=lambda: object(),
            model="test-model",
            parent_mode=Mode.PLAN,
        )
        result = orch.launch_swarm([
            {"agent_type": "researcher", "task": "t1"},
            {"agent_type": "coder", "task": "t2", "agent_id": "mycoder"},
        ])

        self.assertEqual(sorted({m.sender for m in result.messages}), ["mycoder", "researcher_1"])
        for kw in seen_senders:
            self.assertEqual(kw["model"], "test-model")
            self.assertEqual(kw["mode"], Mode.PLAN)
            self.assertIsNotNone(kw["provider_factory"])

    def test_report_includes_worker_output_and_mailbox_transcript(self):
        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            orch.post_message("all", "partial finding")
            return "Researched everything.", 3

        orch = SubagentOrchestrator(mock_runner)
        result = orch.launch_swarm([{"agent_type": "researcher", "task": "investigate"}])

        report = result.format_report()
        self.assertIn("Researched everything.", report)
        self.assertIn("SWARM MAILBOX TRANSCRIPT", report)
        self.assertIn("partial finding", report)


class TestSwarmTools(unittest.TestCase):

    def test_messaging_tools_work_for_main_agent_fallback_bus(self):
        orch = SubagentOrchestrator()
        send = SwarmSendMessageTool(orch)
        read = SwarmReadMessagesTool(orch)

        out = send.execute(recipient="coder_2", message="main thread note")
        self.assertIn("posted", out)

        # Main thread (no TLS ctx) falls back to the orchestrator-wide bus.
        msgs = orch.bus.read()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].sender, "main")
        self.assertEqual(msgs[0].recipient, "coder_2")

        listing = read.execute()
        self.assertIn("main thread note", listing)
        self.assertIn("latest message id: 1", listing)

    def test_tools_registered_in_default_registry(self):
        registry = ToolRegistry()
        self.assertIsInstance(registry.get("spawn_swarm"), SpawnSwarmTool)
        self.assertIsInstance(registry.get("swarm_send_message"), SwarmSendMessageTool)
        self.assertIsInstance(registry.get("swarm_read_messages"), SwarmReadMessagesTool)
        schemas = registry.get_openai_schemas(Mode.BUILD)
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("spawn_swarm", names)
        self.assertIn("swarm_send_message", names)
        self.assertIn("swarm_read_messages", names)

    def test_spawn_swarm_tool_executes_through_registry(self):
        calls = []

        def mock_runner(sys_prompt, prompt, allowed_tools, max_turns, **kwargs):
            calls.append(prompt)
            return f"Output for {prompt}", 1

        orch = SubagentOrchestrator(mock_runner)
        tool = SpawnSwarmTool(orch)
        res = tool.execute(agents=[
            {"agent_type": "researcher", "task": "explore"},
            {"agent_type": "tester", "task": "verify"},
        ])
        self.assertTrue(res.startswith("=== SWARM EXECUTION REPORT ==="))
        for prompt in ("explore", "verify"):
            self.assertIn(prompt, calls)
            self.assertIn(f"Output for {prompt}", res)


class TestSwarmAgentIntegration(unittest.TestCase):

    def _make_agent(self, script):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.subagent_orchestrator.provider_factory = lambda: provider
        return agent, provider

    def test_swarm_worker_sends_reads_and_finishes(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_swarm_1",
                    name="swarm_send_message",
                    arguments_delta='{"recipient": "all", "message": "hello from worker"}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_swarm_2",
                    name="swarm_read_messages",
                    arguments_delta='{"since_index": 0}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_swarm_3",
                    name="finish",
                    arguments_delta='{"summary": "worker done"}',
                )]),
            ],
        ]
        agent, provider = self._make_agent(script)
        result = agent.subagent_orchestrator.launch_swarm([
            {"agent_type": "tester", "task": "verify the feature"},
        ])

        self.assertEqual(result.workers[0].status, "completed")
        # The report includes the agent's final reply plus an action log so the
        # parent always sees what the worker actually did (even terse replies).
        self.assertTrue(result.workers[0].output.strip().startswith("worker done"))
        self.assertIn("[Actions performed by this agent]", result.workers[0].output)
        self.assertIn("swarm_send_message", result.workers[0].output)
        self.assertEqual(len(result.messages), 1)
        self.assertEqual(result.messages[0].sender, "tester_1")
        self.assertEqual(result.messages[0].body, "hello from worker")
        self.assertEqual(provider.call_count, 3)

    def test_plan_mode_restrictions_flow_into_worker_tools(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_edit_1",
                    name="edit_file",
                    arguments_delta='{"path": "x.py", "old_string": "a", "new_string": "b"}',
                )]),
            ],
        ]
        agent, provider = self._make_agent(script)
        agent.mode = Mode.PLAN
        agent.subagent_orchestrator.parent_mode = Mode.PLAN

        executed_modes = {}
        orig_execute = agent.tool_registry.execute
        def spy_execute(name, args, mode):
            executed_modes[name] = mode
            return orig_execute(name, args, mode)
        agent.tool_registry.execute = spy_execute

        result = agent.subagent_orchestrator.launch_swarm([
            {"agent_type": "coder", "task": "attempt edit"},
        ])

        self.assertEqual(executed_modes["edit_file"], Mode.PLAN)
        self.assertEqual(result.workers[0].status, "completed")


class TestSwarmActivityTracking(unittest.TestCase):
    """Recording of worker activity: drainable events, durable records, action logs."""

    def test_command_registry_agents_and_back_return_view_sentinels(self):
        reg = CommandRegistry()
        fake_agent = mock.MagicMock()
        fake_agent.subagent_orchestrator.list_records.return_value = []
        fake_renderer = mock.MagicMock()

        result_agents = reg.handle("/agents", fake_agent, fake_renderer)
        # The registry hands control of the view back to the interactive loop
        # (which renders the board at the top of the next iteration).
        self.assertEqual(result_agents, "agents")
        fake_renderer.print_subagent_board.assert_not_called()

        result_back = reg.handle("/back", fake_agent, fake_renderer)
        self.assertEqual(result_back, "parent")

        # Unknown commands still render an error and report handled.
        result_bad = reg.handle("/nope", fake_agent, fake_renderer)
        self.assertTrue(result_bad)
        fake_renderer.print_error.assert_called()

    def test_input_handler_sentinel_contract(self):
        # Keybind sentinels must differ from any plausible typed input.
        self.assertNotEqual(SENTINEL_OPEN_AGENTS, SENTINEL_BACK)
        self.assertIn("/agents", SLASH_COMMANDS)
        self.assertIn("/agent", SLASH_COMMANDS)
        self.assertIn("/back", SLASH_COMMANDS)

    def test_worker_activity_appears_in_drained_events_and_records(self):
        script = [
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_msg",
                    name="swarm_send_message",
                    arguments_delta='{"recipient": "all", "message": "boot note"}',
                )]),
            ],
            [
                LLMChunk(tool_calls=[ToolCallDelta(
                    index=0,
                    id="call_fin",
                    name="finish",
                    arguments_delta='{"summary": "all set"}',
                )]),
            ],
        ]
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.subagent_orchestrator.provider_factory = lambda: provider

        result = agent.subagent_orchestrator.launch_swarm([
            {"agent_type": "coder", "task": "wire up"},
        ])

        # Events streamed to the caller (TUI) are drained after orchestration.
        events = agent.subagent_orchestrator.drain_events()
        types = [t for t, _ in events]
        self.assertIn("subagent_start", types)
        self.assertIn("subagent_message", types)
        self.assertIn("subagent_end", types)

        # A second drain is empty: events are consumed once.
        self.assertEqual(agent.subagent_orchestrator.drain_events(), [])

        # Durable record holds the worker's activity for the board/detail views.
        records = agent.subagent_orchestrator.list_records()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.agent_type, "coder")
        self.assertEqual(rec.status, "completed")
        self.assertEqual(len(rec.messages), 1)
        self.assertEqual(rec.messages[0]["body"], "boot note")
        self.assertEqual(rec.last_tool(), "swarm_send_message")
        self.assertEqual(len(rec.tool_calls), 1)

    def test_worker_report_includes_action_log_for_terse_replies(self):
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
                    arguments_delta='{"summary": "done."}',
                )]),
            ],
        ]
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        provider = ScriptedProvider(script)
        agent.subagent_orchestrator.provider_factory = lambda: provider

        result = agent.subagent_orchestrator.launch_swarm([
            {"agent_type": "researcher", "task": "check the file"},
        ])
        output = result.workers[0].output
        self.assertTrue(output.startswith("done."))
        self.assertIn("[Actions performed by this agent]", output)
        self.assertIn("view_file", output)


class TestSwarmPrompt(unittest.TestCase):

    def test_swarm_section_present_only_when_enabled(self):
        builder = SystemPromptBuilder(Mode.BUILD)
        with_swarm = builder.build(swarm_enabled=True)
        without = builder.build(swarm_enabled=False)

        self.assertIn("## SWARM / DELEGATION PROTOCOL", with_swarm)
        self.assertIn("spawn_swarm", with_swarm)
        self.assertIn("swarm_send_message", with_swarm)
        self.assertNotIn("## SWARM / DELEGATION PROTOCOL", without)

    def test_base_prompt_contains_delegation_guidance(self):
        builder = SystemPromptBuilder(Mode.BUILD)
        prompt = builder.build()
        self.assertIn("Delegate whenever a subtask is parallelizable", prompt)
        self.assertIn("do NOT delegate trivial single-step work".lower(), prompt.lower())


class TestPermissionDenyFlag(unittest.TestCase):

    def test_interactive_deny_auto_rejects_approval(self):
        pm = PermissionManager(PermissionLevel.SECURE)
        self.assertTrue(pm.check_permission("read_file", {}))
        pm.interactive_deny = True
        # Secure mode writes require approval -> auto-denied while workers run.
        self.assertFalse(pm.check_permission("write_file", {"path": "x"}))


if __name__ == "__main__":
    unittest.main()