"""
Tool Registry and Execution Dispatcher for Harness.
"""
from typing import Dict, Any, List, Optional
from harness.tools.base import Tool
from harness.tools.filesystem import ViewFileTool, EditFileTool, WriteFileTool, ListDirTool, FindFilesTool
from harness.tools.search import GrepSearchTool
from harness.tools.execution import RunCommandTool
from harness.tools.python_exec import ExecutePythonTool
from harness.tools.web_search import ExaSearchTool
from harness.tools.questions import AskUserTool
from harness.tools.todo_tools import TodoCreateTool, TodoUpdateTool, TodoListTool
from harness.tools.subagent_tools import SpawnSubagentTool
from harness.tools.git_tools import GitStatusTool, GitDiffTool
from harness.tools.skill_tools import ListSkillsTool, ReadSkillTool
from harness.tools.finish import FinishTool
from harness.skills.loader import SkillsManager
from harness.core.modes import Mode, is_tool_allowed_in_mode
from harness.core.permissions import PermissionManager
from harness.core.todo import TodoManager
from harness.core.subagents import SubagentOrchestrator

class ToolRegistry:
    """Manages available tools, schema serialization, and safe invocation."""

    def __init__(
        self,
        permission_manager: Optional[PermissionManager] = None,
        todo_manager: Optional[TodoManager] = None,
        subagent_orchestrator: Optional[SubagentOrchestrator] = None,
        skills_manager: Optional[SkillsManager] = None,
        ask_user_handler: Optional[Any] = None,
    ):
        self.permission_manager = permission_manager or PermissionManager()
        self.todo_manager = todo_manager or TodoManager()
        self.subagent_orchestrator = subagent_orchestrator or SubagentOrchestrator()
        self.skills_manager = skills_manager or SkillsManager()
        self.tools: Dict[str, Tool] = {}
        self._register_default_tools(ask_user_handler)

    def _register_default_tools(self, ask_user_handler: Optional[Any] = None):
        # Filesystem
        self.register(ViewFileTool())
        self.register(EditFileTool())
        self.register(WriteFileTool())
        self.register(ListDirTool())
        self.register(FindFilesTool())

        # Search
        self.register(GrepSearchTool())

        # Execution
        self.register(RunCommandTool(self.permission_manager))
        self.register(ExecutePythonTool(self.permission_manager))

        # Web & Asking
        self.register(ExaSearchTool())
        self.register(AskUserTool(ask_user_handler))

        # Tasks
        self.register(TodoCreateTool(self.todo_manager))
        self.register(TodoUpdateTool(self.todo_manager))
        self.register(TodoListTool(self.todo_manager))

        # Subagents
        self.register(SpawnSubagentTool(self.subagent_orchestrator))

        # Git
        self.register(GitStatusTool())
        self.register(GitDiffTool())

        # Skills
        self.register(ListSkillsTool(self.skills_manager))
        self.register(ReadSkillTool(self.skills_manager))

        # Task control
        self.register(FinishTool())

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self.tools.get(name)

    def list_tools(self) -> List[Tool]:
        return list(self.tools.values())

    def get_openai_schemas(self, mode: Mode = Mode.BUILD) -> List[Dict[str, Any]]:
        schemas = []
        for t in self.tools.values():
            if is_tool_allowed_in_mode(t.name, mode):
                schemas.append(t.to_openai_schema())
        return schemas

    def get_anthropic_schemas(self, mode: Mode = Mode.BUILD) -> List[Dict[str, Any]]:
        schemas = []
        for t in self.tools.values():
            if is_tool_allowed_in_mode(t.name, mode):
                schemas.append(t.to_anthropic_schema())
        return schemas

    def get_gemini_schemas(self, mode: Mode = Mode.BUILD) -> List[Dict[str, Any]]:
        schemas = []
        for t in self.tools.values():
            if is_tool_allowed_in_mode(t.name, mode):
                schemas.append(t.to_gemini_schema())
        return schemas

    def execute(self, name: str, arguments: Dict[str, Any], mode: Mode = Mode.BUILD) -> str:
        """Execute a tool by name with arguments and mode check."""
        if not is_tool_allowed_in_mode(name, mode):
            return f"Error: Tool '{name}' is forbidden in {mode.value.upper()} mode to prevent unintended state mutations."

        tool = self.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found in registry."

        try:
            res = tool.execute(**arguments)
            return str(res)
        except TypeError as te:
            return f"Error executing tool '{name}': Invalid arguments provided ({te})."
        except Exception as ex:
            return f"Error executing tool '{name}': {str(ex)}"
