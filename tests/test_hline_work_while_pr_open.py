"""内側の統合 PR が開いている間も受信箱を取る（進捗のタスク W-91）。

    python -m unittest tests.test_hline_work_while_pr_open -v

**なぜ要るか**: CI と承認を待つ間にラインが止まらないようにする。ただし開いている PR の head（統合ブランチの先端）は変えない。
待っている間に Gate 1 を通った What は保留のブランチに積み、PR が閉じたら新しい先端の上に積み直す。
git・gh・実装役・Gate 1 は偽物（ブランチごとの積みを辞書で持つ）。ネットワークと GitHub は使わない。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import test_hline_auto_merge as am  # noqa: E402
import test_hline_queue as tq  # noqa: E402
import hline  # noqa: E402
import hline_git  # noqa: E402

URL1 = "https://github.com/o/r/pull/1"


class WhilePrOpen(tq.World):
    def setUp(self):
        super().setUp()
        self.main_branch = self.cfg["integration_branch"]
        self.pending_branch = hline_git.pending_branch(self.cfg)
        self.stacks, self.staged, self.pushed = {}, [], []
        self.checks, self.labels, self.merge_code, self.calls = list(am.GREEN), [], 0, []
        self.patch(hline, "integrated", side_effect=lambda c, n: n in self.stacks.get(c["integration_branch"], []))
        self.patch(hline, "integrate", side_effect=self.stack_integrate)
        self.patch(hline, "integration_exists", side_effect=lambda c: bool(self.stacks.get(c["integration_branch"])))
        self.patch(hline, "pending_commits", side_effect=lambda c: [(f"sha-{n}", n) for n in self.stacks.get(self.pending_branch, [])])
        self.patch(hline, "cherry_pick", side_effect=lambda c, wt, sha: self.staged.append(sha[4:]) or True)
        self.patch(hline, "push_tip", side_effect=self.stack_push)
        self.patch(hline, "drop_merged_branch", side_effect=self.stack_drop)
        self.patch(hline.proc, "resolve_cli", side_effect=lambda name: [name])
        self.patch(hline.proc, "run", side_effect=self.fake_gh)

    def fake_gh(self, args, cwd, ttl, label):
        import json
        self.calls.append(list(args))
        if args[1:3] == ["pr", "view"]:
            return 0, json.dumps({"statusCheckRollup": self.checks, "labels": [{"name": n} for n in self.labels]}), ""
        if args[1:3] == ["pr", "merge"]:
            return self.merge_code, "", "merge conflict" if self.merge_code else ""
        return 0, "", ""

    def stack_integrate(self, cfg, wt, name, title, task, warnings=None):
        self.stacks.setdefault(cfg["integration_branch"], []).append(name)
        if cfg["integration_branch"] == self.main_branch:
            self.ahead_n += 1

    def stack_push(self, cfg, wt):
        self.stacks.setdefault(cfg["integration_branch"], []).extend(self.staged)
        self.ahead_n += len(self.staged)
        self.pushed.append(cfg["integration_branch"])
        self.staged.clear()

    def stack_drop(self, cfg):
        self.dropped.append(1)
        self.stacks[cfg["integration_branch"]] = []
        if cfg["integration_branch"] == self.main_branch:
            self.ahead_n = 0

    def merges(self):
        return [c for c in self.calls if c[1:3] == ["pr", "merge"]]

    def open_first_pr(self):
        self.put("010-a")
        self.poll()
        self.assertEqual(self.state()["awaiting_pr"], URL1)
        self.assertEqual(self.stacks[self.main_branch], ["010-a"])

    def test_a_what_put_while_the_pr_is_open_is_taken_gated_and_the_head_does_not_change(self):
        self.open_first_pr()
        self.put("020-b")
        self.poll()
        self.assertEqual(self.inbox_names(), [])
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertEqual(self.stacks[self.main_branch], ["010-a"])   # 開いている PR の head は変わらない
        self.assertEqual(self.stacks[self.pending_branch], ["020-b"])
        self.assertEqual(self.pushed, [])
        item = self.state()["items"]["020-b"]
        self.assertEqual((item["status"], item.get("pending")), ("done", True))
        self.assertEqual(len(self.created), 1)
        self.poll()   # 走行をまたいでも保留は失われない
        self.assertEqual(self.state()["items"]["020-b"].get("pending"), True)
        self.assertEqual(self.stacks[self.pending_branch], ["020-b"])

    def test_whats_depending_on_the_pr_and_on_the_pending_ones_are_processed_meanwhile(self):
        self.open_first_pr()
        self.put("020-b", deps=["010-a"])
        self.put("030-c", deps=["020-b"])
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b", "T-030-c"])
        self.assertEqual(self.stacks[self.pending_branch], ["020-b", "030-c"])
        self.assertEqual(self.stacks[self.main_branch], ["010-a"])

    def test_a_pr_closed_without_merge_takes_both_the_earlier_stack_and_the_pending_into_the_next_pr(self):
        self.open_first_pr()
        self.put("020-b")
        self.poll()
        self.pr_state = "CLOSED"
        self.poll()
        self.assertEqual(self.dropped, [1])   # 消したのは保留のブランチだけ。統合ブランチは残る
        self.assertEqual(self.stacks[self.main_branch], ["010-a", "020-b"])
        self.assertEqual(self.pushed, [self.main_branch])
        self.assertEqual(len(self.created), 2)
        for n in ("T-010-a", "T-020-b"):
            self.assertIn(n, self.created[1][1])
        self.assertNotIn("pending", self.state()["items"]["020-b"])

    def merged_with_pending(self, squash, conflicts=(), extra=()):
        """010-a の PR を開き、保留に 020-b（と extra の (名前, 依存)）を積んでから MERGED にして走らせる。"""
        self.open_first_pr()
        self.put("020-b")
        for n, deps in extra:
            self.put(n, deps=deps)
        self.poll()
        if squash:   # squash では開いていた PR のコミットも保留のブランチの履歴に見える（トレーラーつき）
            self.stacks[self.pending_branch].insert(0, "010-a")
        self.conflicts = {f"sha-{n}" for n in conflicts}
        self.patch(hline, "cherry_pick", side_effect=lambda c, wt, sha: sha not in self.conflicts and not self.staged.append(sha[4:]))
        self.pr_state = "MERGED"
        self.poll()

    def check_restacked(self, names):
        self.assertEqual(self.stacks[self.main_branch], names)   # 開いていた PR の 010-a は二重に入らない
        for n in names:
            item = self.state()["items"][n]
            self.assertEqual((item["status"], len(item["tries"])), ("done", 1))
            self.assertNotIn("pending", item)
            self.assertNotIn("reason", item)

    def test_a_squash_merged_pr_restacks_every_pending_what_on_the_new_tip_without_regenerating(self):
        self.merged_with_pending(squash=True)
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])   # 作り直さない
        self.check_restacked(["020-b"])
        self.assertEqual(self.state()["items"]["010-a"].get("merged"), True)

    def test_a_merge_commit_merged_pr_restacks_every_pending_what_without_regenerating(self):
        self.merged_with_pending(squash=False)
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.check_restacked(["020-b"])

    def test_a_conflicting_pending_what_and_its_dependent_go_back_to_waiting_and_the_others_are_restacked(self):
        self.merged_with_pending(squash=True, conflicts=["020-b"], extra=[("030-c", ["020-b"]), ("040-d", [])])
        self.assertEqual(self.implemented.count("T-040-d"), 1)   # 衝突しない What は作り直さない
        self.assertEqual(self.implemented.count("T-020-b"), 2)
        self.assertEqual(self.implemented.count("T-030-c"), 2)
        self.assertEqual(self.stacks[self.main_branch].count("040-d"), 1)
        for n in ("020-b", "030-c"):
            item = self.state()["items"][n]
            self.assertEqual(len(item["tries"]), 1)   # 試行の回数は増えない
            self.assertNotEqual(item["status"], "unconverged")
            self.assertNotIn("reason", item)
        self.assertEqual(self.status("040-d"), "done")
        self.assertNotIn("pending", self.state()["items"]["040-d"])

    def test_the_report_shows_the_count_and_names_of_the_pending_stack(self):
        self.open_first_pr()
        self.put("020-b")
        self.put("030-c")
        self.poll()
        self.assertIn("- 保留の積み（2 件）: 020-b, 030-c", self.report())

    def test_ci_failure_still_stacks_the_fix_on_the_same_pr_and_keeps_one_pr(self):
        self.open_first_pr()
        st = self.state()
        st["awaiting_pr"] = am.URL
        tq.hline_queue.save_state(self.cfg, st)
        self.open_pr = am.URL
        self.checks = [am.run("harness-tests", conclusion="FAILURE"), am.run("approval")]
        self.put("020-fix")
        self.poll()
        self.assertEqual(self.stacks[self.main_branch], ["010-a", "020-fix"])   # 同じ PR に積む
        self.assertNotIn(self.pending_branch, self.stacks)
        self.assertIn(f"CI_FAILED {am.URL}", self.report())
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.state()["awaiting_pr"], am.URL)

    def test_a_merge_failure_is_reported_and_retried_next_run_while_the_inbox_is_still_taken(self):
        self.open_first_pr()
        st = self.state()
        st["awaiting_pr"] = am.URL
        tq.hline_queue.save_state(self.cfg, st)
        self.labels, self.merge_code = ["ms4:approved"], 1
        self.put("020-b")
        self.poll()
        self.assertEqual(len(self.merges()), 1)
        self.assertIn(f"MERGE_FAILED {am.URL}：", self.report())
        self.assertEqual(self.state()["awaiting_pr"], am.URL)
        self.assertEqual(self.inbox_names(), [])
        self.assertIn("T-020-b", self.implemented)
        self.assertEqual(self.stacks[self.main_branch], ["010-a"])
        self.merge_code = 0
        self.poll()
        self.assertEqual(len(self.merges()), 2)   # 次の走行でまたマージを試す
        self.assertEqual(self.dropped[:1], [1])


if __name__ == "__main__":
    unittest.main()
