"""SWE-Game Mode 4 の実行口（harness/sweg_run.py）のベンチ固有の部品の試験（docker に依らない）。

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import sweg_run  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_config_merges_common_and_bench(self):
        cfg = sweg_run.load_config()
        self.assertEqual(cfg["agent"]["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual(cfg["sweg"]["image"], "gamebench-agent:godot-4.5.1")
        self.assertTrue(cfg["sweg"]["image_id"].startswith("sha256:383f1e18441c"))
        self.assertEqual(cfg["sweg"]["visual_judge"], "none")


class PartsTests(unittest.TestCase):
    def test_ids_are_checked_before_reaching_a_shell(self):
        self.assertEqual(sweg_run.check_id("canopy-dash-plank-hold-clock-v1", "case"), "canopy-dash-plank-hold-clock-v1")
        for bad in ("", "a b", "x;rm -rf /", "../up", "A"):
            with self.assertRaises(ValueError):
                sweg_run.check_id(bad, "case")

    def test_probe_lists_the_official_tools(self):
        script = sweg_run.probe_script()
        self.assertIn("for t in jq ffmpeg ffprobe xvfb-run node ruby python3;", script)

    def test_parse_facts(self):
        out = "\n".join(["godot_path=/usr/local/bin/godot", "godot_version=4.5.1.stable.official.f62fdbde1",
                         "home=/home/agent", "home_writable=1", "display=", "path=/opt/venv/bin:/usr/bin",
                         "uname=Linux x 6.6", "tool_jq=1", "tool_ruby=0", "tool_python3=1"])
        f = sweg_run.parse_facts(out, 3600)
        self.assertEqual(f["godot_version"], "4.5.1.stable.official.f62fdbde1")
        self.assertTrue(f["home_writable"])
        self.assertEqual(f["display"], "")
        self.assertEqual((f["tools"]["jq"], f["tools"]["ruby"], f["tools"]["ffmpeg"]), (True, False, False))
        self.assertEqual(f["budget_s"], 3600)

    def test_eval_summary_reads_the_official_report(self):
        report = {"resolved": False, "headline": {"score": 0.0, "status": "complete",
                                                  "score_scope": "mode4_differential_repair_product"},
                  "resolution": {"failing_strict_items": [{"id": "gold_replay"}, {"id": "repair_differential"}]},
                  "score": {"by_verdict": {"failed": 2.0, "passed": 2.0}}}
        s = sweg_run.eval_summary(report)
        self.assertEqual((s["resolved"], s["score"], s["status"]), (False, 0.0, "complete"))
        self.assertEqual(s["failing_strict_items"], ["gold_replay", "repair_differential"])


if __name__ == "__main__":
    unittest.main()
