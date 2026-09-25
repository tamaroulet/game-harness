"""A/B 実験のドライバ・測定器・集計と、pipeline の --local-only（B4-E4）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 本走は無人で何時間も回る。入力が凍結どおりでなければ止まること、条件 A が同じ会話を
続けること、試行の予算（3 回）を守ること、条件 B が push も CI も起こさないこと、指標の計算
（P2P の破壊・トークンの伸び）が正しいことを、実装役も dotnet も使わずに確かめる。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import check, common, driver, measure, report  # noqa: E402
import pipeline  # noqa: E402

MANIFEST = ROOT / "experiments" / "b4_ab" / "tasks.json"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class Manifest(unittest.TestCase):
    def test_real_manifest_loads(self):
        m = common.load_manifest(MANIFEST)
        self.assertEqual(len(m["tasks"]), 5)

    def test_tasks_through_stops_at_the_named_task(self):
        m = common.load_manifest(MANIFEST)
        self.assertEqual([t["id"] for t in common.tasks_through(m, "T1")], ["T1"])
        self.assertEqual([t["id"] for t in common.tasks_through(m, "T3")], ["T1", "T2", "T3"])
        self.assertEqual(len(common.tasks_through(m)), 5)
        with self.assertRaises(common.ABError):
            common.tasks_through(m, "T9")

    def test_changed_input_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / "b4_ab"
            shutil.copytree(MANIFEST.parent, dst)
            (dst / "templates" / "retry.md").write_text("changed", encoding="utf-8")
            with self.assertRaises(common.ABError):
                common.load_manifest(dst / "tasks.json")


class Metrics(unittest.TestCase):
    CLASSES = {"B4abT1Cases": "T1", "B4abT2Cases": "T2"}

    def test_task_of_and_acceptance(self):
        results = {"G.B4abT1Cases.Case_a": "Passed", "G.B4abT1Cases.Case_b": "Failed",
                   "G.Issue12Cases.Case_c": "Passed"}
        self.assertEqual(measure.task_of("G.B4abT1Cases.Case_a", self.CLASSES), "T1")
        self.assertIsNone(measure.task_of("G.Issue12Cases.Case_c", self.CLASSES))
        self.assertEqual(measure.acceptance(results, self.CLASSES, "T1"), (1, 2))
        self.assertEqual(measure.acceptance(None, self.CLASSES, "T1"), (0, None))

    def test_p2p_counts_tests_that_passed_before_and_not_now(self):
        prev = {"G.X.Case_a", "G.X.Case_b", "G.X.Case_gone"}
        now = {"G.X.Case_a": "Passed", "G.X.Case_b": "Failed"}
        self.assertEqual(measure.p2p_broken(prev, now), ["G.X.Case_b", "G.X.Case_gone"])
        self.assertEqual(measure.p2p_broken(prev, now, superseded=["Case_b"]), ["G.X.Case_gone"])
        self.assertEqual(measure.p2p_broken(prev, None), sorted(prev), "ビルドが通らなければ全部が壊れた扱い")


class Report(unittest.TestCase):
    def test_loglog_slope(self):
        self.assertEqual(report.loglog_slope([n * n * 100 for n in range(1, 6)]), 2.0)
        self.assertEqual(report.loglog_slope([n * 100 for n in range(1, 6)]), 1.0)
        self.assertIsNone(report.loglog_slope([100, None, 300]))

    def test_summary_has_both_conditions(self):
        rows = []
        for cond, per in (("A", lambda n: 1000 * n), ("B", lambda n: 1000)):
            for n in range(1, 6):
                rows.append({"run_id": "ab-01", "condition": cond, "task": f"T{n}", "index": n, "accepted": True,
                             "attempts": 1, "p2p_broken": 0, "invariants": {"failures": 0},
                             "diff": {"added": 10, "deleted": 0}, "budget_exceeded": False,
                             "tokens": {"input": per(n)}})
        text = report.summarize(rows)
        self.assertIn("| T5 | A | 1/1 |", text)
        # A は 1000N ずつ増える（累積は N² に近い）、B は一定（累積は N に比例）
        self.assertRegex(text, r"\| A \| 1\.\d+ \|")
        self.assertIn("| B | 1.0 | 1.0 |", text)

    def test_seconds_are_in_the_task_table_and_totals(self):
        rows = [{"run_id": rid, "condition": cond, "task": "T1", "index": 1, "accepted": True, "attempts": 1,
                 "p2p_broken": 0, "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0},
                 "budget_exceeded": False, "tokens": {"input": 10}, "seconds": s}
                for rid, cond, s in (("ab-01", "A", 100.0), ("ab-02", "A", 300.0), ("ab-01", "B", 50.0))]
        text = report.summarize(rows)
        self.assertIn("| T1 | A | 2/2 | 1.0 | 1.0 | 0.0 | 0.0 | 1.0 / 0.0 | 0 | 10.0 | — | — | — | 200.0 |", text)
        self.assertIn("| 0 | 200.0 | 400.0 |", text, "A：1 走行の秒の中央値と全走行の合計")
        self.assertIn("| 0 | 50.0 | 50.0 |", text)


class CostModel(unittest.TestCase):
    """事前投資を合算した総コストと損益分岐（docs/design/b4_ab_fairness_audit.md §3）。"""
    PRICES = {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "cache_read_per_mtok": 0.25}

    def test_call_cost_counts_cache_reads_by_their_own_price(self):
        self.assertAlmostEqual(report.call_cost({"input": 1_000_000, "output": 500_000}, self.PRICES), 2.0)
        self.assertAlmostEqual(report.call_cost({"input": 1_000_000, "output": 0, "cache_read": 400_000},
                                                self.PRICES), 1.1)
        included = dict(self.PRICES, cache_read_included_in_input=True)
        self.assertAlmostEqual(report.call_cost({"input": 1_000_000, "output": 0, "cache_read": 400_000},
                                                included), 0.7)
        self.assertIsNone(report.call_cost({"input": None, "output": 1}, self.PRICES))

    def test_fixed_cost_charges_b_only_to_b(self):
        model = {"fixed": {"shared": {"usd": 1.0}, "B_only": {"usd": 3.0}}}
        self.assertEqual((report.fixed_cost(model, "A", "usd"), report.fixed_cost(model, "B", "usd")), (1.0, 4.0))
        self.assertIsNone(report.fixed_cost({"fixed": {"shared": {"usd": 1.0}}}, "B", "usd"), "未計測は None")

    def test_break_even_measured_and_extrapolated(self):
        # A は k に比例して高くなり、B は一定。B の事前投資 3 は N=3 で回収（S_A=0+1+2+3=6、S_B=3+1+1+1=6）
        self.assertEqual(report.break_even(0, [1, 2, 3, 4, 5], 3, [1, 1, 1, 1, 1]), (3, "measured"))
        # 5 タスクで回収しなければ外挿する。S_A(N) ≈ N²/2、S_B(N) = 20 + N → N = 7
        self.assertEqual(report.break_even(0, [1, 2, 3, 4, 5], 20, [1, 1, 1, 1, 1]), (7, "extrapolated"))
        self.assertEqual(report.break_even(None, [1], 0, [1])[0], None)
        # A が伸びなければ回収しない
        self.assertIsNone(report.break_even(0, [1, 1, 1], 5, [1, 1, 1], horizon=100)[0])
        # 傾きが負（v2-smoke-04 の A：T3 が高く T4・T5 が安い）なら外挿しない。「N ≤ 10000 では回収しない」と言わない
        self.assertLess(report.loglog_slope([0.3, 0.2, 0.3, 0.05, 0.04]), 0)
        n, why = report.break_even(0.1, [0.3, 0.2, 0.3, 0.05, 0.04], 0.5, [0.3, 0.2, 0.3, 0.05, 0.04])
        self.assertIsNone(n)
        self.assertIn("外挿しない", why)
        self.assertNotIn("10000", why)

    def test_summary_has_cost_section_only_with_a_model(self):
        rows = [{"run_id": "ab-01", "condition": c, "task": f"T{k}", "index": k, "accepted": True, "attempts": 1,
                 "p2p_broken": 0, "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0},
                 "budget_exceeded": False, "tokens": {"input": (k if c == "A" else 1) * 1_000_000, "output": 0},
                 "seconds": 10.0} for c in ("A", "B") for k in range(1, 6)]
        self.assertNotIn("損益分岐", report.summarize(rows))
        model = {"prices": self.PRICES, "fixed": {"shared": {"usd": 0.0, "seconds": 0.0},
                                                  "B_only": {"usd": 3.0, "seconds": 0.0}}}
        text = report.summarize(rows, model)
        self.assertIn("| USD | 3 | 実測の範囲 |", text)

    def test_task_table_shows_cache_reads_total_input_and_usd(self):
        row = {"run_id": "ab-01", "condition": "B", "task": "T1", "index": 1, "accepted": True, "attempts": 1,
               "p2p_broken": 0, "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0},
               "budget_exceeded": False, "seconds": 10.0, "detail": {"implementer_calls": 2},
               "tokens": {"input": 1_000_000, "output": 0, "cache_read": 400_000}}
        text = report.summarize([row], {"prices": self.PRICES, "fixed": {}})
        self.assertIn("| T1 | B | 1/1 | 1 | 2 | 0 | 0 | 1 / 0 | 0 | 1000000 | 400000 | 1400000 | 1.1000 | 10.0 |", text)


class ConditionA(unittest.TestCase):
    """同じ会話に積む（2 回目以降は --conversation）。受入を通ったら止め、最大 3 回まで。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.m = common.load_manifest(MANIFEST)
        self.unit = common.unit_of(self.m, self.m["tasks"][0])
        self.ctx = {"m": self.m, "wt": self.tmp / "wt", "out": self.tmp / "out", "imp": {}, "ttl": 60,
                    "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
                    "templates": {k: (MANIFEST.parent / self.m["templates"][k]).read_text(encoding="utf-8")
                                  for k in ("initial", "retry")}}
        self.ctx["out"].mkdir(parents=True)
        self.seen = []

    def call(self, imp, prompt, cwd, cid, ttl):
        self.seen.append((prompt, cid))
        return {"rc": 0, "seconds": 1.0, "conversation_id": cid or "conv-1",
                "usage": {"input_tokens": 100 * len(self.seen), "output_tokens": 10}, "out": "", "err": ""}

    def fast_passing_at(self, k):
        def fast(wt, proj, out, tag):
            ok = len(self.seen) >= k
            return {"G.B4abT1Cases.Case_x": "Passed" if ok else "Failed"}, "tail line"
        return fast

    def test_retries_in_the_same_conversation_until_accepted(self):
        state = {}
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, state,
                                call=self.call, fast=self.fast_passing_at(2))
        self.assertEqual((rec["attempts"], rec["accepted"]), (2, True))
        self.assertEqual([cid for _, cid in self.seen], [None, "conv-1"])
        self.assertIn("G.B4abT1Cases.Case_x", self.seen[1][0], "再試行には落ちたテストの名前が入る")
        self.assertIn(str(self.ctx["wt"]), self.seen[0][0], "初回には作業場所が入る")
        self.assertEqual(state["conversation_id"], "conv-1", "次のタスクへ会話を引き継ぐ")
        # v2.3（N7）：要素ごとの字数を残す（本文は残さない）
        self.assertEqual(set(rec["calls"][0]["prompt_parts"]), {"unit", "interface", "template", "protocol"})
        self.assertEqual(set(rec["calls"][1]["prompt_parts"]), {"retry", "protocol"})

    def test_gives_up_at_the_same_call_budget_as_b(self):
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {},
                                call=self.call, fast=self.fast_passing_at(99))
        # B の最大（試行 3 × 内側ループ 3 ターン）にそろえる（監査 F1）
        self.assertEqual((rec["attempts"], rec["accepted"]), (9, False))
        self.assertEqual(driver.call_budget(self.m), self.m["max_attempts"] * pipeline.MAX_INNER_LOOP_TURNS)

    def test_tools_are_banned_on_every_call_with_the_same_text_as_b(self):
        """再試行を含めて編集だけ（v2 §7）。B（pipeline）と同じ文面。"""
        import tool_policy
        driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=self.call, fast=self.fast_passing_at(2))
        self.assertEqual(len(self.seen), 2)
        for prompt, _ in self.seen:
            self.assertIn(tool_policy.EDIT_ONLY, prompt)
        self.assertFalse(hasattr(tool_policy, "RETRY"), "再試行時の解禁は廃止した")

    def test_retry_carries_the_assertion_message_when_a_trx_exists(self):
        def fast(wt, proj, out, tag):
            (Path(out) / f"{tag}.trx").write_text(TRX_ONE_FAILURE, encoding="utf-8")
            return {"G.B4abT1Cases.Case_x": "Passed" if len(self.seen) >= 2 else "Failed"}, "tail"
        driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=self.call, fast=fast)
        self.assertIn("Expected: 3", self.seen[1][0], "期待値の不一致が再試行に入る")


TRX_ONE_FAILURE = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testId="1" testName="Case_x" outcome="Failed">
      <Output><ErrorInfo><Message>  Expected: 3
  But was:  2
</Message></ErrorInfo></Output>
    </UnitTestResult>
    <UnitTestResult testId="2" testName="Case_y" outcome="Passed" />
  </Results>
  <TestDefinitions>
    <UnitTest name="Case_x" id="1"><TestMethod className="G.B4abT1Cases" name="Case_x" /></UnitTest>
    <UnitTest name="Case_y" id="2"><TestMethod className="G.B4abT1Cases" name="Case_y" /></UnitTest>
  </TestDefinitions>
</TestRun>
"""


class FailureDigest(unittest.TestCase):
    def test_lists_failed_tests_with_their_messages(self):
        from adapters import dotnet
        with tempfile.TemporaryDirectory() as d:
            trx = Path(d) / "r.trx"
            trx.write_text(TRX_ONE_FAILURE, encoding="utf-8")
            text = dotnet.failure_digest(trx)
        self.assertEqual(text, "- G.B4abT1Cases.Case_x\n      Expected: 3\n      But was:  2")


class Tokens(unittest.TestCase):
    def test_sums_every_call_including_cache_reads(self):
        calls = [{"usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_tokens": 100}},
                 {"usage": {"input_tokens": 20, "output_tokens": 2, "cache_read_tokens": 200}}]
        self.assertEqual(driver._tokens(calls), {"input": 30, "output": 3, "cache_read": 300, "per_call_input": [10, 20],
                                                 "partial": False})
        calls[1]["usage"]["cache_read_tokens"] = None
        self.assertIsNone(driver._tokens(calls)["cache_read"], "1 つでも不明なら推測で埋めない")


class ConditionB(unittest.TestCase):
    def test_pipeline_is_called_local_only_in_its_own_places(self):
        args = driver.pipeline_args(Path("u.json"), Path("wt"), Path("sb"), Path("out"), Path("t.json"))
        for flag in ("--local-only", "--sandbox", "--out-dir", "--repo-dir", "--telemetry", "--skip-selftest"):
            self.assertIn(flag, args)

    def test_counts_every_inner_loop_call(self):
        """B の 1 試行の中の内側ループの呼び出しを、最後の 1 回だけでなく全部数える（監査 F2）。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            tel = {"attempts": [{"n": 1, "verdict": "SUCCESS", "stage": "carry",
                                 "implementer": {"usage": {"input_tokens": 20}},
                                 "implementer_calls": [{"usage": {"input_tokens": 10}},
                                                       {"usage": {"input_tokens": 20}}]}]}

            def runner(args, cwd, ttl, label):
                Path(args[args.index("--telemetry") + 1]).write_text(json.dumps(tel), encoding="utf-8")
                return 0, "", ""
            ctx = {"out": out, "m": {"_base": d}, "wt": out / "wt", "sandbox": out / "sb"}
            rec = driver.run_task_b(ctx, {"id": "T1", "unit": "u.json"}, {}, {}, runner=runner)
        self.assertEqual((rec["attempts"], rec["implementer_calls"]), (1, 2))
        self.assertEqual(driver._tokens(rec["calls"])["input"], 30)

    def test_known_failures_of_the_previous_task_are_passed_to_the_pipeline(self):
        """前のタスクの終わりに落ちていたテストを pipeline に渡す（S2）。"""
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)

            def runner(args, cwd, ttl, label):
                seen["names"] = json.loads(Path(args[args.index("--known-failures") + 1]).read_text(encoding="utf-8"))
                return 1, "", ""
            ctx = {"out": out, "m": {"_base": d}, "wt": out / "wt", "sandbox": out / "sb"}
            driver.run_task_b(ctx, {"id": "T3", "unit": "u.json"}, {}, {"known_failures": ["G.B4abT2Cases.Case_a"]},
                              runner=runner)
        self.assertEqual(seen["names"], ["G.B4abT2Cases.Case_a"])
        self.assertEqual(driver.failing_names({"a": "Passed", "b": "Failed"}), ["b"])
        self.assertIsNone(driver.failing_names(None))

    def test_local_only_commits_without_push_or_ci(self):
        with tempfile.TemporaryDirectory() as d:
            repo, sb = Path(d) / "repo", Path(d) / "sb"
            repo.mkdir()
            git("init", "-q", "-b", "main", cwd=repo)
            git("config", "user.email", "t@example.com", cwd=repo)
            git("config", "user.name", "t", cwd=repo)
            (repo / "A.txt").write_text("old", encoding="utf-8")
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "init", cwd=repo)
            sb.mkdir()
            (sb / "A.txt").write_text("new", encoding="utf-8")
            engine = SimpleNamespace(companions=lambda rel: [], carry_companion=lambda *a: ("skip", ""))
            c = SimpleNamespace(unit={"whitelist": ["A.txt"], "id": "u"}, repo=repo, sb=lambda rel: sb / rel,
                                engine=engine, ttl={"git": 60}, local_only=True, metrics={}, gate=None)
            # リモートが無いので、push まで進めば失敗する。SUCCESS なら push も CI も行っていない
            self.assertEqual(pipeline.carry_out_and_ci(c), ("SUCCESS", ""))
            self.assertIn("implement u via pipeline", git("log", "-1", "--format=%s", cwd=repo))
            self.assertEqual((repo / "A.txt").read_text(encoding="utf-8"), "new")


class DryRunCheck(unittest.TestCase):
    """乾式の走行（B4-E5）の検査は、受入の合否を問わず、測定と集計が揃ったかだけを見る。"""

    def row(self, cond, **over):
        r = {"run_id": "dry-01", "condition": cond, "task": "T1", "index": 1, "accepted": False, "attempts": 3,
             "acceptance": {"passed": 0, "total": 4}, "build_ok": True, "p2p_broken": 0,
             "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0}, "budget_exceeded": False,
             "tokens": {"input": 500}, "tests_tampered": False, "seconds": 1.0}
        r.update(over)
        return r

    def run_check(self, rows):
        with tempfile.TemporaryDirectory() as d:
            for cond, r in rows.items():
                out = common.paths("dry-01", cond, out_root=d)["out"]
                out.mkdir(parents=True)
                (out / "metrics.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
            summary = Path(d) / "summary.md"
            summary.write_text(report.summarize(list(rows.values())), encoding="utf-8")
            return check.problems(MANIFEST, "dry-01", "T1", summary, out_root=d)

    def test_complete_dry_run_passes_even_if_not_accepted(self):
        self.assertEqual(self.run_check({"A": self.row("A"), "B": self.row("B")}), [])

    def test_missing_condition_or_unmeasured_values_fail(self):
        self.assertTrue(self.run_check({"A": self.row("A")}))
        self.assertTrue(self.run_check({"A": self.row("A"), "B": self.row("B", tokens={"input": None})}))
        self.assertTrue(self.run_check({"A": self.row("A"), "B": self.row("B", acceptance={"passed": 0, "total": None})}))


if __name__ == "__main__":
    unittest.main()
