"""照合の宣言の overlay・repeat・expect の検査：重ねたファイルが命令の前に木にあること・drafts/ の外と無いファイルと
git_log での誤り・repeat の数え方・expect: fail・写し元を変えると走らせ直すこと。偽の runner だけを使い、本物の git は呼ばない。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_checks as hc  # noqa: E402

SHA = "abcdef1234567890abcdef1234567890abcdef12"


class Runner:
    """git は成功させる。それ以外の命令は codes を順に返し（尽きたら最後の値）、その時点の木の指定ファイルの中身を記録する。"""

    def __init__(self, codes=(0,), peek=()):
        self.codes, self.peek, self.seen, self.n = list(codes), list(peek), [], 0

    def __call__(self, args, cwd, ttl, label):
        if args[:2] == ["git", "rev-parse"]:
            return 0, SHA + "\n", ""
        if args[0] == "git":
            return 0, "", ""
        self.seen.append({p: (Path(cwd) / p).read_text(encoding="utf-8") if (Path(cwd) / p).is_file() else None
                          for p in self.peek})
        code = self.codes[min(self.n, len(self.codes) - 1)]
        self.n += 1
        return code, "out\n", ""


class Overlay(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        (self.inbox / "checks").mkdir(parents=True)
        (self.inbox / "drafts").mkdir()
        self.out.mkdir()
        self.cfg = {"inbox": str(self.inbox), "out": str(self.out), "checks": str(self.inbox / "checks"),
                    "base": "origin/main", "integration_branch": "hline/integration", "ttl_seconds": {"git": 5, "gate": 5}}

    def draft(self, name, text):
        path = self.inbox / "drafts" / name
        path.write_text(text, encoding="utf-8")
        return path

    def declare(self, **decl):
        (self.inbox / "checks" / "c.json").write_text(json.dumps(decl), encoding="utf-8")

    def make(self, runner, clock=1_000_000.0):
        text = hc.section(self.cfg, runner=runner, clock=clock)
        return next(x for x in text.splitlines() if x.startswith("- c:")), text

    def test_overlaid_file_is_in_the_tree_before_the_command(self):
        self.draft("impl.py", "VALUE = 1\n")
        self.declare(kind="unittest", modules=["tests.test_x"], overlay={"pkg/sub/impl.py": "drafts/impl.py"})
        r = Runner(peek=["pkg/sub/impl.py"])
        line, _ = self.make(r)
        self.assertEqual(r.seen, [{"pkg/sub/impl.py": "VALUE = 1\n"}])
        self.assertRegex(line, r"^- c: 合格（unittest／")

    def test_source_outside_drafts_or_missing_is_a_declaration_error(self):
        (self.inbox / "secret.txt").write_text("x", encoding="utf-8")
        for src in ("secret.txt", "drafts/../secret.txt", "drafts/none.py", "/etc/passwd"):
            self.declare(kind="unittest", modules=["tests.test_x"], overlay={"a.py": src})
            r = Runner()
            line, _ = self.make(r)
            self.assertRegex(line, r"^- c: 宣言の誤り", src)
            self.assertEqual(r.n, 0)

    def test_destination_must_be_a_relative_path_inside_the_tree(self):
        self.draft("impl.py", "x")
        for dest in ("../x.py", "/abs.py", "C:/x.py"):
            self.declare(kind="progress_check", overlay={dest: "drafts/impl.py"})
            self.assertRegex(self.make(Runner())[0], r"^- c: 宣言の誤り", dest)

    def test_git_log_rejects_the_new_fields(self):
        self.draft("impl.py", "x")
        extra = [{"overlay": {"a.py": "drafts/impl.py"}}, {"repeat": 2}, {"expect": "pass"}]
        for e in extra:
            self.declare(kind="git_log", paths=["docs"], expect_commits=0, **e)
            self.assertRegex(self.make(Runner())[0], r"^- c: 宣言の誤り", str(e))

    def test_repeat_and_expect_values_are_checked(self):
        for bad in ({"repeat": 0}, {"repeat": 21}, {"repeat": "2"}, {"repeat": True}, {"expect": "maybe"}):
            self.declare(kind="progress_check", **bad)
            self.assertRegex(self.make(Runner())[0], r"^- c: 宣言の誤り", str(bad))
        for good in ({"repeat": 1}, {"repeat": 20}, {"expect": "fail"}):
            self.declare(kind="progress_check", **good)
            self.assertNotRegex(self.make(Runner(codes=(1,)))[0], r"宣言の誤り", str(good))
            (self.out / hc.CACHE_NAME).unlink(missing_ok=True)

    def test_repeat_runs_that_many_times_and_one_miss_fails(self):
        self.declare(kind="progress_check", repeat=3)
        r = Runner(codes=(0, 0, 0))
        line, text = self.make(r)
        self.assertEqual(r.n, 3)
        self.assertRegex(line, r"^- c: 合格")
        self.assertIn("期待どおり 3／3 回", text)
        (self.out / hc.CACHE_NAME).unlink()
        r = Runner(codes=(0, 1, 0))
        line, text = self.make(r)
        self.assertEqual(r.n, 3)
        self.assertRegex(line, r"^- c: 不合格")
        self.assertIn("期待どおり 2／3 回", text)

    def test_default_repeat_is_one_and_adds_no_count_line(self):
        self.declare(kind="progress_check")
        r = Runner()
        _, text = self.make(r)
        self.assertEqual(r.n, 1)
        self.assertNotIn("期待どおり", text)

    def test_expect_fail_passes_only_on_nonzero_exit(self):
        self.declare(kind="progress_check", expect="fail")
        self.assertRegex(self.make(Runner(codes=(2,)))[0], r"^- c: 合格")
        (self.out / hc.CACHE_NAME).unlink()
        self.assertRegex(self.make(Runner(codes=(0,)))[0], r"^- c: 不合格")
        (self.out / hc.CACHE_NAME).unlink()
        self.declare(kind="progress_check", expect="fail", repeat=2)
        line, text = self.make(Runner(codes=(1, 0)))
        self.assertRegex(line, r"^- c: 不合格")
        self.assertIn("期待どおり 1／2 回", text)

    def test_changing_the_source_reruns_and_same_content_reuses(self):
        path = self.draft("impl.py", "A\n")
        self.declare(kind="unittest", modules=["tests.test_x"], overlay={"impl.py": "drafts/impl.py"})
        r1 = Runner()
        self.make(r1)
        self.assertEqual(r1.n, 1)
        r2 = Runner()
        self.make(r2)
        self.assertEqual(r2.n, 0)   # 中身が同じなら前回の結果
        path.write_text("B\n", encoding="utf-8")
        r3 = Runner(peek=["impl.py"])
        self.make(r3)
        self.assertEqual(r3.n, 1)
        self.assertEqual(r3.seen, [{"impl.py": "B\n"}])


if __name__ == "__main__":
    unittest.main()
