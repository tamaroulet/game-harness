"""依存先がキューにも台帳の完了にも無い待ちの What があっても、統合 PR を止めない（W-77）。

    python -m unittest tests.test_hline_orphan_wait -v

**なぜ要るか**: 依存先の欠けた待ちが 1 件残ると統合 PR が出ず、人が見る PR も無いままラインが黙って止まる。
外部は tests/test_hline_queue.py の World と同じ差し替え（偽の git・gh・実装役）。
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline  # noqa: E402
import hline_queue  # noqa: E402
from test_hline_queue import World  # noqa: E402


class OrphanWait(World):
    def test_a_what_without_its_dependency_stays_waiting_and_the_pr_is_made(self):
        self.put("010-a", deps=["020-ghost"])
        self.put("020-b")
        self.poll()
        self.assertEqual(self.implemented, ["T-020-b"])
        self.assertEqual(self.integrated, ["020-b"])
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.status("010-a"), "waiting")
        self.assertEqual(self.status("020-b"), "done")

    def test_a_what_that_waits_for_the_orphan_does_not_stop_the_pr(self):
        self.put("010-a", deps=["020-ghost"])
        self.put("015-c", deps=["010-a"])
        self.put("020-b")
        self.poll()
        self.assertEqual(self.integrated, ["020-b"])
        self.assertEqual(len(self.created), 1)
        self.assertEqual((self.status("010-a"), self.status("015-c")), ("waiting", "waiting"))

    def test_the_report_names_the_missing_dependency(self):
        self.put("010-a", deps=["020-ghost", "030-b"])
        self.put("030-b")
        self.poll()
        line = next(x for x in self.report().splitlines() if x.startswith("- 待ち"))
        self.assertIn("（依存先なし: 020-ghost）", line)
        self.assertNotIn("依存先なし: 020-ghost, 030-b", line)

    def test_a_dependency_present_in_the_queue_is_not_missing(self):
        self.put("010-a", deps=["020-b"])
        self.put("020-b")
        self.poll()
        self.assertNotIn("依存先なし", self.report())
        self.assertEqual((self.status("010-a"), self.status("020-b")), ("done", "done"))

    def test_a_dependency_done_in_the_ledger_is_not_missing(self):
        st = {"items": {"a": {"status": "waiting", "deps": ["T-1", "ghost"], "met": ["T-1"]},
                        "b": {"status": "done", "deps": [], "task": "T-2"}}}
        self.assertEqual(hline_queue.missing_deps(st, st["items"]["a"]), ["ghost"])
        st["items"]["a"]["deps"] = ["b", "T-2"]
        self.assertEqual(hline_queue.missing_deps(st, st["items"]["a"]), [])

    def test_no_pr_while_a_what_waits_because_the_ledger_cannot_be_read(self):
        self.put("010-a", deps=["020-ghost"])
        self.put("020-b")
        with mock.patch.object(hline, "ahead", return_value=0):   # 1 回目は PR を出させない
            self.poll()
        self.assertEqual(self.created, [])
        self.ahead_n = 1
        with mock.patch.object(hline_queue, "read_ledger", side_effect=ValueError("読めない")):
            self.poll()
        self.assertEqual(self.created, [])
        self.assertIn("台帳を読めず待ち", self.report())
        self.poll()   # 台帳が読めるようになれば出る
        self.assertEqual(len(self.created), 1)
