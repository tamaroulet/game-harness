"""poll が環境の異常か予期しない例外で終わったときの停止の通知（W-81）：report.md の先頭の節と、2 回続いたときの issue。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_base  # noqa: E402
import proc  # noqa: E402

BODY = "# 報告\n\n元の中身\n"


class HaltNoticeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.cfg = {"inbox": str(self.inbox), "out": str(self.out), "ttl_seconds": {"gh": 5}}
        self.report = self.inbox / "report.md"
        self.report.write_text(BODY, encoding="utf-8")
        self.calls = []
        self.open_titles = []
        self.gh_code = 0

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append(list(args))
        if self.gh_code:
            return self.gh_code, "", "boom"
        if "list" in args:
            return 0, "\n".join(self.open_titles), ""
        return 0, "https://example.invalid/issues/1", ""

    def main(self, outcome):
        """poll を差し替えて main を 1 回走らせる。outcome は終了コードか、投げる例外。"""
        def poll(cfg):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        with mock.patch.object(hline, "load_config", return_value=self.cfg), \
                mock.patch.object(hline, "poll", poll), \
                mock.patch.object(proc, "run", self.fake_run), \
                mock.patch.object(proc, "resolve_cli", lambda n: [n]):
            return hline.main(["poll"])

    def creates(self):
        return [c for c in self.calls if "create" in c]

    def test_exit_2_writes_section_at_top_and_keeps_body(self):
        self.assertEqual(self.main(hline_base.Infra("git が壊れた")), 2)
        text = self.report.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## 停止\n"))
        self.assertIn("git が壊れた", text.splitlines()[2])
        self.assertTrue(text.endswith(BODY))

    def test_unexpected_exception_writes_section_and_reraises(self):
        with self.assertRaises(ValueError):
            self.main(ValueError("想定外\n二行目"))
        text = self.report.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## 停止\n"))
        self.assertIn("ValueError: 想定外", text)
        self.assertNotIn("二行目", text)
        self.assertTrue(text.endswith(BODY))

    def test_repeated_halt_does_not_stack_sections(self):
        self.main(hline_base.Infra("x"))
        self.main(hline_base.Infra("x"))
        self.assertEqual(self.report.read_text(encoding="utf-8").count("## 停止"), 1)

    def test_issue_opened_on_second_same_reason(self):
        self.main(hline_base.Infra("同じ"))
        self.assertEqual(self.creates(), [])
        self.main(hline_base.Infra("同じ"))
        creates = self.creates()
        self.assertEqual(len(creates), 1)
        self.assertIn("H ラインの停止: 同じ", creates[0])

    def test_no_issue_when_same_title_already_open(self):
        self.open_titles = ["H ラインの停止: 同じ"]
        for _ in range(3):
            self.main(hline_base.Infra("同じ"))
        self.assertEqual(self.creates(), [])

    def test_changed_reason_restarts_count(self):
        self.main(hline_base.Infra("A"))
        self.main(hline_base.Infra("B"))
        self.assertEqual(self.creates(), [])
        self.main(hline_base.Infra("B"))
        self.assertEqual(len(self.creates()), 1)

    def test_exit_0_removes_section_and_halt_json(self):
        self.main(hline_base.Infra("A"))
        self.assertTrue((self.out / "halt.json").exists())
        self.report.write_text("## 停止\n\n- 2026-01-01 00:00 A\n\n" + BODY, encoding="utf-8")
        self.assertEqual(self.main(0), 0)
        self.assertFalse((self.out / "halt.json").exists())
        self.assertNotIn("## 停止", self.report.read_text(encoding="utf-8"))
        self.main(hline_base.Infra("A"))
        self.assertEqual(json.loads((self.out / "halt.json").read_text(encoding="utf-8"))["count"], 1)

    def test_exit_1_also_clears(self):
        self.main(hline_base.Infra("A"))
        self.assertEqual(self.main(1), 1)
        self.assertFalse((self.out / "halt.json").exists())

    def test_gh_failure_keeps_exit_code(self):
        self.gh_code = 1
        self.main(hline_base.Infra("A"))
        self.assertEqual(self.main(hline_base.Infra("A")), 2)

    def test_gh_failure_keeps_raised_exception(self):
        self.gh_code = 1
        with self.assertRaises(RuntimeError):
            self.main(RuntimeError("a"))
        with self.assertRaises(RuntimeError):
            self.main(RuntimeError("a"))

    def test_report_write_failure_keeps_exit_code(self):
        self.report.unlink()
        self.report.mkdir()   # ファイルでなくディレクトリにして、読み書きを失敗させる
        self.assertEqual(self.main(hline_base.Infra("A")), 2)
        with self.assertRaises(KeyError):
            self.main(KeyError("k"))


if __name__ == "__main__":
    unittest.main()
