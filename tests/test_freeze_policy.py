"""H ライン：分解役の入力（hline_spec）の検査。
push・PR の作成・モデルの呼び出しは起こさない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_spec  # noqa: E402
from test_hline_queue import CFG, SPEC  # noqa: E402


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
