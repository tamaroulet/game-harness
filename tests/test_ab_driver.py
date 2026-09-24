"""A/B 実験のドライバ・測定器・集計と、pipeline の --local-only（B4-E4）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 本走は無人で何時間も回る。入力が凍結どおりでなければ止まること、条件 A が同じ会話を
続けること、試行の予算（3 回）を守ること、条件 B が push も CI も起こさないこと、指標の計算
（P2P の破壊・トークンの伸び）が正しいことを、実装役も dotnet も使わずに確かめる。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import common, driver, measure, report  # noqa: E402
import pipeline  # noqa: E402

MANIFEST = ROOT / "experiments" / "b4_ab" / "tasks.json"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class Manifest(unittest.TestCase):
    def test_real_manifest_loads(self):
        m = common.load_manifest(MANIFEST)
        self.assertEqual(len(m["tasks"]), 5)

    def test_changed_input_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / "b4_ab"
            shutil.copytree(MANIFEST.parent, dst)
            (dst / "templates" / "retry.md").write_text("changed", encoding="utf-8")
            with self.assertRaises(common.ABError):
                common.load_manifest(dst / "tasks.json")


class Metrics(unittest.TestCase):
    CLASSES = {"B4abT1Cases": "T1", "B4abT2Cases": "T2"}

    def test_task_of_and_acceptance(self):
        results = {"G.B4abT1Cases.Case_a": "Passed", "G.B4abT1Cases.Case_b": "Failed",
                   "G.Issue12Cases.Case_c": "Passed"}
        self.assertEqual(measure.task_of("G.B4abT1Cases.Case_a", self.CLASSES), "T1")
        self.assertIsNone(measure.task_of("G.Issue12Cases.Case_c", self.CLASSES))
        self.assertEqual(measure.acceptance(results, self.CLASSES, "T1"), (1, 2))
        self.assertEqual(measure.acceptance(None, self.CLASSES, "T1"), (0, None))

    def test_p2p_counts_tests_that_passed_before_and_not_now(self):
        prev = {"G.X.Case_a", "G.X.Case_b", "G.X.Case_gone"}
        now = {"G.X.Case_a": "Passed", "G.X.Case_b": "Failed"}
        self.assertEqual(measure.p2p_broken(prev, now), ["G.X.Case_b", "G.X.Case_gone"])
        self.assertEqual(measure.p2p_broken(prev, now, superseded=["Case_b"]), ["G.X.Case_gone"])
        self.assertEqual(measure.p2p_broken(prev, None), sorted(prev), "ビルドが通らなければ全部が壊れた扱い")


class Report(unittest.TestCase):
    def test_loglog_slope(self):
        self.assertEqual(report.loglog_slope([n * n * 100 for n in range(1, 6)]), 2.0)
        self.assertEqual(report.loglog_slope([n * 100 for n in range(1, 6)]), 1.0)
        self.assertIsNone(report.loglog_slope([100, None, 300]))

    def test_summary_has_both_conditions(self):
        rows = []
        for cond, per in (("A", lambda n: 1000 * n), ("B", lambda n: 1000)):
            for n in range(1, 6):
                rows.append({"run_id": "ab-01", "condition": cond, "task": f"T{n}", "index": n, "accepted": True,
                             "attempts": 1, "p2p_broken": 0, "invariants": {"failures": 0},
                             "diff": {"added": 10, "deleted": 0}, "budget_exceeded": False,
                             "tokens": {"input": per(n)}})
        text = report.summarize(rows)
        self.assertIn("| T5 | A | 1/1 |", text)
        # A は 1000N ずつ増える（累積は N² に近い）、B は一定（累積は N に比例）
        self.assertRegex(text, r"\| A \| 1\.\d+ \|")
        self.assertIn("| B | 1.0 | 1.0 |", text)


class ConditionA(unittest.TestCase):
    """同じ会話に積む（2 回目以降は --conversation）。受入を通ったら止め、最大 3 回まで。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.m = common.load_manifest(MANIFEST)
        self.unit = common.unit_of(self.m, self.m["tasks"][0])
        self.ctx = {"m": self.m, "wt": self.tmp / "wt", "out": self.tmp / "out", "imp": {}, "ttl": 60,
                    "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
                    "templates": {k: (MANIFEST.parent / self.m["templates"][k]).read_text(encoding="utf-8")
                                  for k in ("initial", "retry")}}
        self.ctx["out"].mkdir(parents=True)
        self.seen = []

    def call(self, imp, prompt, cwd, cid, ttl):
        self.seen.append((prompt, cid))
        return {"rc": 0, "seconds": 1.0, "conversation_id": cid or "conv-1",
                "usage": {"input_tokens": 100 * len(self.seen), "output_tokens": 10}, "out": "", "err": ""}

    def fast_passing_at(self, k):
        def fast(wt, proj, out, tag):
            ok = len(self.seen) >= k
            return {"G.B4abT1Cases.Case_x": "Passed" if ok else "Failed"}, "tail line"
        return fast

    def test_retries_in_the_same_conversation_until_accepted(self):
        state = {}
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, state,
                                call=self.call, fast=self.fast_passing_at(2))
        self.assertEqual((rec["attempts"], rec["accepted"]), (2, True))
        self.assertEqual([cid for _, cid in self.seen], [None, "conv-1"])
        self.assertIn("G.B4abT1Cases.Case_x", self.seen[1][0], "再試行には落ちたテストの名前が入る")
        self.assertIn(str(self.ctx["wt"]), self.seen[0][0], "初回には作業場所が入る")
        self.assertEqual(state["conversation_id"], "conv-1", "次のタスクへ会話を引き継ぐ")

    def test_gives_up_after_three_attempts(self):
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {},
                                call=self.call, fast=self.fast_passing_at(99))
        self.assertEqual((rec["attempts"], rec["accepted"]), (3, False))


class ConditionB(unittest.TestCase):
    def test_pipeline_is_called_local_only_in_its_own_places(self):
        args = driver.pipeline_args(Path("u.json"), Path("wt"), Path("sb"), Path("out"), Path("t.json"))
        for flag in ("--local-only", "--sandbox", "--out-dir", "--repo-dir", "--telemetry"):
            self.assertIn(flag, args)

    def test_local_only_commits_without_push_or_ci(self):
        with tempfile.TemporaryDirectory() as d:
            repo, sb = Path(d) / "repo", Path(d) / "sb"
            repo.mkdir()
            git("init", "-q", "-b", "main", cwd=repo)
            git("config", "user.email", "t@example.com", cwd=repo)
            git("config", "user.name", "t", cwd=repo)
            (repo / "A.txt").write_text("old", encoding="utf-8")
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "init", cwd=repo)
            sb.mkdir()
            (sb / "A.txt").write_text("new", encoding="utf-8")
            engine = SimpleNamespace(companions=lambda rel: [], carry_companion=lambda *a: ("skip", ""))
            c = SimpleNamespace(unit={"whitelist": ["A.txt"], "id": "u"}, repo=repo, sb=lambda rel: sb / rel,
                                engine=engine, ttl={"git": 60}, local_only=True, metrics={}, gate=None)
            # リモートが無いので、push まで進めば失敗する。SUCCESS なら push も CI も行っていない
            self.assertEqual(pipeline.carry_out_and_ci(c), ("SUCCESS", ""))
            self.assertIn("implement u via pipeline", git("log", "-1", "--format=%s", cwd=repo))
            self.assertEqual((repo / "A.txt").read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
