"""固定の計画の走行（W-102）の試験：取り込みの監修役の控えを置き、監修役を呼ばずに取り込む。走らせ役と harness/ の版が違えば止まる。
本物の claude・agy の CLI は使わず、監修役の起動は子プロセス（proc.run）の偽物で数える。git は一時のディレクトリのリポジトリだけ。"""
import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import hline_base  # noqa: E402
import hline_queue  # noqa: E402
import hline_review  # noqa: E402
import proc  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
REAL_RUN = proc.run
MODEL = "claude-sonnet-5-5"
REVIEWER = {"cli": "claude", "instruction": "docs/reviewer.md", "headless_flag": "-p", "model_flag": "--model", "model": MODEL,
            "extra_flags": [], "output_format_args": []}
ROW = {"id": "T-1", "title": "一つ目", "status": "pending", "by": "hline", "purpose": "目的", "serves": "S",
       "acceptance": ["通る"], "verification": "python -m unittest x"}


def sh(repo, *args):
    r = subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def make_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "init", "-q")
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sh(path, "add", "-A")
    sh(path, "commit", "-q", "-m", "base")
    return sh(path, "rev-parse", "HEAD")


def what(n, body="本文"):
    return f"# 題 {n}\nマイルストーン: bench\nタスク: T-1\n\n## What\n{body} {n}\n\n## 合格条件\n1. 通る\n"


class Case(unittest.TestCase):
    def tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def patch(self, obj, name, new):
        p = mock.patch.object(obj, name, new)
        p.start()
        self.addCleanup(p.stop)


class SeedTests(Case):
    def setUp(self):
        self.root = self.tmp()
        self.repo = self.root / "repo"
        self.start = make_repo(self.repo, {"docs/reviewer.md": "監修役への指示\n",
                                           "docs/progress.yaml": json.dumps({"tasks": [ROW]})})
        self.reviewer_calls = 0
        self.patch(proc, "run", self.fake_run)
        self.patch(hline_base, "pinned_models", lambda agent, out, label: [])
        self.patch(proc, "resolve_cli", lambda name: ["claude"])
        self.patch(hline_review, "ROOT", self.repo)
        self.patch(hline_queue, "ROOT", self.repo)

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if label == "監修役":
            self.reviewer_calls += 1
            return 0, json.dumps({"verdict": "pass", "findings": []}), ""
        return REAL_RUN(args, cwd, ttl, label, env=env, input=input)

    def build(self, plan=True, names=("P-01", "P-02")):
        """setup_clone まで進めた Factory。受信箱には names の What が入る。"""
        work = self.root / "work"
        (work / "inbox").mkdir(parents=True)
        plan_inbox = work / "plan"
        plan_inbox.mkdir()
        for n in names:
            (plan_inbox / f"{n}.md").write_text(what(n), encoding="utf-8")
        a = argparse.Namespace(task="B1", run_id="r", repo=str(self.repo), base=self.start, plan=str(self.root / "frozen") if plan else None,
                               out=str(self.root / "out"), sessions=None, implementer=None)
        f = bench_run.Factory(a)
        f.work = work
        f.run["start"] = self.start
        f.run["planner_sha256"] = {f"{n}.md": f"sha-{n}" for n in names}
        cfg = {"reviewer": REVIEWER, "ttl_seconds": {"git": 60, "reviewer": 60}, "implementer": {"cli": "claude", "model": MODEL}}
        f.setup_clone(plan_inbox, cfg)
        return f

    def key(self, f, name):
        text = (f.work / "inbox" / f"{name}.md").read_text(encoding="utf-8")
        return hline_review.key_of(hline_review.instruction(f.line_cfg), text, ROW)

    def stored(self, f, name):
        return hline_review.store_dir(f.line_cfg) / f"{self.key(f, name)}.json"

    def intake(self, f):
        st = hline_queue.load_state(f.line_cfg)
        return hline_queue.intake(f.line_cfg, st), st

    def test_seeds_pass_under_same_key_and_reviewer_is_not_called(self):
        f = self.build()
        for n in ("P-01", "P-02"):
            doc = json.loads(self.stored(f, n).read_text(encoding="utf-8"))
            self.assertEqual(doc, {"verdict": "pass", "findings": [], "frozen_plan": f"sha-{n}"})
        taken, st = self.intake(f)
        self.assertEqual(taken, ["P-01", "P-02"])
        self.assertEqual(self.reviewer_calls, 0)
        self.assertEqual(list((f.work / "inbox").glob("*.md")), [])
        self.assertEqual(st["skipped"], [])

    def test_without_plan_no_seed(self):
        f = self.build(plan=False)
        self.assertFalse(hline_review.store_dir(f.line_cfg).exists())
        taken, _ = self.intake(f)
        self.assertEqual(taken, ["P-01", "P-02"])
        self.assertEqual(self.reviewer_calls, 2)

    def test_existing_revise_is_kept_and_what_stays_in_inbox(self):
        f = self.build(plan=False)
        revise = {"verdict": "revise", "findings": [{"kind": "ambiguity", "text": "曖昧"}]}
        hline_review.store_dir(f.line_cfg).mkdir(parents=True, exist_ok=True)
        hline_base.write_json(self.stored(f, "P-01"), revise)
        f.frozen = Path(self.root / "frozen")
        f.seed_reviews()
        self.assertEqual(json.loads(self.stored(f, "P-01").read_text(encoding="utf-8")), revise)
        self.assertEqual(json.loads(self.stored(f, "P-02").read_text(encoding="utf-8"))["verdict"], "pass")
        taken, st = self.intake(f)
        self.assertEqual(taken, ["P-02"])
        self.assertEqual([s["file"] for s in st["skipped"]], ["P-01.md"])
        self.assertIn("曖昧", st["skipped"][0]["reason"])
        self.assertEqual([p.name for p in (f.work / "inbox").glob("*.md")], ["P-01.md"])
        self.assertEqual(self.reviewer_calls, 0)

    def test_what_differing_from_frozen_plan_calls_reviewer(self):
        f = self.build()
        (f.work / "inbox" / "P-02.md").write_text(what("P-02", "固定の計画と違う本文"), encoding="utf-8")
        taken, _ = self.intake(f)
        self.assertEqual(taken, ["P-01", "P-02"])
        self.assertEqual(self.reviewer_calls, 1)


class VersionTests(Case):
    def setUp(self):
        self.root = self.tmp()
        self.mine = make_repo(self.root / "runner", {"harness/a.py": "a = 1\n", "other.txt": "1\n"})
        self.theirs = make_repo(self.root / "clone", {"harness/b.py": "b = 1\n", "docs/bench/B01/spec.md": "x\n"})
        self.same = make_repo(self.root / "same", {"harness/a.py": "a = 1\n", "other.txt": "2\n"})
        self.patch(bench_run, "ROOT", self.root / "runner")
        self.children = []

        def no_children(*a, **k):
            self.children.append(a)
            raise AssertionError("子プロセスを起こしてはいけません")
        self.patch(bench_run.proc, "run", no_children)
        self.patch(bench_run, "stage_tasks", lambda repo, base: self.children.append("stage_tasks") or ["B01"])
        self.patch(bench_run, "set_execution_state", lambda flags: None)

    def run_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return bench_run.main(argv), err.getvalue()

    def factory(self, repo, base):
        return self.run_main(["factory", "--task", "B01", "--run-id", "r", "--repo", str(repo), "--base", base,
                              "--out", str(self.root / "out"), "--sessions", str(self.root / "s")])

    def stage(self, repo, base):
        return self.run_main(["stage", "--tag", "t", "--repo", str(repo), "--base", base, "--out", str(self.root / "out")])

    def test_different_harness_tree_stops_factory_with_both_hashes(self):
        code, err = self.factory(self.root / "clone", self.theirs)
        mine, theirs = sh(self.root / "runner", "rev-parse", "HEAD:harness"), sh(self.root / "clone", "rev-parse", f"{self.theirs}:harness")
        self.assertEqual(code, 2)
        self.assertIn(mine, err)
        self.assertIn(theirs, err)
        self.assertEqual(self.children, [])
        self.assertFalse((self.root / "out").exists())

    def test_different_harness_tree_stops_stage_before_anything(self):
        code, err = self.stage(self.root / "clone", self.theirs)
        self.assertEqual(code, 2)
        self.assertIn(sh(self.root / "runner", "rev-parse", "HEAD:harness"), err)
        self.assertEqual(self.children, [])

    def test_unresolvable_base_stops_with_2(self):
        for run in (self.factory, self.stage):
            code, err = run(self.root / "clone", "no-such-rev")
            self.assertEqual(code, 2)
            self.assertIn("ABORT", err)
        self.assertEqual(self.children, [])

    def test_same_harness_tree_passes_the_check(self):
        self.assertIsNone(bench_run.version_problem(self.root / "same", self.same))
        self.assertIsNotNone(bench_run.version_problem(self.root / "clone", self.theirs))

    def test_other_subcommands_do_not_check(self):
        called = []
        self.patch(bench_run, "version_problem", lambda repo, rev: called.append(rev) or "違う")
        self.run_main(["finish", "--task", "B01", "--run-id", "r", "--repo", str(self.root / "clone"), "--out", str(self.root / "out")])
        self.assertEqual(called, [])


class FinishCallsTests(Case):
    def setUp(self):
        self.root = self.tmp()
        self.repo = self.root / "clone"
        make_repo(self.repo, {"config/hline.json": json.dumps({"implementer": {"cli": "claude", "model": MODEL}})})
        self.out = self.root / "out"
        self.rundir = bench_run.run_dir(self.out, "B1", "r")
        (self.rundir / "factory").mkdir(parents=True)
        run = {"task": "B1", "run_id": "r", "base": "b", "start": "s", "head": "h", "invalid": None, "plan_confirmed": False}
        (self.rundir / "factory" / "run.json").write_text(json.dumps(run), encoding="utf-8")
        self.patch(bench_run.proc, "run", lambda *a, **k: (0, "", ""))

    def finish(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = bench_run.main(["finish", "--task", "B1", "--run-id", "r", "--repo", str(self.repo), "--out", str(self.out)])
        self.assertEqual(code, 0, err.getvalue())
        return json.loads((self.repo / "bench" / "records" / "B1" / "factory-r.json").read_text(encoding="utf-8"))

    def test_implementer_calls_without_reviewer_call_are_valid(self):
        for n in (1, 2):
            d = self.rundir / "calls" / f"{n:03d}-implementer"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"modelUsage": {MODEL: {}}}), encoding="utf-8")
        self.assertEqual(self.finish()["invalid"], False)

    def test_empty_calls_is_still_req22(self):
        (self.rundir / "calls").mkdir()
        record = self.finish()
        self.assertEqual(record["invalid"], "REQ-22")
        self.assertIn("呼び出しの記録がありません", record["reason"])


if __name__ == "__main__":
    unittest.main()
