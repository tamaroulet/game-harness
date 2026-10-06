"""H ライン Gate C（harness/hline_red.py）の検査。外のプロセス・LLM・git・gh・ネットワークは使わず、作業ツリーは一時ディレクトリ、
gate_c の run は呼ばれた引数を記録する偽の関数にする。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_red  # noqa: E402
import hline_spec  # noqa: E402
import model_pin  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOOD = "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(frobnicate_the_widget(1), 2)\n"
M = "harness/zz_new.py"
SPEC = {
    "target_symbols": [{"module": M, "kind": "module", "name": "zz_new"}, {"module": M, "kind": "function", "name": "do_it"},
                       {"module": M, "kind": "class", "name": "Box"}, {"module": M, "kind": "method", "name": "Box.open"}],
    "signatures": [{"symbol": "do_it", "params": [{"name": "a", "type": "int"}, {"name": "b", "type": "str"}], "returns": "int"},
                   {"symbol": "Box.open", "params": [{"name": "key", "type": "str"}], "returns": "bool"}],
    "contracts": {"preconditions": ["a は整数である"], "postconditions": ["do_it は a の 2 倍を返す"], "invariants": ["副作用が無い"]},
    "edit_boundary": {"allowed_files": [M], "forbidden_files": ["config/"], "max_diff_lines": 100},
    "test_oracle": {"guidance": "unittest で書く。固有の指針の文", "verification_command": "python -m unittest tests.test_accept"},
}
NO_DELETE = {"added": 1, "deleted": 0, "deleted_files": ()}
ACC = "tests/test_accept.py"


def fake(code=1, out="Ran 2 tests\n\nFAILED (failures=1)\n"):
    calls = []
    return (lambda command, cwd: calls.append((command, cwd)) or (code, out)), calls


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out, self.wt = os.path.join(tmp.name, "out"), os.path.join(tmp.name, "wt")
        os.makedirs(self.wt)
        self.accept = hline_red.accept_path(self.out)
        self.write_accept(GOOD)
        self.dest = os.path.join(self.wt, "tests", "test_accept.py")

    def write_accept(self, text):
        os.makedirs(os.path.dirname(self.accept), exist_ok=True)
        Path(self.accept).write_text(text, encoding="utf-8")

    def files(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.wt) for d, _, fs in os.walk(self.wt) for f in fs)


class TestGateC(Base):
    def test_broken_syntax_is_reported_without_running(self):
        self.write_accept("def broken(:\n")
        self.assertTrue(hline_red.compile_problems(self.accept))
        self.assertTrue(hline_red.compile_problems(os.path.join(self.out, "none.py")))
        run, calls = fake()
        self.assertTrue(hline_red.gate_c(SPEC, self.accept, self.wt, run))
        self.assertEqual((calls, self.files()), ([], []))

    def test_only_red_passes_and_runs_once_in_wt(self):
        run, calls = fake(0, "Ran 2 tests\n\nOK\n")
        self.assertIn("実装の前に 1 件も落ちません", "".join(hline_red.gate_c(SPEC, self.accept, self.wt, run)))
        self.assertEqual(len(calls), 1)
        run, calls = fake()
        self.assertEqual(hline_red.gate_c(SPEC, self.accept, self.wt, run), [])
        self.assertEqual(calls, [([sys.executable, "-m", "unittest", "tests.test_accept"], self.wt)])
        self.assertEqual(self.files(), [])

    def test_import_failure_is_reported(self):
        out = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'x'\n"
        self.assertIn("受入テストを import できません", "".join(hline_red.red_problems(1, out)))
        self.assertEqual(hline_red.red_problems(1, out + "Ran 1 test\n"), [])

    def test_placed_files_are_discarded_on_exception(self):
        def boom(command, cwd):
            self.assertTrue(os.path.isfile(self.dest))
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            hline_red.gate_c(SPEC, self.accept, self.wt, boom)
        self.assertEqual(self.files(), [])


class TestPlace(Base):
    def test_place_and_discard_restore_files(self):
        self.assertTrue(os.path.isabs(self.accept) and self.accept.endswith(os.path.join("accept", "test_accept.py")))
        self.assertNotEqual(os.path.commonpath([self.accept, self.wt]), self.wt)
        os.makedirs(os.path.join(self.wt, "harness"))
        Path(self.wt, "harness", "keep.py").write_text("x = 1\n", encoding="utf-8")
        before = self.files()
        self.assertFalse(os.path.exists(self.dest))
        placed = hline_red.place(SPEC, self.accept, self.wt)
        self.assertEqual(placed, [ACC, M])
        self.assertEqual(Path(self.dest).read_text(encoding="utf-8"), GOOD)
        hline_red.discard(self.wt, placed + placed)
        self.assertEqual(self.files(), before)
        Path(self.wt, M).write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(hline_red.place(SPEC, self.accept, self.wt), [ACC])
        self.assertEqual(Path(self.wt, M).read_text(encoding="utf-8"), "x = 1\n")

    def test_stub_source_compiles_and_raises(self):
        for module in (M, "harness.zz_new"):
            src = hline_red.stub_source(SPEC, module)
            compile(src, "stub", "exec")
            for part in ("def do_it(a, b):", "class Box:", "def open(self, key):"):
                self.assertIn(part, src)
            self.assertEqual(src.count("raise NotImplementedError"), 2)
        self.assertEqual(hline_red.stub_source(SPEC, "harness/other.py").strip(), "")

    def test_inject_leaves_accept_in_wt(self):
        self.assertEqual(hline_red.inject(self.accept, self.wt), ACC)
        Path(self.dest).write_text("old\n", encoding="utf-8")
        hline_red.inject(self.accept, self.wt)
        self.assertEqual(Path(self.dest).read_text(encoding="utf-8"), GOOD)

    def test_prompt_has_spec_and_not_accept_lines(self):
        text = hline_red.red_prompt(SPEC, None)
        for part in ("a は整数である", "do_it は a の 2 倍を返す", "副作用が無い", "固有の指針の文", M, "do_it", "Box", "Box.open"):
            self.assertIn(part, text)
        self.assertNotIn("前回の受入テストの違反", text)
        self.assertEqual(hline_red.leak_problems(text, self.accept), [])
        for part in ("前回の受入テストの違反", "理由その一"):
            self.assertIn(part, hline_red.red_prompt(SPEC, ["理由その一", "理由その二"]))
        self.assertTrue(hline_red.leak_problems("ここに self.assertEqual(frobnicate_the_widget(1), 2) がある", self.accept))
        self.assertTrue(hline_red.leak_problems("test_accept.py を読め", self.accept))
        self.assertEqual(hline_red.leak_problems("関係のない文章だけ", self.accept), [])

    def test_with_accept_adds_once_and_keeps_original(self):
        original = copy.deepcopy(SPEC)
        spec = hline_red.with_accept(SPEC, ACC)
        self.assertEqual(SPEC, original)
        self.assertEqual(spec["edit_boundary"]["allowed_files"], [M, ACC])
        self.assertEqual(hline_red.with_accept(spec, ACC)["edit_boundary"]["allowed_files"], [M, ACC])
        self.assertEqual(hline_spec.boundary_problems(spec, [ACC, M], NO_DELETE), [])
        self.assertTrue(hline_spec.boundary_problems(SPEC, [ACC, M], NO_DELETE))

    def test_red_agent_is_pinned_and_requires_model(self):
        cfg = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))
        agent = hline_red.red_agent(cfg)
        self.assertEqual(model_pin.require(agent, "x"), cfg["decomposer"]["model"])
        self.assertEqual([p for p in model_pin.problems() if "projects/unity-2d/" not in p], [])
        with self.assertRaises(model_pin.ModelPinError):
            hline_red.red_agent({"decomposer": {"cli": "claude"}})
