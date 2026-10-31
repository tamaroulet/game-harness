import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import bench_start  # noqa: E402
import chain_score  # noqa: E402
import hline_base  # noqa: E402
import progress  # noqa: E402


def git(repo, *args, data=None):
    kw = {"capture_output": True, "text": True, "encoding": "utf-8", "check": True}
    if data is not None:
        kw["input"] = data
    r = subprocess.run(["git", "-C", str(repo), *args], **kw)
    return r.stdout.strip()


def init_repo(d):
    git(d, "init", "-q")
    git(d, "config", "user.name", "Tester")
    git(d, "config", "user.email", "tester@example.com")


class TestBenchChainStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.oracles = self.root / "oracles"
        self.inbox = self.root / "inbox"
        self.out = self.root / "out"
        self.local = self.root / "local"
        for p in (self.oracles, self.inbox, self.out, self.local):
            p.mkdir(parents=True, exist_ok=True)
        self.cfg_file = self.root / "hline.json"
        hline_base.write_json(self.cfg_file, {
            "oracles": str(self.oracles),
            "inbox": str(self.inbox),
            "out": str(self.out),
        })

    def _setup_chain_source(self, base="B07", pkg="pkg_a", stages=2):
        src = self.oracles / "chains" / base
        tree = src / "s0" / "tree"
        (tree / pkg).mkdir(parents=True, exist_ok=True)
        (tree / "tests").mkdir(parents=True, exist_ok=True)
        (tree / pkg / "mod.py").write_text("X = 1\n", encoding="utf-8")
        (tree / "tests" / f"test_{pkg}_base.py").write_text("def test_b(): pass\n", encoding="utf-8")
        for k in range(1, stages + 1):
            s_dir = src / f"s{k}"
            s_dir.mkdir(parents=True, exist_ok=True)
            (s_dir / "spec.md").write_text(f"# 仕様：段{k}\n## 頼みの種類\n追加\n## 要件\n- REQ-01：要件{k}\n", encoding="utf-8")
        return src

    def test_stage_public_files_only(self):
        """A-1: 公開ファイルのみが写り、hidden やメタファイル等の非公開ファイルは写らない。"""
        src = self._setup_chain_source("B07", "pkg_a", 1)
        # 非公開・除外対象ファイルの配置
        (src / "s0" / "hidden").mkdir(parents=True, exist_ok=True)
        (src / "s0" / "hidden" / "test_hidden_B07.py").write_text("hide=0", encoding="utf-8")
        (src / "s0" / "spec.md").write_text("s0 spec", encoding="utf-8")
        (src / "s1" / "hidden").mkdir(parents=True, exist_ok=True)
        (src / "s1" / "hidden" / "test_chain_B07_s1.py").write_text("hide=1", encoding="utf-8")
        (src / "reference").mkdir(parents=True, exist_ok=True)
        (src / "reference" / "ref.txt").write_text("ref", encoding="utf-8")
        for name in ("replaced.json", "mutants.json", "checks.json", "canary.txt", "spec_notes.txt"):
            (src / name).write_text("meta", encoding="utf-8")

        staged_dir = self.root / "staged"
        ns = argparse.Namespace(tag="t1", set="dev", bases="B07", stages=1, chains=str(self.oracles / "chains"), staged=str(staged_dir))
        with patch.object(bench_chain, "CONFIG", self.cfg_file), \
             patch.object(bench_chain.machine, "path", return_value=self.local):
            code = bench_chain.stage(ns)
        self.assertEqual(code, 0)

        # 写し先の検査
        staged_targets = list(staged_dir.glob("t1-*"))
        self.assertEqual(len(staged_targets), 1)
        dest = staged_targets[0] / "B07"
        copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
        expected = ["s0/tree/pkg_a/mod.py", "s0/tree/tests/test_pkg_a_base.py", "s1/spec.md"]
        self.assertEqual(copied, expected)

    def test_stage_destination_and_chain_json(self):
        """A-2: 写し先がユニークで標準出力に出力され、chain.json の情報と指紋が一致する。"""
        self._setup_chain_source("B07", "pkg_a", 1)
        staged_dir = self.root / "staged"
        ns = argparse.Namespace(tag="t1", set="dev", bases="B07", stages=1, chains=str(self.oracles / "chains"), staged=str(staged_dir))

        outs = []
        for _ in range(2):
            buf = io.StringIO()
            with patch.object(bench_chain, "CONFIG", self.cfg_file), \
                 patch.object(bench_chain.machine, "path", return_value=self.local), \
                 patch("sys.stdout", buf):
                code = bench_chain.stage(ns)
            self.assertEqual(code, 0)
            outs.append(buf.getvalue().strip())

        self.assertNotEqual(outs[0], outs[1])
        dest1 = Path(outs[0])
        self.assertTrue(dest1.name.startswith("t1-"))
        self.assertEqual(len(dest1.name.split("-", 1)[1]), 16)

        chain_json = bench_run.read_json(dest1 / "chain.json", {})
        self.assertEqual(chain_json.get("tag"), "t1")
        self.assertEqual(chain_json.get("set"), "dev")
        base_entry = chain_json["bases"][0]
        self.assertEqual(base_entry["base"], "B07")
        self.assertEqual(base_entry["package"], "pkg_a")
        self.assertEqual(base_entry["stages"], 1)
        expected_sha = bench_run.sha256_file(dest1 / "B07" / "s1" / "spec.md")
        self.assertEqual(base_entry["spec_sha256"]["s1"], expected_sha)

    def test_stage_aborts_when_canary_present(self):
        """A-3: 写すファイルに canary が含まれる場合、何も書かずに終了コード 1 となる。"""
        src = self._setup_chain_source("B07", "pkg_a", 1)
        (src / "s1" / "spec.md").write_text("Canary SECRET_CANARY_WORD inside\n", encoding="utf-8")
        staged_dir = self.root / "staged"
        ns = argparse.Namespace(tag="t1", set="dev", bases="B07", stages=1, chains=str(self.oracles / "chains"), staged=str(staged_dir))
        with patch.object(bench_chain, "CONFIG", self.cfg_file), \
             patch.object(bench_chain.bench_guard, "canaries", return_value=["SECRET_CANARY_WORD"]), \
             patch("sys.stderr", io.StringIO()):
            code = bench_chain.stage(ns)
        self.assertEqual(code, 1)
        self.assertFalse(staged_dir.exists())

    def test_stage_stage_count_and_package_validation(self):
        """A-4: stages=1でs1のみ写すこと、段不足や複数パッケージで終了コード 2 となること。"""
        src = self._setup_chain_source("B07", "pkg_a", 2)
        staged_dir = self.root / "staged"
        ns1 = argparse.Namespace(tag="t1", set="dev", bases="B07", stages=1, chains=str(self.oracles / "chains"), staged=str(staged_dir))
        with patch.object(bench_chain, "CONFIG", self.cfg_file):
            code1 = bench_chain.stage(ns1)
        self.assertEqual(code1, 0)
        dest1 = list(staged_dir.glob("t1-*"))[0]
        self.assertTrue((dest1 / "B07" / "s1" / "spec.md").exists())
        self.assertFalse((dest1 / "B07" / "s2").exists())

        # 段不足：s3 が無いのに stages=3
        ns2 = argparse.Namespace(tag="t2", set="dev", bases="B07", stages=3, chains=str(self.oracles / "chains"), staged=str(staged_dir))
        with patch.object(bench_chain, "CONFIG", self.cfg_file), patch("sys.stderr", io.StringIO()):
            code2 = bench_chain.stage(ns2)
        self.assertEqual(code2, 2)

        # 複数パッケージ
        (src / "s0" / "tree" / "pkg_b").mkdir(parents=True, exist_ok=True)
        (src / "s0" / "tree" / "pkg_b" / "m.py").write_text("Y=2\n", encoding="utf-8")
        ns3 = argparse.Namespace(tag="t3", set="dev", bases="B07", stages=1, chains=str(self.oracles / "chains"), staged=str(staged_dir))
        with patch.object(bench_chain, "CONFIG", self.cfg_file), patch("sys.stderr", io.StringIO()):
            code3 = bench_chain.stage(ns3)
        self.assertEqual(code3, 2)


class TestSpecInfo(unittest.TestCase):
    def test_spec_info_parsing(self):
        """B: 題・頼みの種類・字下げ継続要件を抽出し、置換・維持節の REQ は除外する。"""
        text = """# 仕様：累積計算の追加機能
## 頼みの種類
機能追加

## 要件
- REQ-01：最初の要件
  字下げされた継続行です
- REQ-02：二つ目の要件

## 置き換える要件
- REQ-99：置換対象

## 保つ要件
- REQ-98：保つ対象
"""
        info = bench_chain.spec_info(text)
        self.assertEqual(info["title"], "累積計算の追加機能")
        self.assertEqual(info["kind"], "機能追加")
        self.assertEqual(len(info["reqs"]), 2)
        self.assertEqual(info["reqs"][0][0], "REQ-01")
        self.assertEqual(info["reqs"][0][1], "REQ-01：最初の要件 字下げされた継続行です")
        self.assertEqual(info["reqs"][1][0], "REQ-02")
        self.assertEqual(info["reqs"][1][1], "REQ-02：二つ目の要件")


class TestBenchChainCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        init_repo(self.repo)

    def test_s0_commit_structure_and_orphan(self):
        """C-1: base 木から package や judge を除き s0 木を足した親なしコミットを作る。"""
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "old.py").write_text("old", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_pkg_old.py").write_text("old test", encoding="utf-8")
        (self.repo / "tests" / "pkg_judge.py").write_text("judge", encoding="utf-8")
        (self.repo / "tests" / "test_other.py").write_text("other test", encoding="utf-8")
        (self.repo / "harness").mkdir()
        (self.repo / "harness" / "util.py").write_text("util", encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "read.md").write_text("doc", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        rev = git(self.repo, "rev-parse", "HEAD")

        tree_dir = Path(self.tmp.name) / "s0_tree"
        (tree_dir / "pkg").mkdir(parents=True)
        (tree_dir / "pkg" / "new.py").write_text("new", encoding="utf-8")
        (tree_dir / "tests").mkdir(parents=True)
        (tree_dir / "tests" / "test_pkg_base.py").write_text("base test", encoding="utf-8")

        sha = bench_chain.s0_commit(str(self.repo), rev, "B07", "pkg", tree_dir)
        parents = git(self.repo, "rev-list", "--parents", "-n", "1", sha).split()
        self.assertEqual(len(parents), 1)  # 親なし

        names = git(self.repo, "ls-tree", "-r", "--name-only", sha).splitlines()
        self.assertIn("pkg/new.py", names)
        self.assertIn("tests/test_pkg_base.py", names)
        self.assertIn("tests/test_other.py", names)
        self.assertIn("harness/util.py", names)
        self.assertIn("docs/read.md", names)
        self.assertNotIn("pkg/old.py", names)
        self.assertNotIn("tests/test_pkg_old.py", names)
        self.assertNotIn("tests/pkg_judge.py", names)

    def test_start_commit_structure_and_orphan(self):
        """C-2: 先端木に spec と台帳を足し archive を除いた親なしコミットを作る。"""
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "progress_archive.yaml").write_text("arch", encoding="utf-8")
        s1_spec_path = "docs/bench/B07/chain/s1/spec.md"
        (self.repo / Path(s1_spec_path)).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / Path(s1_spec_path)).write_text("s1", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tip")
        tip = git(self.repo, "rev-parse", "HEAD")

        info = {"title": "タイトル", "reqs": [("REQ-01", "REQ-01：要件1")]}
        files = ["pkg/mod.py"]
        rows = bench_chain.ledger_rows("pkg", 2, info, files)
        spec_bytes = b"# s2 spec\n"

        sha = bench_chain.start_commit(str(self.repo), tip, "B07", "pkg", 2, spec_bytes, rows)
        parents = git(self.repo, "rev-list", "--parents", "-n", "1", sha).split()
        self.assertEqual(len(parents), 1)  # 親なし

        names = git(self.repo, "ls-tree", "-r", "--name-only", sha).splitlines()
        self.assertIn("docs/bench/B07/chain/s1/spec.md", names)
        self.assertIn("docs/bench/B07/chain/s2/spec.md", names)
        self.assertIn(bench_start.LEDGER_PATH, names)
        self.assertNotIn("docs/progress_archive.yaml", names)

        ledger_content = git(self.repo, "show", f"{sha}:{bench_start.LEDGER_PATH}")
        self.assertIn("python -m unittest tests.test_pkg_s2", ledger_content)

    def test_changeable_files(self):
        """C-3: package と test_package の py 全部と新規試験ファイルのみが対象となる。"""
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "a.py").write_text("a", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_pkg_base.py").write_text("b", encoding="utf-8")
        (self.repo / "tests" / "test_other.py").write_text("o", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tip")
        tip = git(self.repo, "rev-parse", "HEAD")

        ch = bench_chain.changeable(str(self.repo), tip, "pkg", 1)
        self.assertEqual(sorted(ch), sorted(["pkg/a.py", "tests/test_pkg_base.py", "tests/test_pkg_s1.py"]))
        # 既に test_pkg_s1.py がある場合
        (self.repo / "tests" / "test_pkg_s1.py").write_text("s1", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tip2")
        tip2 = git(self.repo, "rev-parse", "HEAD")
        ch2 = bench_chain.changeable(str(self.repo), tip2, "pkg", 1)
        self.assertEqual(len(ch2), len(set(ch2)))

    def test_write_plan_files_and_hashes(self):
        """C-4: 計画ファイル群を書き、指紋が一致し欠落がない。"""
        plan_dir = Path(self.tmp.name) / "plan"
        info = {"title": "題", "kind": "新機能", "reqs": [("REQ-01", "REQ-01：要件1")]}
        bench_chain.write_plan(plan_dir, "B07", "pkg", 1, info, ["pkg/mod.py"])
        self.assertEqual(bench_run.frozen_missing(plan_dir), [])

        p01 = (plan_dir / "P-01.md").read_text(encoding="utf-8")
        self.assertIn("タスク: P-01", p01)
        self.assertIn("要件: REQ-01", p01)

        planner = bench_run.read_json(plan_dir / "planner.json", {})
        self.assertEqual(planner["files"]["P-01.md"], bench_run.sha256_file(plan_dir / "P-01.md"))
        self.assertEqual(planner["files"]["ledger.yaml"], bench_run.sha256_file(plan_dir / "ledger.yaml"))


class TestBenchChainRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        init_repo(self.repo)
        (self.repo / "base.txt").write_text("base", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base_rev = git(self.repo, "rev-parse", "HEAD")

        self.staged = self.root / "staged"
        self.out = self.root / "out"
        self.staged.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self._setup_staged()

    def _setup_staged(self):
        tree = self.staged / "B07" / "s0" / "tree"
        (tree / "pkg").mkdir(parents=True, exist_ok=True)
        (tree / "tests").mkdir(parents=True, exist_ok=True)
        (tree / "pkg" / "mod.py").write_text("val = 1\n", encoding="utf-8")
        (tree / "tests" / "test_pkg_base.py").write_text("pass\n", encoding="utf-8")

        for k in (1, 2):
            s_dir = self.staged / "B07" / f"s{k}"
            s_dir.mkdir(parents=True, exist_ok=True)
            (s_dir / "spec.md").write_text(f"# 仕様：s{k}\n## 頼みの種類\n追加\n## 要件\n- REQ-01：要件\n", encoding="utf-8")

        chain_json = {
            "tag": "t1", "set": "dev",
            "bases": [{
                "base": "B07", "package": "pkg", "stages": 2,
                "spec_sha256": {
                    "s1": bench_run.sha256_file(self.staged / "B07" / "s1" / "spec.md"),
                    "s2": bench_run.sha256_file(self.staged / "B07" / "s2" / "spec.md"),
                }
            }]
        }
        hline_base.write_json(self.staged / "chain.json", chain_json)

    def _fake_run_stage(self, f, invalid=None):
        rundir = bench_run.run_dir(f.out, f.task, f.run_id)
        (rundir / "factory").mkdir(parents=True, exist_ok=True)
        (rundir / "git").mkdir(parents=True, exist_ok=True)

        git(f.repo, "checkout", "-q", f.start_sha)
        new_file = Path(f.repo) / "pkg" / f"file_{f.run_id}.py"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("print(1)\n", encoding="utf-8")
        git(f.repo, "add", "-A")
        git(f.repo, "commit", "-q", "-m", f"stage {f.run_id}")
        tip_sha = git(f.repo, "rev-parse", "HEAD")
        git(f.repo, "update-ref", "refs/heads/tip", tip_sha)
        bundle_file = rundir / "git" / "run.bundle"
        git(f.repo, "bundle", "create", str(bundle_file), "refs/heads/tip")

        run_json = {
            "head": tip_sha, "start": f.start_sha,
            "invalid": invalid, "reason": "none", "stop": "done",
            "tries": 1, "tokens": {"total": 100}
        }
        hline_base.write_json(rundir / "factory" / "run.json", run_json)
        return 0

    def test_run_advances_tip_and_records(self):
        """D-1: 段 1 の変更が段 2 の開始コミットに入り、記録ファイルが正しく作られる。"""
        captured_starts = []

        def mock_run_stage(f):
            captured_starts.append((f.run_id, f.start_sha))
            return self._fake_run_stage(f)

        ns = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch.object(bench_chain, "run_stage", side_effect=mock_run_stage), \
             patch.object(bench_chain.bench_run, "version_problem", return_value=None):
            code = bench_chain.run(ns)
        self.assertEqual(code, 0)

        # 段 2 の開始コミットに段 1 のファイルが含まれること（巻き戻らない）
        s2_start = captured_starts[1][1]
        s2_names = git(self.repo, "ls-tree", "-r", "--name-only", s2_start).splitlines()
        self.assertIn("pkg/file_t1-s1.py", s2_names)

        rec = bench_run.read_json(self.out / "chains" / "t1" / "B07.json", {})
        self.assertEqual(len(rec["stages"]), 2)
        self.assertEqual(rec["stages"][0]["run_id"], "t1-s1")
        self.assertEqual(rec["stages"][1]["run_id"], "t1-s2")

    def test_run_retries_on_invalid_and_stops(self):
        """D-2: 走りが無効な場合は RETRIES 回まで再試行し、停止して stopped を記録する。"""
        attempts = []

        def mock_run_stage(f):
            attempts.append(f.run_id)
            return self._fake_run_stage(f, invalid="REQ-22")

        ns = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch.object(bench_chain, "run_stage", side_effect=mock_run_stage), \
             patch.object(bench_chain.bench_run, "version_problem", return_value=None):
            code = bench_chain.run(ns)
        self.assertEqual(code, 0)

        self.assertEqual(attempts, ["t1-s1", "t1-s1-r1", "t1-s1-r2"])
        rec = bench_run.read_json(self.out / "chains" / "t1" / "B07.json", {})
        self.assertEqual(rec["stopped"], "s1")

    def test_run_skips_existing_records(self):
        """D-3: 既に記録ファイルが存在する土台はスキップする。"""
        rec_path = self.out / "chains" / "t1" / "B07.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(rec_path, {"done": True})

        called = []
        ns = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch.object(bench_chain, "run_stage", side_effect=lambda f: called.append(True)), \
             patch.object(bench_chain.bench_run, "version_problem", return_value=None):
            code = bench_chain.run(ns)
        self.assertEqual(code, 0)
        self.assertEqual(len(called), 0)

    def test_run_validation_errors(self):
        """D-4: tag 不一致、spec 指紋不一致、version_problem で終了コード 2 となる。"""
        ns_tag = argparse.Namespace(tag="wrong_tag", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(bench_chain.run(ns_tag), 2)

        ns_ver = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch.object(bench_chain.bench_run, "version_problem", return_value="dirty"), patch("sys.stderr", io.StringIO()):
            self.assertEqual(bench_chain.run(ns_ver), 2)

        # spec 指紋不一致
        (self.staged / "B07" / "s1" / "spec.md").write_text("tampered", encoding="utf-8")
        ns_sha = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.base_rev, staged=str(self.staged), out=str(self.out), implementer=None, sessions=None)
        with patch.object(bench_chain.bench_run, "version_problem", return_value=None), patch("sys.stderr", io.StringIO()):
            self.assertEqual(bench_chain.run(ns_sha), 2)


class TestChainScoreStrays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name) / "local"
        self.own = self.local / "work" / "task" / "run"
        self.other = self.local / "other"
        self.outside = Path(self.tmp.name) / "outside"
        for p in (self.own, self.other, self.outside):
            p.mkdir(parents=True, exist_ok=True)

    def _make_log(self, name, tool, params, cwd=None):
        log_file = self.own / name
        summary = json.dumps({"summary": 1})
        init_event = json.dumps({"event": "init", "init": {"cwd": str(cwd or self.own)}})
        step_event = json.dumps({
            "event": "step_update",
            "step_update": {
                "step_index": 1, "state": "DONE", "step_type": "tool",
                "tool_info": {"name": tool, "parameters": params, "output": "ok"}
            }
        })
        content = f"{summary}{bench_run.LOG_SPLIT}{init_event}\n{step_event}\n{bench_run.LOG_SPLIT}stderr\n"
        log_file.write_text(content, encoding="utf-8")
        return log_file

    def test_strays_inside_own_and_outside_local(self):
        """E-1: own 内のパスおよび local 外のパスは当たらない。"""
        log1 = self._make_log("log1.log", "view_file", {"AbsolutePath": str(self.own / "file.py")})
        log2 = self._make_log("log2.log", "view_file", {"AbsolutePath": str(self.outside / "file.py")})
        hits = chain_score.strays([log1, log2], self.own, self.local)
        self.assertEqual(hits, [])

    def test_strays_inside_local_outside_own(self):
        """E-2: local 配下かつ own 外のパスは入れ子の dict や list 内にあっても当たる。"""
        target = str(self.other / "secret.txt")
        params = {"opts": [{"path": target}]}
        log = self._make_log("stray.log", "view_file", params)
        hits = chain_score.strays([log], self.own, self.local)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "stray.log")
        self.assertEqual(hits[0][1], "view_file")

    def test_strays_relative_parent_path_from_cwd(self):
        """E-3: init の cwd から .. で own の外に出る相対パスは当たる。"""
        sub_own = self.own / "sub"
        sub_own.mkdir()
        log = self._make_log("rel.log", "run_cmd", {"cmd": "../../other/file.txt"}, cwd=sub_own)
        hits = chain_score.strays([log], self.own, self.local)
        self.assertEqual(len(hits), 1)

    def test_strays_network_tools(self):
        """E-4: 名前に browser・_url・web を含む道具は引数によらず当たる。"""
        log1 = self._make_log("l1.log", "browser_click", {})
        log2 = self._make_log("l2.log", "read_url_content", {})
        log3 = self._make_log("l3.log", "web_search", {})
        hits = chain_score.strays([log1, log2, log3], self.own, self.local)
        self.assertEqual(len(hits), 3)
        for h in hits:
            self.assertEqual(h[2], "ネットを見る道具")


class TestGateFailures(unittest.TestCase):
    def test_gate_failures_extraction(self):
        """F: FAIL・ERROR の見出しと例外行を抽出し、サマリ行は除外して複数組を返す。"""
        text = """検証コマンド `python -m unittest tests.test_x` が終了コード 1
FAIL: test_one (tests.test_x.T)
Traceback (most recent call last):
  File "x.py", line 10, in test_one
    self.assertEqual(1, 2)
AssertionError: 1 != 2

ERROR: test_two (tests.test_x.T)
Traceback (most recent call last):
  File "x.py", line 17, in test_two
    f(0)
ValueError: steps は 1 以上

----------------------------------------------------------------------
Ran 3 tests in 0.005s

FAILED (failures=1, errors=1)
"""
        res = chain_score.gate_failures(text)
        expected = [
            ("FAIL: test_one (tests.test_x.T)", "AssertionError: 1 != 2"),
            ("ERROR: test_two (tests.test_x.T)", "ValueError: steps は 1 以上"),
        ]
        self.assertEqual(res, expected)


class TestChainScoreGrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        (self.tree / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    def test_grade_pass_fail_error(self):
        """G-1: 通る試験は ok、assert 失敗は FAIL、例外は ERROR で始まる。"""
        test_file = self.root / "test_calc.py"
        test_file.write_text("""import unittest
from calc import add
class T(unittest.TestCase):
    def test_ok(self): self.assertEqual(add(1, 2), 3)
    def test_fail(self): self.assertEqual(add(1, 2), 4)
    def test_error(self): raise RuntimeError("crash")
""", encoding="utf-8")
        res = chain_score.grade(self.tree, [test_file], gone=set())
        keys = {k.split(".")[-1]: v for k, v in res.items()}
        self.assertEqual(keys["test_ok"], "ok")
        self.assertTrue(keys["test_fail"].startswith("FAIL:"))
        self.assertTrue(keys["test_error"].startswith("ERROR:"))

    def test_grade_subtest_failure(self):
        """G-2: subTest の 1 つが落ちる試験は ok にならず、結果件数は試験の数となる。"""
        test_file = self.root / "test_sub.py"
        test_file.write_text("""import unittest
class T(unittest.TestCase):
    def test_sub(self):
        for i in range(3):
            with self.subTest(i=i):
                self.assertNotEqual(i, 1)
""", encoding="utf-8")
        res = chain_score.grade(self.tree, [test_file], gone=set())
        self.assertEqual(len(res), 1)
        val = list(res.values())[0]
        self.assertTrue(val.startswith("FAIL"))

    def test_grade_import_error(self):
        """G-3: import で落ちるテストファイルの試験は ok にならず結果に含まれる。"""
        test_file = self.root / "test_broken.py"
        test_file.write_text("import non_existent_pkg_12345\n", encoding="utf-8")
        res = chain_score.grade(self.tree, [test_file], gone=set())
        self.assertGreater(len(res), 0)
        for v in res.values():
            self.assertNotEqual(v, "ok")

    def test_grade_gone_exclusion(self):
        """G-4: gone にある関数名の試験は結果から除外される。"""
        test_file = self.root / "test_gone.py"
        test_file.write_text("""import unittest
class T(unittest.TestCase):
    def test_stay(self): pass
    def test_del(self): pass
""", encoding="utf-8")
        res = chain_score.grade(self.tree, [test_file], gone={"test_del"})
        names = [k.split(".")[-1] for k in res]
        self.assertIn("test_stay", names)
        self.assertNotIn("test_del", names)


class TestChainScoreFinishChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chains = self.root / "chains"
        self.out = self.root / "out"
        self.priv = self.root / "priv"
        for p in (self.chains, self.out, self.priv):
            p.mkdir(parents=True, exist_ok=True)
        self.cfg = {"out": str(self.out), "oracles": str(self.chains.parent), "inbox": str(self.root / "inbox")}
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        init_repo(self.repo)

        self._setup_chains()

    def _setup_chains(self):
        src = self.chains / "B07"
        s0_tree = src / "s0" / "tree"
        (s0_tree / "pkg").mkdir(parents=True, exist_ok=True)
        (s0_tree / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        (s0_tree / "tests").mkdir(parents=True, exist_ok=True)

        (src / "s0" / "hidden").mkdir(parents=True, exist_ok=True)
        (src / "s0" / "hidden" / "test_hidden_B07.py").write_text("""import unittest
from pkg.mod import f
class T(unittest.TestCase):
    def test_s0(self): self.assertEqual(f(), 1)
""", encoding="utf-8")

        for k in (1, 2):
            s_dir = src / f"s{k}"
            (s_dir / "hidden").mkdir(parents=True, exist_ok=True)
            (s_dir / "hidden" / f"test_chain_B07_s{k}.py").write_text(f"""import unittest
from pkg.mod import f
class T(unittest.TestCase):
    def test_s{k}(self): self.assertEqual(f(), {k + 1})
""", encoding="utf-8")
            hline_base.write_json(s_dir / "replaced.json", [])

    def _make_stage_run(self, run_id, k, return_val=2, extra_file=None):
        rundir = bench_run.run_dir(self.out, "B07", run_id)
        (rundir / "factory").mkdir(parents=True, exist_ok=True)
        (rundir / "git").mkdir(parents=True, exist_ok=True)
        (rundir / "items").mkdir(parents=True, exist_ok=True)

        start_sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", start_sha)
        (self.repo / "pkg" / "mod.py").write_text(f"def f(): return {return_val}\n", encoding="utf-8")
        if extra_file:
            p = self.repo / extra_file[0]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(extra_file[1])
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"tip {k}")
        tip_sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/heads/tip", tip_sha)
        bundle_file = rundir / "git" / "run.bundle"
        git(self.repo, "bundle", "create", str(bundle_file), "refs/heads/tip")

        hline_base.write_json(rundir / "factory" / "run.json", {
            "head": tip_sha, "start": start_sha, "invalid": None, "stop": "done", "tries": 1, "tokens": {"total": 50}
        })
        return {"k": k, "attempt": 0, "run_id": run_id, "start": start_sha, "head": tip_sha, "invalid": None}

    def test_finish_chain_stages_structure_and_no_test_names(self):
        """H-1: stages に new/old/kept の通過数と総数が入り、JSON に試験名が含まれない。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        st1 = self._make_stage_run("t1-s1", 1, return_val=2)
        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1]}

        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            res = chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        self.assertEqual(len(res["stages"]), 1)
        row = res["stages"][0]
        self.assertIsInstance(row["new"], list)
        self.assertIsInstance(row["old"], list)
        self.assertIsInstance(row["kept"], list)
        self.assertEqual(len(row["new"]), 2)

        raw_json = json.dumps(res)
        self.assertNotIn("test_s0", raw_json)
        self.assertNotIn("test_s1", raw_json)

    def test_finish_chain_kept_denominator_condition(self):
        """H-2: 保ちの分母は前の段の先端で通っていた前の必須試験のみとなる。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        # 段1: f() は 2 を返す（s0 テストは落ち、s1 テストは通る）
        st1 = self._make_stage_run("t1-s1", 1, return_val=2)
        # 段2: f() は 3 を返す
        st2 = self._make_stage_run("t1-s2", 2, return_val=3)

        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1, st2]}
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            res = chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        s2_row = res["stages"][1]
        # 段1の先端で s0 は落ちていたので、段2の kept の分母には入らない（分母は s1 のみの 1 件）
        self.assertEqual(s2_row["kept"][1], 1)

    def test_finish_chain_boundary_violation_zeroes_score(self):
        """H-3: 許可範囲外のファイルを変更した段はスコアが 0 点となり violations >= 1。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        st1 = self._make_stage_run("t1-s1", 1, return_val=2, extra_file=("docs/other.md", b"bad\n"))
        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1]}

        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            res = chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        row = res["stages"][0]
        self.assertGreaterEqual(row["violations"], 1)
        self.assertEqual(row["new"][0], 0)
        self.assertEqual(row["old"][0], 0)
        self.assertEqual(row["kept"][0], 0)

    def test_finish_chain_canary_leak_and_tainted(self):
        """H-4: canary 漏れのある段は invalid が REQ-11 となり、後の段は tainted となる。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        st1 = self._make_stage_run("t1-s1", 1, return_val=2)
        st2 = self._make_stage_run("t1-s2", 2, return_val=3)

        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1, st2]}

        # 段1の rundir に leaks を擬似発生
        def mock_leaks(cfg, dirs):
            if any("t1-s1" in str(d) for d in dirs):
                return [("leak.txt", 1)]
            return []

        with patch.object(chain_score.bench_guard, "leaks", side_effect=mock_leaks), \
             patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            res = chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        self.assertEqual(res["stages"][0]["invalid"], "REQ-11")
        self.assertEqual(res["stages"][1]["invalid"], "tainted")

    def test_finish_chain_priv_output_locations(self):
        """H-5: 試験ごとの詳細結果と索引が priv に書かれ、set に応じて配置先が変わる。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        st1 = self._make_stage_run("t1-s1", 1, return_val=2)
        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1]}

        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        self.assertTrue((self.priv / "s1.json").exists())
        self.assertTrue((self.priv / "s1.index.md").exists())

        # finish コマンド経由での set 別 priv 判定
        (self.repo / "config").mkdir(parents=True, exist_ok=True)
        hline_base.write_json(self.repo / "config" / "hline.json", self.cfg)
        (self.out / "chains" / "t1").mkdir(parents=True, exist_ok=True)
        hline_base.write_json(self.out / "chains" / "t1" / "B07.json", rec)

        ns = argparse.Namespace(tag="t1", repo=str(self.repo), chains=str(self.chains), out=str(self.out))
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            code = chain_score.finish(ns)
        self.assertEqual(code, 0)
        # dev の場合は out/chains/<tag>/<base>/ に置かれる
        self.assertTrue((self.out / "chains" / "t1" / "B07" / "s1.json").exists())

        # held の場合は chains/runs/<tag>/<base>/ に置かれる
        rec_held = {"tag": "t2", "set": "held", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1]}
        (self.out / "chains" / "t2").mkdir(parents=True, exist_ok=True)
        hline_base.write_json(self.out / "chains" / "t2" / "B07.json", rec_held)
        ns_held = argparse.Namespace(tag="t2", repo=str(self.repo), chains=str(self.chains), out=str(self.out))
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            code_held = chain_score.finish(ns_held)
        self.assertEqual(code_held, 0)
        self.assertTrue((self.chains / "runs" / "t2" / "B07" / "s1.json").exists())

    def test_finish_chain_stops_on_run_invalid(self):
        """H-6: 記録の段が走りで invalid の場合、そこで集計を止め後の段は含まれない。"""
        (self.repo / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

        st1 = self._make_stage_run("t1-s1", 1, return_val=2)
        st1["invalid"] = "REQ-22"
        st2 = self._make_stage_run("t1-s2", 2, return_val=3)

        rec = {"tag": "t1", "set": "dev", "base": "B07", "package": "pkg", "rev": "HEAD", "stages": [st1, st2]}
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            res = chain_score.finish_chain(rec, self.out, self.chains, self.cfg, self.priv)

        self.assertEqual(len(res["stages"]), 1)
        self.assertEqual(res["stages"][0]["run_id"], "t1-s1")
        self.assertEqual(res["stages"][0]["invalid"], "REQ-22")


if __name__ == "__main__":
    unittest.main()
