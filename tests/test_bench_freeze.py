"""凍結の記録（harness/bench_freeze.py）：--freeze が freeze.json を書き、--check が今の指紋と照らす。
置き場・リポジトリは一時ディレクトリに作った偽物。本物のオラクルの置き場と claude の CLI は使わない。
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import bench_freeze  # noqa: E402
import bench_score  # noqa: E402

TASKS = [f"B{n:02d}" for n in range(1, 11)]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Freeze(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bench-freeze-test-")
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.oracles, self.repo = base / "oracles", base / "repo"
        for t in TASKS:
            write(self.oracles / "bench" / t / "hidden" / "test_hidden_a.py", f"# {t}\n")
            self.pin(t)
        write(self.repo / "docs" / "bench" / "practice" / "README.md", "練習\n")
        write(self.repo / "README.md", "木\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        for patch in (mock.patch.object(bench_freeze, "cli_version", return_value="9.9.9 (Claude Code)"),
                      mock.patch.object(bench_freeze, "model_id", return_value="claude-test-1")):
            patch.start()
            self.addCleanup(patch.stop)

    def fingerprint(self, task):
        return bench_score.fingerprint(self.oracles / "bench" / task / "hidden")

    def pin(self, task, passed=True, floor_fingerprint=None, sha=None):
        fp = self.fingerprint(task)
        write(self.repo / "docs" / "bench" / task / "oracle.sha256", (sha or fp) + "\n")
        write(self.repo / "bench" / "records" / task / "floor.json",
              json.dumps({"task": task, "fingerprint": floor_fingerprint or fp, "reference_rate": 1.0,
                          "empty_rate": 0.0, "passed": passed}))

    def run_cli(self, mode, oracles=None):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            code = bench_freeze.main([mode, "--oracles", str(oracles or self.oracles), "--repo", str(self.repo)], err=err)
        return code, err.getvalue()

    def freeze_file(self):
        return self.repo / "bench" / "records" / "freeze.json"

    def test_freeze_writes_record_and_check_passes(self):
        code, err = self.run_cli("--freeze")
        self.assertEqual(code, 0, err)
        rec = json.loads(self.freeze_file().read_text(encoding="utf-8"))
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        self.assertEqual(rec["base"], head)
        self.assertEqual(rec["tasks"], {t: self.fingerprint(t) for t in TASKS})
        self.assertEqual(rec["cli_version"], "9.9.9 (Claude Code)")
        self.assertEqual(rec["model"], "claude-test-1")
        self.assertEqual(rec["implementer_cli_version"], "1.2.14")
        self.assertEqual(rec["implementer_model"], "gemini-3.8-flash-medium")
        self.assertTrue(rec["frozen_at"])
        self.assertEqual(self.run_cli("--check")[0], 0)

    def test_floor_not_passed_blocks_freeze(self):
        self.pin("B03", passed=False)
        code, err = self.run_cli("--freeze")
        self.assertEqual(code, 1)
        self.assertIn("B03", err)
        self.assertFalse(self.freeze_file().exists())

    def test_fingerprint_differing_from_oracle_sha_blocks_freeze(self):
        self.pin("B07", sha="0" * 64)
        code, err = self.run_cli("--freeze")
        self.assertEqual(code, 1)
        self.assertIn("B07", err)
        self.assertNotIn("B06", err)
        self.assertFalse(self.freeze_file().exists())

    def test_fingerprint_differing_from_floor_blocks_freeze(self):
        self.pin("B02", floor_fingerprint="1" * 64)
        code, err = self.run_cli("--freeze")
        self.assertEqual(code, 1)
        self.assertIn("B02", err)
        self.assertFalse(self.freeze_file().exists())

    def test_check_after_oracle_change_names_the_task(self):
        self.assertEqual(self.run_cli("--freeze")[0], 0)
        write(self.oracles / "bench" / "B05" / "hidden" / "test_hidden_a.py", "# changed\n")
        code, err = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertIn("B05", err)

    def test_check_without_freeze_json_is_one(self):
        self.assertEqual(self.run_cli("--check")[0], 1)

    def test_missing_oracles_is_two(self):
        missing = self.oracles.parent / "nowhere"
        self.assertEqual(self.run_cli("--check", oracles=missing)[0], 2)
        self.assertEqual(self.run_cli("--freeze", oracles=missing)[0], 2)
        self.assertFalse(self.freeze_file().exists())

    def test_cli_failure_is_two_and_writes_nothing(self):
        with mock.patch.object(bench_freeze, "cli_version", side_effect=bench_freeze.Env("claude --version が 1")):
            code, _ = self.run_cli("--freeze")
        self.assertEqual(code, 2)
        self.assertFalse(self.freeze_file().exists())


if __name__ == "__main__":
    unittest.main()
