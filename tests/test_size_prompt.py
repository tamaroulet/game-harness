"""実装役への入力に規模の制約を出す（size_limits.oversized_files・prompt_text、hline.build_prompt・implement）の検査。
一時ディレクトリと mock だけで済ませ、CLI・git・ネットワーク・モデルは呼ばない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import size_limits as sl  # noqa: E402

CFG, LIM = hline.load_config(), sl.limits()
SPEC = {"target_symbols": [], "edit_boundary": {"allowed_files": ["harness/x.py"], "forbidden_files": [],
                                                "max_diff_lines": 123, "deletable_files": ["harness/old_a.py", "harness/old_b.py"]}}
SMALL = {"new_module_max_lines": 10, "max_complexity": 4, "max_added_lines": 77}
WHAT = "# T-010-a\n\n## What\n本文"   # 実装役に渡すのは What の本文（C5）


def lines(n):
    return "x = 1\n" * n


class Base(unittest.TestCase):
    def tree(self, files):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, text in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return d


class Oversized(Base):
    def test_only_files_over_the_limit_come_back_largest_first_with_posix_paths(self):
        wt = self.tree({"harness/a.py": lines(12), "harness/b.py": lines(15), "tests/sub/c.py": lines(12), "tests/ok.py": lines(10),
                        "harness/note.txt": lines(99), "tests/bad.py": b"\xff\xfe\x00bad\n" * 20})
        self.assertEqual(sl.oversized_files(wt, SMALL), [("harness/b.py", 15), ("harness/a.py", 12), ("tests/sub/c.py", 12)])

    def test_missing_directories_and_a_custom_dirs_are_handled(self):
        wt = self.tree({"other/z.py": lines(11)})
        self.assertEqual(sl.oversized_files(wt, SMALL), [])
        self.assertEqual(sl.oversized_files(wt, SMALL, ("other", "nothing")), [("other/z.py", 11)])


class PromptText(Base):
    def test_the_values_come_from_spec_lim_and_the_worktree(self):
        wt = self.tree({"harness/big.py": lines(13)})
        text = sl.prompt_text(SPEC, wt, SMALL)
        for want in ("123", "10", "4", "harness/old_a.py", "harness/old_b.py", "harness/big.py", "13 行", "新しいテストのファイル"):
            self.assertIn(want, text)
        self.assertNotIn("---", text.splitlines())
        for old in (str(LIM["new_module_max_lines"]), str(LIM["max_complexity"])):
            self.assertNotIn(old, text)

    def test_changing_spec_and_lim_changes_the_numbers_and_the_old_ones_vanish(self):
        wt = self.tree({"harness/big.py": lines(13)})
        other = {"new_module_max_lines": 12, "max_complexity": 6}
        spec = {"edit_boundary": dict(SPEC["edit_boundary"], max_diff_lines=456, deletable_files=[])}
        text = sl.prompt_text(spec, wt, other)
        self.assertTrue("456" in text and "12 行以内" in text and "複雑度は 6" in text and "丸ごと消してよいファイル: なし" in text)
        for old in ("123", "複雑度は 4", "old_a"):
            self.assertNotIn(old, text)

    def test_nothing_to_report_says_none_and_odd_inputs_do_not_raise(self):
        empty = self.tree({})
        for spec in ({}, {"edit_boundary": {}}, "# 題", None, {"edit_boundary": "x"}):
            text = sl.prompt_text(spec, empty, SMALL)
            self.assertIn("なし", text)
            self.assertNotIn("max_diff_lines", text)
            self.assertNotIn("追加してよい行数", text)

    def test_lim_none_reads_the_config(self):
        text = sl.prompt_text(SPEC, self.tree({}))
        self.assertTrue(str(LIM["new_module_max_lines"]) in text and str(LIM["max_complexity"]) in text and "123" in text)

    def test_the_source_has_no_hard_coded_limits(self):
        body = (Path(sl.__file__)).read_text(encoding="utf-8").split("def prompt_text", 1)[1].split("def head_source", 1)[0]
        for number in ("300", "500", "15"):
            self.assertNotIn(number, body)


class BuildPrompt(unittest.TestCase):
    def test_without_a_note_the_prompt_is_unchanged(self):
        spec = WHAT
        for note in (None, ""):
            self.assertEqual(hline.build_prompt(spec, None, None, note), hline.build_prompt(spec))
        self.assertEqual(hline.build_prompt(spec, "FB", "# 目次X", None), hline.build_prompt(spec, "FB", "# 目次X"))

    def test_the_note_goes_before_the_first_separator_and_after_it_only_the_what_body(self):
        spec = WHAT
        prompt = hline.build_prompt(spec, None, None, "NOTE-BODY")
        head, tail = prompt.split("\n---\n", 1)
        self.assertIn("NOTE-BODY", head)
        self.assertNotIn("NOTE-BODY", tail)
        self.assertEqual(tail.strip(), spec.strip())
        both = hline.build_prompt(spec, "FB", "# 目次X", "NOTE-BODY")
        self.assertTrue(both.index("# 目次X") < both.index("NOTE-BODY") < both.index("\n---\n"))
        self.assertIn("FB", both.split("\n---\n")[2])


class Implement(Base):
    def run_implement(self, wt, feedback):
        inputs = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            inputs.append(input)
            return 0, json.dumps({"result": "x", "modelUsage": {"claude-sonnet-5-5": {}}, "num_turns": 1}), ""
        with mock.patch.object(hline.proc, "run", side_effect=fake_run), mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]):
            hline.implement(CFG, wt, WHAT, feedback, Path(wt) / "log")
        return inputs[0]

    def test_the_first_attempt_and_a_retry_both_carry_the_size_section(self):
        wt = self.tree({"harness/big.py": lines(LIM["new_module_max_lines"] + 3)})
        for feedback in (None, "前回の出力"):
            sent = self.run_implement(wt, feedback)
            for want in ("規模の制約", str(LIM["new_module_max_lines"]), str(LIM["max_complexity"]), "harness/big.py", "新しいテストのファイル"):
                self.assertIn(want, sent)
            self.assertEqual(sent.split("\n---\n")[1].strip(), WHAT.strip())
            self.assertEqual("前回の出力" in sent, bool(feedback))


class GateSize(Base):
    def gate(self, files, before=None):
        wt = self.tree(files)
        ok, warn = (0, "", ""), []
        with mock.patch.object(sl, "head_source", return_value=lambda path: before), \
                mock.patch.object(hline, "diff_counts", return_value={"added": 1, "deleted": 0, "deleted_files": ()}), \
                mock.patch.object(hline.proc, "run", return_value=ok):
            return (*hline.gate(CFG, wt, list(files), warnings=warn), warn)

    def test_oversized_modules_and_complexity_still_fail_and_small_ones_pass(self):
        """規模の制約は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        ok, why, warn = self.gate({"harness/x.py": lines(LIM["new_module_max_lines"])})
        self.assertTrue(ok, why)
        self.assertEqual(warn, [])
        ok, why, warn = self.gate({"harness/x.py": lines(LIM["new_module_max_lines"] + 1)})
        self.assertTrue(ok)
        self.assertIn(str(LIM["new_module_max_lines"] + 1), "\n".join(warn))
        branchy = "def f(x):\n" + "    if x:\n        x += 1\n" * LIM["max_complexity"] + "    return x\n"
        ok, why, warn = self.gate({"harness/x.py": branchy})
        self.assertTrue(ok)
        self.assertIn("循環的複雑度", "\n".join(warn))


if __name__ == "__main__":
    unittest.main()
