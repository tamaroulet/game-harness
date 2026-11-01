"""止まりの Issue（hline_halt）：設定の open_issue が false なら開かない・ベンチの走りの設定に open_issue が入る・
題と本文のこの PC のパスを <path> に伏せる・同じ題の照らしは伏せた後の題で行う。gh は呼ばない（proc.run を差し替える）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import hline_halt  # noqa: E402
import proc  # noqa: E402

REASON = r"git が壊れた: C:\src\.local\wt\a.py と c:/Src/.local/b と /home/alice/w/c.py と /Users/bob/d と /tmp/e"
MASKED = "git が壊れた: <path> と <path> と <path> と <path> と /tmp/e"


class HaltIssue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "inbox").mkdir()
        self.out = root / "out"
        self.report = root / "inbox" / "report.md"
        self.cfg = {"inbox": str(root / "inbox"), "out": str(self.out), "ttl_seconds": {"gh": 5}}
        self.calls, self.open_titles = [], []

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append(list(args))
        return (0, "\n".join(self.open_titles), "") if "list" in args else (0, "https://example.invalid/issues/1", "")

    def stop(self, cfg, reason, times=2):
        with mock.patch.object(proc, "run", self.fake_run), mock.patch.object(proc, "resolve_cli", lambda n: [n]):
            for _ in range(times):
                hline_halt.stopped(cfg, reason)

    def creates(self):
        return [c for c in self.calls if "create" in c]

    def test_open_issue_false_never_opens_but_keeps_section_and_count(self):
        """open_issue が false なら gh を呼ばない。report.md の節と続いた回数の記録は今どおり。"""
        self.stop({**self.cfg, "open_issue": False}, "同じ", times=3)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.report.read_text(encoding="utf-8").startswith("## 停止\n"))
        self.assertEqual(json.loads((self.out / "halt.json").read_text(encoding="utf-8")), {"reason": "同じ", "count": 3})

    def test_without_the_key_opens_as_before(self):
        """open_issue の欄が無い（true も同じ）なら、2 回目で今どおり開く。"""
        for cfg in (self.cfg, {**self.cfg, "open_issue": True}):
            with self.subTest(cfg=cfg):
                self.calls.clear()
                (self.out / "halt.json").unlink(missing_ok=True)
                self.stop(cfg, "同じ")
                self.assertEqual(len(self.creates()), 1)

    def test_title_and_body_are_masked(self):
        """題と本文のドライブからのパスとホームの下のパスを <path> に伏せる（ほかの / で始まるパスは残す）。"""
        self.stop(self.cfg, REASON)
        (create,) = self.creates()
        title, body = create[create.index("--title") + 1], create[create.index("--body") + 1]
        self.assertEqual(title, hline_halt.MARK + MASKED)
        self.assertIn(MASKED, body)
        for leak in ("alice", "bob", "src", "Src"):
            self.assertNotIn(leak, title + body)
        self.assertIn(r"C:\src\.local", self.report.read_text(encoding="utf-8"))   # 手元の report.md は伏せない

    def test_duplicate_check_uses_the_masked_title(self):
        """同じ題の開いた Issue があるかは、伏せた後の題で照らす（伏せる前の題では照らさない）。"""
        self.open_titles = [hline_halt.MARK + MASKED]
        self.stop(self.cfg, REASON)
        self.assertEqual(self.creates(), [])
        self.open_titles = [hline_halt.MARK + REASON]
        (self.out / "halt.json").unlink()
        self.stop(self.cfg, REASON)
        self.assertEqual(len(self.creates()), 1)

    def test_masked_forms(self):
        cases = {r"C:\Users\a b": "<path> b", "D:/x/y.txt:3": "<path>", r"\\?\C:\x": r"\\?\<path>", "/home/u": "<path>",
                 "at /Users/carol/p/q.py line 2": "at <path> line 2", "/home/": "/home/", "/var/log/x": "/var/log/x", "a/b": "a/b"}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(hline_halt.masked(text), want)


class BenchConfig(unittest.TestCase):
    def test_line_config_turns_issues_off(self):
        """ベンチの走りの設定（bench_run.line_config）は open_pr と並べて open_issue を false にする。"""
        work = Path(tempfile.gettempdir()) / "w"
        for impl in (None, {"cli": "x"}):
            cfg = bench_run.line_config({"open_issue": True}, work, work / "inbox", "abc", "br", impl)
            self.assertIs(cfg["open_issue"], False)
            self.assertIs(cfg["open_pr"], False)


if __name__ == "__main__":
    unittest.main()
