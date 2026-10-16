"""計画の監修のループ（harness/plan_loop.py）の検査。計画役と監修役は偽物で、一時ディレクトリの受信箱に偽の出力を書かせる。
本物の CLI も git も呼ばない。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_base  # noqa: E402
import hline_review  # noqa: E402
import plan_loop  # noqa: E402
import planner  # noqa: E402

AGENT = {"cli": "claude", "instruction": "docs/prompts/planner.md", "ttl_seconds": 5}
SPEC = "# 仕様\n- REQ-01：足し算をする\n- REQ-02：引き算をする\n"
STAGES = ("planner", "plan_check", "reqcov", "ledger", "what", "review")


def what(tid, req, extra=""):
    return (f"# 題 {tid}\nマイルストーン: bench\nタスク: {tid}\n要件: {req}\n{extra}\n"
            "## What\n本文\n\n## 合格条件\n1. 通る\n")


def task(n, **over):
    t = {"id": f"P-0{n}", "title": f"題 {n}", "depends_on": [], "acceptance": ["通る"],
         "files": [f"calc/m{n}.py", f"tests/test_calc_m{n}.py"],
         "oracle": [{"path": f"tests/test_calc_m{n}.py", "from": "self"}],
         "verification": {"command": f"python -m unittest tests.test_calc_m{n}", "expected_exit_code": 0}}
    t.update(over)
    return t


def write(inbox, what2=None, task2=None):
    (inbox / "P-01.md").write_text(what("P-01", "REQ-01"), encoding="utf-8")
    (inbox / "P-02.md").write_text(what2 or what("P-02", "REQ-02"), encoding="utf-8")
    (inbox / "ledger.yaml").write_text(
        yaml.safe_dump([task(1), task2 or task(2)], allow_unicode=True), encoding="utf-8")


BAD = {
    "plan_check": dict(task2=task(2, acceptance=["a", "b", "c", "d", "e"])),
    "reqcov": dict(what2=what("P-02", "REQ-01")),
    "ledger": dict(task2=task(2, oracle=[{"path": "tests/test_calc_other.py", "from": "self"}])),
    "what": dict(what2=what("P-02", "REQ-02", "検証: python -m unittest")),
}
TARGET = {"planner": None, "plan_check": "P-02", "reqcov": None, "ledger": "P-02", "what": "P-02", "review": "P-01"}   # review: 最初の判定（revise）は P-01 に当たる


class PlanLoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.inbox = root / "inbox"
        self.spec = root / "spec.md"
        self.spec.write_text(SPEC, encoding="utf-8")
        self.cfg = {"planner": AGENT, "reviewer": {}, "out": str(root / "out")}
        self.prompts, self.reviews, self.rounds = [], [], []   # self.rounds: 監修役が呼ばれたときの計画役の回

    def run_loop(self, plans, verdicts=("pass",), infra=None, unavailable=None):
        """plans: 回ごとの (書く内容, CLI の終了コード) の列。最後のものを使い回す。verdicts: 監修役の判定の列（同様）。"""
        def fake_agent(agent, wt, ttl, label, prompt, log):
            self.prompts.append(prompt)
            if infra:
                raise hline_base.Infra(infra)
            kwargs, code = plans[min(len(self.prompts), len(plans)) - 1]
            write(Path(wt), **kwargs)
            return code, [], ""

        def fake_decide(cfg, instr, text, tid, row, peers):
            if unavailable:
                raise hline_review.Unavailable(unavailable)
            self.reviews.append(tid)
            self.rounds.append(len(self.prompts))
            verdict = verdicts[min(len(self.reviews), len(verdicts)) - 1]
            return {"verdict": verdict, "findings": [{"kind": "ambiguity", "text": "あいまい"}]}

        with mock.patch.object(planner, "run_agent", fake_agent), \
                mock.patch.object(hline_review, "instruction", lambda cfg: "指示"), \
                mock.patch.object(hline_review, "decide", fake_decide), \
                contextlib.redirect_stdout(io.StringIO()):
            return plan_loop.run_loop(self.cfg, self.spec, "calc", self.inbox)

    def result(self):
        return json.loads((self.inbox / "planner.json").read_text(encoding="utf-8"))

    def test_a_clean_first_round_confirms(self):
        self.assertEqual(self.run_loop([({}, 0)]), 0)
        self.assertEqual(len(self.prompts), 1)
        res = self.result()
        self.assertEqual(res["loop"], {"rounds": 1, "confirmed": True, "failed": []})
        for key in ("files", "problems", "plan_check"):
            self.assertIn(key, res)
        self.assertEqual(self.reviews, ["P-01", "P-02"])

    def test_each_stage_fails_once_then_passes(self):
        cases = [("planner", ({}, 1))] + [(s, (kw, 0)) for s, kw in BAD.items()] + [("review", ({}, 0))]
        self.assertEqual([s for s, _ in cases], list(STAGES))
        for stage, first in cases:
            with self.subTest(stage=stage):
                self.setUp()
                verdicts = ("revise", "pass") if stage == "review" else ("pass",)
                self.assertEqual(self.run_loop([first, ({}, 0)], verdicts), 0)
                self.assertEqual(len(self.prompts), 2)
                self.assertNotIn("## 差し戻された項目", self.prompts[0])
                section = self.prompts[1].split("## 差し戻された項目", 1)[1]
                self.assertIn(stage, section)
                self.assertIn(TARGET[stage] or "全体", section)
                self.assertTrue(self.prompts[1].startswith(self.prompts[0].split(planner.SEPARATOR + "## 差し戻された項目")[0]))
                self.assertEqual(self.result()["loop"], {"rounds": 2, "confirmed": True, "failed": []})
                if stage != "review":
                    self.assertNotIn(1, self.rounds)   # 落ちた回には監修役を呼ばない

    def test_three_failures_exit_one(self):
        self.assertEqual(self.run_loop([(BAD["reqcov"], 0)]), 1)
        self.assertEqual(len(self.prompts), 3)
        loop = self.result()["loop"]
        self.assertEqual((loop["rounds"], loop["confirmed"]), (3, False))
        self.assertEqual([(f["stage"], f["target"]) for f in loop["failed"]], [("reqcov", None)])
        self.assertEqual(self.reviews, [])

    def test_three_review_rejections_report_the_review_stage(self):
        self.assertEqual(self.run_loop([({}, 0)], verdicts=("revise",)), 1)
        self.assertEqual(len(self.prompts), 3)
        self.assertEqual({(f["stage"], f["target"]) for f in self.result()["loop"]["failed"]},
                         {("review", "P-01"), ("review", "P-02")})

    def test_planner_infra_is_not_a_round(self):
        self.assertEqual(self.run_loop([({}, 0)], infra="起動できません"), 2)
        loop = self.result()["loop"]
        self.assertEqual((loop["rounds"], loop["confirmed"]), (0, False))
        self.assertIn("起動できません", loop["error"])

    def test_reviewer_unavailable_is_not_a_round(self):
        self.assertEqual(self.run_loop([({}, 0)], unavailable="利用枠切れ"), 2)
        self.assertEqual(len(self.prompts), 1)
        loop = self.result()["loop"]
        self.assertFalse(loop["confirmed"])
        self.assertIn("利用枠切れ", loop["error"])

    def test_an_unreadable_spec_exits_two(self):
        self.spec.unlink()
        self.assertEqual(self.run_loop([({}, 0)]), 2)
        self.assertEqual(self.prompts, [])
        self.assertFalse(self.result()["loop"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
