"""What が持つ台帳の項目を、取り込みが確かめて統合ブランチの台帳に積む（W-85）。

    python -m unittest tests.test_hline_ledger_entry -v

**なぜ要るか**: 台帳の PR を別に出すと、人の手番が統合 PR の承認と台帳の PR の 2 つになる。項目を What が持ち、取り込みが二重の照合と
同じ規則で確かめて統合ブランチに積めば、台帳の変更は統合 PR の点検に入る。外部は tests/test_hline_queue.py の World と同じ差し替え。
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_ledger_entry  # noqa: E402
import hline_queue  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402
from test_hline_queue import World  # noqa: E402

TASK = "W-900"
ENTRY = {"id": TASK, "title": "新しい改善", "group": "G", "target_repo": "game-harness", "status": "pending", "by": "hline",
         "purpose": "目的の文", "deliverable": "成果物の文", "serves": "R9", "rung": "R9",
         "acceptance": ["条件 1"], "oracle": [{"path": "tests/test_exists.py", "from": "existing"}],
         "verification": {"command": "python -m unittest tests.test_exists", "expected_exit_code": 0}}
BASE = {"project_goal": "g", "nodes": [{"id": "G", "title": "g"}], "constraints": [], "active_task_id": None, "tasks": []}


def section(entry):
    return "\n## 台帳の項目\n```yaml\n" + yaml.safe_dump(entry, allow_unicode=True) + "```\n"


def entry_with(**kw):
    e = copy.deepcopy(ENTRY)
    for k, v in kw.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


class Rules(unittest.TestCase):
    def reasons(self, entry, exists=lambda p: True):
        return hline_ledger_entry.problems(copy.deepcopy(BASE), entry, TASK, exists)

    def test_a_good_entry_passes(self):
        self.assertEqual(self.reasons(ENTRY), [])

    def test_each_broken_rule_is_named(self):
        cases = {
            "規則 validate": entry_with(group="nowhere"),
            "規則 status・by": entry_with(status="completed"),
            "規則 1": entry_with(purpose=None),
            "規則 2": ({}, ENTRY, lambda p: False),
            "規則 3": entry_with(verification={"command": "python -m unittest tests.other", "expected_exit_code": 0}),
            "規則 4": entry_with(verification={"command": "python -m unittest tests.test_exists && echo", "expected_exit_code": 0}),
            "規則 5": entry_with(serves="R0"),
            "規則 id": entry_with(id="W-1"),
        }
        for rule, case in cases.items():
            with self.subTest(rule=rule):
                out = self.reasons(*case[1:]) if isinstance(case, tuple) else self.reasons(case)
                self.assertTrue(any(r.startswith(rule) for r in out), out)

    def test_parse_without_a_section_is_none_and_a_broken_one_has_a_reason(self):
        self.assertEqual(hline_ledger_entry.parse("# t\n\n## What\n本文\n"), (None, None))
        self.assertIsNone(hline_ledger_entry.parse("## 台帳の項目\nなし\n")[0])
        self.assertEqual(hline_ledger_entry.parse("x" + section(ENTRY))[0]["id"], TASK)


class Stack(unittest.TestCase):
    def test_the_entry_alone_is_appended_to_the_end_once(self):
        with tempfile.TemporaryDirectory() as d:
            wt = Path(d)
            (wt / "docs").mkdir()
            base = copy.deepcopy(BASE)
            base["tasks"] = [{"id": "A", "title": "a", "group": "G", "target_repo": "r", "status": "completed"}]
            progress.save(base, wt / progress.REL_PATH)
            cfg = {"ttl_seconds": {"git": 5}, "commit_trailer": "T"}
            calls = []
            with mock.patch.object(hline_ledger_entry, "on_integration", return_value=False), \
                    mock.patch.object(hline_ledger_entry.hline_git, "new_worktree", return_value=(wt, "b")), \
                    mock.patch.object(hline_ledger_entry.hline_git, "push_tip", side_effect=lambda c, w: calls.append("push")), \
                    mock.patch.object(hline_ledger_entry, "must", side_effect=lambda a, *r: calls.append(a[1])):
                self.assertTrue(hline_ledger_entry.stack(cfg, ENTRY, "W-900"))
            ids = [t["id"] for t in progress.load(wt / progress.REL_PATH)["tasks"]]
            self.assertEqual(ids, ["A", TASK])
            self.assertEqual(calls, ["add", "commit", "push"])

    def test_the_same_id_is_not_stacked_twice(self):
        with mock.patch.object(hline_ledger_entry, "on_integration", return_value=True), \
                mock.patch.object(hline_ledger_entry.hline_git, "new_worktree") as wt:
            self.assertFalse(hline_ledger_entry.stack({}, ENTRY, "W-900"))
        wt.assert_not_called()


class Intake(World):
    def setUp(self):
        super().setUp()
        self.ledger = {"OTHER": {"deps": [], "done": False, "by": "hline", "oracle": [], "row": {}, "after": [], "rung": None}}
        self.stacked = []
        self.patch(hline_queue, "read_ledger", side_effect=lambda cfg: self.ledger)
        self.patch(hline_ledger_entry, "base_state", return_value=copy.deepcopy(BASE))
        self.patch(hline_ledger_entry, "exists_in_base", return_value=True)
        self.patch(hline_ledger_entry, "stack", side_effect=self.fake_stack)

    def fake_stack(self, cfg, entry, name):
        self.stacked.append(entry["id"])
        self.ahead_n += 1
        return True

    def reasons(self):
        return [s["reason"] for s in self.state().get("skipped", [])] + [r for r in self.report().splitlines() if "台帳" in r]

    def test_a_good_entry_is_stacked_and_the_what_waits_unprocessed_and_the_pr_is_made(self):
        self.put(TASK, task=TASK, extra=section(ENTRY))
        self.poll()
        self.assertEqual(self.stacked, [TASK])
        self.assertEqual(self.status(TASK), "waiting")
        self.assertEqual(self.implemented, [])
        self.assertEqual(self.inbox_names(), [])
        self.assertEqual(len(self.created), 1)
        self.assertIn("台帳の項目の main への取り込み待ち", self.report())

    def test_the_what_is_processed_after_the_task_reaches_the_base_ledger(self):
        self.put(TASK, task=TASK, extra=section(ENTRY))
        self.poll()
        self.pr_state = "MERGED"
        self.ledger[TASK] = {"deps": [], "done": False, "by": "hline", "oracle": [], "row": ENTRY, "after": [], "rung": "R9"}
        self.poll()
        self.assertEqual(self.implemented, [f"T-{TASK}"])
        self.assertEqual(self.status(TASK), "done")
        self.assertEqual(self.stacked, [TASK])

    def test_a_failing_entry_stacks_nothing_and_the_what_stays_in_the_inbox(self):
        for n, bad in enumerate([entry_with(group="nowhere"), entry_with(status="completed"), entry_with(purpose=None),
                                 entry_with(serves="R0"), entry_with(id="W-1")]):
            with self.subTest(n=n):
                self.put(TASK, task=TASK, extra=section(bad))
                self.poll()
                self.assertEqual(self.stacked, [])
                self.assertEqual(self.inbox_names(), [f"{TASK}.md"])
                self.assertNotIn(TASK, self.state()["items"])
                self.assertIn("規則", self.report())

    def test_a_what_without_a_section_stays_with_the_old_reason(self):
        self.put(TASK, task=TASK)
        self.poll()
        self.assertEqual(self.stacked, [])
        self.assertEqual(self.inbox_names(), [f"{TASK}.md"])
        self.assertIn(f"タスク {TASK} は base の台帳にありません", self.report())

    def test_a_task_already_in_the_base_ledger_ignores_the_section(self):
        self.ledger[TASK] = {"deps": [], "done": False, "by": "hline", "oracle": [], "row": ENTRY, "after": [], "rung": None}
        self.put(TASK, task=TASK, extra=section(entry_with(purpose=None)))
        self.poll()
        self.assertEqual(self.stacked, [])
        self.assertEqual(self.implemented, [f"T-{TASK}"])


if __name__ == "__main__":
    unittest.main()
