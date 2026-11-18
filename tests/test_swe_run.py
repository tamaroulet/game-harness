"""§6-3 の最小の実行口（harness/swe_run.py）のうち、コンテナと agy が無くても確認できる所。

    python -m unittest tests.test_swe_run -v
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import swe_run  # noqa: E402


def step(i, name, state, params, output=None, secs=None):
    su = {"step_index": i, "step_type": "tool", "tool_name": name, "state": state,
          "tool_info": {"name": name, "parameters": params}}
    if output is not None:
        su["tool_info"]["output"] = output
    if secs is not None:
        su["duration_seconds"] = secs
    return json.dumps({"event": "step_update", "step_update": su})


PYTEST_OUT = ("Command exited with code 1.\n\nCommand output:\n....F\n=========================== short test summary info "
              "============================\nFAILED tests/func/test_checkout.py::TestRemove::test - ImportError: cannot import\n"
              "ERROR tests/func/test_checkout.py::test_other\n1 failed, 4 passed, 1 error in 3.21s\n")
STREAM = "\n".join([
    json.dumps({"event": "init", "init": {"model": "gemini-4-hydrogen-medium"}}),
    step(1, "run_command", "ACTIVE", {"CommandLine": "cd /testbed && python -m pytest -k 'test_a or test_b' -x "
                                                     "/testbed/tests/func/test_checkout.py::TestRemove"}),
    step(1, "run_command", "DONE", {"CommandLine": "cd /testbed && python -m pytest -k 'test_a or test_b' -x "
                                                   "/testbed/tests/func/test_checkout.py::TestRemove"}, PYTEST_OUT, 3.4),
    step(2, "run_command", "DONE", {"CommandLine": "pip install pytest-xdist"},
         "Command exited with code 1.\n\nCommand output:\nProxyError 403\n", 1.0),
    step(3, "edit_file", "DONE", {"Path": "/testbed/dvc/repo/graph.py"}, "ok", 0.1),
    step(4, "write_file", "DONE", {"Path": "/testbed/tests/unit/test_new.py"}, "ok", 0.1),
    step(5, "read_file", "DONE", {"Path": "/testbed/dvc/repo/graph.py", "StartLine": 1, "EndLine": 9}, "x", 0.1),
    step(6, "edit_file", "ERROR", {"Path": "/testbed/tests/unit/test_old.py"}, None, 0.1),
    "not json",
])


class Prompt(unittest.TestCase):
    def test_h0_is_the_frame_and_the_release_notes(self):
        self.assertEqual(swe_run.prompt_for("H0", "  notes\n"), swe_run.FRAME + "\n\nnotes\n")

    def test_h_cots_adds_only_the_fixed_paragraph_at_the_end(self):
        h0, cots = swe_run.prompt_for("H0", "notes"), swe_run.prompt_for("H_cots", "notes")
        self.assertEqual(cots, h0 + "\n" + swe_run.COTS + "\n")
        with self.assertRaises(ValueError):
            swe_run.prompt_for("H_stack", "notes")

    def test_the_fixed_texts_are_pinned_by_sha256(self):
        # 文面を変えたら、実験と計画 §6-3 の SHA-256 も書き直す（走らせる前に固定する）
        self.assertEqual(swe_run.sha256(swe_run.FRAME), "e6d62ea4a09b74e7f1b65fcbbe8b1c9c04f9bf85b8c47128d57d4ab16974f2ec")
        self.assertEqual(swe_run.sha256(swe_run.COTS), "276d11d965a01b33552ebdd98aad72f6c6e670dcafca2768e41ea79a9f728c5a")


class Trajectory(unittest.TestCase):
    def test_commands_pytest_runs_and_edits(self):
        t = swe_run.trajectory(STREAM)
        self.assertEqual(len(t["commands"]), 2)                      # ACTIVE の段の event は数えない
        run, pip = t["commands"]
        self.assertTrue(run["pytest"])
        self.assertEqual(run["targets"], ["tests/func/test_checkout.py"])   # -k の式は対象にしない
        self.assertEqual(run["failed"], ["tests/func/test_checkout.py::TestRemove::test", "tests/func/test_checkout.py::test_other"])
        self.assertEqual(run["summary"], "1 failed, 4 passed, 1 error in 3.21s")
        self.assertEqual((run["exit"], run["seconds"]), (1, 3.4))
        self.assertNotIn("pytest", pip)                              # pip install pytest-xdist は pytest の走りではない
        self.assertEqual([(e["path"], e["test"], e["state"]) for e in t["edits"]],
                         [("dvc/repo/graph.py", False, "DONE"), ("tests/unit/test_new.py", True, "DONE"),
                          ("tests/unit/test_old.py", True, "ERROR")])
        s = t["summary"]
        self.assertEqual((s["commands"], s["pytest_runs"], s["pytest_runs_with_failures"], s["reads"]), (2, 1, 1, 1))
        self.assertEqual((s["edited_files"], s["edited_test_files"], s["failed_edits"]), (2, ["tests/unit/test_new.py"], 1))
        self.assertEqual((s["pytest_seconds"], s["command_seconds"]), (3.4, 4.4))

    def test_only_pytest_at_the_head_of_a_command_is_a_run(self):
        runs = ["pytest -q tests/x.py", "cd /testbed && python -m pytest tests/x.py", "timeout 600 python3 -m pytest tests/x.py",
                "PYTHONPATH=. pytest tests", "(cd tests && pytest unit)"]
        others = ['python -c "\nimport numpy as np\nimport pytest\nprint(1)"', "which pytest", "pip install pytest-xdist",
                  "cat pytest.ini"]
        self.assertEqual([c for c in runs + others if swe_run._PYTEST_RE.search(c)], runs)

    def test_failed_items_skip_the_plugin_prefix_but_not_captured_log_lines(self):
        # 本物の記録の形：mlflow のプラグインの「| MEM … | DISK … GB」と、取り込んだログの行
        out = ("ERROR    root:conf.py:12 cannot read /testbed/dvc/x.py now\r\n"
               "=========================== short test summary info ============================\r\n"
               "FAILED | MEM 1.2/15.5 GB | DISK 85.0/1006.9 GB tests/a.py::test_b[x-1] - assert 1 == 2\r\n"
               "ERROR tests/c.py - ImportError: no\r\n"
               "ERROR: file or directory not found: tests/d.py\r\n")
        self.assertEqual(swe_run._NODE_RE.findall(out), ["tests/a.py::test_b[x-1]", "tests/c.py"])

    def test_a_run_that_ends_before_any_item_is_a_file_level_failure(self):
        s = step(1, "run_command", "DONE", {"CommandLine": "pytest tests/a.py tests/unit"},
                 "Command exited with code 4.\n\nCommand output:\nImportError while loading conftest '/testbed/tests/conftest.py'.\n",
                 1.0)
        run = swe_run.trajectory(s)["commands"][0]
        self.assertEqual((run["failed"], run.get("file_level")), (["tests/a.py"], True))

    def test_targets_keep_files_and_test_dirs(self):
        self.assertEqual(swe_run.test_targets("pytest -q tests/unit dask/array/tests/test_core.py -n 4 -W ignore"),
                         ["tests/unit", "dask/array/tests/test_core.py"])

    def test_t0_files_come_from_file_targets_and_from_failed_items(self):
        # ディレクトリを指した走りと引数の無い走りは、落ちた項目のファイルを拾う（収集の誤りは :: の無いファイルの名）
        cmds = [{"pytest": True, "targets": ["tests/unit"], "failed": ["tests/unit/test_a.py::T::test_x[1-2]",
                                                                       "tests/unit/test_b.py"]},
                {"pytest": True, "targets": [], "failed": ["/testbed/dvc/tests/test_c.py::test_y"]},
                {"pytest": True, "targets": ["tests/func/test_d.py"], "failed": []},
                {"command": "ls", "seconds": 0.1}]
        self.assertEqual(swe_run.t0_files(cmds), ["dvc/tests/test_c.py", "tests/func/test_d.py", "tests/unit/test_a.py",
                                                  "tests/unit/test_b.py"])
        self.assertEqual(swe_run.t0_files([{"pytest": True, "targets": ["tests"], "failed": []}]), [])

    def test_t0_overlap_counts_runs_whose_failures_all_fail_at_t0(self):
        cmds = [{"pytest": True, "failed": ["a.py::x"], "seconds": 5.0},
                {"pytest": True, "failed": ["a.py::x", "b.py::y"], "seconds": 7.0},
                {"pytest": True, "failed": [], "seconds": 2.0}]
        self.assertEqual(swe_run.t0_overlap(cmds, ["a.py::x"]),
                         {"runs_with_failures": 2, "runs_only_t0_failures": 1, "seconds_only_t0_failures": 5.0})


class Truncated(unittest.TestCase):
    """agy は長い出力の中ほどを切り、全文を brain の下に置く（X-01・X-02 の本物の記録と同じ形）。"""
    PATH = swe_run.BRAIN + "/abc/.system_generated/steps/7/output.txt"
    FULL = ("Command exited with code 1.\n\nCommand output:\n" + "".join(f"FAILED tests/t.py::test_{i} - E\n" for i in range(50))
            + "50 failed in 9.0s\n")

    def stream(self):
        preview = (f"The tool's output was truncated due to being too long. You can find the full output in a file at "
                   f"file://{self.PATH}.\n\nHere is a preview with the middle of the output truncated:\n{self.FULL[:120]}"
                   f"\n<truncated 1234 of 1414 bytes from the middle of the output>\n{self.FULL[-60:]}")
        return step(7, "run_command", "DONE", {"CommandLine": "python -m pytest tests/t.py"}, preview, 9.5)

    def test_the_failed_items_come_from_the_saved_full_output(self):
        t = swe_run.trajectory(self.stream(), {self.PATH: self.FULL}.get)
        run = t["commands"][0]
        self.assertEqual((run["truncated"], run["exit"], len(run["failed"]), run["summary"]), ("full", 1, 50, "50 failed in 9.0s"))
        self.assertEqual((t["summary"]["truncated_outputs"], t["summary"]["truncated_without_full"]), (1, 0))

    def test_without_the_full_output_the_cut_lines_are_dropped(self):
        t = swe_run.trajectory(self.stream())
        run = t["commands"][0]
        self.assertEqual(run["truncated"], "preview")
        self.assertEqual(run["failed"], ["tests/t.py::test_0", "tests/t.py::test_1", "tests/t.py::test_49"])
        self.assertEqual(t["summary"]["truncated_without_full"], 1)

    def test_saved_output_reads_only_inside_the_brain_copy(self):
        with tempfile.TemporaryDirectory() as d:
            steps = Path(d) / "brain" / "abc" / ".system_generated" / "steps" / "7"
            steps.mkdir(parents=True)
            (steps / "output.txt").write_text("full", encoding="utf-8")
            (Path(d) / "secret.txt").write_text("x", encoding="utf-8")
            read = swe_run.saved_output(Path(d) / "brain")
            self.assertEqual(read(self.PATH), "full")
            self.assertIsNone(read(swe_run.BRAIN + "/abc/.system_generated/steps/8/output.txt"))
            self.assertIsNone(read(swe_run.BRAIN + "/../secret.txt"))
            self.assertIsNone(read("/etc/passwd"))


class Patch(unittest.TestCase):
    def test_test_files_added_modified_deleted(self):
        s = swe_run.patch_summary("A\ttests/test_new.py\nM\tpkg/core.py\nD\ttests/test_old.py\n"
                                  "R100\ttests/a.py\ttests/b.py\nM\tdocs/changes.rst\n")
        self.assertEqual(s["files"], 5)
        self.assertEqual((s["added_tests"], s["deleted_tests"], s["modified_tests"]),
                         (["tests/test_new.py"], ["tests/test_old.py"], ["tests/b.py"]))
        self.assertEqual(s["code_files"], ["pkg/core.py", "docs/changes.rst"])

    def test_merge_puts_the_test_patch_first_like_the_official_evaluation(self):
        self.assertEqual(swe_run.merge_patches("code\r\n", "test"), "test\ncode\n")
        self.assertEqual(swe_run.merge_patches("", "test\n"), "test\n")
        self.assertEqual(swe_run.merge_patches("", ""), "")


class Guards(unittest.TestCase):
    def test_quota_floors(self):
        def rec(w, h):
            return {"buckets": {"gemini-weekly": {"remaining_fraction": w}, "gemini-5h": {"remaining_fraction": h}}}
        self.assertEqual(swe_run.quota_ok(rec(0.9, 0.5)), (True, ""))
        self.assertFalse(swe_run.quota_ok(rec(0.34, 0.9))[0])
        self.assertFalse(swe_run.quota_ok(rec(0.9, 0.14))[0])
        self.assertFalse(swe_run.quota_ok({})[0])

    def test_network_must_reach_only_oauth2_and_only_through_the_proxy(self):
        good = {"oauth2.googleapis.com": {"proxy": "open(404)", "direct": "blocked(gaierror)"},
                "pypi.org": {"proxy": "blocked(URLError)", "direct": "blocked(gaierror)"},
                "github.com": {"proxy": "blocked(URLError)", "direct": "blocked(gaierror)"}}
        self.assertTrue(swe_run.net_ok(good))
        leaky = json.loads(json.dumps(good))
        leaky["pypi.org"]["direct"] = "open"
        self.assertFalse(swe_run.net_ok(leaky))
        self.assertFalse(swe_run.net_ok({}))

    def test_the_agent_runs_after_conda_the_setup_env_and_the_proxy_env(self):
        imp = swe_run.fb_agy.agent_settings(swe_run.fb_agy.load_json(swe_run.fb_agy.AGENT_CONFIG), "gemini-4-hydrogen-medium")
        cmd = swe_run.run_command(imp)
        self.assertTrue(cmd.startswith("set -o pipefail; "))
        self.assertLess(cmd.index("conda activate testbed"), cmd.index(swe_run.swe_s0.ENV_FILE))
        self.assertLess(cmd.index(swe_run.swe_s0.ENV_FILE), cmd.index(swe_run.NET_ENV))
        self.assertIn("--model gemini-4-hydrogen-medium", cmd)
        self.assertIn(f"--print-timeout {swe_run.TTL - 20}s", cmd)
        self.assertIn(f"< {swe_run.fb_agy.PROMPT_PATH} | tee {swe_run.fb_agy.STREAM_PATH}", cmd)
        self.assertNotIn("FAKE_PYTEST", cmd)
        self.assertIn("FAKE_PYTEST=tests/x.py ", swe_run.run_command(imp, "tests/x.py"))

    def test_the_fake_agy_compiles(self):
        compile(swe_run.FAKE_AGY_SWE, "fake_agy_swe", "exec")
        compile(swe_run.NET_CHECK, "net_check", "exec")


@unittest.skipUnless(importlib.util.find_spec("swebench"), "SWE-EVO の swebench が要る（WSL の venv）")
class Official(unittest.TestCase):
    def test_version_follows_the_end_version_like_the_official_loader(self):
        row = {"instance_id": "dask__dask_2024.3.1_2024.4.0", "repo": "dask/dask", "version": "2024.3",
               "end_version": "2024.4.0"}
        d = swe_run.official_row(row)
        self.assertEqual(d["version"], "2024.4")
        self.assertIn("pytest", swe_run.swe_s0.test_cmd_of(d))


if __name__ == "__main__":
    unittest.main()
