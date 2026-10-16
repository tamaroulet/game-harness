"""記録の検め直し（harness/bench_verify.py）：採点の再現・計画役の出力との一致・人の介入の数・束の manifest の照合。
記録・走行のディレクトリ（小さな bundle と manifest）・非公開オラクルの置き場は一時ディレクトリに作った偽物。
本物の記録とオラクルには触れない。
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import bench_score  # noqa: E402
import bench_verify  # noqa: E402
from bench_start import git  # noqa: E402

HIDDEN = '''import unittest
import toypkg


class A(unittest.TestCase):
    def test_add(self):
        self.assertEqual(toypkg.add(1, 2), 3)
'''
GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
TASK = "B1"
PLANNED = {"a.md": "h1", "b.md": "h2"}
QUEUE = {"01": "h1", "02": "h2"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bench_verify.main(argv)
    return code, out.getvalue(), err.getvalue()


class Verify(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bench-verify-test-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.oracles, self.clone, self.out = self.base / "oracles", self.base / "clone", self.base / "out"
        self.hidden = self.oracles / "bench" / TASK / "hidden"
        write(self.hidden / "test_hidden_a.py", HIDDEN)
        self.fp = bench_score.fingerprint(self.hidden)
        write(self.clone / "docs" / "bench" / TASK / "spec.md", "仕様\n")
        self.floor(passed=True, fingerprint=self.fp)
        self.count = 0

    def floor(self, **rec):
        write(self.clone / "bench" / "records" / TASK / "floor.json", json.dumps({"task": TASK, **rec}))

    def bundle(self, package_text):
        """開始のコミット（oracle.sha256 だけ）と先端のコミット（toypkg）の bundle を作り、先端のコミットを返す。"""
        self.count += 1
        repo = self.base / f"src{self.count}"
        repo.mkdir()
        git(repo, ["init", "-q"], env=ENV)
        write(repo / "docs" / "bench" / TASK / "oracle.sha256", self.fp + "\n")
        git(repo, ["add", "-A"], env=ENV)
        git(repo, ["commit", "-q", "-m", "start"], env=ENV)
        start = git(repo, ["rev-parse", "HEAD"], env=ENV).decode().strip()
        write(repo / "toypkg" / "__init__.py", package_text)
        git(repo, ["add", "-A"], env=ENV)
        git(repo, ["commit", "-q", "-m", "head"], env=ENV)
        head = git(repo, ["rev-parse", "HEAD"], env=ENV).decode().strip()
        git(repo, ["update-ref", "refs/heads/tip", head], env=ENV)
        return start, head, repo

    def run_record(self, name, package_text=GOOD, passed=1, total=1, with_bundle=True, **over):
        start, head, repo = self.bundle(package_text)
        rundir = self.out / "runs" / TASK / f"factory-{name}"
        write(rundir / "env.json", "{}")
        if with_bundle:
            (rundir / "git").mkdir(parents=True, exist_ok=True)
            git(repo, ["bundle", "create", "-q", str(rundir / "git" / "run.bundle"), "refs/heads/tip"], env=ENV)
        files = sorted(p for p in rundir.rglob("*") if p.is_file())
        write(rundir / "manifest.json", json.dumps({p.relative_to(rundir).as_posix(): sha(p) for p in files}))
        rec = {"task": TASK, "run_id": name, "base": "base1", "start": start, "head": head,
               "started_at": "2026-10-10T12:00:00+09:00", "invalid": False, "reason": None, "violations": [],
               "passed": passed, "total": total, "rate": 1.0, "timed_out": False,
               "planner_sha256": dict(PLANNED), "queue_sha256": dict(QUEUE), "replaced": 0,
               "manifest_sha256": sha(rundir / "manifest.json"), **over}
        write(self.clone / "bench" / "records" / TASK / f"factory-{name}.json", json.dumps(rec))
        return rundir

    def verify(self, *extra, min_runs=1):
        return call(["--task", TASK, "--min-runs", str(min_runs), "--repo", str(self.clone), "--out", str(self.out),
                     "--oracles", str(self.oracles), *extra])

    def test_clean_records_that_regrade_the_same_exit_zero(self):
        self.run_record("1")
        self.run_record("2")
        code, out, _ = self.verify(min_runs=2)
        self.assertEqual(code, 0)
        self.assertIn("数えた記録 2 件", out)
        self.assertIn("人の介入のあった記録 0 件", out)

    def test_a_regrade_that_differs_from_the_record_exits_one_and_names_the_field(self):
        self.run_record("1", package_text=BAD, passed=1, total=1)
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("factory-1", out)
        self.assertIn("passed", out)

    def test_too_few_counted_records_exits_one(self):
        self.run_record("1")
        code, out, _ = self.verify(min_runs=2)
        self.assertEqual(code, 1)
        self.assertIn("足りません", out)

    def test_a_floor_fingerprint_that_differs_from_now_exits_one(self):
        self.run_record("1")
        self.floor(passed=True, fingerprint="0" * 64)
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("floor.json", out)

    def test_a_failed_or_missing_floor_exits_one(self):
        self.run_record("1")
        self.floor(passed=False, fingerprint=self.fp)
        self.assertEqual(self.verify()[0], 1)
        (self.clone / "bench" / "records" / TASK / "floor.json").unlink()
        self.assertEqual(self.verify()[0], 1)

    def test_a_record_with_violations_is_counted_but_not_regraded(self):
        self.run_record("1", with_bundle=False, violations=["README.md"], passed=0, total=0)
        code, out, _ = self.verify()
        self.assertEqual(code, 0)
        self.assertIn("数えた記録 1 件", out)

    def test_an_invalid_record_is_not_counted(self):
        self.run_record("1", invalid="REQ-11")
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("数えた記録 0 件", out)

    def test_an_unplanned_queue_what_and_a_replacement_are_interventions_not_counted(self):
        self.run_record("1")
        self.run_record("2", queue_sha256={"01": "h1", "02": "unplanned"})
        self.run_record("3", replaced=1)
        code, out, _ = self.verify()
        self.assertEqual(code, 0)
        self.assertIn("数えた記録 1 件", out)
        self.assertIn("人の介入のあった記録 2 件", out)
        self.assertIn("B1/factory-2", out)
        self.assertIn("B1/factory-3", out)
        self.assertEqual(self.verify(min_runs=2)[0], 1)

    def freeze(self, **over):
        doc = {"base": "base1", "tasks": {TASK: self.fp}, "frozen_at": "2026-10-09T00:00:00+09:00", **over}
        write(self.clone / "bench" / "records" / "freeze.json", json.dumps(doc))

    def test_frozen_counts_only_records_after_the_freeze_on_the_frozen_base(self):
        self.freeze()
        self.run_record("1")
        self.run_record("2", started_at="2026-10-08T12:00:00+09:00")
        self.run_record("3", base="other")
        code, out, _ = self.verify("--frozen")
        self.assertEqual(code, 0)
        self.assertIn("数えた記録 1 件", out)
        self.assertEqual(self.verify("--frozen", min_runs=2)[0], 1)
        self.assertIn("数えた記録 3 件", self.verify()[1])

    def test_frozen_with_a_changed_fingerprint_exits_one_and_without_freeze_json_exits_two(self):
        self.run_record("1")
        self.assertEqual(self.verify("--frozen")[0], 2)
        self.freeze(tasks={TASK: "0" * 64})
        code, out, _ = self.verify("--frozen")
        self.assertEqual(code, 1)
        self.assertIn("freeze.json", out)

    def test_a_missing_oracles_directory_exits_two(self):
        self.run_record("1")
        code, _, err = call(["--task", TASK, "--min-runs", "1", "--repo", str(self.clone), "--out", str(self.out),
                             "--oracles", str(self.base / "nowhere")])
        self.assertEqual(code, 2)
        self.assertIn("非公開オラクル", err)

    def test_all_reads_the_tasks_under_docs_bench_that_start_with_b(self):
        self.run_record("1")
        write(self.clone / "docs" / "bench" / "notes" / "x.md", "x\n")
        code, out, _ = call(["--all", "--min-runs", "1", "--repo", str(self.clone), "--out", str(self.out),
                             "--oracles", str(self.oracles)])
        self.assertEqual(code, 0)
        self.assertIn("B1: 合格", out)
        self.assertNotIn("notes", out)

    def test_package_matching_manifests_exit_zero(self):
        self.run_record("1")
        code, _, _ = call(["--task", TASK, "--min-runs", "1", "--repo", str(self.clone), "--package", str(self.out),
                           "--oracles", str(self.oracles)])
        self.assertEqual(code, 0)

    def test_package_with_a_file_that_differs_from_the_manifest_exits_one(self):
        rundir = self.run_record("1")
        write(rundir / "env.json", '{"改ざん": true}')
        code, out, _ = call(["--task", TASK, "--min-runs", "1", "--repo", str(self.clone), "--package", str(self.out),
                             "--oracles", str(self.oracles)])
        self.assertEqual(code, 1)
        self.assertIn("env.json", out)

    def test_package_with_a_manifest_sha256_that_differs_from_the_record_exits_one(self):
        self.run_record("1", manifest_sha256="0" * 64)
        code, out, _ = call(["--task", TASK, "--min-runs", "1", "--repo", str(self.clone), "--package", str(self.out),
                             "--oracles", str(self.oracles)])
        self.assertEqual(code, 1)
        self.assertIn("manifest_sha256", out)

    def test_package_reads_runs_from_the_package_not_from_out(self):
        self.run_record("1")
        elsewhere = self.base / "elsewhere"
        code, out, _ = call(["--task", TASK, "--min-runs", "1", "--repo", str(self.clone), "--out", str(self.out),
                             "--package", str(elsewhere), "--oracles", str(self.oracles)])
        self.assertEqual(code, 1)
        self.assertIn("manifest.json", out)


if __name__ == "__main__":
    unittest.main()
