"""今の構成の実測（W-142、harness/arch_inventory.py）：部品の割り当てが漏れも重なりも無いこと、費用と成果が記録から機械で数えられること。

    python -m unittest tests.test_arch_inventory -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import arch_inventory as ai  # noqa: E402

LOG = ("@@2026-10-01\tfix(hline): a\nharness/hline.py\nharness/proc.py\n\n"
       "@@2026-10-03\tfeat(hline): b\nharness/hline_git.py\n\n"
       "@@2026-10-02\tfix(x): c\nharness/unknown.py\n")


class Assignment(unittest.TestCase):
    def test_every_harness_module_belongs_to_exactly_one_component(self):
        owner, unassigned, doubled = ai.assign(ai.load(), ai.modules())
        self.assertEqual((unassigned, doubled), ([], []), "config/arch_components.json に足す")
        self.assertIn("arch_inventory.py", owner)

    def test_every_listed_module_exists(self):
        mods = ai.modules()
        for name, c in ai.load().items():
            for pattern in c["modules"]:
                self.assertTrue(any(ai._matches(m, pattern) for m in mods), f"{name} の {pattern} が無い")

    def test_unassigned_and_doubled_are_reported(self):
        comps = {"a": {"modules": ["x.py", "d/"]}, "b": {"modules": ["d/y.py"]}}
        self.assertEqual(ai.assign(comps, ["x.py", "d/y.py", "z.py"]), ({"x.py": "a"}, ["z.py"], [("d/y.py", ["a", "b"])]))


class Measure(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_git_log_and_churn(self):
        log = ai.git_log("2026-09-01", git=lambda root, args: LOG.encode("utf-8"))
        self.assertEqual(log[0], ("2026-10-01", "fix(hline): a", ["hline.py", "proc.py"]))
        owner = {"hline.py": "hline", "hline_git.py": "hline", "proc.py": "foundation"}
        self.assertEqual(ai.churn(log, owner), {"hline": {"commits": 2, "fixes": 1, "last": "2026-10-03"},
                                                 "foundation": {"commits": 1, "fixes": 1, "last": "2026-10-01"}})

    def test_runtime_outcomes_come_from_the_records(self):
        queue = {"items": {
            "a": {"status": "done", "merged": True, "task": "W-1",
                  "tries": [{"usage": {"num_turns": 3, "total_tokens": 100, "output_tokens": 10}}]},
            "b": {"status": "unconverged", "task": "S-1", "tries": [{}]}}, "outer": {"awaiting_pr": "u"}}
        got = ai.hline_outcome(queue)
        self.assertEqual((got["items"], got["merged"], got["tries"], got["tokens"], got["tries_without_usage"]), (2, 1, 2, 100, 1))
        self.assertEqual(got["status"], {"done": 1, "unconverged": 1})
        (self.tmp / "director").mkdir()
        (self.tmp / "director" / "1.json").write_text(json.dumps(
            {"launched": True, "total_chars": 50, "usage": {"output_tokens": 7, "cost_usd": 0.5}}), encoding="utf-8")
        (self.tmp / "director" / "2.json").write_text(json.dumps({"launched": False}), encoding="utf-8")
        what = self.tmp / "inbox" / "drafts" / "what"
        what.mkdir(parents=True)
        for name in ("W-1", "W-2"):
            (what / f"{name}.md").write_text("x", encoding="utf-8")
        self.assertEqual(ai.director_outcome(self.tmp, self.tmp / "inbox", queue),
                         {"launches": 1, "skipped": 1, "read_chars": 50, "output_tokens": 7, "cost_usd": 0.5,
                          "what_drafts": 2, "what_drafts_queued": 1})
        self.assertEqual(ai.outer_outcome(queue), {"awaiting_pr": "u", "cycles": 0})
        self.assertIsNone(ai.hline_outcome(None))

    def test_the_repository_is_measured_without_run_records(self):
        cfg = {"out": str(self.tmp / "out"), "inbox": str(self.tmp / "inbox")}
        rep = ai.measure("2026-09-01", cfg=cfg, git=lambda root, args: LOG.encode("utf-8"))
        rows = {r["component"]: r for r in rep["rows"]}
        self.assertEqual(set(rows), set(ai.load()))
        self.assertGreater(rows["game_line"]["code_lines"], 0)
        self.assertEqual(rows["hline"]["fixes"], 1)
        self.assertIsNone(rows["director"]["runtime"])
        text = ai.render(rep)
        self.assertIn("| director（", text)
        self.assertIn("記録なし", text)


if __name__ == "__main__":
    unittest.main()
