"""What の依存を台帳（base の docs/progress.yaml）の depends_on から引く（W-8）。合成の台帳と偽の git で確かめる。"""
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
import hline_report  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()


def ledger(*tasks):
    return yaml.safe_dump({"tasks": [dict(t) for t in tasks]}, allow_unicode=True)


class LedgerDeps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])
        self.git = (0, "", "")
        self.calls = []

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append(args)
        return self.git

    def put(self, name, task=None, deps=()):
        lines = [f"# T-{name}", "マイルストーン: B8.1"] + ([f"タスク: {task}"] if task else [])
        lines += ["依存: " + ", ".join(deps)] if deps else []
        (self.inbox / f"{name}.md").write_text("\n".join(lines + ["", "本文"]), encoding="utf-8")

    def take(self):
        st = hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            hline_queue.intake(self.cfg, st)
        hline_queue.refresh(st)
        return st

    def test_dependencies_come_from_the_ledger_and_are_joined_with_the_what_line(self):
        self.git = (0, ledger({"id": "T1", "status": "pending", "depends_on": ["T0", "T9"]}, {"id": "T0", "status": "pending"}), "")
        self.put("a", task="T1", deps=["x"])
        st = self.take()
        self.assertEqual(st["items"]["a"]["deps"], ["x", "T0", "T9"])
        self.assertEqual(self.calls[0][:2], ["git", "show"])
        self.assertEqual(self.calls[0][2], f"{self.cfg['base']}:docs/progress.yaml")
        self.assertIsNone(hline_queue.next_runnable(st))

    def test_a_dependency_completed_in_the_ledger_is_met_without_a_queue_item(self):
        self.git = (0, ledger({"id": "T1", "status": "pending", "depends_on": ["T0"]}, {"id": "T0", "status": "completed"}), "")
        self.put("a", task="T1")
        st = self.take()
        self.assertEqual(hline_queue.next_runnable(st), "a")

    def test_an_unfinished_ledger_dependency_blocks_until_a_queue_item_of_that_task_is_done(self):
        self.git = (0, ledger({"id": "T1", "status": "pending", "depends_on": ["T0"]}, {"id": "T0", "status": "pending"}), "")
        self.put("a", task="T1")
        self.put("b", task="T0")
        st = self.take()
        self.assertEqual(hline_queue.next_runnable(st), "b")
        st["items"]["b"]["status"] = "done"
        self.assertEqual(hline_queue.next_runnable(st), "a")

    def test_an_id_with_a_space_is_one_dependency(self):
        self.git = (0, ledger({"id": "Phase 4-2", "status": "pending", "depends_on": ["Phase 4-1"]},
                              {"id": "Phase 4-1", "status": "completed"}), "")
        self.put("a", task="Phase 4-2")
        st = self.take()
        self.assertEqual(st["items"]["a"]["deps"], ["Phase 4-1"])
        self.assertEqual(hline_queue.next_runnable(st), "a")
        self.git = (0, ledger({"id": "Phase 4-2", "status": "pending", "depends_on": ["Phase 4-1"]},
                              {"id": "Phase 4-1", "status": "pending"}), "")
        self.put("c", task="Phase 4-2")
        st = self.take()
        self.assertEqual(st["items"]["c"]["deps"], ["Phase 4-1"])
        self.assertIsNone(hline_queue.next_runnable(st))

    def test_an_unreadable_ledger_keeps_only_that_what_waiting_and_the_report_says_why(self):
        self.git = (128, "", "fatal: path not in origin/main")
        self.put("a", task="T1")
        self.put("b")
        st = self.take()
        self.assertEqual(st["items"]["a"]["status"], "waiting")
        self.assertIn("fatal", st["items"]["a"]["ledger_error"])
        self.assertEqual(hline_queue.next_runnable(st), "b")
        with mock.patch.object(hline_report, "progress_report", return_value="## 進捗ツリー\n- 人間作業: NONE\n"):
            text = hline_report.h_section(self.cfg, st)
        self.assertIn("台帳を読めず待ち", text)
        self.git = (0, "{{{ not yaml", "")
        self.put("c", task="T2")
        self.assertIn("解釈できません", self.take()["items"]["c"]["ledger_error"])

    def test_the_wait_ends_when_the_ledger_becomes_readable(self):
        self.git = (1, "", "boom")
        self.put("a", task="T1")
        st = self.take()
        hline_queue.save_state(self.cfg, st)
        self.git = (0, ledger({"id": "T1", "status": "pending"}), "")
        st = self.take()
        self.assertNotIn("ledger_error", st["items"]["a"])
        self.assertEqual(hline_queue.next_runnable(st), "a")

    def test_whats_without_a_task_or_without_depends_on_run_on_the_what_line_alone(self):
        self.git = (0, ledger({"id": "T1", "status": "pending"}), "")
        self.put("a", deps=["b"])
        self.put("b")
        self.put("c", task="T1", deps=["b"])
        st = self.take()
        self.assertEqual(st["items"]["a"]["deps"], ["b"])
        self.assertEqual(st["items"]["c"]["deps"], ["b"])
        self.assertEqual(hline_queue.next_runnable(st), "b")

    def test_without_a_task_the_ledger_is_never_read(self):
        self.put("a")
        self.take()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
