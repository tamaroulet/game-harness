"""決定の記録（docs/design/decision_log.md）と、ADR-005・ADR-006 の取り下げ（W-146）。受入と試験が見る点は総監督が書いた
（reply-architect-20261023-2 の (c)）。

    python -m unittest tests.test_decision_log

試験が見ること（総監督の一覧。削らない）:
1. decision_log.md の表のどの行も 7 列そろう
2. game-harness#276・game-harness#279・取り下げの行があり、残したブランチの名前を書いている
3. 冒頭に役の分け方の節がある
4. 調べものの待ちの置き場に、取り下げた 2 件の問いの ID が無い。置き場は config/research.json の queue（この PC の
   C:\\src\\.local の下で、Git の外）。CI には置き場が無いので、そこでは飛ばす。CI で見られない分は、この PC で走らせて確かめる
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
LOG = ROOT / "docs" / "design" / "decision_log.md"
COLUMNS = 7
WITHDRAWN = ("r2-adr005-rebuttal", "r6-adr006-rebuttal")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


class DecisionLog(unittest.TestCase):
    def setUp(self):
        self.text = LOG.read_text(encoding="utf-8")
        self.rows = [l for l in self.text.splitlines() if re.match(r"^\| 20\d\d-\d\d-\d\d \|", l)]

    def test_every_row_has_seven_columns(self):
        self.assertTrue(self.rows)
        header = next(l for l in self.text.splitlines() if l.startswith("| 日付 |"))
        self.assertEqual(len(cells(header)), COLUMNS)
        for row in self.rows:
            with self.subTest(row=row[:40]):
                self.assertEqual(len(cells(row)), COLUMNS)
                self.assertTrue(all(cells(row)))

    def row_with(self, *words):
        found = [r for r in self.rows if all(w in r for w in words)]
        self.assertTrue(found, words)
        return found

    def test_the_closed_prs_and_the_withdrawal_have_rows_with_the_kept_branches(self):
        self.row_with("game-harness#276", "閉じた", "rebuild/w-143-adr005")
        self.row_with("game-harness#279", "閉じた", "rebuild/adr-006-governance")
        self.row_with("取り下げた", *WITHDRAWN)

    def test_the_roles_come_first(self):
        headings = [l for l in self.text.splitlines() if l.startswith("## ")]
        self.assertTrue(headings[0].startswith("## 立て直しの間の役の分け方"))
        roles = self.text.split(headings[0], 1)[1].split("\n## ", 1)[0]
        for who in ("操縦士", "会話の席", "総監督"):
            self.assertIn(f"**{who}**", roles)
        self.assertEqual(self.text.count("## 立て直しの間の役の分け方"), 1)

    def test_the_withdrawn_questions_are_not_queued(self):
        try:
            import research
            queue = Path(research.load_config()["queue"])
        except Exception as e:   # noqa: BLE001
            self.skipTest(f"調べものの設定を読めない（この PC の外）: {type(e).__name__}")
        if not queue.is_dir():
            self.skipTest(f"調べものの待ちの置き場が無い（この PC の外）: {queue}")
        names = {p.stem for p in queue.iterdir()}
        self.assertFalse(names & set(WITHDRAWN))


if __name__ == "__main__":
    unittest.main()
