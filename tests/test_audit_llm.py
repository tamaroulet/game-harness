"""受信箱の What の取り込み（harness/hline_queue.py の intake）の監修役（LLM。harness/hline_review.py）の検査（W-30）。

    python -m unittest tests.test_audit_llm -v

**なぜ要るか**: 機械の監修を通っても、タスクの目的や横木に的外れな What は実装の前に止めたい。監修役は取り込む前に 1 回だけ呼ぶ。
pass なら取り込み、revise と読めない出力は受信箱に残して理由を出す。同じ組み合わせには 2 回目から呼ばない。呼び出しの失敗は控えない。
git・監修役の CLI は差し替え、受信箱・出力は一時ディレクトリ。固定のパスやポートは使わない。
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
import hline_review  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()
INSTR = "監修役への指示の本文\n"
LEDGER = yaml.safe_dump({"tasks": [
    {"id": "T1", "status": "pending", "title": "対象", "purpose": "目的P", "serves": "R3", "acceptance": "受け入れA",
     "oracle": [], "verification": {"command": "python -m unittest tests.test_x", "expected_exit_code": 0}},
    {"id": "P1", "status": "pending", "rung": "R3", "title": "同じ横木", "acceptance": "仲間の受け入れ"},
    {"id": "P2", "status": "pending", "rung": "R9", "title": "別の横木", "acceptance": "別の受け入れ"},
]}, allow_unicode=True)
BAD_SHAPE = "\n".join(["# 題", "マイルストーン: B8.1", "タスク: T1", "", "本文だけ"])
GOOD = "\n".join(["# 題", "マイルストーン: B8.1", "タスク: T1", "", "## What", "本文", "", "## 合格条件", "1. 満たす"])


def verdict(v, findings=()):
    return json.dumps({"result": json.dumps({"verdict": v, "findings": list(findings)}, ensure_ascii=False)})


class AuditLlm(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])
        self.instr = INSTR
        self.replies, self.inputs, self.argv = [], [], []
        for p in (mock.patch.object(hline_review.proc, "resolve_cli", return_value=["claude"]),
                  mock.patch.object(hline_review.hline_base, "pinned_models", return_value=["m"]),
                  mock.patch.object(hline_queue.proc, "run", side_effect=self.fake_run)):
            p.start()
        self.addCleanup(mock.patch.stopall)

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if args[0] == "git":
            if args[1] == "show":
                return (0, self.instr if args[2].endswith("auditor.md") else LEDGER, "")
            return (0, "", "")
        self.argv.append(list(args))
        self.inputs.append(input)
        reply = self.replies.pop(0) if self.replies else verdict("pass")
        return reply if isinstance(reply, tuple) else (0, reply, "")

    def take(self, text):
        (self.inbox / "a.md").write_text(text, encoding="utf-8")
        st = hline_queue.load_state(self.cfg)
        taken = hline_queue.intake(self.cfg, st)
        return st, taken

    def test_pass_is_taken(self):
        st, taken = self.take(GOOD)
        self.assertEqual(taken, ["a"])
        self.assertEqual(st["skipped"], [])
        self.assertFalse((self.inbox / "a.md").exists())
        self.assertEqual(len(self.inputs), 1)

    def test_revise_is_left_with_the_findings_as_the_reason(self):
        self.replies = [verdict("revise", [{"kind": "goal_mismatch", "text": "的外れ"}, {"kind": "ambiguity", "text": "曖昧"}])]
        st, taken = self.take(GOOD)
        self.assertEqual(taken, [])
        self.assertTrue((self.inbox / "a.md").exists())
        reason = st["skipped"][0]["reason"]
        for s in ("goal_mismatch", "的外れ", "ambiguity", "曖昧"):
            self.assertIn(s, reason)

    def test_an_unreadable_output_is_left_with_the_head_of_it_and_is_remembered(self):
        self.replies = [(0, "JSON ではない出力です", "")]
        st, taken = self.take(GOOD)
        self.assertEqual(taken, [])
        self.assertIn("読めません", st["skipped"][0]["reason"])
        self.assertIn("JSON ではない出力です", st["skipped"][0]["reason"])
        st, taken = self.take(GOOD)
        self.assertEqual(len(self.inputs), 1)
        self.assertIn("JSON ではない出力です", st["skipped"][0]["reason"])

    def test_a_wrong_shape_is_unreadable(self):
        self.replies = [verdict("maybe")]
        st, taken = self.take(GOOD)
        self.assertEqual(taken, [])
        self.assertIn("読めません", st["skipped"][0]["reason"])

    def test_the_second_time_does_not_call_and_a_change_does(self):
        self.replies = [verdict("revise", [{"kind": "oversize", "text": "大きい"}])]
        st, _ = self.take(GOOD)
        st, taken = self.take(GOOD)
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual(taken, [])
        self.assertIn("大きい", st["skipped"][0]["reason"])
        self.take(GOOD.replace("本文", "別の本文"))   # What を変えると呼ぶ
        self.assertEqual(len(self.inputs), 2)
        self.instr = "別の指示\n"                    # 指示を変えると呼ぶ
        self.take(GOOD.replace("本文", "別の本文"))
        self.assertEqual(len(self.inputs), 3)

    def test_a_what_that_failed_the_machine_audit_is_not_sent(self):
        st, taken = self.take(BAD_SHAPE)
        self.assertEqual(taken, [])
        self.assertEqual(self.inputs, [])
        self.assertEqual(len(st["skipped"]), 1)

    def test_a_failed_call_is_not_remembered_and_is_called_again(self):
        for failure in ((127, "", "コマンドが見つかりません"), (124, "", "TTL超過")):
            self.replies = [failure]
            st, taken = self.take(GOOD)
            self.assertEqual(taken, [])
            self.assertTrue((self.inbox / "a.md").exists())
            self.assertIn("控えず", st["skipped"][0]["reason"])
        self.assertEqual(len(self.inputs), 2)
        self.assertFalse(list((self.out / "review").glob("*.json")))
        st, taken = self.take(GOOD)   # 次の走行で呼び直す
        self.assertEqual(taken, ["a"])
        self.assertEqual(len(self.inputs), 3)

    def test_the_input_has_the_instruction_the_what_the_task_fields_and_the_rung_peers(self):
        self.take(GOOD)
        text = self.inputs[0]
        head, _, tail = text.partition("\n---\n")
        self.assertEqual(head, INSTR.rstrip("\n"))
        self.assertTrue(text.startswith(INSTR))
        self.assertIn(GOOD.rstrip("\n"), tail)
        for s in ("目的P", "受け入れA", "R3", "python -m unittest tests.test_x", "title", "purpose", "serves", "acceptance",
                  "oracle", "verification"):
            self.assertIn(s, tail)
        self.assertIn("同じ横木", tail)
        self.assertIn("仲間の受け入れ", tail)
        self.assertNotIn("別の横木", tail)

    def test_the_reviewer_is_called_without_tools(self):
        self.take(GOOD)
        args = self.argv[0]
        self.assertIn(self.cfg["reviewer"]["model"], args)
        self.assertEqual(self.cfg["reviewer"]["model"], self.cfg["implementer"]["model"])
        self.assertEqual(self.cfg["reviewer"]["cli"], self.cfg["implementer"]["cli"])
        self.assertIn("--disallowedTools", args)
        self.assertIn("Bash", args[args.index("--disallowedTools") + 1])
        self.assertIn("Write", args[args.index("--disallowedTools") + 1])


if __name__ == "__main__":
    unittest.main()
