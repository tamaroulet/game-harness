"""SlopCodeBench の実行口（harness/scb_run.py）のベンチ固有の部品の試験（docker に依らない）。共通の層は
tests/test_agy_port.py と tests/test_sandbox.py。

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import scb_run  # noqa: E402

FIXTURE_ENV = Path(__file__).resolve().parent / "fixtures" / "scb_env_docker-python3.12-uv.yaml"


def eval_json(passed, total, infra=False, regression_failed=()):
    tests = {"checkpoint_1-Regression": {"passed": [], "failed": list(regression_failed)}}
    return {"pass_counts": passed, "total_counts": total, "infrastructure_failure": infra, "tests": tests,
            "test_collection_hash": "h", "duration": 1.0}


class ConfigTests(unittest.TestCase):
    def test_config_merges_common_and_bench(self):
        cfg = scb_run.load_config()
        self.assertEqual(cfg["agent"]["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual(cfg["scb"]["image"], "slop-code:python3.12")
        self.assertTrue(cfg["scb"]["image_id"].startswith("sha256:c49426ce309b"))
        self.assertEqual(cfg["sandbox"]["uid"], 1000)


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
        cfg = scb_run.load_config()
        env = scb_run.eval_env(cfg["scb"], "2026-11-20T06:19:36Z", cfg["sandbox"]["uid"])
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


class TimeoutStreakTests(unittest.TestCase):
    """止める線 9：同じ問題で続けて時間切れになった数（末尾から）。"""

    def test_counts_only_the_trailing_timeouts(self):
        cp = lambda t: {"agent": {"rc": 0 if t is None else 3, "timeout": t}}  # noqa: E731
        self.assertEqual(scb_run.timeout_streak([]), 0)
        self.assertEqual(scb_run.timeout_streak([cp(None)]), 0)
        self.assertEqual(scb_run.timeout_streak([cp("print_timeout")]), 1)
        self.assertEqual(scb_run.timeout_streak([cp(None), cp("hard"), cp("print_timeout")]), 2)
        self.assertEqual(scb_run.timeout_streak([cp("hard"), cp(None)]), 0)
        self.assertEqual(scb_run.timeout_streak([cp("hard"), cp(None), cp("hard")]), 1)
        self.assertEqual(scb_run.timeout_streak([{"checkpoint": 1, "stopped": "quota"}]), 0)   # 走らなかった
        self.assertEqual(scb_run.TIMEOUT_STREAK_STOP, 2)


class WorkspaceTests(unittest.TestCase):
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

    def test_prompt_files_in_checkpoint_order(self):
        with tempfile.TemporaryDirectory() as d:
            for n in (10, 2, 1):
                (Path(d) / f"part_{n}.md").write_text("x", encoding="utf-8")
            self.assertEqual([p.name for p in scb_run.prompt_files(d)], ["part_1.md", "part_2.md", "part_10.md"])

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
