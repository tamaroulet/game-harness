"""H ライン：収束しなかった What の再分解（S2-5）の検査。push・PR の作成・モデルの呼び出しは起こさない（World が差し替える）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline  # noqa: E402
import hline_respec  # noqa: E402
import hline_spec  # noqa: E402
from test_hline_queue import CFG, SPEC, World, spec_with  # noqa: E402

N = CFG["max_attempts"]


class Respec(World):
    def patch_agents(self):
        self.dec_calls, self.gate_n, self.verdicts = [], 0, []
        self.patch(hline, "decompose", side_effect=self.decompose)
        self.patch(hline, "second_round", wraps=hline_respec.second_round)
        self.patch(hline, "implement", side_effect=self.implement)
        self.patch(hline, "gate", side_effect=self.gate)
        self.patch(hline, "run_task", wraps=hline.run_task)
        self.patch(hline, "mark", wraps=hline.mark)

    def decompose(self, cfg, wt, what, item, outdir, failure=None, tag="decomposer"):
        self.dec_calls.append({"failure": failure, "tag": tag, "title": item["title"]})
        return spec_with(max_diff_lines=50 if failure else 100), {"attempts": [], "reused": False, "reason": None}

    def implement(self, cfg, wt, spec, feedback, log):
        Path(log).write_text(f"IMPL-LOG-{Path(log).name}", encoding="utf-8")
        return 0, ["claude-sonnet-5-5"], None

    def gate(self, cfg, wt, paths, spec=None, task=None, **kw):
        self.gate_n += 1
        return (self.verdicts[self.gate_n - 1] if self.gate_n <= len(self.verdicts) else False), f"GATE-OUT-{self.gate_n}"

    def item(self, name="010-a"):
        return self.state()["items"][name]

    def runs(self):
        return [c.kwargs.get("run", 0) for c in hline.run_task.call_args_list]

    def assert_no_outside_effect(self):
        self.assertEqual([c.args[0] for c in hline.proc.run.call_args_list if c.args[0] != ["git", "worktree", "prune"]], [])
        hline.proc.resolve_cli.assert_not_called()
        for fn in (hline.create_pr, hline.open_pr, hline.integrate):
            fn.assert_not_called()
        self.assertEqual((self.created, self.integrated), ([], []))


class RespecFlow(Respec):
    def test_a_what_that_fails_max_attempts_times_is_decomposed_again_with_the_failure_and_run_again_once(self):
        self.put("010-a")
        self.assertEqual(self.poll(), 1)
        self.assertEqual([c["tag"] for c in self.dec_calls], ["decomposer", "respec"])
        failure = self.dec_calls[1]["failure"]
        self.assertIn(f"GATE-OUT-{N}", failure)   # 最後の Gate 1 の出力
        self.assertNotIn("GATE-OUT-1", failure)
        self.assertIn("harness/textnorm.py", failure)   # 作り直す前の TaskSpec
        self.assertEqual(self.runs(), [0, 1])
        self.assertEqual(hline.implement.call_count, 2 * N)
        self.assertEqual(hline.new_worktree.call_count, 2)   # 最初の分解と、再分解
        self.assert_no_outside_effect()

    def test_the_second_run_is_announced_and_its_tries_follow_the_first_in_one_list(self):
        self.put("010-a")
        self.poll()
        stages = [c.args[3] for c in hline.mark.call_args_list if len(c.args) > 3]
        self.assertLess(stages.index("再分解"), stages.index("実装 2-1"))
        tries = self.item()["tries"]
        self.assertEqual([(t["run"], t["attempt"]) for t in tries], [(r, a) for r in (0, 1) for a in range(1, N + 1)])
        self.assertTrue((self.out / self.item()["tid"] / f"gate-1-{N}.log").is_file())

    def test_a_pass_in_the_second_run_is_integrated(self):
        self.verdicts = [False] * N + [True]
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        self.assertEqual((self.item()["status"], self.integrated, self.runs()), ("done", ["010-a"], [0, 1]))

    def test_a_first_pass_does_not_decompose_again(self):
        self.verdicts = [True]
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        self.assertEqual(([c["tag"] for c in self.dec_calls], self.runs()), (["decomposer"], [0]))

    def test_with_respecs_zero_the_what_is_given_up_after_the_first_run(self):
        self.cfg["respecs"] = 0
        self.put("010-a")
        self.assertEqual(self.poll(), 1)
        self.assertEqual((len(self.dec_calls), self.runs(), self.item()["status"]), (1, [0], "unconverged"))


class Unconverged(Respec):
    def test_failing_the_second_run_too_is_unconverged_and_freezes_the_downstream(self):
        self.put("020-a")
        self.put("030-b", deps=["020-a"])
        self.assertEqual(self.poll(), 1)
        item = self.item("020-a")
        self.assertEqual((item["status"], bool(item["at"])), ("unconverged", True))
        self.assertEqual(item["reason"], f"{2 * N} 回の試行で Gate 1 に通らず、パッチを捨てた")
        self.assertEqual(self.status("030-b"), "frozen")
        self.assertEqual([c["title"] for c in self.dec_calls], ["T-020-a", "T-020-a"])   # 凍結した What は分解しない
        self.assert_no_outside_effect()

    def test_a_respec_that_fails_gate_a_never_calls_the_implementer_again(self):
        def decompose(cfg, wt, what, item, outdir, failure=None, tag="decomposer"):
            if failure:
                return None, {"attempts": [], "reused": False, "reason": "TaskSpec がスキーマに適合しません: 例"}
            return self.decompose(cfg, wt, what, item, outdir)

        self.patch(hline, "decompose", side_effect=decompose)
        self.put("010-a")
        self.put("020-b", deps=["010-a"])
        self.assertEqual(self.poll(), 1)
        item = self.item()
        self.assertEqual((item["status"], item["reason"]), ("unconverged", item["respec"]["reason"]))
        self.assertEqual((hline.implement.call_count, self.runs()), (N, [0]))   # 作り直しの実装は起きない
        self.assertEqual(self.status("020-b"), "frozen")
        self.assert_no_outside_effect()


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
