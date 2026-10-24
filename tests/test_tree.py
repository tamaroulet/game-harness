"""
tests.test_tree: 木（tree.yaml）の整合性検査と受入試験。
"""
import copy
import unittest
from pathlib import Path
import yaml
from harness import tree, progress

ROOT = Path(__file__).resolve().parent.parent


class TestTreeReal(unittest.TestCase):
    def test_real_tree_and_ledger_pass_all_checks(self):
        tree_path = ROOT / "docs" / "tree" / "tree.yaml"
        self.assertTrue(tree_path.exists(), "docs/tree/tree.yaml must exist")
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = yaml.safe_load(f)

        ledger_path = ROOT / "docs" / "progress.yaml"
        ledger_data = progress.load(ledger_path)

        problems = tree.check(tree_data, ledger_data)
        self.assertEqual(problems, [], f"Real tree checks failed: {problems}")


class TestTreeAcceptanceSynthetic(unittest.TestCase):
    def setUp(self):
        self.base_tree = {
            "objective": "自律開発基盤の確立",
            "requirements": [
                {
                    "id": "req.base",
                    "title": "基盤",
                    "achieved_when": "テストが通る",
                    "verify_by": "test:independent",
                    "status": "検証済み",
                    "depends_on": [],
                    "tasks": ["T-01"]
                },
                {
                    "id": "req.feature",
                    "title": "機能",
                    "achieved_when": "機能が動く",
                    "verify_by": "reviewer",
                    "status": "未達",
                    "depends_on": ["req.base"],
                    "tasks": ["T-02"]
                }
            ]
        }
        self.base_ledger = {
            "project_goal": "自律開発基盤の確立",
            "tasks": [
                {"id": "T-01", "status": "done"},
                {"id": "T-02", "status": "pending"}
            ]
        }

    def test_valid_synthetic_tree_passes(self):
        problems = tree.check(self.base_tree, self.base_ledger)
        self.assertEqual(problems, [])

    def test_objective_multiple_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["objective"] = ["自律開発基盤の確立", "別の目的"]
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("objective が 2 個あります" in p for p in problems))

    def test_objective_mismatch_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["objective"] = "まったく違う目的"
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("objective と project_goal の文字列が一致しません" in p for p in problems))

    def test_duplicate_requirement_id_fails(self):
        t = copy.deepcopy(self.base_tree)
        dup = copy.deepcopy(t["requirements"][0])
        t["requirements"].append(dup)
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("要件 id が重複しています" in p for p in problems))

    def test_invalid_status_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["requirements"][1]["status"] = "完了"
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("許可された値" in p and "完了" in p for p in problems))

    def test_missing_dependency_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["requirements"][1]["depends_on"] = ["req.nonexistent"]
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("req.nonexistent" in p for p in problems))

    def test_cyclic_dependency_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["requirements"][0]["depends_on"] = ["req.feature"]
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("循環があります" in p for p in problems))

    def test_task_not_in_ledger_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["requirements"][0]["tasks"].append("T-999")
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("T-999" in p and "台帳に存在しません" in p for p in problems))

    def test_withdrawn_task_in_tree_fails(self):
        t = copy.deepcopy(self.base_tree)
        l = copy.deepcopy(self.base_ledger)
        l["tasks"].append({"id": "T-WITHDRAWN", "status": "withdrawn"})
        t["requirements"][0]["tasks"].append("T-WITHDRAWN")
        problems = tree.check(t, l)
        self.assertTrue(any("T-WITHDRAWN" in p and "withdrawn" in p for p in problems))

    def test_uncompleted_task_missing_from_tree_fails(self):
        l = copy.deepcopy(self.base_ledger)
        l["tasks"].append({"id": "T-ORPHAN", "status": "pending"})
        problems = tree.check(self.base_tree, l)
        self.assertTrue(any("T-ORPHAN" in p and "どの要件にもありません" in p for p in problems))

    def test_uncompleted_task_in_multiple_requirements_fails(self):
        t = copy.deepcopy(self.base_tree)
        t["requirements"][0]["tasks"].append("T-02")
        problems = tree.check(t, self.base_ledger)
        self.assertTrue(any("T-02" in p and "複数の要件" in p for p in problems))

    def test_completed_task_in_archive_passes(self):
        l = copy.deepcopy(self.base_ledger)
        l["tasks"].append({"id": "T-ARCHIVED", "status": "done"})
        t = copy.deepcopy(self.base_tree)
        t["requirements"][0]["tasks"].append("T-ARCHIVED")
        problems = tree.check(t, l)
        self.assertEqual(problems, [])


class TestSelectableExpansion(unittest.TestCase):
    def test_pending_task_without_verification_not_selected_regardless_of_group(self):
        state = {
            "nodes": [{"id": "A", "title": "A"}, {"id": "B", "title": "B"}],
            "tasks": [
                {"id": "T1", "status": "pending", "group": "B"},
                {"id": "T2", "status": "pending", "group": "B", "verification": {"command": "echo 1"}}
            ]
        }
        self.assertEqual(progress.advance_active(state), "T2")

    def test_verified_pending_task_selected_as_before(self):
        state = {
            "nodes": [{"id": "A", "title": "A"}],
            "tasks": [
                {"id": "T1", "status": "pending", "group": "A", "verification": {"command": "echo 1"}}
            ]
        }
        self.assertEqual(progress.advance_active(state), "T1")


if __name__ == "__main__":
    unittest.main()
