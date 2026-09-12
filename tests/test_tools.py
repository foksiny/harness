"""
Unit tests for Harness tools.
"""
import os
import tempfile
import unittest
from pathlib import Path
from harness.tools.filesystem import ViewFileTool, EditFileTool, WriteFileTool, ListDirTool, FindFilesTool
from harness.tools.search import GrepSearchTool
from harness.tools.execution import RunCommandTool
from harness.tools.questions import AskUserTool
from harness.tools.todo_tools import TodoCreateTool, TodoUpdateTool, TodoListTool
from harness.tools.web_search import ExaSearchTool
from harness.core.todo import TodoManager, TaskStatus
from harness.core.permissions import PermissionManager, PermissionLevel

class TestTools(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_and_view_file(self):
        w_tool = WriteFileTool()
        v_tool = ViewFileTool()
        target = self.dir_path / "sample.txt"

        # Write
        res = w_tool.execute(str(target), "Line 1\nLine 2\nLine 3\nLine 4")
        self.assertIn("Successfully wrote", res)
        self.assertTrue(target.exists())

        # View slice
        out = v_tool.execute(str(target), start_line=2, end_line=3)
        self.assertIn("Line 2", out)
        self.assertIn("Line 3", out)
        self.assertNotIn("Line 1", out)

    def test_edit_file_atomic(self):
        w_tool = WriteFileTool()
        e_tool = EditFileTool()
        target = self.dir_path / "code.py"

        w_tool.execute(str(target), "def hello():\n    return 'old_value'\n")

        # Edit unique string
        res = e_tool.execute(str(target), "return 'old_value'", "return 'new_value'")
        self.assertIn("Successfully updated", res)

        content = target.read_text()
        self.assertIn("return 'new_value'", content)
        self.assertNotIn("return 'old_value'", content)

        # Ambiguous match error
        w_tool.execute(str(target), "foo\nfoo\n")
        err = e_tool.execute(str(target), "foo", "bar")
        self.assertIn("appears 2 times", err)

    def test_list_dir_and_find_files(self):
        w_tool = WriteFileTool()
        l_tool = ListDirTool()
        f_tool = FindFilesTool()

        w_tool.execute(str(self.dir_path / "a.txt"), "hello")
        w_tool.execute(str(self.dir_path / "b.py"), "import os")

        # List
        list_res = l_tool.execute(str(self.dir_path))
        self.assertIn("a.txt", list_res)
        self.assertIn("b.py", list_res)

        # Find
        find_res = f_tool.execute("*.py", str(self.dir_path))
        self.assertIn("b.py", find_res)

    def test_run_command(self):
        pm = PermissionManager(PermissionLevel.FULL)
        cmd_tool = RunCommandTool(pm)
        res = cmd_tool.execute("echo 'Harness Testing'")
        self.assertIn("Harness Testing", res)
        self.assertIn("code 0", res)

    def test_todo_lifecycle(self):
        mgr = TodoManager()
        c_tool = TodoCreateTool(mgr)
        u_tool = TodoUpdateTool(mgr)
        l_tool = TodoListTool(mgr)

        c_tool.execute("Task Alpha", "high priority")
        self.assertEqual(len(mgr.tasks), 1)

        u_tool.execute(1, "completed", "done early")
        self.assertEqual(mgr.tasks[0].status, TaskStatus.COMPLETED)

        summary = l_tool.execute()
        self.assertIn("[x] #1: Task Alpha", summary)

    def test_ask_user_mock_handler(self):
        def mock_handler(q, opts, allow_custom, rec):
            return f"Answered: {opts[0]}"

        tool = AskUserTool(mock_handler)
        res = tool.execute("Which architecture?", options=["Microservices", "Monolith"])
        self.assertEqual(res, "Answered: Microservices")

    def test_exa_search_interface(self):
        tool = ExaSearchTool()
        # Ensure method signature and parameters match
        self.assertEqual(tool.name, "exa_search")
        self.assertTrue(tool.is_read_only)
        self.assertIn("query", tool.parameters["properties"])

if __name__ == "__main__":
    unittest.main()
