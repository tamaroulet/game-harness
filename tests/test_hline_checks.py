"""report.md の「## 照合」節の検査：4 種類の合格と不合格・宣言の誤り・結果の再利用・例外でも report.md が書かれること。
偽の runner だけを使い、本物の git・gh は呼ばない。"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_checks as hc  # noqa: E402
import hline_report  # noqa: E402

SHA = "abcdef1234567890abcdef1234567890abcdef12"


class Runner:
    """git rev-parse は SHA を返し、worktree の作成・削除は成功する。それ以外は codes[引数の要点] の終了コードで返す。"""

    def __init__(self, code=0, out="", log="", resolve=0):
        self.calls, self.code, self.out, self.log, self.resolve = [], code, out, log, resolve

    def __call__(self, args, cwd, ttl, label):
        self.calls.append((list(args), cwd))
        if args[:2] == ["git", "rev-parse"]:
            return (0, SHA + "\n", "") if self.resolve == 0 else (self.resolve, "", "bad ref")
        if args[:3] == ["git", "worktree", "add"] or args[:3] == ["git", "worktree", "remove"]:
            return 0, "", ""
        if args[:2] == ["git", "log"]:
            return 0, self.log, ""
        return self.code, self.out, ""

    def ran(self):
        return [a for a, _ in self.calls if a[0] not in ("git",)]


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        (self.inbox / "checks").mkdir(parents=True)
        self.out.mkdir()
        self.cfg = {"inbox": str(self.inbox), "out": str(self.out), "base": "origin/main",
                    "integration_branch": "hline/integration", "ttl_seconds": {"git": 5, "gate": 5}}

    def declare(self, name, **decl):
        (self.inbox / "checks" / f"{name}.json").write_text(json.dumps(decl), encoding="utf-8")

    def make(self, runner, clock=1_000_000.0):
        return hc.section(self.cfg, runner=runner, clock=clock)

    def line(self, text, name):
        return next(x for x in text.splitlines() if x.startswith(f"- {name}:"))

    def test_no_checks_directory_or_an_empty_one_says_none(self):
        self.assertEqual(self.make(Runner()), "## 照合\n宣言なし\n")
        shutil.rmtree(self.inbox / "checks")
        self.assertEqual(self.make(Runner()), "## 照合\n宣言なし\n")

    def test_each_kind_passes_and_fails_by_exit_code(self):
        decls = {"cov": dict(kind="reqcov", spec="docs/spec.md", tests="tests/test_*.py", whats="*.md"),
                 "prog": dict(kind="progress_check"), "unit": dict(kind="unittest", modules=["tests.test_x"])}
        for name, d in decls.items():
            self.declare(name, **d)
        ok = self.make(Runner(code=0, out="ok\n"))
        for name, d in decls.items():
            self.assertRegex(self.line(ok, name), rf"^- {name}: 合格（{d['kind']}／abcdef1／0／")
        (self.out / hc.CACHE_NAME).unlink()
        ng = self.make(Runner(code=1, out="l1\nl2\nl3\nl4\nl5\nl6\n"))
        for name, d in decls.items():
            self.assertRegex(self.line(ng, name), rf"^- {name}: 不合格（{d['kind']}／abcdef1／1／")
        self.assertIn("    l5", ng)
        self.assertNotIn("l6", ng)   # 出力の先頭 5 行だけ

    def test_git_log_counts_commits_touching_the_paths(self):
        self.declare("log", kind="git_log", paths=["harness/a.py", "docs"], since="1234567", expect_commits=2)
        r = Runner(log="aaa1111\nbbb2222\n")
        self.assertRegex(self.line(self.make(r), "log"), r"^- log: 合格（git_log／abcdef1／0／")
        args = next(a for a, _ in r.calls if a[:2] == ["git", "log"])
        self.assertIn(f"1234567..{SHA}", args)
        self.assertEqual(args[args.index("--") + 1:], ["harness/a.py", "docs"])
        self.declare("log", kind="git_log", paths=["harness/a.py"], expect_commits=3)
        self.assertRegex(self.line(self.make(Runner(log="aaa1111\nbbb2222\n")), "log"), r"^- log: 不合格")

    def test_commands_run_in_a_detached_tree_without_a_shell(self):
        self.declare("unit", kind="unittest", modules=["tests.test_x", "tests.test_y"])
        r = Runner()
        self.make(r)
        add = next(a for a, _ in r.calls if a[:3] == ["git", "worktree", "add"])
        self.assertIn("--detach", add)
        self.assertEqual(add[-1], SHA)
        cmd, cwd = next((a, c) for a, c in r.calls if a[1:3] == ["-m", "unittest"])
        self.assertEqual(cmd[3:], ["tests.test_x", "tests.test_y"])
        self.assertEqual(str(cwd), add[add.index("--detach") + 1])
        self.assertNotEqual(Path(cwd).resolve(), ROOT.resolve())
        self.assertTrue(any(a[:3] == ["git", "worktree", "remove"] for a, _ in r.calls))
        self.assertFalse(Path(cwd).exists())
        for a, _ in r.calls:
            self.assertIsInstance(a, list)   # 文字列（シェル行）では呼ばない

    def test_whats_globs_are_under_the_queue_of_out(self):
        self.declare("cov", kind="reqcov", spec="docs/spec.md", tests=["t/*.py"], whats="*.md")
        r = Runner()
        self.make(r)
        cmd = next(a for a in r.ran() if "harness.reqcov" in a)
        self.assertEqual(cmd[cmd.index("--whats") + 1], str(self.out / "queue" / "*.md"))

    def test_malformed_declarations_are_not_run(self):
        bad = {"kind-unknown": dict(kind="shell", cmd="ls"),
               "dotdot": dict(kind="reqcov", spec="../x.md", tests="tests/*.py"),
               "drive": dict(kind="git_log", paths=["C:/x"], expect_commits=1),
               "bad-ref": dict(kind="progress_check", ref="origin/other"),
               "flag-ref": dict(kind="progress_check", ref="--output=x"),
               "bad-module": dict(kind="unittest", modules=["harness.x"]),
               "bad-count": dict(kind="git_log", paths=["a"], expect_commits="1")}
        for name, d in bad.items():
            self.declare(name, **d)
        (self.inbox / "checks" / "broken.json").write_text("{", encoding="utf-8")
        r = Runner()
        text = self.make(r)
        for name in (*bad, "broken"):
            self.assertRegex(self.line(text, name), rf"^- {name}: 宣言の誤り（")
        self.assertEqual(text.count("宣言の誤り：") + text.count("宣言の誤り（"), 2 * (len(bad) + 1))
        self.assertEqual(r.calls, [])
        self.assertIn("未知の kind", text)
        self.assertIn("ref に許されない値", text)

    def test_allowed_refs_are_accepted(self):
        for i, ref in enumerate(("origin/main", "origin/hline/integration", "abc1234", SHA)):
            self.declare(f"r{i}", kind="progress_check", ref=ref)
        r = Runner()
        text = self.make(r)
        self.assertEqual(len(re.findall(r"合格（progress_check", text)), 4)
        revs = [a[-1] for a, _ in r.calls if a[:2] == ["git", "rev-parse"]]
        self.assertEqual([x[:-9] for x in revs], ["origin/main", "origin/hline/integration", "abc1234", SHA])

    def test_unresolvable_ref_is_a_fetch_failure(self):
        self.declare("unit", kind="unittest", modules=["tests.test_x"])
        r = Runner(resolve=128)
        text = self.make(r)
        self.assertRegex(self.line(text, "unit"), r"^- unit: 取得失敗（unittest／-／-／")
        self.assertEqual(r.ran(), [])

    def test_same_declaration_and_sha_reuses_the_previous_result(self):
        self.declare("unit", kind="unittest", modules=["tests.test_x"])
        first = self.make(Runner(code=1, out="boom\n"), clock=1_000_000.0)
        r = Runner(code=0)
        again = self.make(r, clock=1_000_000.0 + 3600)
        self.assertEqual(r.ran(), [])
        self.assertEqual(first, again)   # 結果も取得時刻も前回のまま
        self.declare("unit", kind="unittest", modules=["tests.test_x", "tests.test_y"])   # 中身が変われば走らせ直す
        r2 = Runner(code=0)
        self.assertIn("合格", self.line(self.make(r2, clock=1_000_000.0 + 7200), "unit"))
        self.assertEqual(len(r2.ran()), 1)

    def test_a_raising_runner_does_not_raise(self):
        self.declare("unit", kind="unittest", modules=["tests.test_x"])

        def boom(*a):
            raise OSError("no git")

        text = hc.safe_section(self.cfg, runner=boom, clock=5.0)
        self.assertIn("## 照合", text)
        self.assertIn("取得失敗", text)

    def test_a_raising_section_still_writes_report_md(self):
        self.declare("unit", kind="unittest", modules=["tests.test_x"])
        with mock.patch.object(hc, "section", side_effect=RuntimeError("boom")), \
                mock.patch.object(hline_report, "progress_report", return_value="P\n"), \
                mock.patch.object(hline_report, "h_section", return_value="## H ライン\n"), \
                mock.patch.object(hline_report.hline_probe, "safe_section", return_value="## probe\n"):
            hline_report.write_report(self.cfg, {"items": {}, "awaiting_pr": None})
        text = (self.inbox / "report.md").read_text(encoding="utf-8")
        self.assertIn("## probe\n", text)
        self.assertLess(text.index("## probe"), text.index("## 照合"))
        self.assertIn("RuntimeError: boom", text)

    def test_no_shell_is_used(self):
        for mod in (hc, ):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertNotIn("shell=True", src)
            self.assertNotIn("os.system", src)
            self.assertNotIn("subprocess", src)


if __name__ == "__main__":
    unittest.main()
