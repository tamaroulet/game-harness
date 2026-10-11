"""受信箱の What の取り込み（harness/hline_queue.py の intake）が、台帳のタスクを宣言した What だけを取ることの検査（W-26）。

    python -m unittest tests.test_hline_task_required -v

**なぜ要るか**: タスクを宣言しない What は台帳を進めずにコードだけを積める。`タスク:` が無い・`検証:` の行がある・base の台帳に無いか
完了したタスク・台帳を読めない What は、受信箱に残して理由を出し、取るのは台帳にある未完了の by: hline のタスクだけにする。
git は差し替え、受信箱・出力は一時ディレクトリ。
"""
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
import yaml  # noqa: E402

CFG = hline.load_config()
LEDGER = yaml.safe_dump({"tasks": [
    {"id": "T-open", "status": "pending"},
    {"id": "T-done", "status": "completed"},
    {"id": "T-pilot", "status": "pending", "by": "pilot"},
]})


def what(*lines):
    return "\n".join(["# 題", "マイルストーン: B8.1", *lines, "", "本文"])


class TaskRequired(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])

    def take(self, text, git=(0, LEDGER, "")):
        (self.inbox / "a.md").write_text(text, encoding="utf-8")
        st = hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", return_value=git):
            taken = hline_queue.intake(self.cfg, st)
        return st, taken

    def assertLeft(self, st, taken, *words):
        self.assertEqual(taken, [])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertEqual(st["items"], {})
        self.assertEqual([s["file"] for s in st["skipped"]], ["a.md"])
        for w in words:
            self.assertIn(w, st["skipped"][0]["reason"])

    def test_a_what_without_a_task_is_left_even_with_a_verification(self):
        self.assertLeft(*self.take(what()), "タスク:")
        self.assertLeft(*self.take(what("検証: python -m unittest tests.test_x")), "タスク:")

    def test_a_what_with_a_verification_line_is_left_even_with_a_task(self):
        self.assertLeft(*self.take(what("タスク: T-open", "検証: python -m unittest tests.test_x")), "検証:")

    def test_a_what_for_a_task_missing_from_the_ledger_is_left(self):
        self.assertLeft(*self.take(what("タスク: T-ghost")), "T-ghost", "台帳にありません")

    def test_a_what_for_a_completed_task_is_left(self):
        self.assertLeft(*self.take(what("タスク: T-done")), "T-done", "完了")

    def test_a_what_for_a_pilot_task_is_left(self):
        self.assertLeft(*self.take(what("タスク: T-pilot")), "T-pilot", "by: pilot")

    def test_a_what_is_left_when_the_ledger_cannot_be_read_and_taken_at_the_next_run(self):
        st, taken = self.take(what("タスク: T-open"), git=(128, "", "fatal: bad ref"))
        self.assertLeft(st, taken, "台帳を読めない", "fatal")
        st, taken = self.take(what("タスク: T-open"))
        self.assertEqual(taken, ["a"])
        self.assertEqual(st["skipped"], [])

    def test_a_what_for_a_pending_hline_task_without_a_verification_is_taken(self):
        st, taken = self.take(what("タスク: T-open"))
        self.assertEqual(taken, ["a"])
        self.assertFalse((self.inbox / "a.md").exists())
        self.assertEqual(st["skipped"], [])
        self.assertEqual(st["items"]["a"]["status"], "waiting")
        self.assertEqual(st["items"]["a"]["task"], "T-open")


if __name__ == "__main__":
    unittest.main()
