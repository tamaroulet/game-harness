"""台帳の project_goal と L5（W-148）。受入と試験が見る点は総監督が書いた（reply-architect-20261023-2 の (c)）。

    python -m unittest tests.test_project_goal

試験が見ること（総監督の一覧。削らない）:
project_goal に「C4」「C5」「単一コンテキスト」「3D の物理演算」の語があり、「D-109」「段 1 軽い 3D 通し」が無い。
字面の試験は弱いので、合否の芯は操縦士が文を読むこと（操縦士は 2026-10-23 に総監督の案を、括弧「勝ちを完了の条件にしない」のまま選んだ）
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "progress.yaml"
CLAUDE_MD = ROOT / "CLAUDE.md"


class ProjectGoal(unittest.TestCase):
    def setUp(self):
        self.goal = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))["project_goal"]

    def test_the_goal_has_the_three_points(self):
        for word in ("C4", "C5", "単一コンテキスト", "3D の物理演算", "勝ちを完了の条件にしない"):
            self.assertIn(word, self.goal)

    def test_the_old_stages_are_gone(self):
        for word in ("D-109", "段 1 軽い 3D 通し", "R5 の製品の付け足し"):
            self.assertNotIn(word, self.goal)

    def test_the_goal_points_at_the_tree(self):
        for node in ("rebuild", "W-18", "段 5"):
            self.assertIn(node, self.goal)

    def test_claude_md_has_no_other_goal_line(self):
        text = CLAUDE_MD.read_text(encoding="utf-8")
        self.assertNotIn("D-109", text)
        self.assertNotIn("段 1 軽い 3D 通し", text)


if __name__ == "__main__":
    unittest.main()
