"""H ラインが止まっている間の report.md（W-144）。受入と試験が見る点は総監督が書いた（reply-architect-20261023-2 の (c)）。

    python -m unittest tests.test_report_stopped

試験が見ること（総監督の一覧。削らない）:
偽の状態（止めた印あり・統合ブランチなし・照合の最後の時刻あり）から書いた report.md に、①書いた時刻 ②main の SHA
③「止まっている」と理由 ④最後に走った時刻 が出る。「統合 PR 待ち」「走行中」の語が出ない。by: executor のタスクに印が付く。
統合ブランチが無くても例外にならない
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline  # noqa: E402
import hline_checks  # noqa: E402
import hline_report  # noqa: E402
import hline_stopped  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()
SHA = "0123456789abcdef0123456789abcdef01234567"
CLOCK = datetime.datetime(2026, 10, 23, 21, 5).timestamp()
LAST_RUN = datetime.datetime(2026, 10, 23, 10, 11).timestamp()
CHECKED = datetime.datetime(2026, 10, 22, 9, 30).timestamp()
LEDGER = {
    "project_goal": "g", "active_task_id": "T1", "constraints": ["c"], "last_verification": None,
    "nodes": [{"id": "A", "title": "root", "parent": None}, {"id": "rebuild", "title": "立て直し", "parent": "A"}],
    "tasks": [
        {"id": "T1", "title": "実行役のタスク", "group": "rebuild", "target_repo": "r", "status": "in_progress", "by": "executor",
         "verification": {"command": "python -m unittest tests.x", "expected_exit_code": 0}},
        {"id": "T2", "title": "止めたタスク", "group": "rebuild", "target_repo": "r", "status": "stopped", "reason": "立て直しの間"},
    ],
}


def fake_run(args, cwd, ttl, label, env=None, input=None):
    if args[:2] == ["git", "fetch"]:
        return 0, "", ""
    if args[:2] == ["git", "show"]:
        return (0, yaml.safe_dump(LEDGER, allow_unicode=True), "") if args[2].startswith("origin/main:") else (128, "", "fatal: bad")
    if args[:2] == ["git", "log"]:
        if args[-1] != "origin/main":
            return 128, "", f"fatal: bad revision '{args[-1]}'"
        if "%H%x1f%s%x1f%cd" in args[3]:
            return 0, f"{SHA}\x1f立て直しの題\x1f2026-10-23 20:30\n", ""
        return 0, f"{SHA[:7]} 立て直しの題\n", ""
    if args[:2] == ["git", "for-each-ref"]:   # 追跡の ref が無い（リモートに統合ブランチが無い）
        return 0, "", ""
    return 1, "", "使えない"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out))
        for name, value in (("goal_section", "## 目的までの距離\n- 偽\n"), ("flow_section", "## 関門ごとの流れ\n- 偽\n")):
            p = mock.patch.object(hline_report, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def put_state(self):
        state = self.out / "queue.json"
        state.write_text(json.dumps({"items": {}, "awaiting_pr": "https://github.com/o/r/pull/261", "skipped": []}), encoding="utf-8")
        os.utime(state, (LAST_RUN, LAST_RUN))
        checks = hline_checks.checks_dir(self.cfg)
        checks.mkdir(parents=True, exist_ok=True)
        for name in ("c1", "c2"):
            (checks / f"{name}.json").write_text(json.dumps({"kind": "progress_check"}), encoding="utf-8")
        (self.out / hline_checks.CACHE_NAME).write_text(json.dumps(
            {"c1": {"key": "k", "res": {"status": "合格", "code": 0, "head": []}, "at": CHECKED}}), encoding="utf-8")

    def write(self):
        hline_stopped.stop(self.cfg, "立て直しの間は止める", now=lambda: datetime.datetime(2026, 10, 23, 10, 30))
        self.put_state()
        return hline_stopped.write(self.cfg, runner=fake_run, clock=CLOCK)


class StoppedReport(Base):
    def test_the_head_lines(self):
        text = self.write()
        self.assertEqual(text, (self.inbox / "report.md").read_text(encoding="utf-8"))
        self.assertIn("- 書いた時刻: 2026-10-23 21:05", text)                       # ①
        self.assertIn(f"- origin/main の HEAD: {SHA}（立て直しの題／コミット: 2026-10-23 20:30）", text)   # ②
        self.assertIn("- H ライン: 止まっている（理由: 立て直しの間は止める／止めた時刻: 2026-10-23T10:30:00", text)   # ③
        self.assertIn("- H ラインが最後に走った時刻: 2026-10-23 10:11", text)       # ④

    def test_no_running_words(self):
        text = self.write()
        self.assertNotIn("統合 PR 待ち", text)
        self.assertNotIn("走行中", text)
        self.assertIn("- 状態: 止まっている（最後に走った時刻: 2026-10-23 10:11）", text)

    def test_queue_and_meters_stay_with_the_last_run_time(self):
        text = self.write()
        self.assertIn("## キュー\n- 最後に走った時刻（2026-10-23 10:11）の状態", text)
        self.assertIn("## 計器（最後に走った時刻 2026-10-23 10:11 の状態）", text)

    def test_the_tree_is_the_main_ledger_with_marks(self):
        text = self.write()
        self.assertIn("T1 実行役のタスク（実行役） ← 現在地", text)
        self.assertIn("T2 止めたタスク（止めた）", text)
        self.assertIn("- 現在タスク: T1（実行役のタスク）", text)

    def test_no_integration_branch_is_one_line_and_no_error(self):
        text = self.write()
        lines = [l for l in text.splitlines() if l.startswith("- 統合ブランチの HEAD:")]
        self.assertEqual(len(lines), 1)
        self.assertIn("なし（リモートに無い）", lines[0])
        self.assertNotIn("取得失敗", lines[0])

    def test_the_checks_are_not_run_and_show_the_last_time(self):
        with mock.patch.object(hline_checks, "execute", side_effect=AssertionError("照合を走らせた")):
            text = self.write()
        self.assertIn("- c1: 走っていない（最後に走った時刻: 2026-10-22 09:30／その結果: 合格）", text)
        self.assertIn("- c2: 走っていない（最後に走った記録なし）", text)


class GoalWithoutRunningChecks(Base):
    """止まっている間は「目的までの距離」も照合を走らせず、前回の結果で数える（W-144 の直し）。"""

    def test_the_goal_section_gets_the_cached_checks(self):
        self.write()
        hline_report.goal_section.assert_called_once_with(self.cfg, checks=hline_checks.cached_section, ref="origin/main")
        hline_report.flow_section.assert_called_once_with(self.cfg, ref="origin/main")   # 古い統合ブランチの台帳を読まない

    def test_cached_section_does_not_run_and_reads_like_section(self):
        import goal_distance
        self.put_state()
        with mock.patch.object(hline_checks, "execute", side_effect=AssertionError("照合を走らせた")):
            text = hline_checks.cached_section(self.cfg)
        self.assertEqual(dict(goal_distance.STATUS_RE.findall(text)).get("c1"), "合格")
        self.assertIn("- c2: 記録なし", text)

    def test_passing_modules_uses_the_given_checks(self):
        import goal_distance
        self.put_state()
        (hline_checks.checks_dir(self.cfg) / "c1.json").write_text(
            json.dumps({"kind": "unittest", "modules": ["tests.test_x"]}), encoding="utf-8")
        with mock.patch.object(hline_checks, "execute", side_effect=AssertionError("照合を走らせた")):
            self.assertEqual(goal_distance.passing_modules(self.cfg, hline_checks.cached_section), {"tests.test_x"})


class SkippedIsTheLastRun(Base):
    def test_the_inbox_section_says_it_is_the_last_run_record(self):
        hline_stopped.stop(self.cfg, "止める")
        self.put_state()
        state = self.out / "queue.json"
        state.write_text(json.dumps({"items": {}, "awaiting_pr": None, "skipped": [{"file": "W-110.md", "reason": "古い理由"}]}),
                         encoding="utf-8")
        os.utime(state, (LAST_RUN, LAST_RUN))
        text = hline_stopped.write(self.cfg, runner=fake_run, clock=CLOCK)
        self.assertIn("## 受信箱に残した What（最後に走った時刻 2026-10-23 10:11 の記録。今の受信箱とは違いうる）", text)


class Marker(Base):
    def test_report_without_the_marker_is_refused(self):
        with self.assertRaises(ValueError):
            hline_stopped.write(self.cfg, runner=fake_run, clock=CLOCK)
        self.assertFalse((self.inbox / "report.md").exists())

    def test_poll_does_nothing_while_stopped(self):
        hline_stopped.stop(self.cfg, "止める")
        with mock.patch.object(hline, "run_line", side_effect=AssertionError("走った")):
            self.assertEqual(hline.poll(self.cfg), 0)

    def test_resume_removes_the_marker(self):
        hline_stopped.stop(self.cfg, "止める")
        self.assertIsNotNone(hline_stopped.marker(self.cfg))
        hline_stopped.resume(self.cfg)
        self.assertIsNone(hline_stopped.marker(self.cfg))

    def test_an_empty_reason_is_refused(self):
        with self.assertRaises(ValueError):
            hline_stopped.stop(self.cfg, "  ")


if __name__ == "__main__":
    unittest.main()
