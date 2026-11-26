"""RoadmapBench の薄い接続（harness/rb_run.py）のベンチ固有の部品の試験（docker に依らない）。

    python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import phase1_dev  # noqa: E402
import rb_run  # noqa: E402

REGISTRIES = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "crates.io", "proxy.golang.org", "goproxy.cn")


class ConfigTests(unittest.TestCase):
    def test_agent_runs_like_harbor_and_registries_are_closed(self):
        cfg = rb_run.load_config()
        self.assertEqual(cfg["agent"]["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual((cfg["sandbox"]["uid"], cfg["sandbox"]["home"], cfg["sandbox"]["workdir"]), (0, "/root", "/app"))
        self.assertFalse(set(REGISTRIES) & set(cfg["network"]["allow"]))
        for task, image_id in cfg["rb"]["dev_tasks"].items():
            self.assertRegex(task, rb_run.TASK_RE)
            self.assertRegex(image_id, r"^sha256:[0-9a-f]{64}$")

    def test_only_dev_tasks_run(self):
        cfg = rb_run.load_config()
        self.assertEqual(rb_run.require_dev(cfg, "fal-3.0.0-roadmap"), "fal-3.0.0-roadmap")
        for bad in ("glz-1.0.0-roadmap", "", "fal-3.0.0-roadmap; rm -rf /"):
            with self.assertRaises(phase1_dev.NotDevError):
                rb_run.require_dev(cfg, bad)


class PartsTests(unittest.TestCase):
    def test_phases_read_the_echo_lines_only(self):
        log = "\n".join([
            "=== Phase 1: ASGI HTTP Support ===", "tests/test_01.py::test_a PASSED  [ 50%]", "tests/test_01.py::test_b FAILED",
            "FAILED", "=== Phase 2: WebSocket & SSE ===", "PASSED", "", "=== Phase 3: Multipart Form Handler ===",
            "Result: 1/3 phases | Reward: 0.4"])
        self.assertEqual(rb_run.phases(log), [{"n": 1, "name": "ASGI HTTP Support", "passed": False},
                                              {"n": 2, "name": "WebSocket & SSE", "passed": True},
                                              {"n": 3, "name": "Multipart Form Handler", "passed": None}])

    def test_reward_and_limits(self):
        self.assertEqual(rb_run.reward_of("0.7\n"), 0.7)
        self.assertIsNone(rb_run.reward_of(""))
        self.assertIsNone(rb_run.reward_of("cat: no such file"))
        self.assertEqual(rb_run.limits({"cpus": 2, "memory_mb": 4096}),
                         ["--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m"])

    def test_read_task_takes_limits_from_task_toml(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "fal-3.0.0-roadmap"
            (t / "tests").mkdir(parents=True)
            (t / "instruction.md").write_text("# Roadmap\n", encoding="utf-8")
            (t / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (t / "task.toml").write_text('version = "1"\n[verifier]\ntimeout_sec = 1800\n[agent]\ntimeout_sec = 7200\n'
                                         '[environment]\ndocker_image = "znpt/roadmapbench-fal-3.0.0-roadmap"\n'
                                         'cpus = 2\nmemory_mb = 4096\n', encoding="utf-8")
            got = rb_run.read_task({"rb": {"data": d}}, "fal-3.0.0-roadmap")
        self.assertEqual((got["agent_timeout_s"], got["verifier_timeout_s"], got["cpus"], got["memory_mb"]), (7200, 1800, 2, 4096))
        self.assertEqual(got["instruction"], "# Roadmap\n")
        self.assertEqual(sorted(got["sha256"]), ["instruction.md", "task.toml", "tests/test.sh"])


if __name__ == "__main__":
    unittest.main()
