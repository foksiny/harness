"""
Tool Registry and Execution Dispatcher for Harness.
"""
from typing import Dict, Any, List, Optional
from harness.tools.base import Tool
from harness.tools.filesystem import ViewFileTool, EditFileTool, WriteFileTool, ListDirTool, FindFilesTool, DeleteFileTool
from harness.tools.search import GrepSearchTool
from harness.tools.execution import RunCommandTool
from harness.tools.python_exec import ExecutePythonTool
from harness.tools.web_search import ExaSearchTool
from harness.tools.questions import AskUserTool
from harness.tools.todo_tools import TodoCreateTool, TodoUpdateTool, TodoListTool
from harness.tools.subagent_tools import SpawnSubagentTool
from harness.tools.swarm_tools import SpawnSwarmTool, SwarmSendMessageTool, SwarmReadMessagesTool
from harness.tools.git_tools import GitStatusTool, GitDiffTool
from harness.tools.skill_tools import ListSkillsTool, ReadSkillTool
from harness.tools.finish import FinishTool
from harness.tools.learning_tools import LearnRecordTool, LearnRecallTool, LearnPromoteTool
from harness.tools.mesh_tools import MeshStatusTool, MeshListPeersTool, MeshSendMessageTool, MeshBroadcastTool, MeshReadMessagesTool
from harness.skills.loader import SkillsManager
from harness.core.modes import Mode, is_tool_allowed_in_mode
from harness.core.permissions import PermissionManager
from harness.core.todo import TodoManager
from harness.core.subagents import SubagentOrchestrator
from harness.core.learning import LearningManager

# Common shell command words. When a model emits one of these (or anything with
# shell metacharacters) as a *tool name*, the call is malformed — the registry
# steers it back to `run_command` instead of a bare "not found" error.
_COMMON_SHELL_COMMANDS = frozenset({
    "ls", "cat", "cd", "pwd", "echo", "find", "grep", "rg", "head", "tail",
    "wc", "sort", "uniq", "sed", "awk", "curl", "wget", "mkdir", "rm",
    "cp", "mv", "touch", "chmod", "which", "whoami", "python", "python3",
    "pip", "node", "npm", "git", "docker", "make", "tar", "zip", "less",
    "diff", "tee", "xargs", "du", "df", "ps", "kill", "killall", "pkill",
    "export", "for", "if", "while", "sudo", "apt", "nano", "vim", "env",
})

# Deterministic suggestions for the near-miss tool names models most often
# emit (difflib alone ranks 'edit_file' closer to 'read_file' than 'view_file').
_TOOL_NAME_ALIASES = {
    "read_file": "view_file", "read": "view_file", "view": "view_file",
    "open_file": "view_file", "cat": "view_file", "show_file": "view_file",
    "write": "write_file", "create_file": "write_file", "save_file": "write_file",
    "new_file": "write_file",
    "edit": "edit_file", "modify_file": "edit_file", "patch_file": "edit_file",
    "search": "grep_search", "grep": "grep_search", "find_in_files": "grep_search",
    "find": "find_files", "find_file": "find_files", "ls": "list_dir",
    "list": "list_dir", "list_directory": "list_dir",
    "run": "run_command", "bash": "run_command", "shell": "run_command",
    "sh": "run_command", "exec": "run_command", "execute": "run_command",
    "command": "run_command",
    "todo": "todo_list", "tasks": "todo_list", "list_tasks": "todo_list",
    "python": "execute_python", "eval": "execute_python", "py": "execute_python",
}

class ToolRegistry:
    """Manages available tools, schema serialization, and safe invocation."""

    def __init__(
        self,
        permission_manager: Optional[PermissionManager] = None,
        todo_manager: Optional[TodoManager] = None,
        subagent_orchestrator: Optional[SubagentOrchestrator] = None,
        skills_manager: Optional[SkillsManager] = None,
        ask_user_handler: Optional[Any] = None,
        learning_manager: Optional[LearningManager] = None,
        learning_enabled: bool = True,
        browser_enabled: bool = True,
    ):
        self.permission_manager = permission_manager or PermissionManager()
        self.todo_manager = todo_manager or TodoManager()
        self.subagent_orchestrator = subagent_orchestrator or SubagentOrchestrator()
        self.skills_manager = skills_manager or SkillsManager()
        self.learning_manager = learning_manager or LearningManager()
        self.learning_enabled = learning_enabled
        self.browser_enabled = browser_enabled
        self.tools: Dict[str, Tool] = {}
        self.browser_manager = None
        self._register_default_tools(ask_user_handler)

    def _register_default_tools(self, ask_user_handler: Optional[Any] = None):
        # Filesystem
        self.register(ViewFileTool())
        self.register(EditFileTool())
        self.register(WriteFileTool())
        self.register(ListDirTool())
        self.register(FindFilesTool())
        self.register(DeleteFileTool())

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

        # Agent swarms (concurrent coordinated subagents)
        self.register(SpawnSwarmTool(self.subagent_orchestrator))
        self.register(SwarmSendMessageTool(self.subagent_orchestrator))
        self.register(SwarmReadMessagesTool(self.subagent_orchestrator))

        # Git
        self.register(GitStatusTool())
        self.register(GitDiffTool())

        # Skills
        self.register(ListSkillsTool(self.skills_manager))
        self.register(ReadSkillTool(self.skills_manager))

        # Task control
        self.register(FinishTool())

        # Learning / self-improvement (lazy: manager loads memory from disk on first use)
        if self.learning_enabled:
            self.register(LearnRecordTool(self.learning_manager))
            self.register(LearnRecallTool(self.learning_manager))
            self.register(LearnPromoteTool(self.learning_manager))

        # API server (no inter-instance mesh) — keep stubs for backward compat
        try:
            self.register(MeshStatusTool())
            self.register(MeshListPeersTool())
            self.register(MeshSendMessageTool())
            self.register(MeshBroadcastTool())
            self.register(MeshReadMessagesTool())
        except Exception:
            pass

        # Web control (Chrome via CDP). All browser_* tools share one
        # BrowserManager so the launched session persists across tool calls.
        if self.browser_enabled:
            try:
                from harness.browser.manager import BrowserManager
                from harness.tools.browser import register_browser_tools
                self.browser_manager = BrowserManager()
                register_browser_tools(
                    self,
                    controller_factory=self.browser_manager.controller_factory,
                    permission_manager=self.permission_manager,
                )
            except Exception:
                self.browser_manager = None

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
            # Give the model an actionable recovery path instead of a bare
            # "not found": models occasionally emit the whole shell command
            # (or a near-miss tool name) as the tool name and then burn many
            # steps rediscovering what went wrong.
            import difflib
            import json as _json
            hints = []
            stripped = (name or "").strip()
            first_word = stripped.split(" ", 1)[0] if stripped else ""
            looks_like_command = (
                " " in stripped
                or any(c in stripped for c in ("/", ";", "|", "&", ">", "<"))
                or first_word in _COMMON_SHELL_COMMANDS
            )
            if looks_like_command:
                hints.append(
                    f"It looks like a shell command was passed as the tool name. "
                    f"To run it, call the `run_command` tool with arguments like "
                    f"{{\"command\": {_json.dumps(stripped)}}}"
                )
            close = _TOOL_NAME_ALIASES.get(stripped) or (
                difflib.get_close_matches(stripped, list(self.tools.keys()), n=1, cutoff=0.6) or [None]
            )[0]
            if close and close != name:
                hints.append(f"Did you mean '{close}'?")
            hint = (" " + " ".join(hints)) if hints else ""
            return (
                f"Error: Tool '{name}' not found in registry.{hint} "
                f"Available tools: {', '.join(sorted(self.tools.keys()))}"
            )

        try:
            res = tool.execute(**arguments)
            return str(res)
        except TypeError as te:
            return f"Error executing tool '{name}': Invalid arguments provided ({te})."
        except Exception as ex:
            return f"Error executing tool '{name}': {str(ex)}"
