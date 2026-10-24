"""台帳の project_goal の検査（tree.yaml の objective と一致すること、確定文の芯になる語を守ること）。"""
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "progress.yaml"
TREE = ROOT / "docs" / "tree" / "tree.yaml"
CLAUDE_MD = ROOT / "CLAUDE.md"


class ProjectGoal(unittest.TestCase):
    def setUp(self):
        self.goal = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))["project_goal"]
        tree_data = yaml.safe_load(TREE.read_text(encoding="utf-8"))
        obj = tree_data["objective"]
        self.objective = obj["title"] if isinstance(obj, dict) else obj

    def test_the_goal_matches_tree_objective(self):
        self.assertEqual(self.goal, self.objective)

    def test_the_goal_has_core_phrases(self):
        """操縦士が確定した文の芯になる語（総監督の一覧。削らない）。"""
        for phrase in ("単一コンテキスト型", "3D の物理演算", "統制された比較実験", "ハーネス側が勝つことは完了の条件にしない"):
            self.assertIn(phrase, self.goal)

    def test_the_old_stages_are_gone(self):
        for word in ("D-109", "段 1 軽い 3D 通し", "R5 の製品の付け足し"):
            self.assertNotIn(word, self.goal)

    def test_claude_md_has_no_other_goal_line(self):
        text = CLAUDE_MD.read_text(encoding="utf-8")
        self.assertNotIn("D-109", text)
        self.assertNotIn("段 1 軽い 3D 通し", text)


if __name__ == "__main__":
    unittest.main()
