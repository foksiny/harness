"""
To-Do management tools for Harness.
Allows the model to organize, update, and inspect tasks in real time.
"""
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.todo import TodoManager, TaskStatus
from harness.core.checkpoints import get_checkpoint_manager

class TodoCreateTool(Tool):
    name = "todo_create"
    description = "Add a new task item to the active project task list."
    action_type = "todo"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Concise task description."},
            "notes": {"type": "string", "description": "Additional context or acceptance criteria (optional)."},
        },
        "required": ["title"],
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, title: str, notes: Optional[str] = None, **kwargs) -> str:
        item = self.todo_manager.add_task(title, notes)
        
        # Record state change for checkpoint
        cp_manager = get_checkpoint_manager()
        cp_manager.record_state_change(
            f"todo.{item.id}", 
            None, 
            item.to_dict(), 
            "create"
        )
        
        return f"Created task #{item.id}: '{item.title}' (Status: {item.status.value})"

class TodoUpdateTool(Tool):
    name = "todo_update"
    description = "Update the status or notes of an existing task item."
    action_type = "todo"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "ID of the task to update."},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "failed", "cancelled"],
                "description": "New status for the task.",
            },
            "notes": {"type": "string", "description": "Updated notes or outcome summary (optional)."},
        },
        "required": ["task_id", "status"],
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, task_id: int, status: str, notes: Optional[str] = None, **kwargs) -> str:
        try:
            st = TaskStatus(status.lower().strip())
        except ValueError:
            return f"Error: Invalid task status '{status}'. Must be one of: pending, in_progress, completed, failed, cancelled."

        # Get old task for checkpoint
        old_task = self.todo_manager.get_task(task_id)
        old_value = old_task.to_dict() if old_task else None
        
        updated = self.todo_manager.update_task(task_id, st, notes)
        if not updated:
            return f"Error: Task #{task_id} not found."
        
        # Record state change for checkpoint
        cp_manager = get_checkpoint_manager()
        cp_manager.record_state_change(
            f"todo.{task_id}",
            old_value,
            updated.to_dict(),
            "update"
        )
        
        return f"Updated task #{updated.id}: '{updated.title}' -> {updated.status.value}"

class TodoListTool(Tool):
    name = "todo_list"
    description = "List all current project tasks and their execution states."
    action_type = "todo"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, **kwargs) -> str:
        return self.todo_manager.format_markdown()
