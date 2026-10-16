"""既定のツリーが、展開した枝に完了したタスクも残して出すこと（harness/progress.py の tree）。

    python -m unittest tests.test_progress_tree_done
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import progress  # noqa: E402


def task(tid, group, status, **extra):
    return {"id": tid, "title": f"title {tid}", "group": group, "target_repo": "r", "status": status,
            "verification": None, **extra}


def synthetic():
    return {
        "project_goal": "g",
        "active_task_id": "T3",
        "constraints": ["c"],
        "nodes": [{"id": "A", "title": "root", "parent": None},
                  {"id": "D1", "title": "done 1", "parent": "A"},
                  {"id": "D2", "title": "done 2", "parent": "A"},
                  {"id": "G1", "title": "group 1", "parent": "A"},
                  {"id": "G2", "title": "group 2", "parent": "A"}],
        "tasks": [task("Y1", "D1", "completed"), task("Y2", "D2", "completed"),
                  task("T1", "G1", "completed"), task("T2", "G1", "pending"),
                  task("T3", "G1", "in_progress"), task("T4", "G1", "pending", by="pilot"),
                  task("T5", "G1", "completed"), task("T6", "G1", "pending"),
                  task("T7", "G1", "pending"), task("Z1", "G2", "pending")],
        "last_verification": None,
    }


class TreeDoneTests(unittest.TestCase):
    def test_open_branch_lists_every_task_in_ledger_order(self):
        t = progress.tree(synthetic())
        ids = [f"{i} title {i}" for i in ("T1", "T2", "T3", "T4", "T5", "T6", "T7")]
        positions = [t.index(x) for x in ids]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("- [x] T1 title T1", t)
        self.assertIn("- [ ] T2 title T2", t)
        self.assertIn("- [x] T5 title T5", t)
        self.assertIn("- [ ] T7 title T7", t)
        self.assertNotIn("ほか", t)

    def test_markers(self):
        t = progress.tree(synthetic())
        self.assertIn("T3 title T3 ← 現在地", t)
        self.assertEqual(t.count("← 現在地"), 1)
        self.assertIn("T4 title T4（操縦士）", t)

    def test_completed_nodes_without_the_active_task_still_fold(self):
        t = progress.tree(synthetic())
        self.assertIn("- [x] D1・D2（完了。tree --all で内訳）", t)
        self.assertNotIn("Y1", t)

    def test_other_branches_fold_into_a_count(self):
        t = progress.tree(synthetic())
        self.assertIn("G2 group 2（0/1）", t)
        self.assertNotIn("Z1", t)

    def test_report_does_not_raise_over_20_lines(self):
        s = synthetic()
        s["tasks"] += [task(f"X{i}", "G1", "pending") for i in range(30)]
        self.assertGreater(len(progress.report(s).splitlines()), 20)


class RepoTreeTests(unittest.TestCase):
    def test_completed_tasks_of_the_active_group_appear(self):
        state = progress.load(ROOT / progress.REL_PATH)
        tid = state.get("active_task_id")
        group = progress.task(state, tid)["group"] if tid else None
        done = [t for t in state["tasks"] if t["group"] == group and t["status"] == "completed"]
        out = progress.tree(state)
        for t in done:
            self.assertIn(f"- [x] {t['id']} ", out)


if __name__ == "__main__":
    unittest.main()
