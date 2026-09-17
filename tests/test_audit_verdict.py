"""監査の判定（audit.py --verdict-json）の読み取りと、複数ファイルのまとめ方。

    python -m unittest discover -s tests -v

判定は合否に使わない（docs/design/spec_pipeline.md §13 の 3）。だからこそ、**取れなかった判定を
`ok` に畳まないこと**がここでの要点になる。0 と「不明」を混ぜると、承認する人間が
「監査は問題なしと言った」と読んでしまう。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import audit  # noqa: E402
import scheduler as ms4  # noqa: E402


def fenced(body):
    return "指摘の本文。\n\n```json\n" + body + "\n```\n"


class ParseVerdictTests(unittest.TestCase):
    def test_a_well_formed_block_is_read(self):
        got, why = audit.parse_verdict(fenced('{"verdict": "concern", "findings": ["境界値"]}'))
        self.assertIsNone(why)
        self.assertEqual(got, {"verdict": "concern", "findings": ["境界値"]})

    def test_the_last_block_wins(self):
        """本文に書式の例を載せられても、最後の 1 つを判定として読む。"""
        text = (fenced('{"verdict": "ok", "findings": []}')
                + fenced('{"verdict": "reject", "findings": ["桁あふれ"]}'))
        got, _ = audit.parse_verdict(text)
        self.assertEqual(got["verdict"], "reject")

    def test_missing_broken_or_unknown_blocks_are_not_ok(self):
        cases = {
            "フェンスが無い": "判定は ok です。",
            "空": "",
            "JSON が壊れている": fenced('{"verdict": "ok",}'),
            "表ではない": fenced('["ok"]'),
            "verdict が知らない値": fenced('{"verdict": "fine", "findings": []}'),
            "verdict が無い": fenced('{"findings": []}'),
            "findings が配列でない": fenced('{"verdict": "ok", "findings": "なし"}'),
            "findings が文字列の配列でない": fenced('{"verdict": "ok", "findings": [1]}'),
        }
        for label, text in cases.items():
            with self.subTest(label):
                got, why = audit.parse_verdict(text)
                self.assertIsNone(got)
                self.assertTrue(why)

    def test_the_file_is_written_even_when_the_verdict_is_unreadable(self):
        """読み手（スケジューラ）が「なぜ取れなかったか」を記録できるように、必ず書く。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "deep" / "v.json"
            audit.write_verdict(out, "x.cs", "判定を書き忘れた応答", Path(d) / "report.md")
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(data["verdict"])
        self.assertEqual(data["findings"], [])
        self.assertIn("判定ブロック", data["verdict_null_reason"])

    def test_the_prompt_is_only_added_when_a_verdict_is_asked_for(self):
        """判定を求めないときに余計な指示を混ぜない（監査の文面を実験条件として固定する）。"""
        self.assertIn("verdict", audit.CFG["verdict_prompt"])
        self.assertNotIn("verdict", audit.CFG["system_prompt"])
        self.assertEqual(audit.CFG["verdicts"], ["ok", "concern", "reject"])


class WorstVerdictTests(unittest.TestCase):
    def test_the_strongest_verdict_wins(self):
        self.assertEqual(ms4.Scheduler.worst_verdict(["ok", "ok"]), "ok")
        self.assertEqual(ms4.Scheduler.worst_verdict(["ok", "concern"]), "concern")
        self.assertEqual(ms4.Scheduler.worst_verdict(["concern", "reject", "ok"]), "reject")

    def test_an_unreadable_verdict_is_unknown_not_ok(self):
        self.assertEqual(ms4.Scheduler.worst_verdict(["ok", None]), "unknown")
        self.assertEqual(ms4.Scheduler.worst_verdict(["ok", "fine"]), "unknown")
        self.assertEqual(ms4.Scheduler.worst_verdict([None]), "unknown")

    def test_an_unreadable_verdict_does_not_hide_a_stronger_one(self):
        self.assertEqual(ms4.Scheduler.worst_verdict(["reject", None]), "reject")
        self.assertEqual(ms4.Scheduler.worst_verdict([None, "concern"]), "concern")

    def test_nothing_audited_is_skipped_not_ok(self):
        self.assertEqual(ms4.Scheduler.worst_verdict([]), "skipped")


if __name__ == "__main__":
    unittest.main()
