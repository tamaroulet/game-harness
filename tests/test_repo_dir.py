"""道具（pipeline / decompose / audit）が --repo-dir の worktree で動くこと（docs/design/spec_pipeline.md §13 の 2）。

    python -m unittest discover -s tests -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import audit  # noqa: E402
import decompose  # noqa: E402
import pipeline  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
                       cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


class RepoDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wt = self.tmp / "wt"
        self.wt.mkdir()
        git(self.wt, "init", "-q", "-b", "main")
        (self.wt / "README.md").write_text("x", encoding="utf-8")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "init")

    def test_pipeline_uses_the_given_repo_dir(self):
        """単位定義を worktree からの相対で解決し、リポジトリの HEAD を worktree から読む。"""
        unit = self.wt / "tools" / "units" / "u.json"
        unit.parent.mkdir(parents=True)
        # テストのパスを whitelist に入れた単位は require_unit_safe で止まる（リポジトリには触らない）
        unit.write_text(json.dumps({"id": "u", "whitelist": ["tests/Core.Tests/XTests.cs"],
                                    "acceptance": {"required_tests": ["X"]}}), encoding="utf-8")
        tel = self.tmp / "tel.json"
        argv = ["pipeline.py", "--project", "unity-2d", "--unit", "tools/units/u.json",
                "--repo-dir", str(self.wt), "--telemetry", str(tel)]
        with mock.patch("builtins.print"), mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
            pipeline.main()
        t = json.loads(tel.read_text(encoding="utf-8"))
        self.assertEqual(t["repo_head"], git(self.wt, "rev-parse", "HEAD"))
        self.assertEqual(t["unit_id"], "u")

    def test_decompose_writes_under_the_given_repo_dir(self):
        decompose.configure("unity-2d", repo_dir=str(self.wt))
        self.assertEqual(decompose.ROOT, self.wt)

    def test_audit_reads_and_writes_under_the_given_repo_dir(self):
        seen = {}

        def fake_audit(path, verdict_json=None):
            seen["root"] = audit.ROOT
            return 0
        with mock.patch.object(audit, "cmd_audit", side_effect=fake_audit), \
                mock.patch.object(sys, "argv", ["audit.py", "--project", "unity-2d", "--file", "x.md",
                                                "--repo-dir", str(self.wt)]):
            audit.main()
        self.assertEqual(seen["root"], self.wt)


if __name__ == "__main__":
    unittest.main()
