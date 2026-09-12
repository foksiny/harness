"""
Tests for Python Execution Tool and AST Safety Analysis.
"""
import unittest
from harness.tools.python_exec import ExecutePythonTool, analyze_python_safety
from harness.core.permissions import PermissionManager, PermissionLevel, RiskLevel

class TestPythonSafety(unittest.TestCase):

    def test_safe_ast_analysis(self):
        code = "a = 10\nb = 20\nprint(f'Sum: {a + b}')"
        risk, flags = analyze_python_safety(code)
        self.assertEqual(risk, RiskLevel.SAFE)
        self.assertEqual(len(flags), 0)

    def test_critical_os_system_flag(self):
        code = "import os\nos.system('echo dangerous')"
        risk, flags = analyze_python_safety(code)
        self.assertEqual(risk, RiskLevel.CRITICAL)
        self.assertTrue(any("os.system" in f for f in flags))

    def test_subprocess_flag(self):
        code = "import subprocess\nsubprocess.run(['ls', '-la'])"
        risk, flags = analyze_python_safety(code)
        self.assertEqual(risk, RiskLevel.HIGH)

    def test_eval_exec_flag(self):
        code = "user_input = '1+1'\nres = eval(user_input)"
        risk, flags = analyze_python_safety(code)
        self.assertEqual(risk, RiskLevel.HIGH)

    def test_safe_execution(self):
        pm = PermissionManager(PermissionLevel.FULL)
        tool = ExecutePythonTool(pm)
        code = "for i in range(3):\n    print(f'Count: {i}')"
        out = tool.execute(code)
        self.assertIn("Exit code 0", out)
        self.assertIn("Count: 0", out)
        self.assertIn("Count: 2", out)

    def test_secure_mode_interlock(self):
        # Callback that rejects execution
        def reject_callback(msg, details):
            return False

        pm = PermissionManager(PermissionLevel.SECURE, approver_callback=reject_callback)
        tool = ExecutePythonTool(pm)
        code = "print('Blocked in secure without approval')"
        out = tool.execute(code)
        self.assertIn("rejected", out.lower())

if __name__ == "__main__":
    unittest.main()
