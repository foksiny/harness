"""
Thread-safe Execution Queue for Harness TUI.
Enables queueing user prompts and background tasks while the agent is busy.
"""
from dataclasses import dataclass, field
import threading
import time
from typing import List, Optional, Dict, Any


@dataclass
class QueuedItem:
    """Represents a queued prompt or task in the execution queue."""
    id: int
    prompt: str
    enqueued_at: float = field(default_factory=time.time)
    status: str = "queued"  # "queued", "running", "completed", "cancelled", "failed"
    mode: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def duration(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return end - self.started_at

    @property
    def wait_time(self) -> float:
        end = self.started_at or time.time()
        return end - self.enqueued_at


class ExecutionQueue:
    """Thread-safe FIFO queue for agent prompts and tasks."""

    def __init__(self):
        self._lock = threading.RLock()
        self._items: List[QueuedItem] = []
        self._history: List[QueuedItem] = []
        self._counter = 0
        self._current_item: Optional[QueuedItem] = None
        self._paused = False

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def current_item(self) -> Optional[QueuedItem]:
        with self._lock:
            return self._current_item

    def set_current_item(self, item: Optional[QueuedItem]) -> None:
        with self._lock:
            self._current_item = item

    def enqueue(self, prompt: str, mode: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> QueuedItem:
        """Add a prompt to the execution queue."""
        with self._lock:
            self._counter += 1
            item = QueuedItem(
                id=self._counter,
                prompt=prompt.strip(),
                mode=mode,
                metadata=metadata or {},
            )
            self._items.append(item)
            return item

    def dequeue(self) -> Optional[QueuedItem]:
        """Pop the next queued item if queue is not paused."""
        with self._lock:
            if self._paused or not self._items:
                return None
            item = self._items.pop(0)
            item.status = "running"
            item.started_at = time.time()
            self._current_item = item
            return item

    def peek(self) -> Optional[QueuedItem]:
        """Look at the next queued item without removing it."""
        with self._lock:
            if not self._items:
                return None
            return self._items[0]

    def remove(self, item_id: int) -> Optional[QueuedItem]:
        """Remove an item from the queue by its ID."""
        with self._lock:
            for idx, it in enumerate(self._items):
                if it.id == item_id:
                    removed = self._items.pop(idx)
                    removed.status = "cancelled"
                    removed.finished_at = time.time()
                    self._history.append(removed)
                    return removed
            return None

    def clear(self) -> int:
        """Remove all queued items, returning count removed."""
        with self._lock:
            count = len(self._items)
            now = time.time()
            for it in self._items:
                it.status = "cancelled"
                it.finished_at = now
                self._history.append(it)
            self._items.clear()
            return count

    def pause(self) -> None:
        """Pause auto-processing of new items from the queue."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Resume auto-processing of items from the queue."""
        with self._lock:
            self._paused = False

    def finish_current(self, status: str = "completed") -> Optional[QueuedItem]:
        """Mark the currently running item as finished and add to history."""
        with self._lock:
            item = self._current_item
            if item:
                item.status = status
                item.finished_at = time.time()
                self._history.append(item)
                if len(self._history) > 50:
                    self._history.pop(0)
                self._current_item = None
            return item

    def list_pending(self) -> List[QueuedItem]:
        """Return a copy of all pending queued items."""
        with self._lock:
            return list(self._items)

    def list_history(self, limit: int = 10) -> List[QueuedItem]:
        """Return recently completed/cancelled items."""
        with self._lock:
            return list(self._history[-limit:])

    def size(self) -> int:
        """Number of items waiting in the queue."""
        with self._lock:
            return len(self._items)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._items) == 0
