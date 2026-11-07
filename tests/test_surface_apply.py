"""提案の「## 差分」をコミットにする道具（harness/surface_apply.py）の検査。一時のリポジトリで本物の git を使い、surface check にも通す。

    python -m unittest tests.test_surface_apply -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "harness")]
import surface  # noqa: E402
import surface_apply as sa  # noqa: E402
from bench_start import BenchError  # noqa: E402

PROMPT = ('"""頭の文。"""\nimport yaml\n\n\ndef build_prompt(what, feedback=None, symbol_map=None):\n    p = ["古い指示"]\n    return "\\n".join(p)\n\n\n'
          'def ledger_section(wt, what):\n    return ""\n\n\ndef implement(host, cfg, wt, what, feedback, log):\n    return 1\n')
SETTING = '{\n  "_comment": "日本語の説明",\n  "implementer_max_turns": 15,\n  "implementer_resume_max_turns": 8,\n  "max_attempts": 2\n}\n'
NEW_FN = 'def build_prompt(what, feedback=None, symbol_map=None):\n    p = ["新しい指示"]\n    return "\\n".join(p)'


def proposal(*blocks):
    body = "\n".join(f"```{lang}\n{text}\n```" for lang, text in blocks)
    return f"前置き\n\n## 狙う失敗の型\n型\n## 差分\n{body}\n## 予想\n予想\n"


class Parse(unittest.TestCase):
    def errs(self, *blocks):
        return sa.parse(proposal(*blocks))[1]

    def test_a_function_and_a_knob(self):
        change, errs = sa.parse(proposal(("python", NEW_FN), ("json", '{"max_attempts": 3}')))
        self.assertEqual(errs, [])
        self.assertEqual(change.functions, {"build_prompt": NEW_FN})
        self.assertEqual(change.knobs, {"max_attempts": 3})

    def test_the_heading_and_a_block_are_needed(self):
        self.assertIn("見出し ## 差分 がありません", sa.parse("## 狙う失敗の型\nx\n")[1])
        self.assertTrue(sa.parse("## 差分\n文だけ\n")[1])
        self.assertTrue(self.errs(("sh", "ls")))

    def test_only_the_two_functions_are_allowed(self):
        self.assertTrue(self.errs(("python", "def implement(host, cfg, wt, what, feedback, log):\n    return 2")))
        self.assertTrue(self.errs(("python", "import os\n" + NEW_FN)))
        self.assertTrue(self.errs(("python", "X = 1")))
        self.assertTrue(self.errs(("python", NEW_FN + "\n" + NEW_FN)))
        self.assertTrue(self.errs(("python", "def build_prompt(")))
        self.assertTrue(self.errs(("python", NEW_FN), ("python", NEW_FN)))

    def test_knobs_are_checked_against_the_frame(self):
        self.assertTrue(self.errs(("json", '{"unknown_knob": 1}')))
        self.assertTrue(self.errs(("json", '{"max_attempts": 99}')))
        self.assertTrue(self.errs(("json", "{}")))
        self.assertTrue(self.errs(("json", "[1]")))
        self.assertTrue(self.errs(("json", "not json")))


class Fence(unittest.TestCase):
    def test_a_heading_like_line_inside_the_block_is_not_a_heading(self):
        fn = 'def build_prompt(what, feedback=None, symbol_map=None):\n    return """\n## 見出しの形の行\n本文\n"""'
        change, errs = sa.parse(proposal(("python", fn)))
        self.assertEqual(errs, [])
        self.assertEqual(change.functions["build_prompt"], fn)

    def test_the_section_ends_at_the_next_real_heading(self):
        text = proposal(("json", '{"max_attempts": 3}'))
        self.assertEqual(sa.section(text), '```json\n{"max_attempts": 3}\n```\n')


class Leaks(unittest.TestCase):
    LINES = [("p", 1, "ふつうの文"), ("p", 2, "SecretPkg を使う"), ("p", 3, "secretpkg_step"), ("p", 4, "test_hidden_a を通す"), ("p", 5, "test_hidden_ab")]

    def test_words_need_word_boundaries_and_packages_do_not(self):
        got = surface.leaks(self.LINES, {"test_hidden_a"}, {"secretpkg"})
        self.assertEqual(got, ["漏れ: p:2", "漏れ: p:3", "漏れ: p:4"])

    def test_either_list_may_be_empty(self):
        self.assertEqual(surface.leaks(self.LINES, set()), [])
        self.assertEqual(surface.leaks(self.LINES, {"test_hidden_a"}), ["漏れ: p:4"])
        self.assertEqual(surface.leaks(self.LINES, set(), {"secretpkg"}), ["漏れ: p:2", "漏れ: p:3"])


class Replace(unittest.TestCase):
    def test_other_lines_stay_exactly_the_same(self):
        new = sa.replace_functions(PROMPT, {"build_prompt": NEW_FN})
        self.assertEqual(new, PROMPT.replace('p = ["古い指示"]', 'p = ["新しい指示"]'))

    def test_both_functions_with_a_longer_body(self):
        longer = "def ledger_section(wt, what):\n    a = 1\n    b = 2\n    return str(a + b)"
        new = sa.replace_functions(PROMPT, {"build_prompt": NEW_FN, "ledger_section": longer})
        self.assertIn(NEW_FN, new)
        self.assertIn(longer, new)
        self.assertIn("def implement(host, cfg, wt, what, feedback, log):\n    return 1\n", new)

    def test_a_missing_function_is_an_error(self):
        with self.assertRaises(BenchError):
            sa.replace_functions('"""頭。"""\n', {"build_prompt": NEW_FN})

    def test_knobs_keep_the_comment_and_the_order(self):
        doc = json.loads(sa.replace_knobs(SETTING, {"max_attempts": 3}))
        self.assertEqual(list(doc), ["_comment", "implementer_max_turns", "implementer_resume_max_turns", "max_attempts"])
        self.assertEqual((doc["_comment"], doc["max_attempts"]), ("日本語の説明", 3))


class Commit(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.com")
        (self.repo / "harness").mkdir()
        (self.repo / "config").mkdir()
        (self.repo / "harness" / "hline_prompt.py").write_bytes(PROMPT.encode("utf-8"))
        (self.repo / "config" / "surface.json").write_bytes(SETTING.encode("utf-8"))
        (self.repo / "README.md").write_bytes(b"x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.rev = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=True, text=True, encoding="utf-8", timeout=60).stdout

    def apply(self, *blocks):
        return sa.apply(self.repo, self.rev, proposal(*blocks), "m")

    def changed(self, sha):
        return self.git("diff", "--name-only", self.rev, sha).split()

    def test_a_function_change_touches_only_the_prompt_file_and_the_parent_is_the_rev(self):
        sha = self.apply(("python", NEW_FN))
        self.assertEqual(self.changed(sha), ["harness/hline_prompt.py"])
        self.assertEqual(self.git("rev-parse", f"{sha}^").strip(), self.rev)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.rev)   # 枝と作業ツリーは動かさない
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("log", "-1", "--format=%an", sha).strip(), "harness-cycle")

    def test_a_knob_change_touches_only_the_setting(self):
        sha = self.apply(("json", '{"max_attempts": 3}'))
        self.assertEqual(self.changed(sha), ["config/surface.json"])
        self.assertEqual(json.loads(self.git("show", f"{sha}:config/surface.json"))["max_attempts"], 3)

    def test_the_commit_passes_the_surface_check(self):
        sha = self.apply(("python", NEW_FN), ("json", '{"max_attempts": 3}'))
        self.assertEqual(surface.outside(surface.name_status(self.repo, self.rev, sha)), [])
        self.assertEqual(surface.setting(self.repo, sha), [])
        self.assertEqual(surface.unit_check(self.repo, self.rev, sha), [])

    def test_the_surface_check_still_catches_a_banned_body(self):
        sha = self.apply(("python", 'def build_prompt(what, feedback=None, symbol_map=None):\n    open("x")\n    return ""'))
        self.assertTrue(surface.unit_check(self.repo, self.rev, sha))

    def test_the_surface_check_still_catches_changed_arguments(self):
        sha = self.apply(("python", "def build_prompt(what):\n    return what"))
        self.assertTrue(surface.unit_check(self.repo, self.rev, sha))

    def test_no_change_makes_no_commit(self):
        same = 'def build_prompt(what, feedback=None, symbol_map=None):\n    p = ["古い指示"]\n    return "\\n".join(p)'
        with self.assertRaisesRegex(BenchError, "何も変わりません"):
            self.apply(("python", same))
        with self.assertRaisesRegex(BenchError, "何も変わりません"):
            self.apply(("json", '{"max_attempts": 2}'))

    def test_errors_in_the_proposal_make_no_commit(self):
        before = self.git("count-objects")
        with self.assertRaises(BenchError):
            self.apply(("python", "import os"))
        self.assertEqual(self.git("count-objects"), before)

    def test_the_command_moves_only_the_named_branch(self):
        path = self.repo.parent / f"{self.repo.name}.md"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        path.write_text(proposal(("python", NEW_FN)), encoding="utf-8")
        with unittest.mock.patch("builtins.print"):
            self.assertEqual(sa.main(["--repo", str(self.repo), "--rev", self.rev, "--proposal", str(path), "--branch", "cycle/c1"]), 0)
        self.assertEqual(self.changed("cycle/c1"), ["harness/hline_prompt.py"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.rev)


class RealFile(unittest.TestCase):
    """本物の hline_prompt.py の 2 つの関数を、そのままの字で読み直して置き換えても、1 字も変わらない。"""

    def test_a_round_trip_on_the_real_file_changes_nothing(self):
        source = (ROOT / surface.PROMPT_FILE).read_bytes().decode("utf-8")
        import ast
        tree = ast.parse(source)
        lines = source.split("\n")
        functions = {n.name: "\n".join(lines[n.lineno - 1:n.end_lineno]) for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name in surface.FACES}
        self.assertEqual(sorted(functions), sorted(surface.FACES))
        text = proposal(("python", "\n\n".join(functions.values())))
        change, errs = sa.parse(text)
        self.assertEqual(errs, [])
        self.assertEqual(sa.replace_functions(source, change.functions), source)


if __name__ == "__main__":
    unittest.main()
