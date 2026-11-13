"""子プロセスの起動（harness/proc.py）：時間切れでも、そこまでの標準出力を捨てない。

    python -m unittest tests.test_proc -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import proc  # noqa: E402


class TimeoutKeepsOutput(unittest.TestCase):
    def test_partial_stdout_survives_the_ttl(self):
        code = "import sys, time; print('STEP_DONE', flush=True); time.sleep(30)"
        rc, out, err = proc.run([sys.executable, "-c", code], ROOT, 3, "t")
        self.assertEqual(rc, 124)
        self.assertIn("STEP_DONE", out)
        self.assertIn("TTL超過", err)


if __name__ == "__main__":
    unittest.main()
