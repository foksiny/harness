"""
Unit tests for Harness tools.
"""
import base64
import json
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

    def test_run_command_delivers_full_output_without_truncation(self):
        # Default behavior caps runaway output at the context-budget limit (16k
        # chars) with a transparent marker; the agent can recover the full output
        # by passing max_chars.
        pm = PermissionManager(PermissionLevel.FULL)
        cmd_tool = RunCommandTool(pm)
        res = cmd_tool.execute(
            "for i in $(seq 1 5000); do printf 'L%05d_abcdefghij\\n' $i; done"
        )
        self.assertIn("L00001_abcdefghij", res)
        # Default cap kicked in — head preserved, tail elided with a marker.
        self.assertIn("output truncated by context budget", res)
        self.assertNotIn("L05000_abcdefghij", res)

        # Explicit max_chars recovers the full output (agent opt-in).
        full = cmd_tool.execute(
            "for i in $(seq 1 5000); do printf 'L%05d_abcdefghij\\n' $i; done",
            max_chars=10 ** 7,
        )
        self.assertGreater(len(full), 20000)
        self.assertIn("L00001_abcdefghij", full)
        self.assertIn("L05000_abcdefghij", full)

    def test_run_command_short_output_untouched(self):
        pm = PermissionManager(PermissionLevel.FULL)
        cmd_tool = RunCommandTool(pm)
        res = cmd_tool.execute("echo 'short output'")
        self.assertIn("short output", res)
        self.assertNotIn("output truncated", res)

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

    def test_exa_search_engine_chain_falls_through(self):
        """A dead engine must never abort the search — the chain moves on."""
        from unittest import mock
        tool = ExaSearchTool()

        def fail(query, limit):
            raise RuntimeError("engine dead")

        def win(query, limit):
            return [{"title": "Python", "url": "https://python.org", "snippet": "Official site"}]

        # Quando the whole chain dies (offline), return a graceful no-result message.
        for attr in ("_search_ddg_html", "_search_ddg_lite", "_search_wikipedia", "_search_ddg_instant", "_search_bing"):
            setattr(tool, attr, fail)
        tool._enrich = lambda items: None
        tool._query_variants = lambda q: ["variant"]
        with mock.patch.object(tool, "_run_chain", side_effect=lambda q, l, s, c, t: None):
            out = tool.execute("python", num_results=2)
        self.assertIn("No results found", out)

        # First engine dead, second returns — execute must still succeed.
        for attr in ("_search_ddg_html", "_search_ddg_lite", "_search_ddg_instant", "_search_bing"):
            setattr(tool, attr, fail)
        tool._search_wikipedia = win
        tool._enrich = lambda items: None
        out = tool.execute("python", num_results=2)
        self.assertIn("python.org", out)
        self.assertIn("Exa Web Search Results", out)

    def test_exa_search_reformulates_on_empty_results(self):
        """Zero results triggers query reformulation instead of giving up."""
        tool = ExaSearchTool()
        calls = []
        tool._enrich = lambda items: None
        tool._query_variants = lambda q: ["fix a broken test", "broken test"]

        def record(query, limit):
            calls.append(query)
            raise RuntimeError("no network in tests")

        for attr in ("_search_ddg_html", "_search_ddg_lite", "_search_wikipedia", "_search_ddg_instant", "_search_bing"):
            setattr(tool, attr, record)
        tool.execute("how do I fix a broken test", num_results=1)
        # Round 1 ran every engine on the original query before any reformulation.
        self.assertEqual(calls[:5], ["how do I fix a broken test"] * 5)

    def test_exa_query_variants(self):
        tool = ExaSearchTool()
        v = tool._query_variants("how do I fix a broken test?")
        self.assertEqual(v[0], "fix a broken test")
        self.assertNotIn("how do I fix a broken test", v)
        w = tool._query_variants("plain compact query")
        self.assertEqual(w, ["plain compact"])

    def test_exa_parsers_extract_ddg_html(self):
        from unittest import mock
        tool = ExaSearchTool()
        fake_html = """
        <div class="result results_links_deep web-result">
          <h2 class="result__title"><a class="result__a" rel="nofollow"
              href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc&rut=1">
              Example Doc</a></h2>
          <a class="result__snippet" href="//duckduckgo.com/l/?uddg=...&rut=2">
             Figuring out <b>how</b> the API works.</a>
        </div>
        """
        with mock.patch.object(tool, "_fetch", return_value=fake_html):
            out = tool._search_ddg_html("test query", 5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Example Doc")
        self.assertEqual(out[0]["url"], "https://example.com/doc")
        self.assertIn("Figuring out how the API works", out[0]["snippet"])

    def test_exa_unpack_bing_redirect(self):
        tool = ExaSearchTool()
        target = base64.urlsafe_b64encode(b"https://docs.python.org/3/").decode()
        # Bing encodes the target as base64url with an 'a1' prefix.
        bing = f"https://www.bing.com/ck/a?a=1&amp;u=a1{target}&amp;q=python"
        self.assertEqual(tool._unpack_redirect(bing), "https://docs.python.org/3/")
        self.assertEqual(tool._unpack_redirect("https://python.org"), "https://python.org")
        self.assertEqual(tool._unpack_redirect("//example.com/x"), "https://example.com/x")
        self.assertEqual(
            tool._unpack_redirect("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc&rut=1"),
            "https://example.com/doc",
        )

    def test_exa_parsers_extract_wikipedia_and_bing(self):
        from unittest import mock
        tool = ExaSearchTool()
        wiki_json = json.dumps({
            "query": {"search": [
                {"title": "Python (programming language)",
                 "snippet": "<span class='searchmatch'>Python</span> is a high-level language."},
            ]},
        })
        with mock.patch.object(tool, "_fetch", return_value=wiki_json):
            items = tool._search_wikipedia("python", 5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Python (programming language)")
        self.assertTrue(items[0]["url"].startswith("https://en.wikipedia.org/wiki/"))

        bing_html = (
            '<li class="b_algo"><h2><a href="https://docs.python.org/3/">Python Docs</a></h2>'
            "<p>Reference documentation for Python.</p></li>"
        )
        with mock.patch.object(tool, "_fetch", return_value=bing_html):
            b = tool._search_bing("python", 5)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["title"], "Python Docs")
        self.assertEqual(b[0]["url"], "https://docs.python.org/3/")

if __name__ == "__main__":
    unittest.main()
