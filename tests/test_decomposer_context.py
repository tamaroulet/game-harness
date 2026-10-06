"""分解役の文脈（What が挙げたモジュールの docstring の節・読む道具・ターン数の上限）の検査。proc.run を差し替え、外の CLI・git・gh は呼ばない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_budget as hb  # noqa: E402
import hline_spec  # noqa: E402
import proc  # noqa: E402
import symbolmap  # noqa: E402

CFG = hline.load_config()
FILES = {"harness/a.py": '"""ALPHA_DOC の本文。"""\nimport b\n\n\ndef fa():\n    pass\n',
         "harness/b.py": '"""BETA_DOC の本文。"""\nimport d\n\n\ndef fb():\n    pass\n',
         "harness/c.py": '"""GAMMA_DOC の本文。"""\n\n\ndef fc():\n    pass\n',
         "harness/d.py": '"""DELTA_DOC の本文。"""\n\n\ndef fd():\n    pass\n',
         "harness/nodoc.py": "def fn():\n    pass\n",
         "tests/test_a.py": "import a\n"}


def make_repo(root):
    for rel, text in FILES.items():
        (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(root) / rel).write_text(text, encoding="utf-8")


def allowed_tools(agent):
    flags = agent["extra_flags"]
    return flags[flags.index("--allowedTools") + 1].split(",")


class WithRepo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp, self.wt = Path(tmp.name), Path(tmp.name) / "wt"
        make_repo(self.wt)


class Docs(WithRepo):
    def text(self, what, cfg=CFG):
        return symbolmap.prompt_text(cfg, self.wt, what=what)

    def test_module_index_carries_the_docstring_or_none(self):
        self.assertEqual(symbolmap.module_index('"""説明\n本文"""\nx = 1\n', "harness/x.py")["doc"], "説明\n本文")
        self.assertIsNone(symbolmap.module_index("x = 1\n", "harness/x.py")["doc"])
        self.assertIsNone(symbolmap.module_index("def (:\n", "harness/x.py"))

    def test_a_named_module_and_what_it_imports_get_a_docstring_section(self):
        text = self.text("harness/a.py を直す")
        for need in (symbolmap.HEAD, symbolmap.DOC_HEAD, "ALPHA_DOC", "BETA_DOC", "## harness/a.py", "## harness/b.py"):
            self.assertIn(need, text)
        self.assertTrue(text.startswith(symbolmap.DOC_HEAD))
        self.assertTrue("GAMMA_DOC" not in text and "DELTA_DOC" not in text)

    def test_a_bare_file_name_and_a_backslash_path_are_picked_up(self):
        for what in ("a.py を直す", "harness\\a.py を直す", "（a.pyを直す）"):
            with self.subTest(what=what):
                self.assertIn("ALPHA_DOC", self.text(what))
        self.assertNotIn("ALPHA_DOC", self.text("data.py を直す"))

    def test_without_what_the_text_is_the_plain_map(self):
        plain = symbolmap.prompt_text(CFG, self.wt)
        for what in (None, "", "どのモジュールも挙げない What"):
            with self.subTest(what=what):
                self.assertEqual(self.text(what), plain)
        self.assertNotIn(symbolmap.DOC_HEAD, plain)

    def test_the_length_and_the_docstring_share_stay_inside_the_limit(self):
        for size in (200, 700, 1500, 5000):
            cfg = dict(CFG, symbol_map={"max_chars": size})
            with self.subTest(size=size):
                self.assertLessEqual(len(self.text("harness/a.py", cfg)), size)
                self.assertLessEqual(len(self.text(None, cfg)), size)
                index = symbolmap.build(self.wt)
                docs = symbolmap.docs_text(index, symbolmap.candidate_modules("a.py", self.wt, index), size // 3)
                self.assertLessEqual(len(docs), size // 3)

    def test_a_small_budget_drops_modules_from_the_tail_and_ends_with_the_cut_line(self):
        index = symbolmap.build(self.wt)
        paths = ("harness/a.py", "harness/b.py")
        full = symbolmap.docs_text(index, paths, 10000)
        room = len(full) - 5
        cut = symbolmap.docs_text(index, paths, room)
        self.assertTrue(cut.endswith(symbolmap.CUT) and len(cut) <= room)
        self.assertTrue("ALPHA_DOC" in cut and "BETA_DOC" not in cut)
        self.assertEqual(symbolmap.docs_text(index, paths, 0), "")
        self.assertEqual(symbolmap.docs_text(index, (), 100), "")
        self.assertEqual(symbolmap.docs_text(index, ("harness/nodoc.py",), 100), "")

    def test_candidates_follow_one_import_step_in_index_order(self):
        index = symbolmap.build(self.wt)
        self.assertEqual(symbolmap.mentioned_modules("b.py と harness/a.py", index), ("harness/a.py", "harness/b.py"))
        self.assertEqual(symbolmap.imported_modules(self.wt, ["harness/a.py"], index), ("harness/b.py",))
        self.assertEqual(symbolmap.candidate_modules("a.py", self.wt, index), ("harness/a.py", "harness/b.py"))
        self.assertEqual(symbolmap.candidate_modules("何も挙げない", self.wt, index), ())
        self.assertEqual(symbolmap.imported_modules(self.wt, ["harness/none.py"], index), ())

    def test_an_unreadable_or_broken_file_is_skipped(self):
        (self.wt / "harness" / "bad.py").write_text("def (:\n", encoding="utf-8")
        index = symbolmap.build(self.wt)
        self.assertEqual(symbolmap.imported_modules(self.wt, ["harness/bad.py", "harness/a.py"], index), ("harness/b.py",))


class Config(unittest.TestCase):
    def test_the_decomposer_may_only_read(self):
        tools = allowed_tools(CFG["decomposer"])
        self.assertIn("Read", tools)
        self.assertTrue("Grep" not in tools and "Glob" not in tools)
        lim = hb.limits(CFG["decomposer"])
        args = hb.agent_args(CFG["decomposer"], ["claude"], lim)
        self.assertEqual(args[args.index("--allowedTools") + 1], ",".join(tools))

    def test_the_turn_cap_comes_from_the_config(self):
        cap = CFG["decomposer"]["budget"]["max_turns"]
        self.assertEqual(cap, 10)
        self.assertEqual(hb.agent_args(CFG["decomposer"], ["claude"], hb.limits(CFG["decomposer"]))[-2:], ["--max-turns", str(cap)])


class Wiring(WithRepo):
    def setUp(self):
        super().setUp()
        self.inputs, self.cfg = [], dict(CFG, out=str(self.tmp / "out"))

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            self.inputs.append(input)
            return 0, json.dumps({"result": "JSON ではない", "modelUsage": {"claude-opus-5": {}}, "num_turns": 2}), ""
        for p in (mock.patch.object(proc, "run", side_effect=fake_run), mock.patch.object(proc, "resolve_cli", return_value=["claude"])):
            p.start()
            self.addCleanup(p.stop)

    def test_every_decomposer_call_carries_the_docstrings_of_the_named_modules(self):
        hline_spec.decompose(self.cfg, self.wt, "# 題\nharness/a.py を直す", {"task": None}, self.tmp)
        self.assertEqual(len(self.inputs), 1 + self.cfg["spec_retries"])
        for text in self.inputs:
            self.assertTrue(symbolmap.DOC_HEAD in text and "ALPHA_DOC" in text and "GAMMA_DOC" not in text)

    def test_a_failing_map_does_not_stop_the_decomposer(self):
        with mock.patch.object(symbolmap, "build", side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")):
            hline_spec.decompose(self.cfg, self.wt, "# 題\nharness/a.py を直す", {"task": None}, self.tmp)
        self.assertEqual(len(self.inputs), 1 + self.cfg["spec_retries"])
        self.assertTrue(all(symbolmap.DOC_HEAD not in t and symbolmap.HEAD not in t for t in self.inputs))


if __name__ == "__main__":
    unittest.main()
