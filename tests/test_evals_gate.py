"""Tests that the eval suite logic holds (J2 safety regressions as evals)."""
import subprocess, sys, unittest


class TestEvalsGate(unittest.TestCase):
    def test_evals_pass(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        proc = subprocess.run([sys.executable, "-m", "evals.run"], capture_output=True, text=True, timeout=120, cwd=str(root))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
