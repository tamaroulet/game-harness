"""目次（harness/symbolmap.py）と、分解役・実装役への配線の検査。proc.run を差し替え、外の CLI・git・gh は呼ばない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_spec  # noqa: E402
import proc  # noqa: E402
import symbolmap  # noqa: E402

CFG = hline.load_config()
ALPHA = '''def run(x: int, y=None, *args, **kw) -> str:
    return ""


class Box:
    def put(self, item, *, c: int = 3):
        return item

    async def get(self) -> int:
        return 1
'''
FILES = {"harness/alpha.py": ALPHA, "harness/beta.py": "def beta_fn(a, b=1):\n    return a\n",
         "harness/ab/deep.py": "def deep(z):\n    return z\n", "tests/test_alpha.py": "import alpha\nimport harness.beta as b\n",
         "tests/test_beta.py": "from beta import beta_fn\nfrom harness import alpha as a\n",
         "tests/test_deep.py": "def f():\n    from harness.ab import deep\n"}
BROKEN = {"harness/broken.py": "def (:\n", "tests/test_broken.py": "import alpha\ndef (:\n"}


def make_repo(root, files=FILES):
    for rel, text in files.items():
        (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(root) / rel).write_text(text, encoding="utf-8")


class Unit(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d, dict(FILES, **BROKEN))
            self.idx = symbolmap.build(d)

    def test_module_index_lists_functions_classes_and_methods_in_order(self):
        idx = symbolmap.module_index(ALPHA, "harness\\alpha.py")
        self.assertEqual((idx["path"], idx["tests"]), ("harness/alpha.py", []))
        (run,), (box,) = idx["functions"], idx["classes"]
        self.assertEqual((run["name"], run["lineno"], run["returns"]), ("run", 1, "str"))
        self.assertEqual(run["params"], ["x: int", "y=None", "*args", "**kw"])
        self.assertEqual((box["name"], box["lineno"]), ("Box", 5))
        self.assertEqual([(m["name"], m["lineno"], m["params"], m["returns"]) for m in box["methods"]],
                         [("put", 6, ["self", "item", "*", "c: int=3"], None), ("get", 9, ["self"], "int")])
        self.assertIsNone(symbolmap.module_index("def (:\n", "x.py"))

    def test_build_maps_every_module_with_its_tests_and_skips_broken_ones(self):
        mods, both = self.idx["modules"], ["tests/test_alpha.py", "tests/test_beta.py"]
        self.assertEqual(sorted(mods), ["harness/ab/deep.py", "harness/alpha.py", "harness/beta.py"])
        self.assertEqual([mods[p]["tests"] for p in sorted(mods)], [["tests/test_deep.py"], both, both])
        self.assertEqual(self.idx["skipped"], ["harness/broken.py"])
        self.assertIn("harness/broken.py", [ln for ln in symbolmap.render(self.idx, 9999).splitlines() if "飛ばした" in ln][0])
        self.assertEqual(symbolmap.build("no/such/dir"), {"modules": {}, "skipped": []})
        idx = {"modules": {"a.py": {}, "b.py": {}}, "skipped": ["c.py", "d.py"]}
        self.assertEqual(symbolmap.for_modules(idx, ["a.py", "none.py", "c.py"]), {"modules": {"a.py": {}}, "skipped": ["c.py"]})
        spec = {"target_symbols": [{"module": "harness\\a.py"}, {"module": "harness/a.py"}, {"module": 3}, "x", {"module": "b.py"}]}
        self.assertEqual(symbolmap.spec_modules(spec), ("harness/a.py", "b.py"))
        self.assertEqual([symbolmap.spec_modules(b) for b in (None, {}, {"target_symbols": [1]})], [()] * 3)

    def test_render_has_a_section_per_module_and_shrinks_in_steps_within_the_limit(self):
        full = symbolmap.render(self.idx, 100000)
        self.assertEqual(sum(ln.startswith("## harness/") for ln in full.splitlines()), 3)
        for word in ("# ", "x: int", "y=None", "L1", "class Box", "put", "L6", "beta_fn", "tests/test_alpha.py"):
            self.assertIn(word, full)
        no_args = symbolmap.render(self.idx, len(full) - 1)
        self.assertTrue("(...)" in no_args and "x: int" not in no_args and "put" in no_args)
        no_methods = symbolmap.render(self.idx, len(no_args) - 1)
        self.assertTrue("put" not in no_methods and "class Box" in no_methods)
        self.assertTrue(all("縮めました" in t.splitlines()[-1] for t in (no_args, no_methods)))
        self.assertIn("切り落と", symbolmap.render(self.idx, len(no_methods) - 1).splitlines()[-1])
        for n in (400, 120, 40, 1):
            self.assertLessEqual(len(symbolmap.render(self.idx, n)), n, n)

    def test_limit_reads_the_real_config_and_rejects_bad_ones(self):
        self.assertEqual((symbolmap.limit(CFG), "_symbol_map_note" in CFG), (30000, True))
        for bad in ({}, {"symbol_map": {}}, {"symbol_map": {"max_chars": 0}}, {"symbol_map": {"max_chars": True}}):
            with self.assertRaises(hline.Infra):
                symbolmap.limit(bad)

    def test_prompts_change_only_with_a_non_empty_map(self):
        args = ("何か", {"task": None}, {"type": "object"}, None, None)
        plain, with_map = hline_spec.decompose_prompt(*args), hline_spec.decompose_prompt(*args, symbol_map="# 目次X")
        self.assertTrue(plain == hline_spec.decompose_prompt(*args, symbol_map="") and "目次X" not in plain)
        self.assertTrue("# 目次X" in with_map and "ここに無いモジュール" in with_map)
        plain, mapped = hline.build_prompt("# 題"), hline.build_prompt("# 題", None, "# 目次X")
        self.assertEqual((plain, mapped.replace("\n# 目次X\n", "", 1)), (hline.build_prompt("# 題", None, ""), plain))


class Wiring(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp, self.wt, self.inputs, self.after_first = Path(tmp.name), Path(tmp.name) / "wt", [], None
        make_repo(self.wt)
        self.cfg, self.log = dict(CFG, out=str(self.tmp / "out")), self.tmp / "log"

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            self.inputs.append((label, input))
            if self.after_first and len(self.inputs) == 1:
                self.after_first()
            model = "claude-opus-5" if label == "分解役" else "claude-sonnet-5-5"
            return 0, json.dumps({"result": "JSON ではない", "modelUsage": {model: {}}, "num_turns": 2}), ""
        for p in (mock.patch.object(proc, "run", side_effect=fake_run), mock.patch.object(proc, "resolve_cli", return_value=["claude"])):
            p.start()
            self.addCleanup(p.stop)

    def test_every_decomposer_call_rebuilds_the_map_from_the_worktree(self):
        self.after_first = lambda: make_repo(self.wt, {"harness/gamma.py": "def gamma_fn():\n    pass\n"})
        self.assertIsNone(hline_spec.decompose(self.cfg, self.wt, "# 題\n本文", {"task": None}, self.tmp)[0])
        prompts = [text for _, text in self.inputs]
        self.assertEqual(len(prompts), 1 + self.cfg["spec_retries"])
        self.assertTrue(symbolmap.HEAD in prompts[0] and "harness/alpha.py" in prompts[0] and "gamma_fn" not in prompts[0])
        self.assertTrue(all("gamma_fn" in p for p in prompts[1:]))

    def test_the_implementer_gets_the_whole_map_and_the_modules_the_what_names(self):
        """段を 1 つにしたので（C5）、実装役は TaskSpec の対象ではなく、What が挙げたモジュールと目次の全体を受け取る。"""
        what = "# 題\n\n## What\nharness/alpha.py の run を直す"
        self.assertEqual(hline.implement(self.cfg, self.wt, what, None, self.log)[0], 0)
        ((label, prompt),) = self.inputs
        self.assertTrue(label == "実装役" and symbolmap.HEAD in prompt and "harness/alpha.py" in prompt)
        self.assertIn("harness/beta.py", prompt)

    def test_a_failing_map_does_not_stop_either_agent(self):
        with mock.patch.object(symbolmap, "build", side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")):
            hline.implement(self.cfg, self.wt, "# 題\n本文", None, self.log)
            hline_spec.decompose(self.cfg, self.wt, "# 題\n本文", {"task": None}, self.tmp)
        self.assertEqual(len(self.inputs), 2 + self.cfg["spec_retries"])
        self.assertTrue(all(symbolmap.HEAD not in text for _, text in self.inputs))


if __name__ == "__main__":
    unittest.main()
