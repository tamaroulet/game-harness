"""進捗の台帳の依存（depends_on）。検査・現在地の選び方・完了の拒否（harness/progress.py）。

    python -m unittest tests.test_progress_deps

合成の台帳だけを使う。実リポジトリの台帳には触れない。
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import progress  # noqa: E402


def make(*specs):
    """specs: (id, status, depends_on or None)。最初の in_progress を現在地にする。"""
    tasks = []
    for tid, status, deps in specs:
        t = {"id": tid, "title": tid, "group": "G", "target_repo": "r", "status": status,
             "verification": {"command": "exit 0", "expected_exit_code": 0}}
        if deps is not None:
            t["depends_on"] = deps
        tasks.append(t)
    active = next((t["id"] for t in tasks if t["status"] == "in_progress"), None)
    return {"project_goal": "g", "active_task_id": active, "constraints": ["c"],
            "nodes": [{"id": "A", "title": "root", "parent": None}, {"id": "G", "title": "g", "parent": "A"}],
            "tasks": tasks, "last_verification": None}


class ValidateTests(unittest.TestCase):
    def test_no_depends_on_is_valid(self):
        self.assertEqual(progress.validate(make(("A", "in_progress", None), ("B", "pending", None))), [])

    def test_valid_dependency(self):
        self.assertEqual(progress.validate(make(("A", "in_progress", None), ("B", "pending", ["A"]))), [])

    def test_unknown_dependency_is_rejected(self):
        problems = progress.validate(make(("A", "in_progress", ["ZZ"])))
        self.assertTrue(any("ZZ" in p for p in problems), problems)

    def test_cycle_is_rejected(self):
        problems = progress.validate(make(("A", "in_progress", ["B"]), ("B", "pending", ["A"])))
        self.assertTrue(any("循環" in p for p in problems), problems)

    def test_self_dependency_is_rejected(self):
        problems = progress.validate(make(("A", "in_progress", ["A"])))
        self.assertTrue(any("循環" in p for p in problems), problems)

    def test_completed_task_with_open_dependency_is_rejected(self):
        problems = progress.validate(make(("A", "in_progress", None), ("B", "completed", ["A"])))
        self.assertTrue(any("B" in p and "A" in p for p in problems), problems)

    def test_completed_task_with_completed_dependency_is_valid(self):
        self.assertEqual(progress.validate(make(("A", "completed", None), ("B", "completed", ["A"]),
                                                ("C", "in_progress", None))), [])


class AdvanceTests(unittest.TestCase):
    def test_skips_task_whose_dependency_is_open(self):
        state = make(("A", "completed", None), ("B", "pending", ["D"]), ("C", "pending", None), ("D", "pending", None))
        self.assertEqual(progress.advance_active(state), "C")
        self.assertEqual(state["active_task_id"], "C")
        self.assertEqual(state["tasks"][2]["status"], "in_progress")
        self.assertEqual(state["tasks"][1]["status"], "pending")

    def test_picks_first_in_order_when_dependencies_are_done(self):
        state = make(("A", "completed", None), ("B", "pending", ["A"]), ("C", "pending", None))
        self.assertEqual(progress.advance_active(state), "B")

    def test_without_depends_on_keeps_order(self):
        state = make(("A", "pending", None), ("B", "pending", None))
        self.assertEqual(progress.advance_active(state), "A")

    def test_none_when_everything_is_blocked_or_done(self):
        state = make(("A", "completed", None))
        self.assertIsNone(progress.advance_active(state))


class CompleteTests(unittest.TestCase):
    def test_refuses_without_running_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"
            state = make(("A", "in_progress", None), ("B", "pending", ["A"]))
            state["tasks"][1]["verification"] = {"command": f'"{sys.executable}" -c "open(r\'{marker}\', \'w\')"',
                                                 "expected_exit_code": 0}
            (Path(tmp) / "docs").mkdir()
            progress.save(state, Path(tmp) / progress.REL_PATH)
            before = copy.deepcopy(state)
            with self.assertRaises(progress.ProgressError) as cm:
                progress.complete("B", repo_root=tmp, fetch=False, out=lambda s: None, require_active=False)
            self.assertIn("A", str(cm.exception))
            self.assertFalse(marker.exists())
            self.assertEqual(progress.load(Path(tmp) / progress.REL_PATH), before)


if __name__ == "__main__":
    unittest.main()
