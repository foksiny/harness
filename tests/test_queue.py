"""
Unit tests for the TUI ExecutionQueue and QueuedItem data structures.
"""
import time
import threading
import unittest
from harness.tui.queue import ExecutionQueue, QueuedItem


class TestExecutionQueue(unittest.TestCase):
    def setUp(self):
        self.queue = ExecutionQueue()

    def test_enqueue_and_fifo_order(self):
        self.assertTrue(self.queue.is_empty())
        self.assertEqual(self.queue.size(), 0)

        item1 = self.queue.enqueue("First task")
        item2 = self.queue.enqueue("Second task", mode="plan")
        item3 = self.queue.enqueue("Third task")

        self.assertEqual(item1.id, 1)
        self.assertEqual(item2.id, 2)
        self.assertEqual(item3.id, 3)
        self.assertEqual(self.queue.size(), 3)
        self.assertFalse(self.queue.is_empty())

        # Dequeue should follow FIFO
        d1 = self.queue.dequeue()
        self.assertIsNotNone(d1)
        self.assertEqual(d1.id, 1)
        self.assertEqual(d1.prompt, "First task")
        self.assertEqual(d1.status, "running")
        self.assertEqual(self.queue.current_item, d1)

        d2 = self.queue.dequeue()
        self.assertIsNotNone(d2)
        self.assertEqual(d2.id, 2)
        self.assertEqual(d2.mode, "plan")

        d3 = self.queue.dequeue()
        self.assertIsNotNone(d3)
        self.assertEqual(d3.id, 3)

        # Now empty
        self.assertIsNone(self.queue.dequeue())
        self.assertEqual(self.queue.size(), 0)

    def test_peek(self):
        self.assertIsNone(self.queue.peek())
        self.queue.enqueue("Peekable item")
        peeked = self.queue.peek()
        self.assertIsNotNone(peeked)
        self.assertEqual(peeked.prompt, "Peekable item")
        # Size shouldn't change
        self.assertEqual(self.queue.size(), 1)

    def test_remove_by_id(self):
        it1 = self.queue.enqueue("Task 1")
        it2 = self.queue.enqueue("Task 2")
        it3 = self.queue.enqueue("Task 3")

        # Remove middle item
        removed = self.queue.remove(it2.id)
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, it2.id)
        self.assertEqual(removed.status, "cancelled")
        self.assertEqual(self.queue.size(), 2)

        # Remaining items
        pending = self.queue.list_pending()
        self.assertEqual([i.id for i in pending], [it1.id, it3.id])

        # Remove non-existent ID
        self.assertIsNone(self.queue.remove(999))

    def test_clear_queue(self):
        self.queue.enqueue("Task A")
        self.queue.enqueue("Task B")
        self.queue.enqueue("Task C")
        self.assertEqual(self.queue.size(), 3)

        count = self.queue.clear()
        self.assertEqual(count, 3)
        self.assertEqual(self.queue.size(), 0)
        self.assertTrue(self.queue.is_empty())

    def test_pause_and_resume(self):
        self.assertFalse(self.queue.is_paused)
        self.queue.enqueue("Paused task")

        self.queue.pause()
        self.assertTrue(self.queue.is_paused)
        # Dequeue returns None when paused
        self.assertIsNone(self.queue.dequeue())
        self.assertEqual(self.queue.size(), 1)

        self.queue.resume()
        self.assertFalse(self.queue.is_paused)
        item = self.queue.dequeue()
        self.assertIsNotNone(item)
        self.assertEqual(item.prompt, "Paused task")

    def test_finish_current_and_history(self):
        self.queue.enqueue("Tracked task")
        d = self.queue.dequeue()
        self.assertEqual(self.queue.current_item, d)

        finished = self.queue.finish_current(status="completed")
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "completed")
        self.assertIsNone(self.queue.current_item)

        history = self.queue.list_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].id, d.id)

    def test_concurrent_enqueue_dequeue(self):
        # Stress test thread safety
        total_items = 200
        dequeued_items = []

        def producer():
            for i in range(total_items):
                self.queue.enqueue(f"Prompt {i}")

        def consumer():
            while len(dequeued_items) < total_items:
                item = self.queue.dequeue()
                if item:
                    dequeued_items.append(item.id)
                else:
                    time.sleep(0.001)

        t_prod = threading.Thread(target=producer)
        t_cons = threading.Thread(target=consumer)

        t_prod.start()
        t_cons.start()

        t_prod.join(timeout=5.0)
        t_cons.join(timeout=5.0)

        self.assertEqual(len(dequeued_items), total_items)
        self.assertEqual(self.queue.size(), 0)


if __name__ == "__main__":
    unittest.main()
