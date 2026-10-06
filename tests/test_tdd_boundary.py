"""H ライン S3-3 の TDD 境界（harness/hline_tdd.py）の検査。外のプロセス・LLM・git・gh・ネットワークは使わず、
作業ツリーと outdir は毎回一時ディレクトリ、gate_tdd の run は呼び出しを記録する偽の関数にする。"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_red  # noqa: E402
import hline_tdd  # noqa: E402

M = "harness/zz_new.py"
SPEC = {
    "target_symbols": [{"module": M, "kind": "function", "name": "do_it"}],
    "signatures": [{"symbol": "do_it", "params": [{"name": "a", "type": "int"}], "returns": "int"}],
    "contracts": {"preconditions": ["背景の説明その一"], "postconditions": ["背景の説明その二"], "invariants": ["背景の説明その三"]},
    "edit_boundary": {"allowed_files": [M], "forbidden_files": ["config/"], "max_diff_lines": 100},
    "test_oracle": {"guidance": "背景の説明その四", "verification_command": "python -m unittest tests.test_zz"},
}
ACCEPT = (
    "import unittest\n\nSHARED = 'shared_marker'\n\n\nclass TestA(unittest.TestCase):\n"
    "    def setUp(self):\n        self.v = 1\n\n"
    "    def test_one(self):\n        self.assertEqual(marker_one_xyz(1), 2)\n\n"
    "    def test_two(self):\n        self.assertEqual(marker_two_xyz(2), 4)\n\n\n"
    "class TestB(unittest.TestCase):\n"
    "    def test_three(self):\n        self.assertTrue(marker_three_xyz())\n"
)


def fake(codes):
    calls = []

    def run(command, cwd):
        calls.append((command, cwd))
        return codes[len(calls) - 1], "Ran 1 test\nFAILED\n"
    return run, calls


def test_names(path):
    import ast
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {f"{c.name}.{m.name}" for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body if m.name.startswith("test")}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out, self.wt = os.path.join(tmp.name, "out"), os.path.join(tmp.name, "wt")
        os.makedirs(self.out)
        os.makedirs(os.path.join(self.wt, "tests"))
        self.accept = os.path.join(self.out, "test_accept.py")
        Path(self.accept).write_text(ACCEPT, encoding="utf-8")
        self.public, self.hidden = hline_tdd.split(self.accept, self.out)

    def files(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.wt) for d, _, fs in os.walk(self.wt) for f in fs)

    def put_public(self, text=None):
        dest = os.path.join(self.wt, "tests", os.path.basename(self.public))
        Path(dest).write_text(Path(self.public).read_text(encoding="utf-8") if text is None else text, encoding="utf-8")
        return dest


class TestSplit(Base):
    def test_split_makes_two_compilable_files_and_keeps_accept(self):
        self.assertNotEqual(self.public, self.hidden)
        for p in (self.public, self.hidden):
            self.assertTrue(os.path.isabs(p) and p.startswith(os.path.abspath(self.out)) and p != self.accept)
            self.assertEqual(hline_red.compile_problems(p), [])
            self.assertTrue(test_names(p))
        self.assertEqual(test_names(self.public) | test_names(self.hidden), test_names(self.accept))
        self.assertEqual(test_names(self.public) & test_names(self.hidden), set())
        self.assertEqual(Path(self.accept).read_text(encoding="utf-8"), ACCEPT)

    def test_split_of_single_check_gives_both_a_check(self):
        Path(self.accept).write_text("import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(x)\n", encoding="utf-8")
        public, hidden = hline_tdd.split(self.accept, self.out)
        self.assertEqual((test_names(public), test_names(hidden)), ({"T.test_a"}, {"T.test_a"}))

    def test_split_without_checks_is_refused(self):
        Path(self.accept).write_text("import unittest\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            hline_tdd.split(self.accept, self.out)


class TestVisibleSpecAndPrompt(Base):
    def test_visible_spec_has_three_keys_and_does_not_mutate(self):
        before = copy.deepcopy(SPEC)
        got = hline_tdd.visible_spec(SPEC)
        self.assertEqual(SPEC, before)
        self.assertEqual(sorted(got), ["edit_boundary", "signatures", "target_symbols"])
        got["edit_boundary"]["allowed_files"].append("x")
        self.assertEqual(SPEC, before)

    def test_prompt_shows_visible_parts_and_public_only(self):
        text = hline_tdd.build_prompt(SPEC, self.public)
        for part in ("do_it", "harness/zz_new.py", "config/", '"max_diff_lines": 100', Path(self.public).read_text(encoding="utf-8")):
            self.assertIn(part, text)
        for part in ("背景の説明", "python -m unittest tests.test_zz", os.path.basename(self.hidden)):
            self.assertNotIn(part, text)
        for line in Path(self.hidden).read_text(encoding="utf-8").splitlines():
            if "assert" in line:
                self.assertNotIn(line.strip(), text)

    def test_prompt_adds_feedback_and_map_only_when_given(self):
        plain = hline_tdd.build_prompt(SPEC, self.public, None, None)
        self.assertEqual(plain, hline_tdd.build_prompt(SPEC, self.public, "", ""))
        text = hline_tdd.build_prompt(SPEC, self.public, "前回の出力ZZ", "# 目次ZZ")
        self.assertIn("# 目次ZZ", text)
        self.assertTrue(text.rstrip().endswith("前回の出力ZZ"))
        self.assertNotIn("目次ZZ", plain)


class TestTamper(Base):
    def test_untouched_public_is_clean(self):
        self.put_public()
        self.assertEqual(hline_tdd.tamper_problems(self.wt, self.public), [])

    def test_rewritten_removed_and_skipped_are_reported(self):
        self.assertTrue(hline_tdd.tamper_problems(self.wt, self.public))
        text = Path(self.public).read_text(encoding="utf-8")
        dest = self.put_public(text.replace("assertEqual", "assertIsNotNone", 1))
        self.assertTrue(hline_tdd.tamper_problems(self.wt, self.public))
        self.put_public("import unittest\n@unittest.skip('x')\n" + text)
        self.assertIn("skip", "".join(hline_tdd.tamper_problems(self.wt, self.public)))
        os.remove(dest)
        self.assertTrue(hline_tdd.tamper_problems(self.wt, self.public))


class TestGate(Base):
    def test_hidden_failure_is_named_and_output_is_hidden(self):
        run, calls = fake([0, 1])
        problems = hline_tdd.gate_tdd(self.wt, self.public, self.hidden, run)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(problems), 1)
        self.assertIn("非公開分", problems[0])
        self.assertNotIn("Ran 1 test", problems[0])
        self.assertEqual(self.files(), [])

    def test_public_failure_is_named(self):
        run, _ = fake([1, 0])
        problems = hline_tdd.gate_tdd(self.wt, self.public, self.hidden, run)
        self.assertEqual(len(problems), 1)
        self.assertIn("公開分", problems[0])
        self.assertNotIn("非公開分", problems[0])

    def test_both_pass_is_empty_runs_in_wt_and_cleans_up(self):
        run, calls = fake([0, 0])
        self.assertEqual(hline_tdd.gate_tdd(self.wt, self.public, self.hidden, run), [])
        self.assertEqual([c[1] for c in calls], [self.wt, self.wt])
        self.assertEqual([c[0][-1] for c in calls], ["tests." + Path(self.public).stem, "tests." + Path(self.hidden).stem])
        self.assertEqual(self.files(), [])

    def test_injected_files_exist_during_run_and_are_removed_on_exception(self):
        seen = []

        def boom(command, cwd):
            seen.append(self.files())
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            hline_tdd.gate_tdd(self.wt, self.public, self.hidden, boom)
        self.assertEqual(len(seen[0]), 2)
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
