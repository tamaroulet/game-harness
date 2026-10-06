"""テスト同士が互いの状態に頼らないこと（agy の照合の無効化を、各テストの中に閉じる）。モジュール 1 つずつ単独でも通る。"""
import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(TESTS))

import agy_stub  # noqa: E402
import pipeline  # noqa: E402

SKIPPERS = {"test_fileops", "test_hline_queue", "test_job_object", "test_mutate_table", "test_v2r_preflight"}
IMPLEMENTER_LOG_TESTS = {
    "test_response_is_kept_even_when_rc_is_zero", "test_stderr_is_kept_when_the_cli_fails",
    "test_the_prompt_and_the_feedback_are_kept", "test_each_attempt_gets_its_own_file",
    "test_the_log_sits_next_to_the_telemetry", "test_it_falls_back_to_the_out_dir_without_telemetry",
    "test_a_failed_write_does_not_change_the_verdict"}
AGY_PINNED_TESTS = {
    "Check": {"test_different_content_stops", "test_missing_unset_or_malformed_stops", "test_load_record_errors",
              "test_same_content_passes_and_returns_the_record", "test_measure_reads_only_inside_install_dir"},
    "Wiring": {"test_guard_checks_before_measuring_the_version", "test_guard_returns_the_cli_version_and_other_clis_skip_the_check",
               "test_a_failed_check_does_not_call_the_runner", "test_pipeline_guard", "test_call_implementer_guards_first"},
    "Static": {"test_acl_runbook_has_the_commands_and_sections", "test_module_does_not_import_subprocess"}}
FORBIDDEN_CALLS = ("subprocess", "os.system", "proc.run", "urllib", "requests")
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


MODULES = sorted(TESTS.glob("test_*.py"))


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join(reversed(parts + [node.id])) if isinstance(node, ast.Name) else ""


def import_time_violations(path):
    found, stack = [], list(parse(path).body)
    while stack:
        n = stack.pop()
        if isinstance(n, SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(n))
        targets = n.targets if isinstance(n, ast.Assign) else [getattr(n, "target", None)]
        found += [f"{path.name}:{n.lineno} 属性への代入 {dotted(t)}" for t in targets if isinstance(t, ast.Attribute)]
        name = dotted(n.func) if isinstance(n, ast.Call) else ""
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_CALLS):
            found.append(f"{path.name}:{n.lineno} 最上位の呼び出し {name}")
    return found


def uses_skip(tree):
    names = ("skipTest", "skip", "skipIf", "skipUnless")
    return any(isinstance(n, ast.Attribute) and n.attr in names or isinstance(n, ast.Name) and n.id in names
               for n in ast.walk(tree))


def methods(path, class_name):
    return {f.name for c in parse(path).body if isinstance(c, ast.ClassDef) and c.name == class_name
            for f in c.body if isinstance(f, ast.FunctionDef)}


class Isolation(unittest.TestCase):
    def test_implementer_log_passes_in_its_own_process(self):
        proc = subprocess.run([sys.executable, "-m", "unittest", "tests.test_implementer_log"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertGreaterEqual(int(re.search(r"Ran (\d+) tests?", out).group(1)), 7)

    def test_no_module_changes_another_module_or_starts_a_process_at_import(self):
        found = [v for p in MODULES for v in import_time_violations(p)]
        self.assertEqual(found, [], "\n".join(found))


class NothingIsRemoved(unittest.TestCase):
    def test_only_the_known_modules_skip(self):
        self.assertEqual({p.stem for p in MODULES if uses_skip(parse(p))}, SKIPPERS)

    def test_implementer_log_keeps_its_seven_tests(self):
        got = methods(TESTS / "test_implementer_log.py", "ImplementerLogTests")
        self.assertEqual(IMPLEMENTER_LOG_TESTS - got, set())

    def test_agy_pinned_keeps_its_tests(self):
        for cls, names in AGY_PINNED_TESTS.items():
            with self.subTest(cls=cls):
                self.assertEqual(names - methods(TESTS / "test_agy_pinned.py", cls), set())


class Stub(unittest.TestCase):
    def test_the_helper_does_not_mention_subprocess(self):
        self.assertNotIn("subprocess", (TESTS / "agy_stub.py").read_text(encoding="utf-8"))

    def test_disabled_replaces_the_guard_only_inside(self):
        original = pipeline.guard_implementer
        with agy_stub.disabled(), mock.patch.object(pipeline, "run", side_effect=AssertionError):
            self.assertIsNone(pipeline.guard_implementer({"cli": "agy"}))
        self.assertIs(pipeline.guard_implementer, original)
        with self.assertRaises(SystemExit):
            pipeline.guard_implementer({"cli": "agy"})

    def test_disabled_restores_the_guard_when_the_body_raises(self):
        original = pipeline.guard_implementer
        with self.assertRaises(RuntimeError), agy_stub.disabled():
            raise RuntimeError("本体の失敗")
        self.assertIs(pipeline.guard_implementer, original)

    def test_install_restores_the_guard_even_when_the_test_fails(self):
        original, inside = pipeline.guard_implementer, []

        class Failing(unittest.TestCase):
            def setUp(self):
                agy_stub.install(self)

            def test_fails(self):
                inside.append(pipeline.guard_implementer({"cli": "agy"}))
                self.fail("わざと落とす")

        result = unittest.TestResult()
        Failing("test_fails").run(result)
        self.assertEqual((len(result.failures), inside), (1, [None]))
        self.assertIs(pipeline.guard_implementer, original)


if __name__ == "__main__":
    unittest.main()
