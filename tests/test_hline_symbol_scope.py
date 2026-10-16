"""実装役に渡す目次の絞り込み（What が名指すモジュールとその import 先だけ）と、試行の記録の目次の字数の検査。外の CLI・git・gh は呼ばない。"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_task  # noqa: E402
import symbolmap  # noqa: E402

CFG = hline.load_config()
FILES = {"harness/alpha.py": '"""ALPHA_DOC"""\nimport beta\n\n\ndef fa():\n    pass\n',
         "harness/beta.py": '"""BETA_DOC"""\nimport delta\n\n\ndef fb():\n    pass\n',
         "harness/gamma.py": '"""GAMMA_DOC"""\n\n\ndef fc():\n    pass\n',
         "harness/delta.py": '"""DELTA_DOC"""\n\n\ndef fd():\n    pass\n'}


def sections(text):
    return {line[3:] for line in text.splitlines() if line.startswith("## harness/")}


class Scope(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = Path(tmp.name)
        for rel, text in FILES.items():
            (self.wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / rel).write_text(text, encoding="utf-8")

    def shown(self, what):
        return symbolmap.prompt_text(CFG, self.wt, None, what, scope=True)

    def test_a_named_path_keeps_that_module_and_what_it_imports(self):
        self.assertEqual(sections(self.shown("harness/alpha.py を直す")), {"harness/alpha.py", "harness/beta.py"})

    def test_a_named_module_name_does_the_same(self):
        self.assertEqual(sections(self.shown("alpha の関数を直す")), {"harness/alpha.py", "harness/beta.py"})
        self.assertEqual(sections(self.shown("gamma モジュール")), {"harness/gamma.py"})

    def test_no_naming_keeps_the_whole_map(self):
        whole = symbolmap.prompt_text(CFG, self.wt)
        for what in (None, "", "どのモジュールも挙げない What"):
            with self.subTest(what=what):
                self.assertEqual(self.shown(what), whole)
        self.assertEqual(sections(whole), {"harness/alpha.py", "harness/beta.py", "harness/gamma.py", "harness/delta.py"})

    def test_without_scope_the_map_stays_whole(self):
        text = symbolmap.prompt_text(CFG, self.wt, None, "harness/gamma.py")
        self.assertIn("## harness/alpha.py", text)


class Record(unittest.TestCase):
    def test_implement_remembers_the_size_and_the_attempt_record_carries_it(self):
        with tempfile.TemporaryDirectory() as d:
            wt = Path(d)
            for rel, text in FILES.items():
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_text(text, encoding="utf-8")
            with mock.patch.object(hline.proc, "run", return_value=(0, "{}", "")), \
                    mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]), \
                    mock.patch.object(hline.hline_prompt.hline_quota, "message", return_value=None), \
                    mock.patch.object(hline.hline_prompt, "outcome", return_value=([], None, None, None)):
                hline.implement(CFG, wt, "harness/gamma.py を直す", None, wt / "log")
                sent = hline.proc.run.call_args.kwargs["input"]
            self.assertTrue(isinstance(hline.map_chars, int) and 0 < hline.map_chars < len(sent))
            self.assertNotIn("## harness/alpha.py", sent)

    def test_run_task_puts_the_size_in_every_record(self):
        outdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outdir, ignore_errors=True))
        host = types.SimpleNamespace(map_chars=1234, new_worktree=mock.Mock(), changed_paths=lambda w, c: [],
                                     implement=lambda cfg, wt, what, fb, log: (0, [], None, None, None),
                                     gate=lambda *a, **k: (True, "ok"))
        cfg = dict(CFG, max_attempts=1)
        with mock.patch.object(hline_task, "digest_of", return_value=None), \
                mock.patch.object(hline_task.hline_abort, "abort_reason", return_value=None), \
                mock.patch.object(hline_task.hline_resume, "previous", return_value=None):
            _, _, tries = hline_task.run_task(host, cfg, "t", "# x", outdir, first=(Path("."), "b"))
        self.assertEqual([t["map_chars"] for t in tries], [1234])


if __name__ == "__main__":
    unittest.main()
