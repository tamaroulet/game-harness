"""走りを箱に通す口の検査（v3 §8 の E4）：実装役・門・積む前の全件・進捗の記録・採点が、箱の印があるときは箱で走り、箱の外で実装役のコードを走らせない。

    python -m unittest tests.test_box_run -v

Docker は呼ばない。box の関数を差し替える。本物の箱の確かめは python -m harness.box check。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import bench_chain  # noqa: E402
import bench_pair  # noqa: E402
import box  # noqa: E402
import box_run  # noqa: E402
import box_tree  # noqa: E402
import chain_score  # noqa: E402
import gate_order  # noqa: E402
import hline_abort  # noqa: E402
import hline_agy  # noqa: E402
import hline_gate  # noqa: E402
import hline_git  # noqa: E402
import hline_suite  # noqa: E402
import hline_task  # noqa: E402
import exitcode  # noqa: E402
import impacted  # noqa: E402
import progress  # noqa: E402
from hline_base import Infra, Quota  # noqa: E402

MODEL = "gemini-3.8-flash-medium"
AGENT = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "--dangerously-skip-permissions", "model_flag": "--model", "model": MODEL,
         "model_name": MODEL, "output_format_args": ["--output-format", "stream-json"], "extra_flags": ["--sandbox"]}
CFG = {"implementer": AGENT, "ttl_seconds": {"implementer": 600, "gate": 300, "git": 30}, "box": {"package": "pkg"}}
RECEIVED = {"written": [], "removed": [], "dropped": []}


def stream(*tools, model=MODEL, result=True):
    """agy の stream-json の出力。tools は実行した道具の (名, 引数)。"""
    rows = [{"event": "init", "conversation_id": "c1", "init": {"cwd": "/work", "model": model}},
            {"event": "step_update", "step_update": {"step_index": 0, "step_type": "agent_response", "state": "DONE", "conversation_id": "c1",
                                                     "usage": {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 0, "cache_read_tokens": 5}}}]
    for i, (name, params) in enumerate(tools, 1):
        rows.append({"event": "step_update", "step_update": {"step_index": i, "step_type": "tool", "state": "DONE", "tool_name": name,
                                                             "tool_info": {"name": name, "parameters": params}}})
    if result:
        rows.append({"event": "result", "result": {"status": "SUCCESS", "conversation_id": "c1", "response": "済み", "usage": {"input_tokens": 100}}})
    return "\n".join(map(json.dumps, rows)) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "implementer-0-1.log"
        self.host = SimpleNamespace(proc=mock.Mock(side_effect=AssertionError("箱の走りで箱の外の命令を呼んだ")))
        self.host.proc.run = mock.Mock(side_effect=AssertionError("箱の走りで箱の外の命令を呼んだ"))


class Implementer(Base):
    def implement(self, code=0, out=None, err="", received=RECEIVED, cfg=CFG):
        self.agent = mock.Mock(return_value=(code, stream() if out is None else out, err, received))
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "require"), mock.patch.object(box, "ensure_net"), \
                mock.patch.object(box, "agent", self.agent):
            return hline_agy.implement(self.host, cfg, self.tmp, "頼みの本文", self.log)

    def test_runs_in_the_box_without_sandbox_and_without_the_no_command_note(self):
        got = self.implement()
        self.assertEqual(got[0], 0)
        self.assertEqual(got[1], [MODEL])
        _, argv, wt, ttl, stdin, allowed = self.agent.call_args.args
        self.assertEqual(argv[0], "agy")
        self.assertNotIn("--sandbox", argv)
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertEqual((wt, ttl), (self.tmp, 600))
        self.assertIn("頼みの本文", stdin)
        self.assertNotIn(hline_agy.NOTE.strip(), stdin)
        self.assertTrue(allowed("pkg/a.py"))
        self.assertFalse(allowed("scratch.py"))

    def test_running_a_command_does_not_stop_the_try_and_is_counted(self):
        self.implement(out=stream(("run_command", {"CommandLine": "python -m unittest"})))
        doc = json.loads(self.log.read_text(encoding="utf-8").split(hline_agy.LOG_SPLIT)[0])
        self.assertEqual((doc["commands"], doc["commands_run"]), ([], 1))
        self.assertIsNone(hline_abort.command_reason(self.log))
        self.assertIsNone(self.host.blocked_commands)

    def test_without_the_box_a_command_still_stops_the_try(self):
        self.host.proc.run = mock.Mock(return_value=(0, stream(("run_command", {"CommandLine": "python x"})), ""))
        self.host.proc.resolve_cli = lambda name: [name]
        plain = {k: v for k, v in CFG.items() if k != "box"}
        with mock.patch.object(hline_agy, "require_pinned"):
            hline_agy.implement(self.host, plain, self.tmp, "頼み", self.log)
        args, _, _, _ = self.host.proc.run.call_args.args
        self.assertIn("--sandbox", args)
        self.assertIn(hline_agy.NOTE.strip(), self.host.proc.run.call_args.kwargs["input"])
        self.assertIn("REQ-09", hline_abort.command_reason(self.log))

    def test_dropped_files_go_to_the_host(self):
        self.implement(received={**RECEIVED, "dropped": [["scratch.py", "外"]]})
        self.assertEqual(self.host.box_dropped, [["scratch.py", "外"]])

    def test_print_timeout_exits_0_but_is_a_timeout_and_keeps_the_usage(self):
        err = ('error: print timeout\nAGY_ERROR: {"short_error":"print timeout after 3s","status":"DEADLINE_EXCEEDED","retryable":false,"reason":"print_timeout"}\n')
        got = self.implement(code=0, err=err)
        self.assertEqual(got[0], hline_agy.TIMEOUT_EXIT)
        self.assertEqual(got[1], [MODEL])
        self.assertEqual(got[2]["input_tokens"], 100)
        self.assertEqual(got[4], 1)

    def test_the_outer_timeout_keeps_the_usage_too(self):
        got = self.implement(code=hline_agy.TIMEOUT_EXIT, out=stream(result=False))
        self.assertEqual(got[0], hline_agy.TIMEOUT_EXIT)
        self.assertEqual(got[2]["input_tokens"], 100)

    def test_other_api_errors_are_infra_not_the_implementers_failure(self):
        err = 'AGY_ERROR: {"short_error":"backend unavailable","status":"UNAVAILABLE","retryable":true}\n'
        for code in (0, 3):
            with self.assertRaisesRegex(Infra, "backend unavailable"):
                self.implement(code=code, err=err)

    def test_quota_is_quota_even_with_agy_error(self):
        err = 'AGY_ERROR: {"short_error":"too many","status":"RESOURCE_EXHAUSTED","retryable":false}\n'
        with self.assertRaises(Quota):
            self.implement(code=3, err=err)
        with self.assertRaises(Quota):
            self.implement(code=1, err="Your AI credits balance is too low to continue.\n")

    def test_a_broken_box_is_infra(self):
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "require", side_effect=box.BoxError("像が無い")), \
                self.assertRaisesRegex(Infra, "像が無い"):
            hline_agy.implement(self.host, CFG, self.tmp, "頼み", self.log)

    def test_a_wrong_model_report_is_infra(self):
        with self.assertRaises(Infra):
            self.implement(out=stream(model="gemini-other"))

    def test_agy_error_reads_only_its_own_line(self):
        self.assertIsNone(hline_agy.agy_error("warn\nother\n"))
        self.assertEqual(hline_agy.agy_error('x\nAGY_ERROR: {"reason":"print_timeout"}\n')["reason"], "print_timeout")
        self.assertIn("AGY_ERROR", hline_agy.agy_error("AGY_ERROR: not json")["short_error"])


class Gate(Base):
    def tree(self):
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_x.py").write_text("", encoding="utf-8")
        return self.tmp

    def test_focused_gate_runs_in_the_box(self):
        cfg = {"gate_command": ["python", "-m", "harness.fastsuite", "discover", "-s", "tests"], "ttl_seconds": {"gate": 300}, "gate_tail_chars": 4000, "box": {"package": "pkg"}}
        run = mock.Mock(return_value=(0, "OK", ""))
        with mock.patch.object(box_run, "gate", run), mock.patch.object(gate_order.proc, "run", side_effect=AssertionError("箱の外で走った")):
            ok, _ = gate_order.focused(cfg, self.tree(), ["tests/test_x.py"], None)
        self.assertTrue(ok)
        cmd, wt, ttl = run.call_args.args
        self.assertEqual(cmd[0], "python")
        self.assertIn("tests.test_x", cmd)
        self.assertEqual((wt, ttl), (self.tmp, 300))

    def test_focused_gate_without_the_box_stays_outside(self):
        cfg = {"gate_command": ["python", "-m", "harness.fastsuite", "discover", "-s", "tests"], "ttl_seconds": {"gate": 300}, "gate_tail_chars": 4000}
        with mock.patch.object(box_run, "gate", side_effect=AssertionError("箱で走った")), mock.patch.object(gate_order.proc, "run", return_value=(0, "OK", "")) as run:
            gate_order.focused(cfg, self.tree(), ["tests/test_x.py"], None)
        self.assertTrue(run.called)

    def test_canary_runs_in_the_box(self):
        cfg = {"gate_command": ["python", "-m", "harness.fastsuite", "discover", "-s", "tests"], "canary_modules": ["tests.test_x"], "ttl_seconds": {"gate": 300},
               "gate_tail_chars": 4000, "box": {"package": "pkg"}}
        self.host.gate_order = SimpleNamespace(report=lambda *a: "報告")
        with mock.patch.object(box_run, "gate", return_value=(1, "落ちた", "")) as run:
            ok, text = hline_gate.canary(self.host, cfg, self.tmp)
        self.assertFalse(ok)
        self.assertEqual(text, "報告")
        self.assertEqual(run.call_args.args[0][0], "python")

    def test_box_failure_in_the_gate_is_infra(self):
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "tests", side_effect=box.BoxError("動かない")), self.assertRaises(Infra):
            box_run.gate(["python"], self.tmp, 10)

    def test_suite_uses_fastsuite_inside_the_box(self):
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "tests", return_value=(1, "報告", "stderr")) as tests:
            code, body = box_run.suite(("tests.test_x",), self.tmp, 60)
        self.assertEqual(tests.call_args.args[1], ["python", "-m", "harness.fastsuite", "tests.test_x"])
        self.assertEqual((code, body), (1, "報告\nstderr"))

    def test_hline_suite_runs_in_the_box_only_when_told(self):
        with mock.patch.object(hline_suite, "select", return_value=("tests.test_x",)), mock.patch.object(hline_suite.fastsuite, "run", side_effect=AssertionError("箱の外で走った")), \
                mock.patch.object(box_run, "suite", return_value=(1, "落ちた")) as suite:
            ok, text, rec = hline_suite.run(self.tmp, ["pkg/a.py"], 60, boxed=True)
        self.assertFalse(ok)
        self.assertIn("落ちた", text)
        self.assertEqual(rec["modules"], ["tests.test_x"])
        self.assertEqual(suite.call_args.args, (("tests.test_x",), self.tmp, 60))

    def test_the_prompts_no_longer_forbid_running_tests(self):
        self.assertNotIn("テストは走らせない", impacted.prompt_note())


class Record(Base):
    CFG = {**CFG, "base": "abc"}

    def test_verification_runs_in_the_box_and_the_ledger_is_written_here(self):
        seen = {}

        def complete(task, **kw):
            seen.update(task=task, **kw)
            rc, log = kw["verify"]("python -m unittest tests.test_pkg_s1")
            kw["out"](f"検証 {rc} {log}")
            return 0
        with mock.patch.object(progress, "complete", complete), mock.patch.object(box_run, "gate", return_value=(0, "OK", "")) as gate, \
                mock.patch.object(hline_git.proc, "run", side_effect=AssertionError("箱の外で走った")):
            code, text = hline_git.record(self.CFG, self.tmp, "P-01")
        self.assertEqual(code, 0)
        self.assertIn("検証 0 OK", text)
        self.assertEqual(gate.call_args.args[0], ["bash", "-c", "python -m unittest tests.test_pkg_s1"])
        self.assertEqual((seen["repo_root"], seen["ref"], seen["require_active"]), (self.tmp, "abc", False))

    def test_a_refused_record_restores_the_ledger(self):
        with mock.patch.object(progress, "complete", return_value=1), mock.patch.object(hline_git.proc, "run", return_value=(0, "", "")) as run:
            code, _ = hline_git.record(self.CFG, self.tmp, "P-01")
        self.assertEqual(code, 1)
        self.assertEqual(run.call_args.args[0][:2], ["git", "checkout"])

    def test_a_broken_ledger_is_an_abort_like_the_command(self):
        with mock.patch.object(progress, "complete", side_effect=progress.ProgressError("壊れた")), mock.patch.object(hline_git.proc, "run", return_value=(0, "", "")):
            code, text = hline_git.record(self.CFG, self.tmp, "P-01")
        self.assertEqual(code, exitcode.ABORT)
        self.assertIn("壊れた", text)

    def test_the_verifier_hands_the_command_to_the_shell_in_the_box(self):
        command = "python -m unittest t && python -m unittest u"
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "tests", return_value=(3, "出力", "エラー")) as tests:
            got = box_run.verifier(self.tmp, 300)(command)
        self.assertEqual(got, (3, "出力エラー"))
        self.assertEqual(tests.call_args.args[1:], (["bash", "-c", command], self.tmp, 300))

    def test_complete_uses_the_given_verifier_instead_of_a_shell(self):
        state = {"tasks": [{"id": "T1", "status": "pending", "verification": {"command": "python -m unittest t", "expected_exit_code": 0}}], "active_task_id": "T1"}
        verify = mock.Mock(return_value=(0, "OK"))
        with mock.patch.object(progress, "load", return_value=state), mock.patch.object(progress, "validate", return_value=[]), mock.patch.object(progress, "main_version", return_value=state), \
                mock.patch.object(progress, "save"), mock.patch.object(progress, "is_held", return_value=False), mock.patch.object(progress, "unmet_dependencies", return_value=[]), \
                mock.patch.object(progress, "advance_active"), mock.patch.object(progress.subprocess, "run", side_effect=AssertionError("箱の外で走った")):
            self.assertEqual(progress.complete("T1", repo_root=self.tmp, out=lambda s: None, verify=verify), 0)
        verify.assert_called_once_with("python -m unittest t")


class Tries(Base):
    def run_task(self, dropped, gate_ok):
        outdir = self.tmp / "out"
        outdir.mkdir()
        host = SimpleNamespace(new_worktree=None, changed_paths=lambda wt, cfg: ["pkg/a.py"], gate=lambda *a, **k: (gate_ok, "門の出力"))

        def implement(cfg, wt, what, feedback, log):
            host.box_dropped = dropped
            Path(log).write_text("{}", encoding="utf-8")
            return (0, [MODEL], None, None, 1)
        host.implement = implement
        cfg = {"max_attempts": 1, "ttl_seconds": {"implementer": 10, "gate": 10, "git": 10}, "box": {"package": "pkg"}}
        with mock.patch.object(hline_task.hline_resume, "keep"), mock.patch.object(hline_task.hline_resume, "previous", return_value=None):
            got = hline_task.run_task(host, cfg, "t", "本文", outdir, first=(self.tmp, "b"))
        return got, (outdir / "gate-0-1.log").read_text(encoding="utf-8")

    def test_a_failed_gate_tells_the_implementer_what_was_not_received(self):
        got, gate_log = self.run_task([["tests/helpers.py", "許されたパスの外の新しいファイル"]], False)
        self.assertIn("tests/helpers.py", gate_log)
        self.assertIn("受け取らなかった", gate_log)
        self.assertEqual(got[2][0]["box_dropped"], [["tests/helpers.py", "許されたパスの外の新しいファイル"]])

    def test_a_passed_gate_only_records_it(self):
        got, gate_log = self.run_task([["scratch.py", "許されたパスの外の新しいファイル"]], True)
        self.assertNotIn("受け取らなかった", gate_log)
        self.assertEqual(got[2][0]["box_dropped"], [["scratch.py", "許されたパスの外の新しいファイル"]])

    def test_nothing_dropped_leaves_no_trace(self):
        got, gate_log = self.run_task(None, False)
        self.assertNotIn("受け取らなかった", gate_log)
        self.assertNotIn("box_dropped", got[2][0])


class Grade(Base):
    def test_grade_runs_in_the_box_and_reads_the_same_line(self):
        line = chain_score.MARK + json.dumps({"test_a.A.test_x": "ok", "test_a.A.test_gone": "ok"})
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "grade", return_value=(0, line + "\n", "")) as run, \
                mock.patch.object(chain_score.proc, "run", side_effect=AssertionError("箱の外で走った")):
            got = chain_score.grade(self.tmp, [self.tmp / "test_a.py"], {"test_gone"}, boxed=True)
        self.assertEqual(got, {"test_a.A.test_x": "ok"})
        self.assertEqual(run.call_args.args[1], chain_score.RUNNER)

    def test_box_failure_stops_the_grade(self):
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "grade", side_effect=box.BoxError("動かない")), self.assertRaises(chain_score.BenchError):
            chain_score.grade(self.tmp, [], set(), boxed=True)

    def test_box_grade_passes_tree_and_tests_under_separate_roots(self):
        (self.tmp / "tree" / "pkg").mkdir(parents=True)
        (self.tmp / "tree" / "pkg" / "a.py").write_bytes(b"A")
        hidden = self.tmp / "test_h.py"
        hidden.write_bytes(b"H")
        with mock.patch.object(box, "tests", return_value=(0, "", "")) as run:
            box.grade({}, "RUNNER", self.tmp / "tree", [hidden], 60)
        cfg, argv, tree, ttl, extra = run.call_args.args
        self.assertIsNone(tree)
        self.assertEqual(ttl, 60)
        self.assertEqual(argv, ["python", "-I", f"{box.WORK}/runner.py", f"{box.WORK}/tree", f"{box.WORK}/tests"])
        self.assertEqual(extra, {"runner.py": b"RUNNER", "tree/pkg/a.py": b"A", "tests/test_h.py": b"H"})

    def test_pair_start_uses_the_box_mark(self):
        with mock.patch.object(chain_score, "tip_tree", return_value=self.tmp) as tip, mock.patch.object(chain_score, "grade", return_value={}) as grade, \
                mock.patch.object(chain_score, "hidden_files", return_value=[]), mock.patch.object(chain_score, "replaced", return_value=set()):
            bench_pair.start_prev({"base": "B", "box": True}, {"k": 2, "run_id": "r", "start": "s"}, self.tmp, self.tmp)
        self.assertTrue(tip.called)
        self.assertTrue(grade.call_args.args[-1])


class Stage(Base):
    def stage_obj(self, boxed="pkg"):
        ns = SimpleNamespace(task="B", run_id="r", repo=str(self.tmp), base="x", out=str(self.tmp), sessions=str(self.tmp), plan=None, implementer=None)
        return bench_chain.ChainStage(ns, "start", self.tmp / "spec.md", boxed)

    def test_box_mark_reaches_the_config_only_when_boxed(self):
        self.assertEqual(self.stage_obj().box_mark({"a": 1}), {"a": 1, "box": {"package": "pkg"}})
        self.assertEqual(self.stage_obj(None).box_mark({"a": 1}), {"a": 1})

    def test_guard_uses_the_box_check_and_invalidates_on_problems(self):
        f = self.stage_obj()
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "require", return_value="sha256:i"), mock.patch.object(box, "check", return_value=["直の接続が届く"]), \
                mock.patch.object(bench_chain.bench_run.Factory, "guard", side_effect=AssertionError("bench_guard を呼んだ")):
            self.assertFalse(f.guard({}))
        self.assertEqual(f.run["invalid"], "REQ-10")
        self.assertIn("直の接続", f.run["reason"])

    def test_guard_records_the_image_when_good(self):
        f = self.stage_obj()
        with mock.patch.object(box, "settings", return_value={}), mock.patch.object(box, "require", return_value="sha256:i"), mock.patch.object(box, "check", return_value=[]), \
                mock.patch.object(f, "effective") as effective:
            self.assertTrue(f.guard({"x": 1}))
        self.assertEqual(f.run["box_image"], "sha256:i")
        effective.assert_called_once_with({"x": 1})

    def test_guard_without_the_box_is_the_old_one(self):
        f = self.stage_obj(None)
        with mock.patch.object(bench_chain.bench_run.Factory, "guard", return_value=True) as old:
            self.assertTrue(f.guard({}))
        self.assertTrue(old.called)

    def test_version_record_is_the_box_agy(self):
        with mock.patch.object(box, "agy_version", return_value="9.9.9"):
            self.assertEqual(self.stage_obj().cli_version("implementer"), "9.9.9")

    def test_no_sessions_is_an_empty_directory_of_its_own(self):
        path = Path(bench_chain.no_sessions(self.tmp))
        self.assertTrue(path.is_dir())
        self.assertEqual(list(path.iterdir()), [])
        self.assertNotIn(".claude", path.parts)

    def test_stage_writes_the_box_mark(self):
        chains = self.tmp / "chains"
        tree = chains / "B" / "s0" / "tree"
        (tree / "pkg").mkdir(parents=True)
        (tree / "pkg" / "a.py").write_text("x", encoding="utf-8")
        (tree / "tests").mkdir()
        (tree / "tests" / "test_pkg_base.py").write_text("x", encoding="utf-8")
        (chains / "B" / "s1").mkdir()
        (chains / "B" / "s1" / "spec.md").write_text("# 題：x\n", encoding="utf-8")
        for flag, want in ((["--box"], True), ([], None)):
            staged = self.tmp / f"staged{bool(flag)}"
            with mock.patch("builtins.print") as printed, mock.patch.object(bench_chain.bench_guard, "canaries", return_value=[]):
                code = bench_chain.main(["stage", "--tag", "t", "--set", "dev", "--bases", "B", "--chains", str(chains), "--staged", str(staged), *flag])
            self.assertEqual(code, 0)
            doc = json.loads((Path(printed.call_args.args[0]) / "chain.json").read_text(encoding="utf-8"))
            self.assertEqual(doc.get("box"), want)


class Note(unittest.TestCase):
    def test_dropped_note_names_the_files_and_the_reason(self):
        text = box_tree.dropped_note([["tests/helpers.py", "外"], ["x", "リンク"]])
        self.assertIn("- tests/helpers.py：外", text)
        self.assertIn("- x：リンク", text)


if __name__ == "__main__":
    unittest.main()
