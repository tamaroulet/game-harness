"""台帳の after（弱い依存）を H ラインが守り、未収束では止めない（W-72）。台帳は偽の git で差し替える。"""
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
import hline_task  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()


def ledger(*tasks):
    return yaml.safe_dump({"tasks": [dict(t) for t in tasks]}, allow_unicode=True)


def item(task, status, **kw):
    return {"status": status, "title": f"T-{task}", "task": task, "milestone": "B8.1", "deps": [], "header_deps": [], **kw}


class SoftDeps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.object(hline_queue.hline_review, "review", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])
        self.git = (0, "", "")

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        return self.git

    def apply(self, st):
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            hline_queue.apply_ledger(self.cfg, st)
        hline_queue.refresh(st)
        return st

    def state(self, **items):
        return {"items": items, "awaiting_pr": None, "skipped": []}

    LEDGER = ledger({"id": "D", "status": "pending", "after": ["U"]}, {"id": "U", "status": "pending"})

    def test_waits_while_the_upstream_is_waiting_processing_or_frozen(self):
        self.git = (0, self.LEDGER, "")
        for status in ("waiting", "processing", "frozen"):
            st = self.apply(self.state(d=item("D", "waiting"), u=item("U", status)))
            self.assertEqual(st["items"]["d"]["after"], ["U"])
            self.assertNotEqual(hline_queue.next_runnable(st), "d", status)

    def test_runs_when_the_upstream_is_done_unconverged_or_absent_or_completed_in_the_ledger(self):
        self.git = (0, self.LEDGER, "")
        for upstream in (item("U", "done"), item("U", "unconverged"), None):
            items = {"d": item("D", "waiting")}
            if upstream:
                items["u"] = upstream
            st = self.apply(self.state(**items))
            self.assertEqual(hline_queue.next_runnable(st), "d")
        self.git = (0, ledger({"id": "D", "status": "pending", "after": ["U"]}, {"id": "U", "status": "completed"}), "")
        self.assertEqual(hline_queue.next_runnable(self.apply(self.state(d=item("D", "waiting")))), "d")

    def test_after_does_not_freeze(self):
        self.git = (0, self.LEDGER, "")
        st = self.apply(self.state(d=item("D", "waiting"), u=item("U", "unconverged")))
        self.assertEqual(st["items"]["d"]["status"], "waiting")
        self.assertEqual(st["items"]["d"]["deps"], [])

    def test_unconverged_upstream_is_a_warning_in_the_tries_and_the_report(self):
        self.git = (0, self.LEDGER, "")
        st = self.apply(self.state(d=item("D", "waiting"), u=item("U", "unconverged", reason="r")))
        host = mock.Mock()
        host.what_path.return_value = self.tmp / "what.md"
        host.what_path.return_value.write_text("# x\n", encoding="utf-8")
        host.slug.return_value = "d"
        host.today.return_value = "2026-10-12"
        host.new_worktree.return_value = (self.tmp / "wt", "b")
        host.changed_paths.return_value = []
        host.self_change.return_value = []
        host.run_task.return_value = (self.tmp / "wt", "b", [{"warnings": []}])
        host.mark = lambda *a, **k: None
        cfg = {**self.cfg, "infra_retry": {"max_retries": 0, "wait_seconds": 0}}
        self.assertTrue(hline_task.process(host, cfg, st, "d"))
        d = st["items"]["d"]
        self.assertEqual(d["after_unconverged"], ["U"])
        self.assertIn("U", d["tries"][-1]["warnings"][-1])
        with mock.patch.object(hline_report, "progress_report", return_value="## 進捗ツリー\n- 人間作業: NONE\n"):
            text = hline_report.h_section(cfg, st)
        self.assertIn("after の上流 U が未収束", text)

    def test_unconverged_row_counts_frozen_downstream_and_mainline_impact(self):
        self.git = (0, ledger({"id": "A", "status": "pending"}, {"id": "B", "status": "pending", "depends_on": ["A"]},
                              {"id": "C", "status": "pending", "depends_on": ["B"]},
                              {"id": "R", "status": "pending", "rung": "R1", "depends_on": ["C"]},
                              {"id": "X", "status": "pending"}, {"id": "Y", "status": "pending", "depends_on": ["X"]},
                              {"id": "Z", "status": "pending", "after": ["A"]}), "")
        st = self.apply(self.state(a=item("A", "unconverged", reason="r"), b=item("B", "waiting"), c=item("C", "waiting"),
                                   x=item("X", "unconverged", reason="r"), y=item("Y", "waiting"), z=item("Z", "waiting")))
        self.assertEqual({n: i["status"] for n, i in st["items"].items()},
                         {"a": "unconverged", "b": "frozen", "c": "frozen", "x": "unconverged", "y": "frozen", "z": "waiting"})
        with mock.patch.object(hline_report, "progress_report", return_value="## 進捗ツリー\n- 人間作業: NONE\n"):
            text = hline_report.h_section(self.cfg, st)
        row = next(l for l in text.splitlines() if l.startswith("- 未収束"))
        self.assertIn("a（r）", row)
        self.assertIn("（凍結した下流 2 件・本線への影響 あり）", row.split("x（")[0])
        self.assertIn("（凍結した下流 1 件・本線への影響 なし）", row.split("x（")[1])


if __name__ == "__main__":
    unittest.main()
