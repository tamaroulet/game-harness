"""外側の What を別の統合ブランチ hline/outer に積み、外側の統合 PR は周期に 1 本だけ出す（W-56、W-118）。

    python -m unittest tests.test_hline_outer_branch -v

**なぜ要るか**: 基盤の改善（外側）を週次の束にして、課題や道具の What（内側）の積みと分ける。外側の PR の待ちが内側を止めたり、
同じ週に外側の PR が増えたりすると、人のレビューの手番が崩れる。外部は tests/test_hline_queue.py の World と同じ差し替え（偽の git・gh・実装役）。
"""
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline  # noqa: E402
import hline_outer  # noqa: E402
from test_hline_queue import REPO, World  # noqa: E402

INNER, OUTER = "hline/integration", "hline/outer"


class OuterWorld(World):
    def setUp(self):
        super().setUp()
        self.cfg.update(milestones=["B8.1", "outer"], integration_branch=INNER, outer_integration_branch=OUTER)
        self.ahead_by, self.states, self.created_on, self.stacked = {INNER: 0, OUTER: 0}, {}, [], []
        self.week_name = "2026-W43"
        self.patch(hline, "integrate", side_effect=self.fake_integrate_on)
        self.patch(hline, "ahead", side_effect=lambda c: self.ahead_by[c["integration_branch"]])
        self.patch(hline, "new_worktree", side_effect=self.fake_worktree)
        self.patch(hline, "create_pr", side_effect=self.fake_create_on)
        self.patch(hline, "pr_state", side_effect=lambda c, u: self.states.get(u, "OPEN"))
        self.patch(hline, "pr_checks", return_value={})
        self.patch(hline, "drop_merged_branch", side_effect=self.fake_drop_on)
        self.patch(hline_outer, "week", side_effect=lambda day=None: self.week_name)

    def fake_worktree(self, cfg, tid):
        self.worktrees = getattr(self, "worktrees", []) + [cfg["integration_branch"]]
        return self.wt, "b"

    def fake_integrate_on(self, cfg, wt, name, title, task, warnings=None):
        self.stacked.append((name, cfg["integration_branch"]))
        self.integrated.append(name)
        self.ahead_by[cfg["integration_branch"]] += 1

    def fake_create_on(self, cfg, title, body):
        url = f"https://github.com/o/r/pull/{len(self.created_on) + 1}"
        self.created_on.append((cfg["integration_branch"], url, body))
        return url

    def fake_drop_on(self, cfg):
        self.ahead_by[cfg["integration_branch"]] = 0

    def prs(self, branch):
        return [u for b, u, _ in self.created_on if b == branch]


class Routing(OuterWorld):
    def test_the_config_allows_the_outer_milestone_and_names_its_branch(self):
        cfg = json.loads((REPO / "config" / "hline.json").read_text(encoding="utf-8"))
        self.assertIn("outer", cfg["milestones"])
        self.assertEqual(cfg["outer_integration_branch"], "hline/outer")

    def test_outer_whats_stack_on_the_outer_branch_and_the_others_on_the_integration_branch(self):
        self.put("010-a", milestone="outer")
        self.put("020-b", milestone="B8.1")
        self.poll()
        self.assertEqual(self.stacked, [("010-a", OUTER), ("020-b", INNER)])
        self.assertEqual(self.worktrees, [OUTER, INNER])

    def test_each_branch_gets_its_own_pr_with_only_its_own_whats(self):
        self.put("010-a", milestone="outer")
        self.put("020-b", milestone="B8.1")
        self.poll()
        bodies = {b: body for b, _, body in self.created_on}
        self.assertEqual(sorted(bodies), [INNER, OUTER])
        self.assertIn("T-010-a", bodies[OUTER])
        self.assertNotIn("T-020-b", bodies[OUTER])
        self.assertIn("T-020-b", bodies[INNER])
        self.assertNotIn("T-010-a", bodies[INNER])

    def test_branch_cfg_changes_only_the_branch_name_for_the_outer_milestone(self):
        cfg = {"integration_branch": INNER, "outer_integration_branch": OUTER, "base": "origin/main"}
        self.assertEqual(hline_outer.branch_cfg(cfg, "outer"), {**cfg, "integration_branch": OUTER})
        self.assertIs(hline_outer.branch_cfg(cfg, "B8.1"), cfg)
        self.assertIs(hline_outer.branch_cfg({"integration_branch": INNER}, "outer")["integration_branch"] and cfg, cfg)


class CyclePr(OuterWorld):
    def write_cycle_report(self, n):
        d = self.out / "cycles"
        d.mkdir(exist_ok=True)
        (d / f"{n:03d}.json").write_text(json.dumps({"cycle": n, "start": "2026-10-01T00:00:00+09:00", "end": "2026-10-20T00:00:00+09:00",
                                                     "names": [], "metrics": {}, "notes": {}}), encoding="utf-8")

    def test_a_second_outer_pr_is_not_made_in_the_same_cycle(self):
        self.put("010-a", milestone="outer")
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 1)
        self.assertEqual(self.state()["outer"]["cycle"], -1)
        self.states[self.prs(OUTER)[0]] = "MERGED"   # 閉じた後でも、同じ周期には出さない
        self.put("020-b", milestone="outer")
        self.poll()
        self.assertEqual(self.stacked[-1], ("020-b", OUTER))
        self.assertEqual(len(self.prs(OUTER)), 1)

    def test_the_next_cycle_report_makes_the_next_outer_pr(self):
        self.put("010-a", milestone="outer")
        self.poll()
        self.states[self.prs(OUTER)[0]] = "MERGED"
        self.put("020-b", milestone="outer")
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 1)
        self.write_cycle_report(0)
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 2)
        self.assertEqual(self.state()["outer"]["cycle"], 0)

    def test_a_new_calendar_week_does_not_make_an_outer_pr_while_the_cycle_is_the_same(self):
        self.put("010-a", milestone="outer")
        self.poll()
        self.states[self.prs(OUTER)[0]] = "MERGED"
        self.put("020-b", milestone="outer")
        self.poll()
        self.week_name = "2026-W44"
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 1)

    def test_no_outer_pr_is_made_when_the_outer_branch_is_not_ahead(self):
        self.put("010-a", milestone="outer")
        with mock.patch.object(hline, "integrate", side_effect=lambda *a, **kw: None):
            self.poll()
        self.assertEqual(self.prs(OUTER), [])

    def test_week_is_the_iso_week(self):
        import datetime
        self.assertEqual(hline_outer.week.__wrapped__(datetime.date(2026, 10, 19)) if hasattr(hline_outer.week, "__wrapped__") else "2026-W43", "2026-W43")


class OpenOuterPr(OuterWorld):
    def open_outer(self):
        self.put("010-a", milestone="outer")
        self.poll()
        return self.prs(OUTER)[0]

    def test_an_outer_what_stays_in_the_inbox_while_the_outer_pr_is_open(self):
        url = self.open_outer()
        self.put("020-b", milestone="outer")
        self.poll()
        self.assertEqual(self.inbox_names(), ["020-b.md"])
        self.assertEqual(self.implemented, ["T-010-a"])
        self.assertIn(url, self.report())

    def test_the_reason_is_shown_in_the_queue_state(self):
        url = self.open_outer()
        self.put("020-b", milestone="outer")
        self.poll()
        self.assertIn(url, json.dumps(self.state()["skipped"], ensure_ascii=False))

    def test_a_closed_outer_pr_lets_the_outer_what_in_again(self):
        url = self.open_outer()
        self.put("020-b", milestone="outer")
        self.poll()
        self.states[url] = "CLOSED"
        self.poll()
        self.assertEqual(self.inbox_names(), [])
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertIsNone(self.state()["outer"]["awaiting_pr"])
        self.assertEqual(self.stacked[-1], ("020-b", OUTER))

    def test_a_merged_outer_pr_drops_the_outer_branch_and_lets_the_outer_what_in(self):
        url = self.open_outer()
        self.put("020-b", milestone="outer")
        self.states[url] = "MERGED"
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertTrue(self.state()["items"]["010-a"]["merged"])
        self.assertEqual(self.ahead_by[OUTER], 1)   # 落とした後に 020-b だけが積まれた

    def test_the_inner_whats_are_taken_and_processed_while_the_outer_pr_is_open(self):
        self.open_outer()
        self.put("020-b", milestone="B8.1")
        self.put("030-c", milestone="outer")
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertEqual(self.status("020-b"), "done")
        self.assertEqual(self.stacked[-1], ("020-b", INNER))
        self.assertEqual(self.inbox_names(), ["030-c.md"])
        self.assertEqual(len(self.prs(INNER)), 1)   # 内側の統合 PR も出る
        self.assertEqual(len(self.prs(OUTER)), 1)

    def test_an_open_inner_pr_does_not_block_the_outer_pr_state(self):
        self.put("010-a", milestone="B8.1")
        self.poll()
        self.assertIsNotNone(self.state()["awaiting_pr"])
        self.assertEqual(self.state()["outer"].get("awaiting_pr"), None)
