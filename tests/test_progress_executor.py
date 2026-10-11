"""台帳の執行者の欄 by・完了後の記録の欄 pr（W-14、D-056）。合成の台帳と偽の git で確かめる。"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_queue  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()


def sample():
    return {
        "project_goal": "g",
        "active_task_id": "T1",
        "constraints": ["c1"],
        "nodes": [{"id": "A", "title": "root", "parent": None},
                  {"id": "G1", "title": "group 1", "parent": "A"}],
        "tasks": [
            {"id": "T1", "title": "first", "group": "G1", "target_repo": "r", "status": "in_progress"},
            {"id": "T2", "title": "second", "group": "G1", "target_repo": "r", "status": "pending"},
        ],
        "last_verification": None,
    }


def with_fields(tid, **fields):
    state = sample()
    next(t for t in state["tasks"] if t["id"] == tid).update(fields)
    return state


class Validate(unittest.TestCase):
    def test_a_ledger_without_the_fields_is_valid(self):
        self.assertEqual(progress.validate(sample()), [])

    def test_correct_values_are_valid(self):
        for by in ("pilot", "hline"):
            self.assertEqual(progress.validate(with_fields("T2", by=by)), [])
        self.assertEqual(progress.validate(with_fields("T2", pr="game-harness#165")), [])
        self.assertEqual(progress.validate(with_fields("T2", by="pilot", pr="falling-blocks#15")), [])

    def test_a_wrong_by_is_a_problem(self):
        for by in ("human", "", None, 3):
            problems = progress.validate(with_fields("T2", by=by))
            self.assertTrue(any("T2" in p and "by" in p for p in problems), (by, problems))

    def test_a_wrong_pr_is_a_problem(self):
        for pr in ("165", "#165", "game-harness#", "game-harness 165", "game-harness#1a", "", None, 165):
            problems = progress.validate(with_fields("T2", pr=pr))
            self.assertTrue(any("T2" in p and "pr" in p for p in problems), (pr, problems))


class Views(unittest.TestCase):
    def test_a_ledger_without_the_fields_shows_no_mark(self):
        text = progress.tree(sample(), True)
        self.assertNotIn("操縦士", text)
        self.assertIn("- [ ] T2 second\n", text)
        self.assertIn("- 人間作業: NONE", progress.report(sample()))

    def test_the_tree_marks_pilot_tasks(self):
        state = with_fields("T2", by="pilot")
        for text in (progress.tree(state), progress.tree(state, True)):
            self.assertIn("- [ ] T2 second（操縦士）\n", text)
            self.assertNotIn("first（操縦士）", text)
        self.assertIn("T2 second（操縦士）", progress.report(state))

    def test_hline_tasks_are_not_marked(self):
        self.assertNotIn("操縦士", progress.tree(with_fields("T2", by="hline"), True))

    def test_a_pilot_task_at_the_current_position_is_a_human_task(self):
        state = with_fields("T1", by="pilot")
        text = progress.report(state)
        self.assertIn("- 人間作業: TASK T1 first（操縦士）\n", text)
        self.assertIn("T1 first（操縦士） ← 現在地", text)

    def test_a_pilot_task_that_is_not_the_current_position_does_not_show(self):
        self.assertIn("- 人間作業: NONE", progress.report(with_fields("T2", by="pilot")))

    def test_waiting_for_the_integration_pr_is_unchanged(self):
        state = with_fields("T1", by="pilot", review_pr="https://github.com/o/r/pull/1")
        self.assertIn("- 人間作業: REVIEW_REQUIRED https://github.com/o/r/pull/1\n", progress.report(state))
        plain = with_fields("T1", review_pr="https://github.com/o/r/pull/1")
        self.assertIn("- 人間作業: REVIEW_REQUIRED https://github.com/o/r/pull/1\n", progress.report(plain))

    def test_the_input_is_not_changed(self):
        state = with_fields("T1", by="pilot")
        before = copy.deepcopy(state)
        progress.report(state)
        self.assertEqual(state, before)


class Intake(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["G0"])
        state = sample()
        state["tasks"][0]["by"] = "pilot"
        self.git = (0, yaml.safe_dump({"tasks": state["tasks"]}, allow_unicode=True), "")

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        return self.git

    def put(self, name, task):
        (self.inbox / f"{name}.md").write_text(f"# T-{name}\nマイルストーン: G0\nタスク: {task}\n\n本文", encoding="utf-8")

    def take(self):
        st = hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            hline_queue.intake(self.cfg, st)
        return st

    def test_a_what_for_a_pilot_task_is_left_in_the_inbox_with_a_reason(self):
        self.put("a", "T1")
        st = self.take()
        self.assertNotIn("a", st["items"])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertEqual([s["file"] for s in st["skipped"]], ["a.md"])
        self.assertIn("T1", st["skipped"][0]["reason"])
        self.assertIn("pilot", st["skipped"][0]["reason"])

    def test_a_what_for_an_hline_task_is_taken(self):
        self.put("b", "T2")
        st = self.take()
        self.assertEqual(list(st["items"]), ["b"])
        self.assertFalse((self.inbox / "b.md").exists())
        self.assertEqual(st["skipped"], [])

    def test_only_the_pilot_what_is_left_when_both_are_in_the_inbox(self):
        self.put("a", "T1")
        self.put("b", "T2")
        st = self.take()
        self.assertEqual(list(st["items"]), ["b"])
        self.assertEqual([s["file"] for s in st["skipped"]], ["a.md"])

    def test_the_reason_reaches_report_md(self):
        import hline_report
        self.put("a", "T1")
        st = self.take()
        text = hline_report.h_section(self.cfg, st)
        self.assertIn("## 受信箱に残した What", text)
        self.assertIn("a.md", text)


if __name__ == "__main__":
    unittest.main()
