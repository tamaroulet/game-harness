# -*- coding: utf-8 -*-
"""Unit tests for harness.probe module."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT))
import argparse
from pathlib import Path
from harness.probe import (
    get_git_info,
    get_env_info,
    get_progress_info,
    get_modules_summary,
    format_probe_output
)

class TestProbe(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(__file__).resolve().parent.parent

    def test_get_git_info_returns_dict(self):
        info = get_git_info(self.repo_dir)
        self.assertIn("branch", info)
        self.assertIn("head", info)
        self.assertIn("commit_msg", info)
        self.assertIn("dirty", info)
        self.assertIsInstance(info["dirty"], bool)

    def test_get_env_info_detects_python_and_mujoco(self):
        info = get_env_info()
        self.assertIn("python", info)
        self.assertIn("mujoco", info)
        self.assertIn("os", info)
        self.assertTrue(info["mujoco"] != "not installed")

    def test_get_progress_info_parses_officially(self):
        info = get_progress_info(self.repo_dir)
        self.assertIn("active_task_id", info)
        self.assertIn("uncompleted_count", info)
        from harness import progress as p
        expected_id = p.load(self.repo_dir / p.REL_PATH).get("active_task_id")
        self.assertEqual(info["active_task_id"], expected_id)
        from harness import progress as p
        expected_group = next((t["group"] for t in p.load(self.repo_dir / p.REL_PATH).get("tasks", []) if t["id"] == info["active_task_id"]), "")
        self.assertEqual(info["active_group"], expected_group)
        self.assertGreater(info["uncompleted_count"], 0)

    def test_get_modules_summary_counts_correctly(self):
        info = get_modules_summary(self.repo_dir)
        self.assertGreater(info["harness_count"], 50)
        self.assertGreater(info["test_count"], 80)

    def test_format_probe_output_contains_expected_anchors(self):
        output = format_probe_output(self.repo_dir)
        self.assertIn("[GIT HEAD]", output)
        self.assertIn("[ENVIRONMENT]", output)
        self.assertIn("[CODEBASE STRUCTURE]", output)
        self.assertIn("[PROGRESS STATUS]", output)
        from harness import progress as p
        expected_id = p.load(self.repo_dir / p.REL_PATH).get("active_task_id")
        self.assertIn(expected_id, output)
        self.assertIn("[OPERATIONAL BOUNDARIES & CONSTRAINTS]", output)

    def test_format_probe_output_with_on_demand_args(self):
        args = argparse.Namespace(tests=True, git=True)
        output = format_probe_output(self.repo_dir, args)
        self.assertIn("[ON-DEMAND: RECENT GIT LOGS]", output)
        self.assertIn("[ON-DEMAND: ALL TEST FILES", output)

if __name__ == "__main__":
    unittest.main()
