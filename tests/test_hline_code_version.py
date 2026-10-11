"""走っているコードの版の照合（進捗のタスク W-65）。

    python -m unittest tests.test_hline_code_version -v

**なぜ要るか**: 積んだ自分の変更（再開・監修）を読み込まないまま古いコードで走ると、直したはずの穴が働かない（病理 ④）。
走っているハーネスの木の HEAD が統合ブランチの先端（無ければ base）と違えば、取り込みも処理も統合 PR もせず、report.md に出す。
git の呼び出し（proc.run）は偽物。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import test_hline_queue as tq  # noqa: E402
import hline  # noqa: E402
import hline_git  # noqa: E402

RUNNING, TIP, BASE = "1" * 40, "2" * 40, "3" * 40


class Shas(tq.World):
    """code_shas 本体。proc.run を差し替え、参照ごとの答えと呼ばれた参照を見る。"""

    def setUp(self):
        super().setUp()
        mock_refs = {"HEAD": RUNNING, "origin/" + self.cfg["integration_branch"]: TIP, self.cfg["base"]: BASE}
        self.refs, self.asked, self.integration = mock_refs, [], True
        self.patch(hline_git.proc, "run", side_effect=self.fake_git)

    def fake_git(self, args, cwd, ttl, label):
        if "refs/remotes/" in args[-1]:
            return (0 if self.integration else 1), "", ""
        ref = args[-1][:-len("^{commit}")]
        self.asked.append(ref)
        if ref in self.refs:
            return 0, self.refs[ref] + "\n", ""
        return 1, "", "unknown revision"

    def test_the_tip_is_the_integration_branch_when_it_exists(self):
        self.assertEqual(hline_git.code_shas(self.cfg), (RUNNING, TIP))

    def test_without_an_integration_branch_it_compares_with_base(self):
        self.integration = False
        self.assertEqual(hline_git.code_shas(self.cfg), (RUNNING, BASE))
        self.assertIn(self.cfg["base"], self.asked)

    def test_a_ref_that_cannot_be_resolved_is_an_environment_failure(self):
        del self.refs["HEAD"]
        with self.assertRaises(hline_git.Infra):
            hline_git.code_shas(self.cfg)


class Line(tq.World):
    def shas(self, running, tip):
        self.patch(hline, "code_shas", return_value=(running, tip))

    def test_a_different_head_takes_no_inbox_processes_nothing_and_opens_no_pr(self):
        self.put("010-a")
        self.shas(RUNNING, TIP)
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.inbox_names(), ["010-a.md"])   # 取り込まれていない（受信箱に残る）
        self.assertEqual(self.implemented, [])
        self.assertEqual(self.created, [])
        self.assertEqual(self.state()["items"], {})
        self.assertIn(f"- 人間作業: STALE_CODE 走っている {RUNNING[:7]} ／ 先端 {TIP[:7]}", self.report())

    def test_the_same_head_runs_as_usual(self):
        self.put("010-a")
        self.shas(RUNNING, RUNNING)
        self.poll()
        self.assertEqual(self.status("010-a"), "done")
        self.assertNotIn("STALE_CODE", self.report())

    def test_the_code_line_is_in_the_h_section_either_way(self):
        for running, tip in ((RUNNING, TIP), (RUNNING, RUNNING)):
            self.shas(running, tip)
            self.poll()
            report = self.report()
            self.assertIn(f"- コード: {RUNNING[:7]}", report.split("## H ライン", 1)[1].split("## キュー", 1)[0])

    def test_the_comparison_also_runs_while_waiting_for_the_integration_pr(self):
        st = self.state()
        st["awaiting_pr"] = "https://github.com/o/r/pull/9"
        tq.hline_queue.save_state(self.cfg, st)
        self.shas(RUNNING, TIP)
        self.poll()
        self.assertIn("STALE_CODE", self.report())
        self.assertEqual(self.state()["awaiting_pr"], "https://github.com/o/r/pull/9")
        self.assertIn(f"- コード: {RUNNING[:7]}", self.report())

    def test_a_git_that_cannot_answer_is_an_infra_failure_like_fetch(self):
        self.patch(hline, "code_shas", side_effect=hline.Infra("git rev-parse HEAD が失敗しました"))
        self.patch(hline, "load_config", return_value=self.cfg)
        self.assertEqual(tq.quiet(hline.main, ["poll"]), 2)


if __name__ == "__main__":
    unittest.main()
