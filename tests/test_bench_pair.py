"""対の走り（harness/bench_pair.py）の試験。一時ディレクトリの偽のクローン・束・記録だけで書き、工廠は走らせない
（run_step と子のプロセスは差し替える）。"""
import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_pair  # noqa: E402
import bench_run  # noqa: E402
import hline_base  # noqa: E402


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def init_repo(d, files):
    d.mkdir(parents=True, exist_ok=True)
    git(d, "init", "-q")
    git(d, "config", "user.name", "Tester")
    git(d, "config", "user.email", "tester@example.com")
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "base")
    return git(d, "rev-parse", "HEAD")


def bundle_run(repo, out, run_id, start, files, base="B07"):
    """start の上に files を足したコミットを先端にした走りの束を、run_dir(out, base, run_id) に作る。(開始, 先端)。"""
    git(repo, "checkout", "-q", "--detach", start)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", f"tip {run_id}")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/tip", tip)
    rundir = bench_run.run_dir(out, base, run_id)
    for sub in ("git", "factory", "items"):
        (rundir / sub).mkdir(parents=True, exist_ok=True)
    git(repo, "bundle", "create", "-q", str(rundir / "git" / "run.bundle"), "refs/heads/tip")
    return start, tip


def quiet():
    return contextlib.redirect_stderr(io.StringIO())


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "clone"
        self.rev = init_repo(self.repo, {"base.txt": "base\n"})
        self.staged, self.out = self.root / "staged", self.root / "out"
        tree = self.staged / "B07" / "s0" / "tree"
        (tree / "pkg").mkdir(parents=True)
        (tree / "tests").mkdir(parents=True)
        (tree / "pkg" / "mod.py").write_text("val = 0\n", encoding="utf-8")
        (tree / "tests" / "test_pkg_base.py").write_text("pass\n", encoding="utf-8")
        sha = {}
        for k in range(1, 5):
            (self.staged / "B07" / f"s{k}").mkdir(parents=True)
            spec = self.staged / "B07" / f"s{k}" / "spec.md"
            spec.write_text(f"# 仕様：s{k}\n## 頼みの種類\n追加\n## 要件\n- REQ-01：要件\n", encoding="utf-8")
            sha[f"s{k}"] = bench_run.sha256_file(spec)
        hline_base.write_json(self.staged / "chain.json", {"tag": "ref1", "set": "dev", "bases": [
            {"base": "B07", "package": "pkg", "stages": 4, "spec_sha256": sha}]})


class TestRun(Base):
    def setUp(self):
        super().setUp()
        self.tips, self.calls = {}, []

    def ref_record(self, stages):
        path = self.out / "chains" / "ref1" / "B07.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(path, {"tag": "ref1", "set": "dev", "base": "B07", "package": "pkg", "rev": self.rev,
                                     "s0": "x", "stages": stages, "stopped": None})

    def ref_stage(self, k, run_id, marker, invalid=None):
        _, tip = bundle_run(self.repo, self.out, run_id, self.rev, {"marker.txt": marker})
        return {"k": k, "attempt": 0, "run_id": run_id, "code": 0, "head": tip, "invalid": invalid}

    def fake_step(self, a, repo, out, staged, entry, tip, k, rec):
        self.calls.append(k)
        self.tips[k] = tip
        st = {"k": k, "attempt": 0, "run_id": f"{a.tag}-s{k}", "head": "f" * 40, "invalid": None}
        rec["stages"].append(st)
        return st

    def run_cli(self, stages, ref="ref1", step=None):
        argv = ["run", "--tag", "p-new", "--repo", str(self.repo), "--base", self.rev, "--staged", str(self.staged),
                "--from", ref, "--stages", stages, "--out", str(self.out)]
        with patch.object(bench_pair.bench_chain, "run_step", side_effect=step or self.fake_step), \
             patch.object(bench_pair.bench_run, "version_problem", return_value=None), quiet():
            return bench_pair.main(argv)

    def record(self):
        return bench_run.read_json(self.out / "chains" / "p-new" / "B07.json", {})

    def marker(self, sha):
        return git(self.repo, "show", f"{sha}:marker.txt")

    def test_tag_of_chain_json_must_be_the_reference_tag(self):
        self.ref_record([])
        self.assertEqual(self.run_cli("B07:1", ref="p-new"), 2)
        self.assertEqual(self.run_cli("B07:1", ref="other"), 2)
        self.assertEqual(self.calls, [])

    def test_version_problem_stops_the_run(self):
        self.ref_record([])
        argv = ["run", "--tag", "p-new", "--repo", str(self.repo), "--base", self.rev, "--staged", str(self.staged),
                "--from", "ref1", "--stages", "B07:1", "--out", str(self.out)]
        with patch.object(bench_pair.bench_chain, "run_step", side_effect=self.fake_step), \
             patch.object(bench_pair.bench_run, "version_problem", return_value="木が違います"), quiet():
            self.assertEqual(bench_pair.main(argv), 2)
        self.assertEqual(self.calls, [])

    def test_uses_the_last_valid_attempt_not_the_last_attempt(self):
        good = self.ref_stage(1, "ref1-s1", "good")
        bad = self.ref_stage(1, "ref1-s1-r1", "bad", invalid="REQ-22")
        self.ref_record([good, dict(bad, attempt=1)])
        self.assertEqual(self.run_cli("B07:2"), 0)
        self.assertEqual(self.tips[2], good["head"])
        self.assertEqual(self.marker(self.tips[2]), "good")

    def test_stage_one_starts_from_the_stage_zero_tip(self):
        self.ref_record([])
        self.assertEqual(self.run_cli("B07:1"), 0)
        names = git(self.repo, "ls-tree", "-r", "--name-only", self.tips[1]).splitlines()
        self.assertIn("pkg/mod.py", names)
        self.assertIn("tests/test_pkg_base.py", names)
        self.assertIn("base.txt", names)
        self.assertEqual(git(self.repo, "rev-list", "--count", self.tips[1]), "1")   # 親の無い段 0 の先端

    def test_stages_of_one_base_do_not_chain(self):
        s1, s2, s3 = (self.ref_stage(k, f"ref1-s{k}", f"ref s{k}") for k in (1, 2, 3))
        self.ref_record([s1, s2, s3])
        self.assertEqual(self.run_cli("B07:2,B07:4"), 0)
        self.assertEqual(self.calls, [2, 4])
        self.assertEqual(self.tips[2], s1["head"])
        self.assertEqual(self.tips[4], s3["head"])   # 段 2 の結果（f…f）にはつながない
        rec = self.record()
        self.assertEqual((rec["from"], rec["set"], rec["pairs"], rec["tag"]), ("ref1", "dev", [2, 4], "p-new"))
        self.assertEqual([st["k"] for st in rec["stages"]], [2, 4])

    def test_stage_without_usable_reference_is_skipped_and_others_run(self):
        bad = self.ref_stage(2, "ref1-s2", "bad", invalid="REQ-11")
        self.ref_record([self.ref_stage(1, "ref1-s1", "s1"), bad])
        self.assertEqual(self.run_cli("B07:3,B07:2"), 0)
        self.assertEqual(self.calls, [2])
        rec = self.record()
        self.assertEqual([s["k"] for s in rec["skipped"]], [3])
        self.assertIn("段 2", rec["skipped"][0]["reason"])

    def test_missing_reference_record_skips_every_stage(self):
        self.assertEqual(self.run_cli("B07:1,B07:2"), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual([s["k"] for s in self.record()["skipped"]], [1, 2])
        self.assertIn("基準の記録がありません", self.record()["skipped"][0]["reason"])

    def test_finished_stages_are_written_before_a_later_stage_aborts(self):
        self.ref_record([self.ref_stage(k, f"ref1-s{k}", f"s{k}") for k in (1, 2)])

        def step(a, repo, out, staged, entry, tip, k, rec):
            if k == 3:
                raise bench_pair.BenchError("止まった")
            return self.fake_step(a, repo, out, staged, entry, tip, k, rec)

        self.assertEqual(self.run_cli("B07:2,B07:3", step=step), 2)
        self.assertEqual([st["k"] for st in self.record()["stages"]], [2])
        self.calls.clear()
        self.assertEqual(self.run_cli("B07:2,B07:3"), 0)   # 走らせ直しは、済んだ段 2 を走らせない
        self.assertEqual(self.calls, [3])

    def test_malformed_stages_run_nothing(self):
        self.ref_record([self.ref_stage(1, "ref1-s1", "s1")])
        for stages in ("B99:1", "B07:0", "B07:5", "B07:2,B07:2", "B07:x", "B07", "", "B07:2,B99:1"):
            with self.subTest(stages=stages):
                self.assertEqual(self.run_cli(stages), 2)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.out / "chains" / "p-new").exists())


class TestPair(Base):
    def setUp(self):
        super().setUp()
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.old_rev = init_repo(self.old, {"harness/m.py": "A = 1\n"})
        self.new_rev = init_repo(self.new, {"harness/m.py": "A = 2\n"})
        self.calls, self.codes = [], {}

    def spawn(self, cmd, cwd):
        self.calls.append((cmd, cwd))
        return self.codes.get(len(self.calls), 0)

    def pair(self, stages="B07:1,B07:2,B07:3", new=None, new_rev=None, *extra):
        argv = ["pair", "--tag", "p", "--old", str(self.old), "--old-rev", self.old_rev, "--new", str(new or self.new),
                "--new-rev", new_rev or self.new_rev, "--staged", str(self.staged), "--from", "ref1", "--stages", stages,
                "--out", str(self.out), "--implementer", "impl.json", *extra]
        with patch.object(bench_pair, "spawn", side_effect=self.spawn), quiet():
            return bench_pair.main(argv)

    @staticmethod
    def opt(cmd, name):
        return cmd[cmd.index(name) + 1]

    def test_first_version_alternates_per_pair(self):
        self.assertEqual(self.pair(), 0)
        self.assertEqual([self.opt(c, "--tag") for c, _ in self.calls], ["p-old", "p-new", "p-new", "p-old", "p-old", "p-new"])
        self.assertEqual([self.opt(c, "--stages") for c, _ in self.calls], ["B07:1"] * 2 + ["B07:2"] * 2 + ["B07:3"] * 2)
        log = bench_run.read_json(self.out / "pairs" / "p.json", {})
        self.assertEqual([(r["pair"], r["side"], r["code"]) for r in log["runs"]],
                         [(0, "old", 0), (0, "new", 0), (1, "new", 0), (1, "old", 0), (2, "old", 0), (2, "new", 0)])
        self.assertTrue(all(r["started"] and r["ended"] for r in log["runs"]))

    def test_children_get_their_own_clone_and_rev_and_the_same_out(self):
        self.assertEqual(self.pair("B07:2"), 0)
        (old_cmd, old_cwd), (new_cmd, new_cwd) = self.calls
        for cmd, cwd, clone, rev, tag in ((old_cmd, old_cwd, self.old, self.old_rev, "p-old"),
                                          (new_cmd, new_cwd, self.new, self.new_rev, "p-new")):
            self.assertEqual(cmd[:4], [sys.executable, "-m", "harness.bench_pair", "run"])
            self.assertEqual(self.opt(cmd, "--tag"), tag)
            self.assertEqual(Path(cwd).resolve(), clone.resolve())
            self.assertEqual(Path(self.opt(cmd, "--repo")).resolve(), clone.resolve())
            self.assertEqual(self.opt(cmd, "--base"), rev)
            self.assertEqual(self.opt(cmd, "--from"), "ref1")
            self.assertEqual(self.opt(cmd, "--implementer"), "impl.json")
            self.assertEqual(Path(self.opt(cmd, "--staged")).resolve(), self.staged.resolve())
        self.assertEqual(Path(self.opt(old_cmd, "--out")).resolve(), Path(self.opt(new_cmd, "--out")).resolve())
        self.assertEqual(Path(self.opt(old_cmd, "--out")).resolve(), self.out.resolve())
        self.assertNotIn("--sessions", old_cmd)

    def test_same_harness_tree_needs_allow_same(self):
        twin = self.root / "twin"
        twin_rev = init_repo(twin, {"harness/m.py": "A = 1\n", "other.txt": "x\n"})   # harness/ の木は旧版と同じ
        self.assertEqual(self.pair("B07:1", twin, twin_rev), 2)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.pair("B07:1", twin, twin_rev, "--allow-same"), 0)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(bench_run.read_json(self.out / "pairs" / "p.json", {})["same"])

    def test_a_failed_child_does_not_stop_the_rest(self):
        self.codes = {1: 2}
        self.assertEqual(self.pair(), 1)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual([r["code"] for r in bench_run.read_json(self.out / "pairs" / "p.json", {})["runs"]], [2, 0, 0, 0, 0, 0])

    def test_malformed_stages_or_wrong_reference_run_nothing(self):
        self.assertEqual(self.pair("B07:9"), 2)
        hline_base.write_json(self.staged / "chain.json", {"tag": "p", "bases": [{"base": "B07", "stages": 4}]})
        self.assertEqual(self.pair("B07:1"), 2)
        self.assertEqual(self.calls, [])


HIDDEN = "import unittest\nfrom pkg.mod import f\nclass T(unittest.TestCase):\n    def test_s{k}(self): self.assertEqual(f(), {v})\n"


class TestFinish(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chains, self.out, self.hout = self.root / "oracles" / "chains", self.root / "out", self.root / "hout"
        for k, name in ((0, "s0/hidden/test_hidden_B07.py"), (1, "s1/hidden/test_chain_B07_s1.py"), (2, "s2/hidden/test_chain_B07_s2.py")):
            (self.chains / "B07" / name).parent.mkdir(parents=True, exist_ok=True)
            (self.chains / "B07" / name).write_text(HIDDEN.format(k=k, v=k + 1), encoding="utf-8")
            if k:
                hline_base.write_json(self.chains / "B07" / f"s{k}" / "replaced.json", [])
        self.repo = self.root / "clone"
        self.cfg = {"out": str(self.hout), "oracles": str(self.chains.parent), "inbox": str(self.root / "inbox")}
        init_repo(self.repo, {"config/hline.json": "{}\n"})
        hline_base.write_json(self.repo / "config" / "hline.json", self.cfg)
        self.work = self.root / "work"
        self.base_rev = init_repo(self.work, {"pkg/__init__.py": "", "pkg/mod.py": "def f(): return 1\n"})

    def stage(self, k, run_id, tip_val):
        """開始のコミットは f()=1 の木（基準の先端の代わり）。先端は f()=tip_val。"""
        start, tip = bundle_run(self.work, self.out, run_id, self.base_rev, {"pkg/mod.py": f"def f(): return {tip_val}\n"})
        return {"k": k, "attempt": 0, "run_id": run_id, "code": 0, "start": start, "head": tip, "invalid": None, "tokens": 9}

    def write(self, tag, base, rec):
        path = self.out / "chains" / tag / f"{base}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(path, rec)

    def finish(self, tag):
        ns = argparse.Namespace(tag=tag, repo=str(self.repo), chains=str(self.chains), out=str(self.out))
        with patch.object(bench_pair.chain_score.machine, "path", return_value=self.root / "local"), quiet():
            return bench_pair.finish(ns)

    def public(self, tag, base="B07"):
        return bench_run.read_json(self.repo / "bench" / "records" / "chains" / tag / f"{base}.json", {})

    def test_kept_is_measured_from_the_start_tree_not_the_previous_stage_of_the_record(self):
        # 段 1 の先端は f()=2（段 0 が落ち、段 1 が通る）。段 2 は基準の先端（f()=1。段 0 が通り、段 1 が落ちる）から走り、
        # 何も変えなかった。記録の前の段を起点にすると kept は [0, 1]、開始の木を起点にすると [1, 1]。
        s1 = self.stage(1, "p-new-s1", 2)
        s2 = self.stage(2, "p-new-s2", 1)
        self.write("p-new", "B07", {"tag": "p-new", "set": "dev", "base": "B07", "package": "pkg", "rev": self.base_rev,
                                    "from": "ref1", "pairs": [1, 2], "stages": [s1, s2], "skipped": [], "stopped": None})
        self.assertEqual(self.finish("p-new"), 0)
        pub = self.public("p-new")
        self.assertEqual(pub["from"], "ref1")
        rows = {r["k"]: r for r in pub["stages"]}
        self.assertEqual(rows[2]["kept"], [1, 1])
        self.assertEqual(rows[2]["old"], [1, 2])
        self.assertEqual(rows[2]["new"], [0, 1])
        self.assertEqual(rows[1]["kept"], [0, 1])   # 段 1 の起点は開始の木の段 0 の組（通っていた段 0 が落ちた）
        self.assertEqual(rows[1]["new"], [1, 1])
        self.assertNotEqual(rows[2]["invalid"], "tainted")
        self.assertTrue((self.hout / "chains" / "p-new" / "B07" / "s2.json").is_file())

    def test_records_without_from_are_not_graded(self):
        st = self.stage(1, "plain-s1", 2)
        rec = {"tag": "plain", "set": "dev", "base": "B07", "package": "pkg", "rev": self.base_rev, "stages": [st], "stopped": None}
        self.write("plain", "B07", rec)
        self.assertEqual(self.finish("plain"), 1)
        self.assertEqual(self.public("plain"), {})
        self.write("mix", "B07", dict(rec, tag="mix", **{"from": "ref1"}))
        self.write("mix", "B08", dict(rec, tag="mix", base="B08"))
        self.assertEqual(self.finish("mix"), 0)
        self.assertEqual(self.public("mix")["from"], "ref1")
        self.assertEqual(self.public("mix", "B08"), {})


class TestCompare(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def write(self, side, stages, skipped=()):
        path = self.repo / "bench" / "records" / "chains" / f"p-{side}" / "B07.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(path, {"tag": f"p-{side}", "base": "B07", "from": "ref1", "stages": stages,
                                     "skipped": [{"k": k, "reason": "基準の段が無い"} for k in skipped]})

    @staticmethod
    def row(k, invalid=False, new=(2, 2), kept=(3, 3)):
        r = {"k": k, "invalid": invalid, "new": list(new), "old": [3, 3], "new_at_start": [0, 2], "reason": None,
             "tokens": 10, "tries": 1}
        if kept is not None:
            r["kept"] = list(kept)
        return r

    def test_fixed_broken_and_missing(self):
        self.write("old", [self.row(1, invalid="REQ-22"), self.row(2), self.row(3), self.row(5), self.row(6, kept=(1, 2)),
                           self.row(7, new=(1, 2))], skipped=[4])
        self.write("new", [self.row(1), self.row(2, kept=None), self.row(4), self.row(5), self.row(6), self.row(7)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(bench_pair.main(["compare", "--tag", "p", "--repo", str(self.repo)]), 0)
        doc = bench_run.read_json(self.repo / "bench" / "records" / "pairs" / "p.json", {})
        self.assertEqual({p["k"]: p["verdict"] for p in doc["pairs"]},
                         {1: "直した", 2: "壊した", 3: "欠け", 4: "欠け", 5: "同じ", 6: "直した", 7: "直した"})
        self.assertEqual((doc["fixed"], doc["broke"], doc["missing"]), (3, 1, 2))
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[-1], "直した 3・壊した 1・欠け 2")
        first = doc["pairs"][0]
        self.assertEqual(sorted(first["old"]), sorted(bench_pair.FIELDS))
        self.assertEqual(first["old"]["invalid"], "REQ-22")
        self.assertIsNone(next(p for p in doc["pairs"] if p["k"] == 3)["new"])

    def test_no_records_is_an_error(self):
        with quiet():
            self.assertEqual(bench_pair.main(["compare", "--tag", "p", "--repo", str(self.repo)]), 1)


if __name__ == "__main__":
    unittest.main()
