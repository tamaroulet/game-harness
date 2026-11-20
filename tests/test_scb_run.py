"""SlopCodeBench の実行口（harness/scb_run.py）の、docker に依らない部品の試験。

    python -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_stream  # noqa: E402
import scb_run  # noqa: E402

FIXTURE_ENV = Path(__file__).resolve().parent / "fixtures" / "scb_env_docker-python3.12-uv.yaml"


def eval_json(passed, total, infra=False, regression_failed=()):
    tests = {"checkpoint_1-Regression": {"passed": [], "failed": list(regression_failed)}}
    return {"pass_counts": passed, "total_counts": total, "infrastructure_failure": infra, "tests": tests,
            "test_collection_hash": "h", "duration": 1.0}


class ConfigTests(unittest.TestCase):
    def test_config_pins_model_and_stream(self):
        cfg = scb_run.load_config()
        self.assertEqual(cfg["model_name"], "gemini-4-hydrogen-medium")
        self.assertIn("stream-json", cfg["output_format_args"])
        self.assertEqual(cfg["install"]["version"], "1.5.3")

    def test_hooks_deny_every_denied_tool(self):
        hooks = json.loads((scb_run.ASSETS / "hooks.json").read_text(encoding="utf-8"))
        script = {h["matcher"]: h["hooks"][0]["command"].rsplit("/", 1)[-1] for h in hooks["scb-deny-tools"]["PreToolUse"]}
        self.assertEqual(set(script), set(scb_run.DENIED_TOOLS))
        self.assertEqual({t for t, s in script.items() if s == "deny_web.sh"}, set(scb_run.WEB_TOOLS))
        self.assertEqual({t for t, s in script.items() if s == "deny_subagent.sh"}, set(scb_run.SUBAGENT_TOOLS))
        for s in scb_run.HOOK_SCRIPTS:
            self.assertIn('"decision": "deny"', (scb_run.ASSETS / s).read_text(encoding="utf-8"))

    def test_filter_is_anchored_and_escaped(self):
        self.assertEqual(scb_run.filter_lines(["pypi.org", "oauth2.googleapis.com"]),
                         "^pypi\\.org$\n^oauth2\\.googleapis\\.com$\n")


class EnvConfigTests(unittest.TestCase):
    def test_os_env_added_and_env_untouched(self):
        text = FIXTURE_ENV.read_text(encoding="utf-8")
        out = scb_run.env_config_with_os_env(text)
        lines = out.splitlines()
        i = lines.index("environment:")
        self.assertEqual(lines[i + 1], "  include_os_env: true")

        def env_block(t):
            return t[t.index("  env:"):t.index("setup:")]
        self.assertEqual(env_block(out), env_block(text))   # environment.env（基本のイメージのハッシュの元）は同じ

    def test_refuses_unexpected_shape(self):
        with self.assertRaises(ValueError):
            scb_run.env_config_with_os_env("type: docker\n")
        once = scb_run.env_config_with_os_env(FIXTURE_ENV.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            scb_run.env_config_with_os_env(once)

    def test_eval_env_is_minimal_and_carries_cutoff(self):
        scb = scb_run.load_config()["scb"]
        env = scb_run.eval_env(scb, "2026-11-20T06:19:36Z")
        self.assertEqual(env["UV_EXCLUDE_NEWER"], "2026-11-20T06:19:36Z")
        self.assertEqual(env["HOME"], "/tmp/agent_home")
        self.assertEqual((env["HUID"], env["HGID"]), ("1000", "1000"))
        self.assertEqual(set(env), {"PATH", "HOME", "SCBENCH_PROBLEMS_PATH", "HUID", "HGID", "UV_EXCLUDE_NEWER"})


class EvalSummaryTests(unittest.TestCase):
    def test_strict_iso_core(self):
        s = scb_run.eval_summary(eval_json({"Core": 3, "Functionality": 5, "Regression": 4, "Error": 2},
                                           {"Core": 3, "Functionality": 5, "Regression": 4, "Error": 2}))
        self.assertEqual((s["strict"], s["iso"], s["core"]), (True, True, True))

    def test_regression_failure_keeps_iso(self):
        s = scb_run.eval_summary(eval_json({"Core": 3, "Functionality": 5, "Regression": 3, "Error": 2},
                                           {"Core": 3, "Functionality": 5, "Regression": 4, "Error": 2},
                                           regression_failed=["t::a"]))
        self.assertEqual((s["strict"], s["iso"], s["core"]), (False, True, True))
        self.assertEqual(s["regression_failed"], ["t::a"])

    def test_infrastructure_failure_is_never_a_pass(self):
        s = scb_run.eval_summary(eval_json({"Core": 0}, {"Core": 0}, infra=True))
        self.assertEqual((s["strict"], s["iso"], s["core"]), (False, False, False))
        self.assertTrue(s["infrastructure_failure"])


def step(i, tool, state, error=None):
    """agy の stream-json の手番（道具のエラーは state: ERROR と tool_info.error。実際の agy の記録の形）。"""
    su = {"step_index": i, "step_type": "tool", "tool_name": tool, "state": state}
    if error:
        su["tool_info"] = {"name": tool, "error": {"type": "TOOL_ERROR", "message": error}}
    return {"event": "step_update", "step_update": su}


class AuditTests(unittest.TestCase):
    def test_denied_tool_steps_read_state_and_hook_error(self):
        text = "\n".join(json.dumps(e) for e in [
            {"event": "init", "init": {"model": "m", "conversation_id": "c"}},
            step(0, "run_command", "DONE"),
            step(1, "search_web", "ACTIVE"),
            step(1, "search_web", "ERROR", "Tool call denied by pre-tool hook: Web access is not available in this environment."),
            step(2, "invoke_subagent", "ERROR", "tool call denied by pre-tool hook: Subagents are not available in this environment."),
            step(3, "read_url_content", "ERROR", "dial tcp: lookup example.com: no such host"),
            step(4, "webview_browser", "DONE"),
            {"event": "result", "result": {"status": "SUCCESS", "conversation_id": "c"}}])
        steps = scb_run.denied_tool_steps(agy_stream.parse(text)["steps"], scb_run.step_errors(text))
        got = {s["index"]: (s["tool"], s["state"], s["hook_denied"], s["ran"]) for s in steps}
        self.assertEqual(got, {1: ("search_web", "ERROR", True, False),        # フックが止めた：無効にしない
                               2: ("invoke_subagent", "ERROR", True, False),
                               3: ("read_url_content", "ERROR", False, False),  # ほかのエラーでも走っていない
                               4: ("webview_browser", "DONE", False, True)})    # 走った：無効

    def test_valid_only_when_no_denied_tool_ran(self):
        ok = {"denied_tools": [{"ran": False}], "reserved_path": False, "hooks_sha256_after": "h"}
        self.assertTrue(scb_run.is_valid(ok, "h"))
        self.assertFalse(scb_run.is_valid(dict(ok, denied_tools=[{"ran": False}, {"ran": True}]), "h"))
        self.assertFalse(scb_run.is_valid(dict(ok, reserved_path=True), "h"))
        self.assertFalse(scb_run.is_valid(ok, "other"))
        self.assertFalse(scb_run.is_valid(dict(ok, model_error="報告が無い"), "h"))

    def test_reserved_present_sees_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(scb_run.reserved_present(d))
            link = Path(d) / scb_run.RESERVED
            try:
                os.symlink(Path(d) / "missing", link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("この環境ではシンボリックリンクを作れない")
            self.assertFalse(link.exists())
            self.assertTrue(scb_run.reserved_present(d))

    def test_timeout_output_keeps_bytes_and_text(self):
        e = subprocess.TimeoutExpired(["agy"], 1, output=b'{"event": "init"}\n', stderr=b"late\n")
        self.assertEqual(scb_run.timeout_output(e), '{"event": "init"}\nlate\n')
        self.assertEqual(scb_run.timeout_output(subprocess.TimeoutExpired(["agy"], 1, output="x\n")), "x\n")
        self.assertEqual(scb_run.timeout_output(subprocess.TimeoutExpired(["agy"], 1)), "")

    def test_timeout_output_from_a_real_timeout(self):
        code = "import time; print('{\"event\": \"init\"}', flush=True); time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            scb_run.sh3([sys.executable, "-c", code], timeout=3)
        self.assertIn('{"event": "init"}', scb_run.timeout_output(cm.exception))

    def test_prompt_files_in_checkpoint_order(self):
        with tempfile.TemporaryDirectory() as d:
            for n in (10, 2, 1):
                (Path(d) / f"part_{n}.md").write_text("x", encoding="utf-8")
            self.assertEqual([p.name for p in scb_run.prompt_files(d)], ["part_1.md", "part_2.md", "part_10.md"])

    def test_quota_floor(self):
        floor = {"gemini-weekly": 0.35, "gemini-5h": 0.15}
        ok = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.5}, "gemini-5h": {"remaining_fraction": 0.2}}}
        low = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.3}, "gemini-5h": {"remaining_fraction": 0.9}}}
        self.assertEqual(scb_run.quota_ok(ok, floor), (True, None))
        self.assertFalse(scb_run.quota_ok(low, floor)[0])
        self.assertFalse(scb_run.quota_ok(None, floor)[0])

    def test_snapshot_drops_official_ignores(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "ws", Path(d) / "snap"
            (src / ".venv" / "lib").mkdir(parents=True)
            (src / ".venv" / "lib" / "x.py").write_text("x", encoding="utf-8")
            (src / "pkg").mkdir()
            (src / "pkg" / "a.py").write_text("a", encoding="utf-8")
            (src / "pkg" / "a.cpython-312.pyc").write_bytes(b"\0")
            (src / "main.py").write_text("m", encoding="utf-8")
            scb_run.copy_snapshot(src, dst)
            got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
            self.assertEqual(got, ["main.py", "pkg/a.py"])


if __name__ == "__main__":
    unittest.main()
