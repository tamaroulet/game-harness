"""利用枠切れ（429）の扱いの検査：試行に数えない・待ちに戻す・再開の時刻の前は何もしない・後は再開する。

    python -m unittest tests.test_hline_quota -v

入力は、2026-10-06 の走行（20261006-2109-500-b7-5-0-state-json の implementer-0-2.log）で実装役の CLI が実際に返した
標準出力と標準エラーをそのまま写したもの（tests/fixtures/hline_quota_429.*）。proc.run を差し替え、外部の CLI・push・PR は起こさない。
"""
import datetime
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_quota as hq  # noqa: E402
import test_hline_queue as base  # noqa: E402

OUT = (HERE / "fixtures" / "hline_quota_429.stdout.json").read_text(encoding="utf-8")
ERR = (HERE / "fixtures" / "hline_quota_429.stderr.txt").read_text(encoding="utf-8")
RAW = "You've hit your session limit · resets 12am (Asia/Tokyo)"
JST = datetime.timezone(datetime.timedelta(hours=9))
OK = json.dumps({"result": "できた", "modelUsage": {"claude-sonnet-5-5": {}}, "num_turns": 3})


def at(hour, minute=0, day=6):
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=JST)


class Pure(unittest.TestCase):
    def test_the_fixture_is_the_raw_message(self):
        self.assertEqual(json.loads(OUT)["result"], RAW)

    def test_the_message_is_found_in_the_json_result_and_not_in_other_outputs(self):
        self.assertEqual(hq.message(OUT, ERR), RAW)
        self.assertEqual(hq.message("not json", "Error: You've hit your usage limit · resets 3pm"),
                         "Error: You've hit your usage limit · resets 3pm")
        for out, err in ((OK, ERR), ("", ""), ("not json", "rate limit"), ('{"result": "ターン数の上限"}', "")):
            self.assertIsNone(hq.message(out, err), out)

    def test_the_reset_time_is_the_next_one_after_now(self):
        self.assertEqual(hq.resume_at(RAW, at(21, 9)), at(0, day=7))
        self.assertEqual(hq.resume_at("resets 10:30pm (Asia/Tokyo)", at(21, 9)), at(22, 30))
        self.assertEqual(hq.resume_at("resets 4:30am (Asia/Tokyo)", at(4, 31)), at(4, 30, day=7))
        self.assertEqual(hq.resume_at("resets 12pm", at(9)), at(12))

    def test_the_weekly_limit_has_a_date(self):
        weekly = "You've hit your weekly limit · resets Oct 7, 7pm (Asia/Tokyo)"   # 2026-10 の走行の原文
        self.assertEqual(hq.message(json.dumps({"api_error_status": 429, "result": weekly}), ""), weekly)
        self.assertEqual(hq.resume_at(weekly, at(21, 9, day=5)), at(19, day=7))
        self.assertEqual(hq.resume_at(weekly, at(19, 1, day=7)), at(19, day=7).replace(year=2027))

    def test_an_unreadable_reset_time_waits_60_minutes(self):
        for text in ("You've hit your session limit", "resets soon", "resets 13pm", "resets Foo 7, 7pm", "resets Feb 30, 7pm", "", None):
            self.assertEqual(hq.resume_at(text, at(21, 9)), at(22, 9), text)


class Flow(base.World):
    """実装役の呼び出し（proc.run）だけを差し替え、run_task・process・run_line は本物を通す。"""

    def patch_agents(self):
        self.impl_outs, self.calls = [], 0

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            if label != "実装役":   # 残骸の掃除（git worktree prune）など
                return 0, "", ""
            self.calls += 1
            return self.impl_outs.pop(0) if self.impl_outs else (0, OK, "")
        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", return_value=(True, "ok"))

    def hit_quota(self, now):
        self.put("010-a")
        self.impl_outs = [(1, OUT, ERR)]
        self.patch(hq, "now", return_value=now)
        return self.poll()

    def test_the_attempt_is_not_counted_and_the_what_goes_back_to_waiting(self):
        self.assertEqual(self.hit_quota(at(21, 9)), 0)
        item = self.state()["items"]["010-a"]
        self.assertEqual((item["status"], item["tries"]), ("waiting", []))   # 試行に数えない（未収束にもしない）
        self.assertEqual(self.calls, 1)   # 呼び直さない（プロセスの中で眠らない）
        self.assertEqual(self.slept, [])
        wait = self.state()["quota_wait"]
        self.assertEqual((wait["name"], wait["until"]), ("010-a", at(0, day=7).isoformat(timespec="minutes")))
        self.assertIn(RAW, wait["reason"])
        self.assertEqual(self.created, [])   # 待ちの What が残るので統合 PR は出さない
        report = self.report()
        self.assertIn("- 人間作業: NONE（利用枠の回復待ち（再開 ", report)
        self.assertNotIn("REVIEW_REQUIRED", report)
        self.assertNotIn("INFRA_HALTED", report)

    def test_before_the_reset_time_a_run_does_nothing(self):
        self.hit_quota(at(21, 9))
        self.patch(hq, "now", return_value=at(23, 59))
        self.patch(hline, "sweep", side_effect=AssertionError("掃除も走らせない"))
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.status("010-a"), "waiting")
        self.assertIsNotNone(self.state()["quota_wait"])

    def test_after_the_reset_time_the_record_is_cleared_and_the_run_resumes(self):
        self.hit_quota(at(21, 9))
        self.assertEqual((self.calls, self.status("010-a")), (1, "waiting"))
        self.patch(hq, "now", return_value=at(0, 1, day=7))
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.status("010-a"), "done")
        self.assertIsNone(self.state()["quota_wait"])
        self.assertEqual(len(self.created), 1)


if __name__ == "__main__":
    unittest.main()
