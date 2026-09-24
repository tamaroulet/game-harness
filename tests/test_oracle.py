"""二相判定（Step 3: F2P / P2P / 制御群 / quarantine）。

    python -m unittest discover -s tests -v

- 判定は件数ではなくテスト名で行うこと（入れ替わり・偽テストを件数の合算では見逃す）
- quarantine は承認した PR 番号（approved_in）が無ければ読み込み時に止まること
- 実物の結果ファイル（TRX / NUnit3）をアダプタで読んだ辞書に、そのまま適用できること
- pipeline の base 測定（establish_base）と受入判定が、上の判定を実際に使っていること
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import oracle  # noqa: E402
import pipeline  # noqa: E402
import project  # noqa: E402
from adapters import dotnet, unity  # noqa: E402

FIX = ROOT / "tests" / "fixtures"

P, F, S = "Passed", "Failed", "Skipped"
W = SimpleNamespace(LABEL="W", PASSED=P, FAILED=F, SKIPPED=(S, "Inconclusive"))
CG = {"must_pass": "AlwaysPasses_ControlGroup", "must_fail": "control_must_fail"}
REQ = "BossChargeStateTests"


def q_entry(**over):
    e = {"test": "S.Flaky.Sometimes", "reason": "時刻に依存して揺れる", "approved_in": 12}
    e.update(over)
    return e


# ============================================================ 設定の検証

class OracleLoadTests(unittest.TestCase):
    def test_valid_table(self):
        o = oracle.load(CG, {"quarantine": [q_entry()]})
        self.assertEqual(o["must_pass"], "AlwaysPasses_ControlGroup")
        self.assertEqual(o["must_fail"], "control_must_fail")
        self.assertEqual(o["quarantine"], ("S.Flaky.Sometimes",))

    def test_missing_table_means_no_quarantine(self):
        self.assertEqual(oracle.load(CG, None)["quarantine"], ())
        self.assertEqual(oracle.load(CG, {})["quarantine"], ())

    def test_real_project_config_loads(self):
        cfg = project.pipeline_config(project.load("unity-2d"))
        o = oracle.load(cfg["control_groups"], cfg["oracle"])
        self.assertEqual(o["quarantine"], ())

    def test_approved_in_is_required_and_must_be_a_pr_number(self):
        for bad in ("missing", 0, -1, "12", "#12", True, 1.5, None):
            e = q_entry()
            if bad == "missing":
                del e["approved_in"]
            else:
                e["approved_in"] = bad
            with self.subTest(bad), self.assertRaises(oracle.OracleError) as ctx:
                oracle.load(CG, {"quarantine": [e]})
            self.assertIn("approved_in", str(ctx.exception))

    def test_unknown_keys_are_refused(self):
        for table in ({"control_must_pass": "X"}, {"quarantine": [], "extra": 1},
                      {"quarantine": [q_entry(approved_by="someone")]}):
            with self.subTest(table), self.assertRaises(oracle.OracleError):
                oracle.load(CG, table)

    def test_malformed_entries_are_refused(self):
        for table in ({"quarantine": {"test": "X"}}, {"quarantine": ["X"]},
                      {"quarantine": [q_entry(test="")]}, {"quarantine": [q_entry(reason=" ")]},
                      {"quarantine": [{"test": "X", "approved_in": 3}]},
                      {"quarantine": [q_entry(), q_entry()]},
                      {"quarantine": [q_entry(test="AlwaysPasses_ControlGroup")]},
                      {"quarantine": [q_entry(test="control_must_fail")]},
                      "quarantine"):
            with self.subTest(table), self.assertRaises(oracle.OracleError):
                oracle.load(CG, table)

    def test_control_groups_are_required(self):
        for cg in (None, {}, {"must_pass": "", "must_fail": "f"}, {"must_pass": "p"}):
            with self.subTest(cg), self.assertRaises(oracle.OracleError):
                oracle.load(cg, {})


# ============================================================ F2P

class F2PTests(unittest.TestCase):
    def test_failed_to_passed_is_accepted(self):
        base = {"S.BossChargeStateTests.a": F, "S.BossChargeStateTests.b": F, "S.Other.x": P}
        impl = {"S.BossChargeStateTests.a": P, "S.BossChargeStateTests.b": P, "S.Other.x": P}
        self.assertEqual(oracle.f2p_base([REQ], [(W, base)]), ([], []))
        self.assertEqual(oracle.f2p_impl([REQ], [(W, base, impl)]), ([], []))

    def test_fake_test_already_passing_at_base_is_named(self):
        """1 件は Failed なので「受入テストに失敗がある」だけ見ると見逃す。名前ごとに見る。"""
        base = {"S.BossChargeStateTests.a": F, "S.BossChargeStateTests.b": P}
        fake, aborts = oracle.f2p_base([REQ], [(W, base)])
        self.assertEqual(fake, ["S.BossChargeStateTests.b"])
        self.assertEqual(aborts, [])

    def test_one_required_case_still_failing_is_rejected_even_if_pass_count_rose(self):
        base = {"S.BossChargeStateTests.a": F, "S.BossChargeStateTests.b": F}
        impl = {"S.BossChargeStateTests.a": P, "S.BossChargeStateTests.b": F, "S.Other.new": P}
        self.assertGreater(sum(o == P for o in impl.values()), sum(o == P for o in base.values()))
        ng, aborts = oracle.f2p_impl([REQ], [(W, base, impl)])
        self.assertEqual(ng, ["S.BossChargeStateTests.b"])
        self.assertEqual(aborts, [])

    def test_required_not_executed_after_implementation_aborts(self):
        ng, aborts = oracle.f2p_impl([REQ], [(W, None, {"S.Other.x": P})])
        self.assertEqual(ng, [])
        self.assertTrue(aborts and "実行されていない" in aborts[0], aborts)

    def test_skipped_required_aborts(self):
        _, aborts = oracle.f2p_impl([REQ], [(W, None, {"S.BossChargeStateTests.a": S})])
        self.assertTrue(aborts and "skip" in aborts[0], aborts)
        fake, aborts = oracle.f2p_base([REQ], [(W, {"S.BossChargeStateTests.a": S})])
        self.assertEqual(fake, [])
        self.assertTrue(aborts, "base で skip は Failed ではない")

    def test_unbuilt_base_is_non_passing_but_impl_is_still_checked(self):
        engine_base = {"E.Old.x": P}
        self.assertEqual(oracle.f2p_base([REQ], [(W, None), (W, engine_base)]), ([], []))
        impl = {"S.BossChargeStateTests.a": P, "S.BossChargeStateTests.b": F}
        ng, aborts = oracle.f2p_impl([REQ], [(W, None, impl), (W, engine_base, {"E.Old.x": P})])
        self.assertEqual(ng, ["S.BossChargeStateTests.b"])
        self.assertEqual(aborts, [])

    def test_required_missing_at_a_fully_built_base_aborts(self):
        fake, aborts = oracle.f2p_base([REQ], [(W, {"S.Other.x": F})])
        self.assertEqual(fake, [])
        self.assertTrue(aborts and "base" in aborts[0], aborts)

    def test_names_must_be_trackable_between_base_and_impl(self):
        base = {"S.BossChargeStateTests.a": F}
        impl = {"S.BossChargeStateTests.a": P, "S.BossChargeStateTests.c": P}
        ng, aborts = oracle.f2p_impl([REQ], [(W, base, impl)])
        self.assertTrue(any("S.BossChargeStateTests.c" in a for a in aborts), aborts)


# ============================================================ P2P

class P2PTests(unittest.TestCase):
    def test_all_kept(self):
        base = {"A": F, "B": P, "C": P}
        self.assertEqual(oracle.p2p(base, {"A": P, "B": P, "C": P, "D": P}, W, ()), [])

    def test_swap_with_equal_counts_is_detected(self):
        """1 件直して 1 件壊す。Passed の件数は同じ。"""
        base = {"A": F, "B": P}
        impl = {"A": P, "B": F}
        self.assertEqual(sum(o == P for o in base.values()), sum(o == P for o in impl.values()))
        self.assertEqual(oracle.p2p(base, impl, W, ()), ["B"])

    def test_disappeared_or_skipped_test_is_broken(self):
        base = {"A": P, "B": P}
        self.assertEqual(oracle.p2p(base, {"B": S}, W, ()), ["A", "B"])

    def test_tests_failing_at_base_are_not_required(self):
        self.assertEqual(oracle.p2p({"A": F}, {"A": F}, W, ()), [])

    def test_quarantine_exempts_exact_or_dotted_suffix_only(self):
        base = {"S.X.Flaky": P}
        impl = {"S.X.Flaky": F}
        for q in (("S.X.Flaky",), ("X.Flaky",), ("Flaky",)):
            with self.subTest(q):
                self.assertEqual(oracle.p2p(base, impl, W, q), [])
        for q in (("Flak",), ("S.X",), ("X.Fla",), ("laky",)):
            with self.subTest(q):
                self.assertEqual(oracle.p2p(base, impl, W, q), ["S.X.Flaky"])

    def test_p2p_count_excludes_quarantine(self):
        self.assertEqual(oracle.p2p_count({"A": P, "S.B": P, "C": F}, W, ("B",)), 1)

    def test_unexpected_failures_exclusions(self):
        results = {"H.control_must_fail": F, "S.Flaky": F, "S.BossChargeStateTests.a": F,
                   "S.Real": F, "S.Ok": P}
        self.assertEqual(oracle.unexpected_failures(results, W, "control_must_fail", ("Flaky",), [REQ]),
                         ["S.Real"])
        self.assertEqual(oracle.unexpected_failures(results, W, "control_must_fail", ()),
                         ["S.BossChargeStateTests.a", "S.Flaky", "S.Real"])


# ============================================================ 制御群

class ControlsTests(unittest.TestCase):
    def ctl(self, results, require_fail):
        return oracle.controls(results, W, CG["must_pass"], CG["must_fail"], require_fail)

    def test_healthy(self):
        r = {"G.AlwaysPasses_ControlGroup": P, "H.golden_holdout_control_must_fail": F}
        self.assertEqual(self.ctl(r, True), [])
        self.assertEqual(self.ctl({"G.AlwaysPasses_ControlGroup": P}, False), [])

    def test_must_pass_missing_or_not_passing(self):
        for r in ({}, {"G.AlwaysPasses_ControlGroup": F}, {"G.AlwaysPasses_ControlGroup": S},
                  {"G.AlwaysPasses_ControlGroup": P, "H.AlwaysPasses_ControlGroup": F}):
            with self.subTest(r):
                self.assertTrue(self.ctl(r, False))

    def test_must_fail_passing_is_an_environment_anomaly(self):
        for o in (P, S):
            r = {"G.AlwaysPasses_ControlGroup": P, "H.control_must_fail": o}
            with self.subTest(o):
                aborts = self.ctl(r, False)
                self.assertTrue(aborts and "落ちなかった" in aborts[0], aborts)

    def test_must_fail_absence_only_matters_when_required(self):
        r = {"G.AlwaysPasses_ControlGroup": P}
        aborts = self.ctl(r, True)
        self.assertTrue(aborts and "実行されていない" in aborts[0], aborts)


# ============================================================ 実物の結果ファイル

class RealAdapterOutputTests(unittest.TestCase):
    def test_trx_two_phase(self):
        impl, _ = dotnet.parse_results(FIX / "dotnet_results.trx")
        required = [n for n in impl if REQ in n]
        self.assertEqual(len(required), 22)
        base = {n: (dotnet.FAILED if REQ in n else o) for n, o in impl.items()}

        self.assertEqual(oracle.f2p_base([REQ], [(dotnet, base)]), ([], []))
        self.assertEqual(oracle.f2p_impl([REQ], [(dotnet, base, impl)]), ([], []))
        self.assertEqual(oracle.p2p(base, impl, dotnet, ()), [])
        self.assertEqual(oracle.controls(impl, dotnet, CG["must_pass"], CG["must_fail"], False), [])

        base[required[0]] = dotnet.PASSED
        fake, _ = oracle.f2p_base([REQ], [(dotnet, base)])
        self.assertEqual(fake, [required[0]])

    def test_nunit3_p2p_and_controls(self):
        base = unity.parse_results(FIX / "unity_nunit3_results.xml")
        self.assertEqual(oracle.controls(base, unity, CG["must_pass"], CG["must_fail"], True), [])
        victim = sorted(n for n, o in base.items() if o == unity.PASSED and "ControlGroup" not in n)[0]

        broken = dict(base)
        broken[victim] = unity.FAILED
        self.assertEqual(oracle.p2p(base, broken, unity, ()), [victim])

        gone = dict(base)
        del gone[victim]
        self.assertEqual(oracle.p2p(base, gone, unity, ()), [victim])


# ============================================================ pipeline への組み込み

def fake_engine(results):
    return SimpleNamespace(LABEL="Eng", PASSED=P, FAILED=F, SKIPPED=(S, "Inconclusive"),
                           skip_baseline=lambda c: 1,
                           run_tests=lambda c, tag: (results, None))


def fast_ctrl(**more):
    d = {"S.GoldenMasterTests.AlwaysPasses_ControlGroup": P, "S.Old.Keeps": P}
    d.update(more)
    return d


def engine_ctrl(**more):
    d = {"E.AlwaysPasses_ControlGroup": P, "E.Old.Works": P, "E.Explicit.x": S}
    d.update(more)
    return d


class EstablishBaseTests(unittest.TestCase):
    """base の測定。高速検査のアダプタは「受入テストのファイルがあるとビルドできない」を真似る。"""

    TEST_REL = Path("tests") / "Core.Tests" / "BossChargeStateTests.cs"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.test_file = self.tmp / self.TEST_REL
        self.test_file.parent.mkdir(parents=True)
        self.test_file.write_text("// 受入テスト", encoding="utf-8")
        self.calls = []

        def reset(c):   # サンドボックスのリセット = コミット済みの受入テストが戻る
            if not self.test_file.exists():
                self.test_file.write_text("// 受入テスト", encoding="utf-8")

        for name, fn in (("sandbox_reset", reset), ("stage_golden", lambda c, with_holdout: [])):
            patcher = mock.patch.object(pipeline, name, side_effect=fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ctx(self, with_tests, without_tests, engine, required=(REQ,), test_driven=True,
            quarantine=()):
        def run_tests(c, tag):
            self.calls.append(tag)
            results = with_tests if self.test_file.exists() else without_tests
            return (None, "TRX が生成されませんでした") if results is None else (results, None)

        fast = SimpleNamespace(LABEL="Fast", PASSED=P, FAILED=F, run_tests=run_tests,
                               test_file_glob=dotnet.test_file_glob)
        table = {"quarantine": [q_entry(test=t) for t in quarantine]}
        return SimpleNamespace(
            test_driven=test_driven, unit={"acceptance": {"required_tests": list(required)}},
            sandbox=self.tmp, fast=fast, engine=fake_engine(engine),
            oracle=oracle.load(CG, table), metrics={}, base=None)

    def test_unbuilt_base_measures_p_base_without_the_new_tests(self):
        """2 つの受入テストが同じファイルにある。ファイルは 1 回だけ消す（2 回目で落ちない）。"""
        isolated = fast_ctrl()
        c = self.ctx(None, isolated, engine_ctrl(), required=("BossCharge", REQ))
        verdict, msg = pipeline.establish_base(c)
        self.assertIsNone(verdict, msg)
        self.assertEqual(self.calls, ["base_fast", "base_fast_isolated"])
        self.assertIsNone(c.base.fast)
        self.assertIs(c.base.fast_p2p, isolated)
        self.assertEqual(c.metrics["p2p_base"], 4)
        self.assertTrue(self.test_file.exists(), "受入テストを戻していない")

    def test_new_test_files_are_deduplicated(self):
        c = self.ctx(None, fast_ctrl(), engine_ctrl(), required=("BossCharge", REQ, "ChargeState"))
        self.assertEqual(pipeline.new_test_files(c), [self.test_file.resolve()])

    def test_unbuilt_base_that_still_fails_without_the_new_tests_aborts(self):
        c = self.ctx(None, None, engine_ctrl())
        verdict, msg = pipeline.establish_base(c)
        self.assertEqual(verdict, "ABORT")
        self.assertIn("受入テストを除いても", msg)
        self.assertIsNone(c.base)

    def test_unbuilt_base_without_any_test_file_aborts(self):
        self.test_file.unlink()
        c = self.ctx(None, None, engine_ctrl(), required=("NoSuchTests",))
        verdict, _ = pipeline.establish_base(c)
        self.assertEqual(verdict, "ABORT")

    def test_golden_unit_with_unbuilt_base_aborts(self):
        c = self.ctx(None, fast_ctrl(), engine_ctrl(), test_driven=False)
        verdict, _ = pipeline.establish_base(c)
        self.assertEqual(verdict, "ABORT")
        self.assertEqual(self.calls, ["base_fast"])

    def test_prepassing_required_test_is_kept_as_p2p(self):
        """実装前から通る受入テストは REJECT せず、P2P で守らせる（S1 を標準にした。v2 §5.2）。

        v1 は偽テストとして REJECT し、タスクを積み重ねた b4-smoke-01 の B が T2 から走れなかった（game-harness#63）。
        """
        with_tests = fast_ctrl(**{"S.BossChargeStateTests.a": F, "S.BossChargeStateTests.b": P})
        c = self.ctx(with_tests, fast_ctrl(), engine_ctrl())
        verdict, msg = pipeline.establish_base(c)
        self.assertIsNone(verdict, msg)
        self.assertEqual(c.metrics["prepassing"], 1)
        self.assertEqual(c.base.fast_p2p["S.BossChargeStateTests.b"], P, "P_base に入り、P2P で守られる")

    def test_built_base_with_failing_required_tests_is_accepted(self):
        with_tests = fast_ctrl(**{"S.BossChargeStateTests.a": F})
        c = self.ctx(with_tests, fast_ctrl(), engine_ctrl())
        verdict, msg = pipeline.establish_base(c)
        self.assertIsNone(verdict, msg)
        self.assertIs(c.base.fast, with_tests)
        self.assertEqual(self.calls, ["base_fast"])

    def test_unexpected_failure_at_base_aborts(self):
        c = self.ctx(None, fast_ctrl(), engine_ctrl(**{"E.Old.Broken": F}))
        verdict, msg = pipeline.establish_base(c)
        self.assertEqual(verdict, "ABORT")
        self.assertIn("E.Old.Broken", msg)

    def test_quarantined_failure_at_base_is_tolerated(self):
        c = self.ctx(None, fast_ctrl(), engine_ctrl(**{"E.Old.Flaky": F}), quarantine=("E.Old.Flaky",))
        verdict, msg = pipeline.establish_base(c)
        self.assertIsNone(verdict, msg)
        self.assertEqual(c.metrics["quarantined"], 1)

    def test_known_failures_are_tolerated_at_base(self):
        """前のタスクの終わりに落ちていたテストは、base の検査から外す（S2 を標準にした。v2 §5.2）。"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "known.json"
            path.write_text('["E.Old.Broken"]', encoding="utf-8")
            q = pipeline.with_known_failures(("E.Old.Flaky",), path)
            path.write_text('{"not": "a list"}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                pipeline.with_known_failures((), path)
        self.assertEqual(q, ("E.Old.Flaky", "E.Old.Broken"))
        c = self.ctx(None, fast_ctrl(), engine_ctrl(**{"E.Old.Broken": F}))
        c.oracle["quarantine"] = q
        verdict, msg = pipeline.establish_base(c)
        self.assertIsNone(verdict, msg)

    def test_controls_are_checked_at_base(self):
        cases = (
            (fast_ctrl(), engine_ctrl(**{"H.control_must_fail": P})),
            ({"S.Old.Keeps": P}, engine_ctrl()),
        )
        for isolated, engine in cases:
            with self.subTest(engine=engine):
                self.calls.clear()
                c = self.ctx(None, isolated, engine)
                verdict, msg = pipeline.establish_base(c)
                self.assertEqual(verdict, "ABORT", msg)


class AcceptanceTestDrivenTests(unittest.TestCase):
    def ctx(self, quarantine=()):
        base = SimpleNamespace(fast=None, fast_p2p=fast_ctrl(), engine=engine_ctrl())
        table = {"quarantine": [q_entry(test=t) for t in quarantine]}
        return SimpleNamespace(
            test_driven=True, unit={"acceptance": {"required_tests": [REQ]}},
            fast=SimpleNamespace(LABEL="Fast", PASSED=P, FAILED=F),
            engine=fake_engine({}), oracle=oracle.load(CG, table),
            metrics={"p2p_base": 3}, base=base)

    def good(self):
        return fast_ctrl(**{"S.BossChargeStateTests.a": P}), engine_ctrl()

    def test_healthy_implementation(self):
        c = self.ctx()
        ng, fatal = pipeline.check_acceptance(c, *self.good(), expect_holdout=False)
        self.assertEqual((ng, fatal), ([], False))
        self.assertEqual(c.metrics["f2p_tests"], 1)
        self.assertEqual(c.metrics["p2p_kept"], 3)

    def test_disappeared_existing_test_is_a_p2p_regression(self):
        """消えたテストは Failed としては現れない。P2P でしか捕まらない。"""
        fast, engine = self.good()
        del engine["E.Old.Works"]
        ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=False)
        self.assertFalse(fatal)
        self.assertTrue(any("P2P" in m and "E.Old.Works" in m for m in ng), ng)

    def test_broken_existing_test_is_rejected_for_retry(self):
        fast, engine = self.good()
        fast["S.Old.Keeps"] = F
        ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=False)
        self.assertFalse(fatal)
        self.assertTrue(any("P2P" in m for m in ng), ng)

    def test_quarantined_test_is_exempt_from_p2p(self):
        fast, engine = self.good()
        fast["S.Old.Keeps"] = F
        ng, fatal = pipeline.check_acceptance(self.ctx(quarantine=("S.Old.Keeps",)), fast, engine,
                                              expect_holdout=False)
        self.assertEqual((ng, fatal), ([], False))

    def test_quarantine_does_not_exempt_required_tests(self):
        fast, engine = self.good()
        fast["S.BossChargeStateTests.a"] = F
        ng, fatal = pipeline.check_acceptance(self.ctx(quarantine=("S.BossChargeStateTests.a",)),
                                              fast, engine, expect_holdout=False)
        self.assertFalse(fatal)
        self.assertTrue(any("受入テストが通っていない" in m for m in ng), ng)

    def test_fatal_conditions(self):
        cases = {
            "must_pass が落ちた": lambda f, e: f.update({"S.GoldenMasterTests.AlwaysPasses_ControlGroup": F}),
            "must_fail が通った": lambda f, e: e.update({"H.control_must_fail": P}),
            "受入テストが実行されていない": lambda f, e: f.pop("S.BossChargeStateTests.a"),
        }
        for label, breaker in cases.items():
            fast, engine = self.good()
            breaker(fast, engine)
            with self.subTest(label):
                ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=False)
                self.assertTrue(fatal, ng)

    def test_not_executed_required_in_a_suite_without_skip_words_is_not_passing(self):
        """高速検査のアダプタは skip の語を持たない（TRX の NotExecuted）。Passed でなければ不合格。"""
        fast, engine = self.good()
        fast["S.BossChargeStateTests.a"] = "NotExecuted"
        ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=False)
        self.assertFalse(fatal)
        self.assertTrue(any("受入テストが通っていない" in m for m in ng), ng)

    def test_unmeasured_base_is_fatal(self):
        c = self.ctx()
        c.base = None
        ng, fatal = pipeline.check_acceptance(c, *self.good(), expect_holdout=False)
        self.assertTrue(fatal, ng)


class AcceptanceGoldenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "golden.json").write_text(json.dumps({"cases": [{}]}), encoding="utf-8")

    def ctx(self):
        return SimpleNamespace(
            test_driven=False, cfg={},
            g={"disclosed_tag": "gd", "holdout_tag": "gh", "disclosed_rel": "golden.json"},
            sb=lambda rel: self.tmp / rel, ctrl_fail=CG["must_fail"],
            fast=SimpleNamespace(LABEL="Fast", PASSED=P, FAILED=F),
            engine=fake_engine({}), oracle=oracle.load(CG, {}), metrics={"p2p_base": 3},
            base=SimpleNamespace(fast=fast_ctrl(), fast_p2p=fast_ctrl(), engine=engine_ctrl()))

    def good(self):
        return fast_ctrl(**{"S.gd_1": P}), engine_ctrl(**{"E.gd_1": P})

    def test_healthy(self):
        self.assertEqual(pipeline.check_acceptance(self.ctx(), *self.good(), expect_holdout=False),
                         ([], False))

    def test_disappeared_existing_test_is_a_p2p_regression(self):
        fast, engine = self.good()
        del engine["E.Old.Works"]
        ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=False)
        self.assertFalse(fatal)
        self.assertTrue(any("P2P" in m for m in ng), ng)

    def test_holdout_run_requires_the_must_fail_control(self):
        fast, engine = self.good()
        engine["E.gh_1"] = P
        ng, fatal = pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=True)
        self.assertTrue(fatal, ng)
        engine["E.gh_control_must_fail"] = F
        self.assertEqual(pipeline.check_acceptance(self.ctx(), fast, engine, expect_holdout=True),
                         ([], False))


if __name__ == "__main__":
    unittest.main()
