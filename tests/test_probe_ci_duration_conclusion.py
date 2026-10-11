"""main の CI の所要：conclusion が success か failure の走行だけを数える。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_probe as hp  # noqa: E402


def row(status, conclusion, minutes):
    return {"status": status, "conclusion": conclusion, "startedAt": "2026-10-01T00:00:00Z",
            "updatedAt": f"2026-10-01T00:{minutes:02d}:00Z"}


def runner_for(runs):
    def runner(args, cwd, ttl, label):
        return 0, json.dumps(runs), ""
    return runner


class Conclusion(unittest.TestCase):
    cfg = {"base": "origin/main", "integration_branch": "hline/integration", "out": ".", "ttl_seconds": {"git": 5}}

    def test_only_success_and_failure_are_counted(self):
        runs = [row("completed", "success", 4), row("completed", "failure", 10),
                row("completed", "cancelled", 50), row("in_progress", "", 30), row("completed", "success", 6)]
        line = hp.duration_fetch(self.cfg, runner_for(runs))
        self.assertIn("中央値 6 分 0 秒", line)
        self.assertIn("最大 10 分 0 秒", line)
        self.assertIn("数えた走行 3 回／直近 5 回", line)

    def test_other_conclusions_are_not_counted(self):
        runs = [row("completed", c, 5) for c in ("skipped", "timed_out", "cancelled", "", None)]
        line = hp.duration_fetch(self.cfg, runner_for(runs))
        self.assertEqual(line, "完了した走行なし（直近 5 回のうち 0 回）")

    def test_gh_asks_for_conclusion(self):
        seen = []

        def runner(args, cwd, ttl, label):
            seen.append(args)
            return 0, "[]", ""
        hp.duration_fetch(self.cfg, runner)
        self.assertIn("status,conclusion,startedAt,updatedAt", seen[0])


if __name__ == "__main__":
    unittest.main()
