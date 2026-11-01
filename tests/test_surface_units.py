"""面の差分の検査（harness/surface.py の check）の、harness/hline_prompt.py を関数の単位で照らす規則の試験。
面の中は build_prompt と ledger_section の 2 つの関数だけで、ほかの頂の文（import・implement()・定数・docstring）は面の外。
一時ディレクトリの偽のリポジトリと、形だけを似せた偽の hline_prompt.py で確かめる（本物のファイルの中身には依らない）。
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import surface  # noqa: E402

PROMPT = '''"""偽の実装役の入力の組み立て。"""
import time
from pathlib import Path

import yaml

TIMEOUT_EXIT = 124


def build_prompt(what, feedback=None, symbol_map=None):
    """実装役に渡す入力。"""
    p = ["あなたは実装役です。", "作業ディレクトリの中だけで変えてください。"]  # 指示の並び
    p += ["", "---", what.strip()]
    if feedback:
        p += ["", "前回は通りませんでした。", feedback.replace("\\r", "")]
    return "\\n".join(p) + "\\n"


def ledger_section(wt, what):
    """台帳の欄を節にする。"""
    try:
        state = yaml.safe_load((Path(wt) / "docs" / "progress.yaml").read_text(encoding="utf-8"))
        one = lambda s: " ".join(s.split())  # noqa: E731
        return "\\n".join(["## 台帳", one(str(state))])
    except Exception:  # noqa: BLE001
        return ""


def implement(host, cfg, wt, what, feedback, log):
    """実装役を 1 回呼ぶ。"""
    began = time.monotonic()
    code = host.proc.run(["claude", "--max-turns", str(cfg["max_turns"])], wt, cfg["ttl"])
    host.seconds = round(time.monotonic() - began)
    return code
'''


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@example.com")
        self.path = self.repo / surface.PROMPT_FILE
        write(self.path, PROMPT)
        write(self.repo / surface.SETTING, json.dumps({"max_attempts": 2}))
        self.old = self.commit("base")

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = surface.main(["check", "--repo", str(self.repo), "--old", self.old, "--new", "HEAD"])
        hits = [ln for ln in out.getvalue().splitlines() if ln.startswith("関数の単位: ")]
        return rc, hits

    def edit(self, old, new, count=1):
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), count, old)
        write(self.path, text.replace(old, new))
        self.commit("edit")
        return self.check()

    def assert_hits(self, got, n, where=None):
        rc, hits = got
        self.assertEqual(len(hits), n, hits)
        self.assertEqual(rc, 1 if n else 0)
        for hit in hits:
            if where:
                self.assertIn(where, hit)
        return hits

    # ---- 当たらない差分
    def test_only_changing_the_instruction_strings_has_no_hits(self):
        self.assert_hits(self.edit("作業ディレクトリの中だけで変えてください。", "作業ディレクトリの外は変えないでください。"), 0)

    def test_string_methods_and_a_new_line_of_instructions_have_no_hits(self):
        got = self.edit('    p += ["", "---", what.strip()]\n',
                        '    p += ["", "---", what.strip()]\n    p.append(" ".join(x.strip().split() for x in [what]).format())\n'
                        '    p = [s.replace("　", " ") for s in p]\n')
        self.assert_hits(got, 0)

    def test_only_changing_comments_has_no_hits(self):
        got = self.edit("  # 指示の並び\n", "  # 指示の並び（並びを変えるときは試験も直す）\n# 頂のコメント\n")
        self.assert_hits(got, 0)

    def test_an_unchanged_file_is_not_checked(self):
        write(self.repo / surface.SETTING, json.dumps({"max_attempts": 3}))
        self.commit("setting only")
        self.assert_hits(self.check(), 0)

    # ---- 頂の文
    def test_one_line_in_implement_is_a_hit_at_the_top(self):
        hits = self.assert_hits(self.edit('str(cfg["max_turns"])', '"999"'), 1, "頂の文")
        self.assertIn("面の外の頂の文が違います", hits[0])

    def test_adding_an_import_is_a_hit(self):
        self.assert_hits(self.edit("import time\n", "import time\nimport subprocess\n"), 1, "頂の文")

    def test_adding_a_top_function_or_constant_is_a_hit(self):
        for new in ("TIMEOUT_EXIT = 124\nEXTRA = 1\n", "TIMEOUT_EXIT = 124\n\n\ndef helper():\n    return 1\n"):
            with self.subTest(new):
                self.assert_hits(self.edit("TIMEOUT_EXIT = 124\n", new), 1, "頂の文")
                git(self.repo, "reset", "-q", "--hard", self.old)

    def test_changing_the_module_docstring_is_a_hit(self):
        self.assert_hits(self.edit("偽の実装役の入力の組み立て。", "別の説明。"), 1, "頂の文")

    def test_swapping_the_order_of_top_statements_is_a_hit(self):
        self.assert_hits(self.edit("import time\nfrom pathlib import Path\n", "from pathlib import Path\nimport time\n"),
                         1, "頂の文")

    def test_a_second_definition_of_a_face_is_a_hit_at_the_top(self):
        got = self.edit("TIMEOUT_EXIT = 124\n", "TIMEOUT_EXIT = 124\n\n\ndef build_prompt(what, feedback=None, symbol_map=None):\n"
                        "    return what\n")
        self.assert_hits(got, 1, "頂の文")

    # ---- 2 つの関数の形
    def test_changing_the_arguments_is_a_hit(self):
        self.assert_hits(self.edit("def ledger_section(wt, what):", "def ledger_section(wt, what, extra=None):"),
                         1, "ledger_section")

    def test_a_decorator_is_a_hit(self):
        self.assert_hits(self.edit("def build_prompt(", "@staticmethod\ndef build_prompt("), 1, "build_prompt")

    def test_removing_a_function_is_a_hit(self):
        text = self.path.read_text(encoding="utf-8")
        start, end = text.index("def ledger_section"), text.index("def implement")
        write(self.path, text[:start] + text[end:])
        self.commit("remove")
        hits = self.assert_hits(self.check(), 1, "ledger_section")
        self.assertIn("関数がありません", hits[0])

    def test_a_broken_python_file_and_a_missing_file_are_hits(self):
        self.assert_hits(self.edit("    return code\n", "    return code(\n"), 1, "ast で読めません")
        self.path.unlink()
        self.commit("gone")
        rc, hits = self.check()
        self.assertEqual(rc, 1)
        self.assertEqual(sum("ast で読めません" in h for h in hits), 1)

    # ---- 2 つの関数の中
    def test_forbidden_things_inside_a_function_are_one_hit_each(self):
        anchor = '    p += ["", "---", what.strip()]\n'
        cases = {"import": "    import os\n",
                 "from import": "    from os import system\n",
                 "global": "    global TIMEOUT_EXIT\n",
                 "open": "    open('x.txt').close()\n",
                 "exec": "    exec('p = 1')\n",
                 "getattr": "    getattr(what, 'strip')\n",
                 "write_text": "    Path('x.txt').write_text('x')\n",
                 "attribute store": "    what.y = 1\n",
                 "attribute augassign": "    what.y += 1\n",
                 "attribute del": "    del what.y\n",
                 "attribute in a tuple target": "    a, what.y = 1, 2\n",
                 "subprocess.run": "    subprocess.run(['x'])\n",
                 "time.sleep": "    time.sleep(1)\n",
                 "unlink": "    Path(what).unlink()\n"}
        for name, line in cases.items():
            with self.subTest(name):
                hits = self.assert_hits(self.edit(anchor, anchor + line), 1, "build_prompt")
                self.assertIn(" 行", hits[0])
                git(self.repo, "reset", "-q", "--hard", self.old)

    def test_a_forbidden_call_in_a_default_value_is_a_hit(self):
        got = self.edit("def build_prompt(what, feedback=None, symbol_map=None):",
                        "def build_prompt(what, feedback=None, symbol_map=None) -> __import__('os').getcwd():")
        self.assert_hits(got, 1, "build_prompt")

    def test_a_hit_shows_only_the_rule_the_function_and_the_line(self):
        anchor = '    p += ["", "---", what.strip()]\n'
        hits = self.assert_hits(self.edit(anchor, anchor + "    open('secret_name.txt')\n"), 1)
        line = PROMPT.splitlines().index(anchor.rstrip("\n")) + 2
        self.assertEqual(hits[0], f"関数の単位: 禁じた名の呼び出しがあります: build_prompt {line} 行")
        self.assertNotIn("secret_name", hits[0])


if __name__ == "__main__":
    unittest.main()
