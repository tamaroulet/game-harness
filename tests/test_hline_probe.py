"""report.md の「## probe」節の検査：4 項目・取得失敗の表示・gh の 10 分キャッシュ・行数と固定文言。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_probe as hp  # noqa: E402

PROGRESS = "active_task_id: H0-1\ntasks: []\n"
CI = json.dumps([{"workflowName": "CI", "headSha": "abcdef1234567", "conclusion": "success", "status": "completed"}])


class Runner:
    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    def __call__(self, args, cwd, ttl, label):
        self.calls.append(args)
        if args[0] in self.fail:
            return 1, "", "boom: 理由"
        if args[0] == "gh":
            return 0, CI, ""
        if args[1] == "show":
            return 0, PROGRESS, ""
        return 0, f"abc1234 件名 {args[-1]}\n", ""

    def gh_calls(self):
        return [c for c in self.calls if c[0] == "gh"]


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache = self.tmp / "probe_ci.json"
        self.cfg = {"base": "origin/main", "integration_branch": "hline/integration", "out": str(self.tmp),
                    "ttl_seconds": {"git": 5}}

    def make(self, runner, clock=1_000_000.0):
        return hp.section(self.cfg, runner=runner, clock=clock, cache=self.cache)

    def test_four_items_each_with_value_source_and_time(self):
        text = self.make(Runner())
        self.assertTrue(text.startswith("## probe\n"))
        for s in ("origin/main の HEAD: abc1234 件名 origin/main", "統合ブランチの HEAD: abc1234 件名 origin/hline/integration",
                  "CI／対象 abcdef1／結論 success", "MuJoCo ", "Python ", "active_task_id: H0-1"):
            self.assertIn(s, text)
        body = text.splitlines()[1:]
        self.assertEqual(len(body), 5)
        for line in body:
            self.assertIn("出典:", line)
            self.assertIn("取得:", line)

    def test_a_failing_git_marks_only_those_items(self):
        text = self.make(Runner(fail=("git",)))
        failed = [x for x in text.splitlines() if "取得失敗：" in x]
        self.assertEqual(len(failed), 3)   # 2 つの HEAD と active_task_id
        self.assertIn("結論 success", text)

    def test_a_failing_gh_marks_only_the_ci_item(self):
        text = self.make(Runner(fail=("gh",)))
        failed = [x for x in text.splitlines() if "取得失敗：" in x]
        self.assertEqual(len(failed), 1)
        self.assertIn("main の最新の CI", failed[0])
        self.assertIn("H0-1", text)

    def test_a_raising_runner_does_not_raise(self):
        def boom(*a):
            raise OSError("no git")

        text = hp.safe_section(self.cfg, runner=boom, clock=5.0, cache=self.cache)
        self.assertIn("取得失敗：", text)
        self.assertIn("## probe", text)

    def test_gh_is_called_once_within_ten_minutes(self):
        r = Runner()
        first = self.make(r, 1_000_000.0)
        second = self.make(r, 1_000_000.0 + hp.CI_TTL - 1)
        self.assertEqual(len(r.gh_calls()), 1)
        ci = lambda t: next(x for x in t.splitlines() if "CI" in x and "gh run list" in x)   # noqa: E731
        self.assertEqual(ci(first), ci(second))   # 前回の値と取得時刻
        self.make(r, 1_000_000.0 + hp.CI_TTL + 1)
        self.assertEqual(len(r.gh_calls()), 2)

    def test_section_is_short_and_has_no_fixed_text(self):
        text = self.make(Runner())
        self.assertLessEqual(len(text.splitlines()), 12)
        for fixed in ("Role", "Forbidden", "Ground Truth", "1,344", "JIT OBJECTIVE", "CODEBASE STRUCTURE"):
            self.assertNotIn(fixed, text)


if __name__ == "__main__":
    unittest.main()
