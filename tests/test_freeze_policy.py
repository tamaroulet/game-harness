"""H ライン：収束しなかった What の失敗の要約（hline_respec）と、分解役の入力（hline_spec）の検査。
段を 1 つにしたので（C5）、ラインは再分解を呼ばない。ここで確かめるのは、残したモジュールの中身だけ。
push・PR の作成・モデルの呼び出しは起こさない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_respec  # noqa: E402
import hline_spec  # noqa: E402
from test_hline_queue import CFG, SPEC  # noqa: E402


class FailureSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out, self.cfg = Path(self.tmp.name), {"gate_tail_chars": 40}

    def put(self, name, text):
        (self.out / name).write_text(text, encoding="utf-8")

    def tries(self, *pairs):
        return [{"run": r, "attempt": a, "cli_exit": 7, "models": [], "gate": False, "usage": None} for r, a in pairs]

    def test_it_reads_the_last_try_and_names_the_gate_output_the_boundary_violation_and_the_exit_code(self):
        self.put("gate-0-1.log", "FIRST-GATE")
        self.put("gate-1-2.log", "変えてよいファイルの外を変えています: harness/x.py")
        self.put("implementer-1-2.log", "IMPL-TAIL-OK")
        got = hline_respec.failure_summary(self.cfg, self.out, self.tries((0, 1), (1, 2)))
        for word in ("変えてよいファイルの外を変えています: harness/x.py", "IMPL-TAIL-OK", "7"):
            self.assertIn(word, got)
        for word in ("FIRST", "記録なし", "作り直す前の TaskSpec"):
            self.assertNotIn(word, got)

    def test_missing_logs_and_an_empty_list_say_no_record_without_raising(self):
        for tries in (self.tries((0, 1)), []):
            self.assertGreaterEqual(hline_respec.failure_summary(self.cfg, self.out, tries).count("記録なし"), 2)
        self.assertTrue(hline_respec.failure_summary(self.cfg, self.out, [], {"目的": "x"}).rstrip().endswith(json.dumps({"目的": "x"}, ensure_ascii=False, indent=2)))

    def test_a_long_log_is_cut_to_the_tail_by_gate_tail_chars(self):
        self.put("gate-0-1.log", "Z" * 100 + "0123456789")
        self.put("implementer-0-1.log", "Q" * 100 + "ABCDEFGHIJ")
        got = hline_respec.failure_summary(self.cfg, self.out, self.tries((0, 1)))
        self.assertIn("Z" * 30 + "0123456789", got)
        self.assertNotIn("Z" * 31, got)


class PromptAndDecompose(unittest.TestCase):
    ARGS = ("何か", {"task": None}, {"type": "object"}, None, None)

    def test_a_failure_adds_one_separator_the_summary_and_the_request_and_without_it_the_prompt_is_unchanged(self):
        plain = hline_spec.decompose_prompt(*self.ARGS, symbol_map="# 目次")
        self.assertEqual(plain, hline_spec.decompose_prompt(*self.ARGS, symbol_map="# 目次", failure=None))
        got = hline_spec.decompose_prompt(*self.ARGS, symbol_map="# 目次", failure="FAIL-BODY")
        self.assertEqual(got.count("\n---\n"), plain.count("\n---\n") + 1)
        self.assertTrue(got.startswith(plain.rstrip("\n")))
        for word in ("収束しませんでした", "FAIL-BODY", "範囲を狭める", "編集境界を直して"):
            self.assertIn(word, got[len(plain.rstrip("\n")):])
            self.assertNotIn(word, plain)

    def run_decompose(self, cached=SPEC, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        reply = (0, json.dumps({"result": json.dumps(SPEC)}), None, None)
        with mock.patch.object(hline_spec, "call", return_value=reply) as call, \
                mock.patch.object(hline_spec, "pinned_models", return_value=["claude-opus-5"]), \
                mock.patch.object(hline_spec, "load_schema", return_value={"type": "object"}), \
                mock.patch.object(hline_spec.symbolmap, "prompt_text", return_value=""), \
                mock.patch.object(hline_spec, "load_spec", return_value=cached) as load, \
                mock.patch.object(hline_spec, "save_spec") as save:
            got = hline_spec.decompose(CFG, out, "# 題", {"task": None}, out, **kw)
        return got, call, load, save, sorted(p.name for p in out.iterdir())

    def test_with_a_failure_the_saved_taskspec_is_not_used_or_written_and_the_names_follow_the_tag(self):
        (spec, record), call, load, save, files = self.run_decompose(failure="FAIL-BODY", tag="respec")
        self.assertEqual((spec, record["reused"], files), (SPEC, False, ["taskspec-respec.json"]))
        load.assert_not_called()
        save.assert_not_called()
        self.assertEqual(call.call_args.args[5].name, "respec-1.log")
        self.assertIn("FAIL-BODY", call.call_args.args[4])



if __name__ == "__main__":
    unittest.main()
