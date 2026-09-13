"""
Tests for the Checkpoint System: creation, undo/redo navigation with
actual materialization of message/todo/file changes, and id-based navigation.
"""
import unittest
from types import SimpleNamespace

from harness.config import HarnessConfig
from harness.core.agent import HarnessAgent
from harness.core.checkpoints import CheckpointManager
from harness.core.todo import TaskStatus


class TestCheckpointManager(unittest.TestCase):
    """Manager-level tests for undo/redo and navigation."""

    def test_undo_redo_applies_message_changes(self):
        cp = CheckpointManager()
        msgs = []

        def apply_message(mc, undo):
            if mc.action == "append":
                if undo:
                    if 0 <= mc.index < len(msgs) and msgs[mc.index] == mc.new_message:
                        del msgs[mc.index]
                        return True
                    return False
                msgs.insert(min(mc.index, len(msgs)), mc.new_message)
                return True
            return False

        cp.set_applicators(
            apply_file_change=lambda fc, undo: True,
            apply_message_change=apply_message,
            apply_state_change=lambda sc, undo: True,
        )

        # Turn 1
        cp.record_message_append(0, {"role": "user", "content": "q1"})
        msgs.append({"role": "user", "content": "q1"})
        cp.create_checkpoint("Turn 1 start")
        # Turn 2
        cp.record_message_append(1, {"role": "assistant", "content": "a1"})
        msgs.append({"role": "assistant", "content": "a1"})
        cp.create_checkpoint("Turn 2 start")

        self.assertEqual(cp.current_index, 1)
        self.assertFalse(cp.can_redo())
        self.assertTrue(cp.can_undo())

        # Plain redo at the newest checkpoint -> nothing to redo (correct).
        self.assertIsNone(cp.redo())

        # Undo applies the reversal of the current checkpoint's changes.
        target = cp.undo()
        self.assertEqual(target.label, "Turn 1 start")
        self.assertEqual([m["content"] for m in msgs], ["q1"])

        # Redo re-applies them.
        target = cp.redo()
        self.assertEqual(target.label, "Turn 2 start")
        self.assertEqual([m["content"] for m in msgs], ["q1", "a1"])

        # Undo to the base state removes the first checkpoint's changes too.
        first = cp.undo()
        self.assertIsNotNone(first)
        second = cp.undo()
        self.assertIsNone(second)  # initial state
        self.assertEqual(msgs, [])

    def test_navigate_to_supports_jumping_back_from_latest(self):
        cp = CheckpointManager()
        msgs = []

        def apply_message(mc, undo):
            if mc.action == "append":
                if undo:
                    if 0 <= mc.index < len(msgs) and msgs[mc.index] == mc.new_message:
                        del msgs[mc.index]
                        return True
                    return False
                msgs.insert(min(mc.index, len(msgs)), mc.new_message)
                return True
            return False

        cp.set_applicators(
            apply_file_change=lambda fc, undo: True,
            apply_message_change=apply_message,
            apply_state_change=lambda sc, undo: True,
        )
        for turn, (role, text) in enumerate([("user", "q1"), ("assistant", "a1"),
                                             ("user", "q2"), ("assistant", "a2")]):
            cp.record_message_append(turn, {"role": role, "content": text})
            msgs.append({"role": role, "content": text})
            cp.create_checkpoint(f"Turn {turn + 1} start")

        ids = [c.id for c in cp.checkpoints]
        # At the latest checkpoint, navigate back to an earlier id (the case
        # that previously reported "Nothing to redo").
        target, moved = cp.navigate_to(ids[1])
        self.assertTrue(moved)
        self.assertEqual(target.label, "Turn 2 start")
        self.assertEqual(cp.current_index, 1)
        self.assertEqual([m["content"] for m in msgs], ["q1", "a1"])

        # Re-navigating to the same id reports no movement.
        target, moved = cp.navigate_to(ids[1])
        self.assertFalse(moved)
        self.assertEqual(target.label, "Turn 2 start")

        # And forward navigation re-applies the dropped changes.
        target, moved = cp.navigate_to(ids[3])
        self.assertTrue(moved)
        self.assertEqual(cp.current_index, 3)
        self.assertEqual([m["content"] for m in msgs], ["q1", "a1", "q2", "a2"])

        # Unknown ids resolve to (None, False).
        self.assertEqual(cp.navigate_to("nope"), (None, False))

    def test_truncate_is_atomic_across_undo_redo(self):
        cp = CheckpointManager()
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(5)]

        def apply_message(mc, undo):
            if mc.action == "truncate":
                if undo:
                    idx = min(mc.index, len(msgs))
                    msgs[idx:idx] = list(mc.removed_messages)
                    return True
                if mc.index + mc.count > len(msgs):
                    return False
                del msgs[mc.index:mc.index + mc.count]
                return True
            return False

        cp.set_applicators(
            apply_file_change=lambda fc, undo: True,
            apply_message_change=apply_message,
            apply_state_change=lambda sc, undo: True,
        )

        # Record a compaction that already happened: m0..m2 were removed.
        removed = msgs[:3]
        del msgs[:3]
        cp.record_message_truncate(0, 3, removed)
        cp.create_checkpoint("Compacted")
        self.assertEqual(len(cp.checkpoints[0].message_changes), 1)

        # Undo restores the compacted messages.
        cp.undo()
        self.assertEqual([m["content"] for m in msgs], [f"m{i}" for i in range(5)])
        # Redo truncates them again.
        cp.redo()
        self.assertEqual([m["content"] for m in msgs], ["m3", "m4"])


class TestCheckpointAgentMaterialization(unittest.TestCase):
    """Agent-level tests wiring undo/redo to the live session and todos."""

    def _make_agent(self):
        cfg = HarnessConfig()
        cfg.provider = "mock"
        agent = HarnessAgent(cfg)
        agent.session = SimpleNamespace(messages=[])
        return agent

    def test_agent_undo_redo_materializes_session_messages(self):
        agent = self._make_agent()
        cp = agent.checkpoint_manager

        cp.record_message_append(0, {"role": "user", "content": "hello"})
        agent.session.messages.append({"role": "user", "content": "hello"})
        cp.create_checkpoint("Turn 1 start")

        cp.record_message_append(1, {"role": "assistant", "content": "hi there"})
        agent.session.messages.append({"role": "assistant", "content": "hi there"})
        cp.create_checkpoint("Turn 2 start")

        cp.undo()
        self.assertEqual([m["content"] for m in agent.session.messages], ["hello"])
        cp.redo()
        self.assertEqual([m["content"] for m in agent.session.messages], ["hello", "hi there"])

    def test_agent_span_replace_undo_redo_restores_compacted_history(self):
        agent = self._make_agent()
        cp = agent.checkpoint_manager

        original = [
            {"role": "user", "content": "old goal"},
            {"role": "assistant", "content": "old work"},
        ]
        agent.session.messages = list(original)
        cp.record_message_append(0, original[0])
        cp.create_checkpoint("Turn 1 start")
        cp.record_message_append(1, original[1])
        cp.create_checkpoint("Turn 2 start")

        # Simulate a graduated compaction: the whole span is swapped for a digest.
        old_msgs = list(agent.session.messages)
        new_msgs = [
            {"role": "assistant", "content": "### [CONTEXT SUMMARY: 2 messages compacted] old summary"},
            {"role": "user", "content": "mixed-length replacement"},
        ]
        cp.record_message_replace_span(0, old_msgs, new_msgs)
        agent.session.messages = list(new_msgs)
        cp.create_checkpoint("Compacted")

        # Undo restores the exact original conversations.
        cp.undo()
        self.assertEqual(agent.session.messages, original)

        # Redo re-applies the compaction span.
        cp.redo()
        self.assertEqual(agent.session.messages, new_msgs)

    def test_agent_undo_redo_materializes_todo_changes(self):
        agent = self._make_agent()
        cp = agent.checkpoint_manager

        task = agent.todo_manager.add_task("Ship feature")
        cp.record_state_change(f"todo.{task.id}", None, task.to_dict(), "create")
        cp.create_checkpoint("Created todo")

        cp.undo()
        self.assertIsNone(agent.todo_manager.get_task(task.id))

        cp.redo()
        restored = agent.todo_manager.get_task(task.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.title, "Ship feature")
        self.assertEqual(restored.status, TaskStatus.PENDING)

    def test_agent_undo_restores_updated_task(self):
        agent = self._make_agent()
        cp = agent.checkpoint_manager

        task = agent.todo_manager.add_task("Ship feature")
        old = task.to_dict()
        agent.todo_manager.update_task(task.id, TaskStatus.COMPLETED)
        new = task.to_dict()
        cp.record_state_change(f"todo.{task.id}", old, new, "update")
        cp.create_checkpoint("Updated todo")

        cp.undo()
        restored = agent.todo_manager.get_task(task.id)
        self.assertEqual(restored.status, TaskStatus.PENDING)
        cp.redo()
        self.assertEqual(agent.todo_manager.get_task(task.id).status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()