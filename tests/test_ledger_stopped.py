"""台帳の「止めた」「取り下げた」（W-145）。受入と試験が見る点は総監督が書いた（reply-architect-20261023-2 の (c)）。

    python -m unittest tests.test_ledger_stopped

試験が見ること（総監督の一覧。削らない）:
1. progress.validate が 2 つの新しい値を通し、ほかの値を落とす
2. 偽の台帳で、止めたタスクが次のタスクに選ばれず、H ラインの取り込みが理由つきで受信箱に残す
3. 本物の docs/progress.yaml で、止めた 6 件・取り下げた W-143 の status と、validate が空を返すこと
4. 本物の台帳で、通しのタスクが ADR-004 の取り下げたタスク（W-127〜W-131・W-133・W-137〜W-139）に依存していないこと。
   通しは W-140 から W-170 に移した（台帳の案。W-140 は取り下げ）ので、W-170 と W-140 の両方を見る
総監督の検め（reply-architect-20261023-3 の 1）で足した点:
5. 偽の台帳で、検証の無い立て直しのタスクが前の行にあっても、次に選ばれるのは検証のあるタスク。どちらも無ければ active_task_id が空
6. 本物の台帳で、立て直しのノードの下で in_progress のタスクは、受入・検証・オラクルの 3 つを持つ
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import goal_distance  # noqa: E402
import hline  # noqa: E402
import hline_queue  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()
LEDGER = ROOT / progress.REL_PATH
DECISION_LOG = ROOT / "docs" / "design" / "decision_log.md"
STOPPED = ("W-61", "W-25", "W-94", "W-108", "W-151")
WITHDRAWN = ("W-143", "W-127", "W-128", "W-129", "W-130", "W-131", "W-133", "W-137", "W-138", "W-139", "W-140")
ADR004_REST = ("W-127", "W-128", "W-129", "W-130", "W-131", "W-133", "W-137", "W-138", "W-139")
SECTORS = 7
REUSED_DEPS = {"W-132": [], "W-134": ["W-156"], "W-135": ["W-156"], "W-136": []}   # 台帳の案の依存


def task(tid, status, **extra):
    return {"id": tid, "title": f"title {tid}", "group": "G1", "target_repo": "r", "status": status, **extra}


def sample(*tasks, active=None):
    return {"project_goal": "g", "active_task_id": active, "constraints": ["c"],
            "nodes": [{"id": "A", "title": "root", "parent": None}, {"id": "G1", "title": "group", "parent": "A"}],
            "tasks": list(tasks), "last_verification": None}


class Values(unittest.TestCase):
    """1. 新しい 2 つの値を通し、ほかの値を落とす。"""

    def test_the_two_new_values_pass_with_a_reason(self):
        for status in ("stopped", "withdrawn"):
            self.assertEqual(progress.validate(sample(task("T1", status, reason="立て直しの間は止める"))), [], status)

    def test_other_values_fail(self):
        for status in ("halted", "stop", "cancelled", "done", "", None):
            problems = progress.validate(sample(task("T1", status, reason="r")))
            self.assertTrue(any("T1" in p and "status" in p for p in problems), (status, problems))

    def test_a_held_task_without_a_reason_fails(self):
        for status in ("stopped", "withdrawn"):
            for reason in (None, "", "  "):
                problems = progress.validate(sample(task("T1", status, reason=reason)))
                self.assertTrue(any("T1" in p and "reason" in p for p in problems), (status, reason, problems))


class NotPicked(unittest.TestCase):
    """2 の前半と受入 1・3：次のタスクに選ばれず、分母に入らず、依存した未完了は一覧に出るか不合格になる。"""

    def test_a_held_task_is_not_made_the_next_task(self):
        for status in ("stopped", "withdrawn"):
            state = sample(task("T1", status, reason="r"), task("T2", "pending", verification=VERIFY))
            self.assertEqual(progress.advance_active(state), "T2", status)
            self.assertEqual(state["tasks"][0]["status"], status)

    def test_no_next_task_when_only_held_tasks_are_left(self):
        state = sample(task("T1", "stopped", reason="r"), task("T2", "withdrawn", reason="r"), task("T3", "completed"))
        self.assertIsNone(progress.advance_active(state))
        self.assertEqual([t["status"] for t in state["tasks"]], ["stopped", "withdrawn", "completed"])

    def test_a_held_task_cannot_be_completed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "docs").mkdir()
        state = sample(task("T1", "stopped", reason="r", verification={"command": "exit 0", "expected_exit_code": 0}))
        progress.save(state, tmp / progress.REL_PATH)
        with self.assertRaises(progress.ProgressError):
            progress.complete("T1", repo_root=tmp, fetch=False, out=lambda *_: None, require_active=False)

    def test_an_open_task_on_a_stopped_task_is_listed_but_passes(self):
        state = sample(task("T1", "stopped", reason="r"), task("T2", "pending", depends_on=["T1"]))
        self.assertEqual(progress.validate(state), [])
        listed = progress.held_dependents(state["tasks"])
        self.assertEqual(len(listed), 1)
        self.assertIn("T2", listed[0])
        self.assertIn("T1", listed[0])
        self.assertIsNone(progress.advance_active(state))

    def test_check_prints_the_open_tasks_on_a_stopped_task(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "docs").mkdir()
        progress.save(sample(task("T1", "stopped", reason="r"), task("T2", "pending", depends_on=["T1"])), tmp / progress.REL_PATH)
        out = io.StringIO()
        with mock.patch.object(progress, "ROOT", tmp), contextlib.redirect_stdout(out):
            code = progress.main(["check"])
        self.assertEqual(code, 0)
        self.assertIn("待ち: task T2 は止めた T1 に依存して待っています", out.getvalue())

    def test_an_open_task_on_a_withdrawn_task_fails(self):
        state = sample(task("T1", "withdrawn", reason="r"), task("T2", "pending", depends_on=["T1"]))
        self.assertTrue(any("T2" in p and "T1" in p for p in progress.validate(state)))

    def test_a_held_task_on_a_withdrawn_task_passes(self):
        state = sample(task("T1", "withdrawn", reason="r"), task("T2", "stopped", reason="r", depends_on=["T1"]))
        self.assertEqual(progress.validate(state), [])

    def test_held_tasks_are_not_in_the_denominator(self):
        tasks = [task("R", "pending", rung="R4", depends_on=["P1", "P2", "P3"]), task("P1", "completed"),
                 task("P2", "stopped", reason="r", depends_on=["P4"]), task("P3", "withdrawn", reason="r"),
                 task("P4", "pending")]
        self.assertEqual(goal_distance.prerequisite_note(tasks, "R4"), "（前提タスク 1／2）")

    def test_the_tree_marks_held_tasks_and_leaves_them_out_of_the_count(self):
        state = sample(task("T1", "completed"), task("T2", "stopped", reason="r"), task("T3", "withdrawn", reason="r"),
                       task("T4", "in_progress"), active="T4")
        text = progress.tree(state, True)
        self.assertIn("G1 group（1/2、止めた 1、取り下げた 1）", text)
        self.assertIn("- [ ] T2 title T2（止めた）", text)
        self.assertIn("- [-] T3 title T3（取り下げた）", text)


VERIFY = {"command": "python -m unittest tests.x", "expected_exit_code": 0}


def rebuild_ledger(*tasks):
    state = sample(*tasks)
    state["nodes"] += [{"id": "rebuild", "title": "rebuild", "parent": "A"},
                       {"id": "rebuild", "title": "sector", "parent": "rebuild"}]
    return state


class WaitForAcceptance(unittest.TestCase):
    """5：立て直しのタスクは、検証が書かれるまで次のタスクに選ばない（操縦士のタスクも同じ）。"""

    def test_a_rebuild_task_with_a_verification_is_chosen_over_an_earlier_one_without(self):
        for by in ("executor", "pilot", "director"):
            state = rebuild_ledger({**task("T1", "pending", by=by), "group": "rebuild"},
                                   {**task("T2", "pending"), "group": "rebuild", "verification": VERIFY})
            self.assertEqual(progress.advance_active(state), "T2", by)
            self.assertEqual([t["status"] for t in state["tasks"]], ["pending", "in_progress"])

    def test_no_active_task_when_no_rebuild_task_has_a_verification(self):
        state = rebuild_ledger({**task("T1", "in_progress"), "group": "rebuild"}, {**task("T2", "pending"), "group": "rebuild"})
        self.assertIsNone(progress.advance_active(state))
        self.assertIsNone(state["active_task_id"])
        self.assertEqual([t["status"] for t in state["tasks"]], ["pending", "pending"])

    def test_tasks_outside_the_rebuild_node_are_chosen_as_before(self):
        state = rebuild_ledger(task("T1", "pending", verification=VERIFY))
        self.assertEqual(progress.advance_active(state), "T1")


class Intake(unittest.TestCase):
    """2 の後半：H ラインの取り込みが、止めた・取り下げたタスクの What を理由つきで受信箱に残す。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.object(hline_queue.hline_review, "review", return_value=None).start()   # 監修役（LLM）の段は pass に差し替える
        self.addCleanup(mock.patch.stopall)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["G0"])
        tasks = [task("T1", "stopped", reason="立て直しの間は止める"), task("T2", "withdrawn", reason="ADR-004 を取り下げた"),
                 task("T3", "pending")]
        self.git = (0, yaml.safe_dump({"tasks": tasks}, allow_unicode=True), "")

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        return self.git

    def put(self, name, tid):
        (self.inbox / f"{name}.md").write_text(f"# T-{name}\nマイルストーン: G0\nタスク: {tid}\n\n## What\n本文\n\n## 合格条件\n1. 満たす",
                                               encoding="utf-8")

    def take(self, st=None):
        st = st or hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            hline_queue.intake(self.cfg, st)
        return st

    def test_held_whats_stay_in_the_inbox_with_the_reason(self):
        self.put("a", "T1")
        self.put("b", "T2")
        self.put("c", "T3")
        st = self.take()
        self.assertEqual(list(st["items"]), ["c"])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertTrue((self.inbox / "b.md").exists())
        reasons = {s["file"]: s["reason"] for s in st["skipped"]}
        self.assertEqual(sorted(reasons), ["a.md", "b.md"])
        self.assertIn("止めた", reasons["a.md"])
        self.assertIn("立て直しの間は止める", reasons["a.md"])
        self.assertIn("取り下げた", reasons["b.md"])
        self.assertIn("ADR-004 を取り下げた", reasons["b.md"])

    def test_the_reason_reaches_report_md(self):
        import hline_report
        self.put("a", "T1")
        text = hline_report.h_section(self.cfg, self.take())
        self.assertIn("## 受信箱に残した What", text)
        self.assertIn("立て直しの間は止める", text)

    def test_a_queued_what_whose_task_was_stopped_is_not_run(self):
        st = hline_queue.load_state(self.cfg)
        st["items"]["x"] = {"status": "waiting", "task": "T1", "deps": [], "header_deps": []}
        st["items"]["y"] = {"status": "waiting", "task": "T3", "deps": [], "header_deps": []}
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            hline_queue.apply_ledger(self.cfg, st)
        self.assertIn("止めた", st["items"]["x"]["held"])
        self.assertEqual(hline_queue.next_runnable(st), "y")

    def test_a_director_task_is_not_taken(self):
        self.git = (0, yaml.safe_dump({"tasks": [task("T4", "pending", by="director")]}, allow_unicode=True), "")
        self.put("d", "T4")
        st = self.take()
        self.assertEqual(st["items"], {})
        self.assertIn("director", st["skipped"][0]["reason"])


class RealLedger(unittest.TestCase):
    """3・4 と受入 2・4：本物の台帳。"""

    def setUp(self):
        self.state = progress.load(LEDGER)
        self.tasks = {t["id"]: t for t in self.state["tasks"]}
        self.nodes = {n["id"]: n for n in self.state["nodes"]}

    def test_the_ledger_validates(self):
        self.assertEqual(progress.validate(self.state), [])

    def test_the_seven_tasks_are_held(self):
        for tid in STOPPED:
            self.assertEqual(self.tasks[tid]["status"], "stopped", tid)
        self.assertEqual(self.tasks["W-143"]["status"], "withdrawn")

    def test_the_rest_of_adr004_is_withdrawn(self):
        for tid in WITHDRAWN:
            self.assertEqual(self.tasks[tid]["status"], "withdrawn", tid)

    def test_every_held_task_has_a_reason_and_a_decision_log_row(self):
        rows = [line for line in DECISION_LOG.read_text(encoding="utf-8").splitlines() if line.startswith("| 20")]
        for tid in STOPPED + WITHDRAWN:
            with self.subTest(task=tid):
                self.assertTrue(self.tasks[tid]["reason"].strip())
                self.assertTrue(any(re.search(rf"(?<![\w-]){re.escape(tid)}(?!\d)", r) for r in rows))

    def test_the_active_task_is_a_rebuild_task_or_none(self):
        tid = self.state["active_task_id"]
        if tid is not None:
            self.assertIn(self.tasks[tid]["group"], self.rebuild_nodes())

    def test_an_in_progress_rebuild_task_has_its_acceptance_verification_and_oracle(self):
        for t in self.state["tasks"]:
            if t["status"] == "in_progress" and progress.in_rebuild(self.state, t):
                with self.subTest(task=t["id"]):
                    self.assertTrue(t.get("acceptance"))
                    self.assertTrue(t.get("verification"))
                    self.assertTrue(t.get("oracle"))

    def rebuild_nodes(self):
        found = {"rebuild"} | {n for n, x in self.nodes.items() if x.get("parent") == "rebuild"}
        return found

    def test_the_rebuild_node_is_under_a_and_has_seven_sectors(self):
        self.assertEqual(self.nodes["rebuild"]["parent"], "A")
        self.assertEqual(len(self.rebuild_nodes() - {"rebuild"}), 0)

    def test_the_reused_ids_have_the_new_plan(self):
        for tid, deps in REUSED_DEPS.items():
            with self.subTest(task=tid):
                t = self.tasks[tid]
                self.assertEqual(t["status"], "pending")
                self.assertEqual(t["group"], "rebuild")
                self.assertEqual(t.get("depends_on") or [], deps)
                self.assertNotIn("serves", t)
                self.assertNotIn("ADR-004", t["purpose"])

    def test_the_trial_does_not_depend_on_the_withdrawn_adr004_tasks(self):
        def reach(tid):
            seen, stack = set(), list(self.tasks[tid].get("depends_on") or [])
            while stack:
                d = stack.pop()
                if d not in seen:
                    seen.add(d)
                    stack += self.tasks[d].get("depends_on") or []
            return seen
        self.assertFalse(reach("W-170") & set(ADR004_REST))
        self.assertEqual(self.tasks["W-140"]["status"], "withdrawn")
        for tid, t in self.tasks.items():
            if t["status"] in progress.OPEN:
                self.assertFalse(set(t.get("depends_on") or []) & set(ADR004_REST), tid)


if __name__ == "__main__":
    unittest.main()
