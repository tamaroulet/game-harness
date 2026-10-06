"""H ライン Gate B（hline_symbols）：TaskSpec の中身の矛盾と、実装が宣言どおりかの検査。一時ディレクトリとニセの host だけを使う。"""
import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_gate  # noqa: E402
import hline_spec  # noqa: E402
import hline_symbols as hs  # noqa: E402

IMPL = ("class K:\n    def m(self, a: int) -> str:\n        return ''\n\n\n"
        "def f(a: int, b: str = '', *, c=None) -> list[str]:\n    return []\n\n\n"
        "def g(x, y):\n    pass\n")
SPEC = {
    "target_symbols": [{"module": "harness/x.py", "kind": "function", "name": "f"}],
    "signatures": [{"symbol": "f", "params": [{"name": "a", "type": "int"}, {"name": "b", "type": "str"}, {"name": "c", "type": "Any"}],
                    "returns": "list[str]"}],
    "contracts": {"preconditions": [], "postconditions": ["x"], "invariants": []},
    "edit_boundary": {"allowed_files": ["harness/x.py", "tests/test_x.py"], "forbidden_files": ["docs/"], "max_diff_lines": 100},
    "test_oracle": {"guidance": "g"},
}


def make(**changes):
    return {**SPEC, **changes}


def sig(*params, returns="list[str]", symbol="f"):
    return [{"symbol": symbol, "params": [{"name": n, "type": t} for n, t in params], "returns": returns}]


class Tree(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.write("harness/x.py", IMPL)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class Declared(Tree):
    def check(self, spec):
        return hs.declared_problems(spec, self.root)

    def test_a_faithful_implementation_has_no_problem(self):
        self.assertEqual(self.check(SPEC), [])

    def test_a_param_without_annotation_matches_only_any(self):
        self.assertEqual(self.check(make(signatures=sig(("x", "Any"), ("y", "Any"), returns="Any", symbol="g"),
                                         target_symbols=[{"module": "harness/x.py", "kind": "function", "name": "g"}])), [])
        out = self.check(make(signatures=sig(("x", "int"), ("y", "Any"), returns="Any", symbol="g"),
                              target_symbols=[{"module": "harness/x.py", "kind": "function", "name": "g"}]))
        self.assertEqual(len(out), 1)
        self.assertIn("g", out[0])

    def test_a_missing_symbol_or_one_in_another_module_is_reported_with_its_name(self):
        self.write("harness/y.py", "def other(): pass\n")
        for module, name in (("harness/x.py", "nothing"), ("harness/y.py", "f")):
            out = self.check(make(target_symbols=[{"module": module, "kind": "function", "name": name}]))
            self.assertTrue(len(out) == 1 and name in out[0] and module in out[0])

    def test_a_wrong_param_name_order_or_annotation_is_reported(self):
        for params in ((("a", "int"), ("z", "str"), ("c", "Any")), (("b", "str"), ("a", "int"), ("c", "Any")),
                       (("a", "str"), ("b", "str"), ("c", "Any")), (("a", "int"), ("b", "str"))):
            out = self.check(make(signatures=sig(*params)))
            self.assertEqual(len(out), 1, params)
            self.assertIn("f", out[0])

    def test_a_wrong_return_annotation_shows_both_sides(self):
        out = self.check(make(signatures=sig(("a", "int"), ("b", "str"), ("c", "Any"), returns="int")))
        self.assertEqual(len(out), 1)
        self.assertTrue("f" in out[0] and "int" in out[0] and "list[str]" in out[0])

    def test_whitespace_is_ignored_and_modules_may_be_dotted(self):
        self.assertEqual(self.check(make(signatures=sig(("a", " int "), ("b", "str"), ("c", "Any"), returns="list [ str ]"),
                                         target_symbols=[{"module": "harness.x", "kind": "function", "name": "f"}])), [])

    def test_methods_and_classes_and_modules(self):
        spec = make(target_symbols=[{"module": "harness/x.py", "kind": "method", "name": "K.m"},
                                    {"module": "harness/x.py", "kind": "class", "name": "K"},
                                    {"module": "harness/x.py", "kind": "module", "name": "x"}],
                    signatures=sig(("a", "int"), returns="str", symbol="K.m"))
        self.assertEqual(self.check(spec), [])
        spec["signatures"] = sig(("a", "int"), returns="int", symbol="K.m")
        self.assertEqual(len(self.check(spec)), 1)
        spec["target_symbols"] = [{"module": "harness/x.py", "kind": "method", "name": "K.none"}]
        self.assertEqual(len(self.check(spec)), 1)

    def test_a_file_that_cannot_be_read_is_one_problem_not_an_exception(self):
        self.write("harness/x.py", "def f(:\n")
        self.assertEqual(len(self.check(SPEC)), 1)
        self.assertEqual(len(hs.declared_problems(SPEC, self.root / "none")), 1)


class Content(unittest.TestCase):
    def test_a_consistent_spec_has_no_problem(self):
        self.assertEqual(hs.content_problems(SPEC), [])

    def test_a_target_file_outside_allowed_files(self):
        out = hs.content_problems(make(target_symbols=[{"module": "harness/z.py", "kind": "function", "name": "f"}]))
        self.assertEqual(len(out), 1)
        self.assertIn("allowed_files", out[0])

    def test_allowed_and_forbidden_overlapping_or_a_target_file_that_is_forbidden(self):
        out = hs.content_problems(make(edit_boundary=dict(SPEC["edit_boundary"], forbidden_files=["docs/", "tests/"])))
        self.assertEqual(len(out), 1)
        self.assertIn("tests/test_x.py", out[0])
        box = dict(SPEC["edit_boundary"], forbidden_files=["harness/x.py"])
        self.assertTrue(any("forbidden_files" in x for x in hs.content_problems(make(edit_boundary=box))))

    def test_an_annotation_that_is_not_a_type(self):
        for ptype, returns in (("list[", "int"), ("int", "-> x")):
            out = hs.content_problems(make(signatures=sig(("a", ptype), returns=returns)))
            self.assertEqual(len(out), 1, (ptype, returns))
            self.assertIn("f", out[0])

    def test_max_diff_lines_over_the_limit(self):
        out = hs.content_problems(make(edit_boundary=dict(SPEC["edit_boundary"], max_diff_lines=301)))
        self.assertEqual(len(out), 1)
        self.assertIn("max_diff_lines", out[0])

    def test_gate_a_adds_content_problems_only_after_the_schema_passes(self):
        schema = hline_spec.load_schema({"taskspec_schema": "config/taskspec.schema.json"})
        self.assertEqual(hline_spec.gate_a(schema, SPEC, None), [])
        bad = make(target_symbols=[{"module": "harness/z.py", "kind": "function", "name": "f"}])
        self.assertEqual(hline_spec.validate(schema, bad), [])
        self.assertEqual(len(hline_spec.gate_a(schema, bad, None)), 1)
        broken = {k: v for k, v in bad.items() if k != "signatures"}
        self.assertTrue(all("allowed_files" not in x for x in hline_spec.gate_a(schema, broken, None)))


class GateB(Tree):
    CFG = {"gate_command": ["x"], "ttl_seconds": {"gate": 1}, "gate_tail_chars": 100}

    def run_gate(self, spec):
        ns, ran = types.SimpleNamespace, []

        host = ns(hline_protect=ns(violations=lambda p: [], reason=str), boundary_problems=lambda s, p, c: [], diff_counts=lambda c, w: {},
                  size_limits=ns(scan=lambda *a: [], limits=lambda: {}, head_source=lambda c, w: lambda p: None),
                  gate_order=ns(focused=lambda *a: ran.append("focused"), report=lambda *a: "ran"),
                  proc=ns(run=lambda *a, **kw: ran.append("run") or (0, "", "")))
        return hline_gate.gate(host, self.CFG, self.root, ["harness/x.py"], spec), ran

    def test_a_mismatch_or_missing_symbol_stops_the_gate_before_any_test_runs(self):
        missing = [{"module": "harness/x.py", "kind": "function", "name": "nothing"}]
        for spec, word in ((make(signatures=sig(("a", "int"), returns="int")), "int"), (make(target_symbols=missing), "nothing")):
            (ok, why), ran = self.run_gate(spec)
            self.assertFalse(ok)
            self.assertTrue("f" in why and word in why)
            self.assertEqual(ran, [])

    def test_a_faithful_spec_or_no_spec_goes_on_to_the_tests(self):
        for spec in (SPEC, None):
            out, ran = self.run_gate(spec)
            self.assertEqual(out, (True, "ran"))
            self.assertEqual(ran, ["focused", "run"])


class NoModelCalls(unittest.TestCase):
    def test_hline_symbols_imports_nothing_that_runs_a_model_or_a_process(self):
        tree = ast.parse((Path(hs.__file__)).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(names, {"ast", "pathlib", "sys", "hline_spec", "size_limits"})


if __name__ == "__main__":
    unittest.main()
