"""
To-Do task tracking system for Harness.
Provides structured task planning, progress tracking, and HUD reporting.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
import time

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class TaskItem:
    id: int
    title: str
    status: TaskStatus = TaskStatus.PENDING
    notes: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskItem":
        data = data.copy()
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TaskStatus(data["status"])
        return cls(**data)

class TodoManager:
    """Manages active task lists for agent planning and execution."""

    def __init__(self):
        self.tasks: List[TaskItem] = []
        self._next_id: int = 1

    def add_task(self, title: str, notes: Optional[str] = None) -> TaskItem:
        task = TaskItem(id=self._next_id, title=title.strip(), notes=notes)
        self._next_id += 1
        self.tasks.append(task)
        return task

    def update_task(self, task_id: int, status: TaskStatus, notes: Optional[str] = None) -> Optional[TaskItem]:
        for task in self.tasks:
            if task.id == task_id:
                task.status = status
                task.updated_at = time.time()
                if notes:
                    task.notes = notes
                return task
        return None

    def get_task(self, task_id: int) -> Optional[TaskItem]:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def list_tasks(self) -> List[TaskItem]:
        return list(self.tasks)

    def clear(self) -> None:
        self.tasks.clear()
        self._next_id = 1

    def summary(self) -> str:
        """One-line summary for status bar."""
        if not self.tasks:
            return "No tasks"
        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        in_prog = sum(1 for t in self.tasks if t.status == TaskStatus.IN_PROGRESS)
        total = len(self.tasks)
        pct = int((completed / total) * 100) if total > 0 else 0
        return f"{completed}/{total} done ({pct}%)" + (f" | ⚙️ {in_prog} running" if in_prog > 0 else "")

    def format_markdown(self) -> str:
        """Formatted markdown task list for prompts and inspection."""
        if not self.tasks:
            return "No active tasks recorded."
        lines = ["### Active Tasks:"]
        for t in self.tasks:
            icon = {
                TaskStatus.PENDING: "[ ]",
                TaskStatus.IN_PROGRESS: "[>]",
                TaskStatus.COMPLETED: "[x]",
                TaskStatus.FAILED: "[!]",
                TaskStatus.CANCELLED: "[-]",
            }.get(t.status, "[ ]")
            line = f"- {icon} #{t.id}: {t.title} ({t.status.value})"
            if t.notes:
                line += f" — *{t.notes}*"
            lines.append(line)
        return "\n".join(lines)

    def to_list(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.tasks]

    def load_list(self, data: List[Dict[str, Any]]) -> None:
        self.tasks = [TaskItem.from_dict(d) for d in data]
        if self.tasks:
            self._next_id = max(t.id for t in self.tasks) + 1
        else:
            self._next_id = 1
