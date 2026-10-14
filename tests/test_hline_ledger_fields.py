"""H ライン：実装役への指示に、What が宣言したタスクの台帳の欄（題・目的・acceptance・検証コマンド）を入れる（hline_prompt.ledger_section）の検査。
作業ツリーは一時ディレクトリの偽物（docs/progress.yaml だけ）。実装役の CLI は呼ばず、host.proc.run の入力だけを捕まえる。"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_prompt  # noqa: E402

LEDGER = """tasks:
- id: T-1
  title: 台帳の題
  purpose: 台帳の目的
  acceptance:
  - 一つ目の条件
  - 二つ目の条件
  verification:
    command: python -m unittest tests.test_x
    expected_exit_code: 0
"""
WHAT = "# T-1\n\nマイルストーン: M-1\nタスク: T-1\n\n## What\n本文\n"
CFG = {"implementer": {"cli": "x", "budget": {"max_turns": 5}},"ttl_seconds": {"implementer": 5}}


def worktree(root, ledger):
    if ledger is not None:
        (Path(root) / "docs").mkdir()
        (Path(root) / "docs" / "progress.yaml").write_text(ledger, encoding="utf-8")
    return root


def run_implement(wt, what, feedback=None):
    """implement を 1 回通し、実装役に渡った指示を返す。"""
    seen = []
    host = types.SimpleNamespace(
        symbolmap=types.SimpleNamespace(prompt_text=lambda *a, **k: ""),
        proc=types.SimpleNamespace(run=lambda *a, **k: seen.append(k["input"]) or (0, "", ""), resolve_cli=lambda cli: cli),
        build_prompt=hline_prompt.build_prompt)
    log = Path(wt) / "log.txt"
    with mock.patch.object(hline_prompt.oracle_deny, "with_deny", lambda agent, cfg: agent), \
            mock.patch.object(hline_prompt.impacted, "scoped_agent", lambda agent: agent), \
            mock.patch.object(hline_prompt.impacted, "prompt_note", lambda: ""), \
            mock.patch.object(hline_prompt, "capped_args", lambda agent, cli, cap: [cli]), \
            mock.patch.object(hline_prompt, "outcome", lambda agent, out, cap: ([], None, None, None)):
        hline_prompt.implement(host, CFG, wt, what, feedback, log)
    return seen[0]


class LedgerFieldsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def plain(self, what=WHAT, feedback=None):
        return hline_prompt.build_prompt(what, feedback, None)

    def test_fields_appear_before_first_separator(self):
        prompt = run_implement(worktree(self.root, LEDGER), WHAT)
        head = prompt.split("\n---\n")[0]
        lines = head.splitlines()
        for need in ("台帳の題", "台帳の目的", "python -m unittest tests.test_x", "台帳に従う"):
            self.assertTrue(any(need in line for line in lines), need)
        for item in ("一つ目の条件", "二つ目の条件"):
            self.assertEqual(sum(item in line for line in lines), 1, item)
        self.assertNotIn("台帳の題", prompt.split("\n---\n", 1)[1])

    def test_follow_up_attempt_also_has_section(self):
        prompt = run_implement(worktree(self.root, LEDGER), WHAT, feedback="FAILED: test_a")
        self.assertIn("台帳の題", prompt.split("\n---\n")[0])
        self.assertIn("FAILED: test_a", prompt)

    def test_no_section_and_same_prompt_otherwise(self):
        bad = {
            "宣言しない What": (LEDGER, "# T\n\n## What\n本文\n"),
            "台帳が無い": (None, WHAT),
            "台帳に無いタスク": (LEDGER, WHAT.replace("T-1\n\n##", "T-9\n\n##")),
            "YAML として読めない": ("tasks: [unclosed\n  - : :\n", WHAT),
            "tasks が列でない": ("tasks: 3\n", WHAT),
            "acceptance が列でない": (LEDGER.replace("  acceptance:\n  - 一つ目の条件\n  - 二つ目の条件\n", "  acceptance: 文字列\n"), WHAT),
        }
        for name, (ledger, what) in bad.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                wt = worktree(root, ledger)
                self.assertEqual(hline_prompt.ledger_section(wt, what), "")
                self.assertEqual(run_implement(wt, what), self.plain(what))
                self.assertEqual(run_implement(wt, what, "FAILED"), self.plain(what, "FAILED"))


if __name__ == "__main__":
    unittest.main()
