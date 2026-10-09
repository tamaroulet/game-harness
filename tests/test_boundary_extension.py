"""H ライン：Gate 1 の編集境界の自己拡張（hline_boundary）の検査。一時ディレクトリとニセの host だけを使う。"""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_boundary as hb  # noqa: E402
import hline_gate  # noqa: E402
import hline_report  # noqa: E402
import hline_task  # noqa: E402

FAIL = "FAIL: test_a (tests.test_sample.T.test_a)\nTraceback (most recent call last):\n  File \"tests/test_sample.py\", line 3\n"
BOX = {"allowed_files": ["harness/x.py"], "forbidden_files": ["tests/test_forbidden.py"], "max_diff_lines": 100}
SPEC = {"target_symbols": [], "edit_boundary": BOX}
TESTS = "import x\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n    def test_b(self):\n        pass\n"


def boom(*a, **kw):
    raise AssertionError("push・PR・モデル・子プロセスは呼ばれないはず")


def tree(root, **files):
    for name, text in {"harness/x.py": "X = 1\n", **{k.replace("__", "/") + ".py": v for k, v in files.items()}}.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class Failing(unittest.TestCase):
    def test_every_form_gives_the_same_module_once_in_order(self):
        text = "FAIL: t (tests.test_b.C.t)\nFile 'tests/test_a.py'\nERROR: tests.test_b\nC:\\w\\tests\\test_c.py"
        self.assertEqual(hb.failing_modules(text), ("tests.test_b", "tests.test_a", "tests.test_c"))
        self.assertEqual((hb.failing_modules(None), hb.failing_modules("")), ((), ()))


class Candidates(Base):
    def setUp(self):
        super().setUp()
        tree(self.root, tests__test_sample=TESTS, tests__test_other="import y\n", tests__test_forbidden=TESTS,
             tests__test_protected_paths=TESTS)

    def pick(self, feedback=FAIL, spec=SPEC, changed=("harness/x.py",)):
        return hb.candidates(self.root, spec, feedback, list(changed))

    def test_a_failing_test_that_imports_the_changed_module_is_added(self):
        self.assertEqual(self.pick(), ("tests/test_sample.py",))

    def test_a_failing_test_that_does_not_import_it_is_not_added(self):
        self.assertEqual(self.pick("FAIL: t (tests.test_other.T.t)"), ())

    def test_a_test_importing_an_unchanged_module_is_not_added(self):
        tree(self.root, harness__y="Y = 1\n")
        self.assertEqual(self.pick(changed=("harness/y.py",)), ())

    def test_a_passing_test_is_not_added(self):
        self.assertEqual(self.pick("FAIL: t (tests.test_nothing.T.t)"), ())

    def test_forbidden_files_are_never_added(self):
        self.assertEqual(self.pick("tests.test_forbidden failed"), ())

    def test_protected_paths_are_never_added(self):
        self.assertEqual(self.pick("tests.test_protected_paths failed"), ())

    def test_a_file_already_allowed_is_not_added_again(self):
        spec = {**SPEC, "edit_boundary": {**BOX, "allowed_files": ["harness/x.py", "tests/"]}}
        self.assertEqual(self.pick(spec=spec), ())

    def test_no_index_means_nothing_is_added(self):
        self.assertEqual(hb.candidates(self.root / "none", SPEC, FAIL, ["harness/x.py"]), ())


class Extend(unittest.TestCase):
    def test_extend_appends_without_duplicates_and_leaves_the_argument(self):
        out = hb.extend(SPEC, ["tests/test_sample.py", "harness/x.py", "tests/test_sample.py"])
        self.assertEqual(out["edit_boundary"]["allowed_files"], ["harness/x.py", "tests/test_sample.py"])
        self.assertEqual(SPEC["edit_boundary"]["allowed_files"], ["harness/x.py"])
        self.assertEqual(set(out["edit_boundary"]), set(BOX))
        self.assertIn("tests/test_sample.py", hb.note(["tests/test_sample.py"]))
        self.assertEqual(hb.note([]), "")


class Shrink(Base):
    def problems(self, head, now):
        tree(self.root, tests__test_sample=now)
        return hb.shrink_problems(self.root, ["tests/test_sample.py"], lambda p: head)

    def test_fewer_tests_is_a_violation_naming_the_file_and_counts(self):
        out = self.problems(TESTS, "import x\n")
        self.assertEqual(len(out), 1)
        self.assertTrue("tests/test_sample.py" in out[0] and "2 → 0" in out[0])

    def test_more_skips_is_a_violation(self):
        out = self.problems(TESTS, TESTS.replace("    def test_a", "    @unittest.skip('x')\n    def test_a"))
        self.assertEqual(len(out), 1)
        self.assertIn("0 → 1", out[0])
        self.assertEqual(len(self.problems(TESTS, TESTS.replace("pass", "self.skipTest('x')", 1))), 1)

    def test_a_fix_that_keeps_the_count_and_the_skips_is_fine(self):
        self.assertEqual(self.problems(TESTS, TESTS + "    def test_c(self):\n        pass\n"), [])
        self.assertEqual(self.problems(TESTS, TESTS.replace("pass", "assert True", 1)), [])

    def test_a_file_missing_in_head_is_not_a_violation(self):
        self.assertEqual(self.problems(None, "import x\n"), [])


def gate_host(head, run_log):
    ns = types.SimpleNamespace
    return ns(hline_protect=ns(violations=lambda p: [], reason=str), boundary_problems=lambda s, p, c: [], diff_counts=lambda c, w: {},
              size_limits=ns(scan=lambda *a: [], limits=lambda: {}, head_source=lambda c, w: lambda p: head),
              gate_order=ns(focused=lambda *a: None, report=lambda *a: "ran"),
              proc=ns(run=lambda *a, **kw: run_log.append(a) or (0, "", "")))


class GateWiring(Base):
    CFG = {"gate_command": ["x"], "ttl_seconds": {"gate": 1}, "gate_tail_chars": 100}

    def run_gate(self, *extended, warn=None):
        tree(self.root, tests__test_sample="import x\n")
        ran = []
        out = hline_gate.gate(gate_host(TESTS, ran), self.CFG, self.root, ["harness/x.py"], SPEC, None, *extended, warnings=warn)
        return out, ran

    def test_extended_files_that_lost_tests_stop_the_gate_before_running(self):
        """件数の減少・skip の増加は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        warn = []
        (ok, _), ran = self.run_gate(["tests/test_sample.py"], warn=warn)
        self.assertTrue(ok)
        self.assertIn("tests/test_sample.py", "\n".join(warn))
        self.assertEqual(len(ran), 1)

    def test_without_extended_the_gate_goes_on_as_before(self):
        self.assertEqual(self.run_gate()[0], (True, "ran"))


class RunTask(Base):
    def host(self, gate_results):
        calls, feedbacks, ns = [], [], types.SimpleNamespace

        def implement(cfg, wt, spec, feedback, log):
            feedbacks.append(feedback)
            Path(log).write_text("done\n", encoding="utf-8")
            return 0, ["m"]

        def gate(*args, **kw):
            calls.append(args)
            return gate_results.pop(0)
        host = ns(base_check=lambda c, w: (True, ""), changed_paths=lambda w, c: ["harness/x.py"], gate=gate, implement=implement,
                  new_worktree=boom, create_pr=boom, open_pr=boom, proc=ns(run=boom))
        return host, calls, feedbacks

    def run_two(self, results):
        tree(self.root, tests__test_sample=TESTS)
        host, calls, feedbacks = self.host(results)
        cfg = {"max_attempts": 2, "ttl_seconds": {"git": 1}}
        with contextlib.redirect_stdout(io.StringIO()):
            out = hline_task.run_task(host, cfg, "t", SPEC, self.root, (self.root, "b"))
        return out, calls, feedbacks

    def test_an_outside_test_that_fails_is_added_for_the_next_attempt(self):
        (wt, _, tries), calls, feedbacks = self.run_two([(False, FAIL), (True, "ok")])
        self.assertEqual(wt, self.root)
        self.assertEqual(len(calls[0]), 5)
        self.assertEqual(calls[1][3]["edit_boundary"]["allowed_files"], ["harness/x.py", "tests/test_sample.py"])
        self.assertEqual(calls[1][5], ("tests/test_sample.py",))
        self.assertTrue(feedbacks[1].endswith(hb.note(["tests/test_sample.py"])))
        self.assertEqual(tries[0]["boundary_added"], ["tests/test_sample.py"])
        self.assertNotIn("boundary_added", tries[1])

    def test_nothing_is_added_when_the_failing_test_is_unrelated(self):
        _, calls, _ = self.run_two([(False, "FAIL: t (tests.test_other.T.t)"), (True, "ok")])
        self.assertEqual((len(calls[1]), calls[1][3]), (5, SPEC))

    def test_the_report_names_the_added_files_only_when_there_are_some(self):
        item = {"title": "t", "milestone": "m", "task": None, "decompose": {"attempts": []}}
        tries = [{"run": 0, "attempt": 1, "cli_exit": 0, "models": ["m"], "gate": False, "boundary_added": ["tests/test_sample.py"]}]
        cfg = {"out": str(self.root)}
        self.assertIn("tests/test_sample.py", hline_report.item_section(cfg, "n", {**item, "tries": tries}, False).split("| 作業")[0])
        self.assertNotIn("ハーネスが足した", hline_report.item_section(cfg, "n", {**item, "tries": []}, False))


if __name__ == "__main__":
    unittest.main()
