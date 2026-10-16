"""受信箱の What の取り込み（harness/hline_queue.py の intake）の機械の監修の検査（W-29）。

    python -m unittest tests.test_audit_machine -v

**なぜ要るか**: オラクルの無い What と形の崩れた What は、実装の前に止める。宣言したタスクの oracle のうち `from` が existing か
完了したタスクのものは、そのファイルが base に無ければ受信箱に残す。本文は `## What` と、箇条が 1〜4 の `## 合格条件` を持つこと。
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
    {"id": "T-done", "status": "completed"},
    {"id": "T-open", "status": "pending"},
    {"id": "T-existing", "status": "pending", "oracle": [{"path": "tests/old.py", "from": "existing"}]},
    {"id": "T-fromdone", "status": "pending", "oracle": [{"path": "tests/made.py", "from": "T-done"}]},
    {"id": "T-self", "status": "pending", "oracle": [{"path": "tests/new.py", "from": "self"}]},
    {"id": "T-fromopen", "status": "pending", "oracle": [{"path": "tests/later.py", "from": "T-open"}]},
]}, allow_unicode=True)

BODY = ["## What", "本文", "", "## 合格条件", "1. 満たす"]


def what(task, *body):
    return "\n".join(["# 題", "マイルストーン: B8.1", f"タスク: {task}", "", *(body or BODY)])


class AuditMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.object(hline_queue.hline_review, "review", return_value=None).start()   # 監修役（LLM）の段は pass に差し替える
        self.addCleanup(mock.patch.stopall)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])
        self.present = set()
        self.checked = []

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if args[:2] == ["git", "cat-file"]:
            self.checked.append(args[3])
            return (0, "", "") if args[3] in self.present else (128, "", "fatal: not found")
        return (0, LEDGER, "")

    def take(self, text):
        (self.inbox / "a.md").write_text(text, encoding="utf-8")
        st = hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run):
            taken = hline_queue.intake(self.cfg, st)
        return st, taken

    def assertLeft(self, st, taken, *words):
        self.assertEqual(taken, [])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertEqual(st["items"], {})
        self.assertEqual([s["file"] for s in st["skipped"]], ["a.md"])
        for w in words:
            self.assertIn(w, st["skipped"][0]["reason"])

    def assertTaken(self, st, taken):
        self.assertEqual(taken, ["a"])
        self.assertFalse((self.inbox / "a.md").exists())
        self.assertEqual(st["skipped"], [])

    def test_a_missing_existing_oracle_leaves_the_what_with_the_path(self):
        self.assertLeft(*self.take(what("T-existing")), "tests/old.py")

    def test_a_missing_oracle_of_a_completed_task_leaves_the_what_with_the_path(self):
        self.assertLeft(*self.take(what("T-fromdone")), "tests/made.py")

    def test_oracles_present_at_base_are_taken(self):
        base = self.cfg["base"]
        self.present = {f"{base}:tests/old.py", f"{base}:tests/made.py"}
        self.assertTaken(*self.take(what("T-existing")))
        self.assertTaken(*self.take(what("T-fromdone")))

    def test_self_and_unfinished_task_oracles_are_not_checked(self):
        self.assertTaken(*self.take(what("T-self")))
        self.assertTaken(*self.take(what("T-fromopen")))
        self.assertEqual(self.checked, [])

    def test_a_missing_what_section_leaves_the_what(self):
        self.assertLeft(*self.take(what("T-open", "本文", "", "## 合格条件", "1. 満たす")), "## What")

    def test_a_missing_criteria_section_leaves_the_what(self):
        self.assertLeft(*self.take(what("T-open", "## What", "本文", "- 項目")), "## 合格条件")

    def test_zero_and_five_items_leave_the_what(self):
        self.assertLeft(*self.take(what("T-open", "## What", "本文", "", "## 合格条件", "項目は無い")), "0")
        five = [f"{n}. 項目" for n in range(1, 6)]
        self.assertLeft(*self.take(what("T-open", "## What", "本文", "", "## 合格条件", *five)), "5")

    def test_items_are_counted_up_to_the_next_section_and_only_when_not_indented(self):
        body = ["## What", "- 数えない", "", "## 合格条件", "1. a", "  - 字下げは数えない", "- b", "", "## 補足", "- c", "- d", "- e", "- f"]
        self.assertTaken(*self.take(what("T-open", *body)))

    def test_one_and_four_items_are_taken(self):
        four = [f"- 項目{n}" for n in range(4)]
        self.assertTaken(*self.take(what("T-open", "## What", "本文", "", "## 合格条件", *four)))
        self.assertTaken(*self.take(what("T-open")))


if __name__ == "__main__":
    unittest.main()
