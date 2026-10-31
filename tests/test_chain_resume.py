"""鎖の走らせ役の 2 つの直し（10-31、L4 の前）。
- 段の台帳の行に purpose があり、実装役の頼みに台帳の節（要件の本文と検証のコマンド）が入ること。
- 中断の後に走らせ直すと、書き終えた段の走りは使い回し、run.json の無い残りは中断した試しとして数えて次へ進むこと。"""
import argparse
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
import hline_base  # noqa: E402
import hline_prompt  # noqa: E402
import yaml  # noqa: E402

SPEC = "# 仕様：速さの上限\n\n## 頼みの種類\n\n「機能を足す」。坂で速さを保つ。\n\n## 要件\n\n- REQ-09：速さを返す\n  字下げの続き\n- REQ-10：上限で止める\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


class TestLedgerSection(unittest.TestCase):
    def test_prompt_gets_ledger_section(self):
        """段の台帳の行で作った docs/progress.yaml と What から、実装役の頼みの台帳の節が組める。"""
        info = bench_chain.spec_info(SPEC)
        files = ["pkg/__init__.py", "tests/test_pkg_s1.py"]
        rows = bench_chain.ledger_rows("pkg", 1, info, files)
        self.assertEqual(rows[0]["purpose"], "「機能を足す」。坂で速さを保つ。")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs").mkdir()
            (Path(tmp) / "docs" / "progress.yaml").write_text(
                yaml.safe_dump(bench_start.ledger_state(rows, "pkg"), allow_unicode=True, sort_keys=False), encoding="utf-8")
            text = hline_prompt.ledger_section(tmp, bench_chain.what_text("B07", "pkg", 1, info, files))
        self.assertTrue(text.startswith("## 台帳のタスク（P-01）"), text)
        self.assertIn("- 目的: 「機能を足す」。坂で速さを保つ。", text)
        self.assertIn("  - REQ-09：速さを返す 字下げの続き", text)
        self.assertIn("  - REQ-10：上限で止める", text)
        self.assertIn("- 検証コマンド: python -m unittest tests.test_pkg_s1", text)

    def test_purpose_falls_back_to_title(self):
        """頼みの種類の節が無い仕様でも、purpose は空にならない（題を使う）。"""
        info = bench_chain.spec_info("# 仕様：題だけ\n\n## 要件\n\n- REQ-01：ある\n")
        self.assertEqual(bench_chain.ledger_rows("pkg", 1, info, [])[0]["purpose"], "題だけ")


class TestResume(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.staged, self.out = root / "repo", root / "staged", root / "out"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.com")
        (self.repo / "base.txt").write_text("base", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.rev = git(self.repo, "rev-parse", "HEAD")
        tree = self.staged / "B07" / "s0" / "tree"
        (tree / "pkg").mkdir(parents=True)
        (tree / "tests").mkdir()
        (tree / "pkg" / "__init__.py").write_text("val = 1\n", encoding="utf-8")
        (tree / "tests" / "test_pkg_base.py").write_text("pass\n", encoding="utf-8")
        sha = {}
        for k in (1, 2, 3):
            (self.staged / "B07" / f"s{k}").mkdir(parents=True)
            p = self.staged / "B07" / f"s{k}" / "spec.md"
            p.write_text(SPEC.replace("速さの上限", f"段 {k}"), encoding="utf-8")
            sha[f"s{k}"] = bench_run.sha256_file(p)
        hline_base.write_json(self.staged / "chain.json", {"tag": "t1", "set": "dev", "bases": [
            {"base": "B07", "package": "pkg", "stages": 3, "spec_sha256": sha}]})
        self.calls = []

    def fake_run_stage(self, f):
        """本物の走りの代わり：開始のコミットの上に 1 つコミットし、先端の bundle と run.json を書く。"""
        self.calls.append(f.run_id)
        rundir = bench_run.run_dir(f.out, f.task, f.run_id)
        (rundir / "git").mkdir(parents=True)
        (rundir / "factory").mkdir()
        git(f.repo, "checkout", "-q", "--detach", f.start_sha)
        (Path(f.repo) / "pkg" / f"{f.run_id.replace('-', '_')}.py").write_text("x = 1\n", encoding="utf-8")
        git(f.repo, "add", "-A")
        git(f.repo, "commit", "-q", "-m", f.run_id)
        head = git(f.repo, "rev-parse", "HEAD")
        git(f.repo, "update-ref", "refs/heads/tip", head)
        git(f.repo, "bundle", "create", "-q", str(rundir / "git" / "run.bundle"), "refs/heads/tip")
        hline_base.write_json(rundir / "factory" / "run.json", {"base": f.rev, "start": f.start_sha, "head": head,
                                                                "invalid": None, "stop": "done", "tries": 1,
                                                                "tokens": {"total": 10}})
        return 0

    def run_chain(self, fake):
        ns = argparse.Namespace(tag="t1", repo=str(self.repo), base=self.rev, staged=str(self.staged), out=str(self.out),
                                implementer=None, sessions=None)
        with patch.object(bench_chain, "run_stage", side_effect=fake), \
                patch.object(bench_chain.bench_run, "version_problem", return_value=None):
            self.assertEqual(bench_chain.run(ns), 0)
        return bench_run.read_json(self.out / "chains" / "t1" / "B07.json", {})

    def interrupted(self, f):
        """段 1 は書き終え、段 2 の途中で落ちた前の起動（走りのディレクトリだけが残り、run.json が無い）。"""
        if f.run_id == "t1-s1":
            return self.fake_run_stage(f)
        bench_run.run_dir(f.out, f.task, f.run_id).mkdir(parents=True)
        raise KeyboardInterrupt

    def test_rerun_reuses_finished_stage_and_skips_leftover(self):
        """走らせ直すと、段 1 は走らせずに前の記録を使い、段 2 の残りは中断の試しとして数え、段 2 の r1 と段 3 を走らせる。"""
        with self.assertRaises(KeyboardInterrupt):
            self.run_chain(self.interrupted)
        first = bench_run.read_json(bench_run.run_dir(self.out, "B07", "t1-s1") / "factory" / "run.json", {})
        self.calls = []
        rec = self.run_chain(self.fake_run_stage)
        self.assertEqual(self.calls, ["t1-s2-r1", "t1-s3"])
        got = [(s["run_id"], s["invalid"], s["resumed"]) for s in rec["stages"]]
        self.assertEqual(got, [("t1-s1", None, True), ("t1-s2", "ABORT", True), ("t1-s2-r1", None, False), ("t1-s3", None, False)])
        s1, s2 = rec["stages"][0], rec["stages"][1]
        self.assertEqual((s1["start"], s1["head"], s1["base"]), (first["start"], first["head"], first["base"]))
        self.assertIn("中断", s2["reason"])
        self.assertIsNone(rec["stopped"])
        names = git(self.repo, "ls-tree", "-r", "--name-only", rec["stages"][2]["start"]).splitlines()
        self.assertIn("pkg/t1_s1.py", names)   # 段 2 の r1 は、使い回した段 1 の先端から始まる

    def test_fresh_run_marks_nothing_resumed(self):
        """残りの無い初めての起動では、どの段も resumed が False で、3 段を 1 回ずつ走らせる。"""
        rec = self.run_chain(self.fake_run_stage)
        self.assertEqual(self.calls, ["t1-s1", "t1-s2", "t1-s3"])
        self.assertEqual([s["resumed"] for s in rec["stages"]], [False, False, False])


if __name__ == "__main__":
    unittest.main()
