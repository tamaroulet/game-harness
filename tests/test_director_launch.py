"""総監督の無人の起動の関所（W-84）：字数の上限・起動の 1 回・記録・report.md の 1 行。本物の CLI・受信箱は使わない。"""
import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import director_launch  # noqa: E402
import hline_base  # noqa: E402
import hline_report  # noqa: E402

REAL = json.loads(hline_base.CONFIG.read_text(encoding="utf-8"))["director"]
MODEL = REAL["model"]
NOW = datetime.datetime(2026, 10, 19, 3, 0, 0)


def cli_out(turns=3, tokens=(100, 50, 0, 0), models=None, subtype="success"):
    i, o, r, c = tokens
    return json.dumps({"type": "result", "subtype": subtype, "num_turns": turns, "result": "ok",
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": r,
                                 "cache_creation_input_tokens": c},
                       "modelUsage": {m: {} for m in (models or [MODEL])}})


class DirectorLaunch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.prompt = self.tmp / "director_weekly.md"
        self.prompt.write_text("今週の計画を立ててください。\n", encoding="utf-8")
        patch = mock.patch.object(director_launch, "PROMPT", self.prompt)
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = {"inbox": str(self.inbox), "out": str(self.out), "director": json.loads(json.dumps(REAL))}
        self.budget = self.cfg["director"]["director_budget"]

    def doc(self, rel, chars):
        p = self.inbox / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("あ" * chars, encoding="utf-8")

    def launch(self, result=(0, cli_out(), ""), at=NOW):
        with mock.patch.object(director_launch.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(director_launch.proc, "run", return_value=result) as run:
            rec = director_launch.launch(self.cfg, now=at)
        return rec, run

    def records(self):
        return sorted((self.out / "director").glob("*.json"))

    def test_a_document_over_its_limit_stops_the_launch_and_names_it(self):
        self.doc("drafts/director/INDEX.md", 20001)
        self.doc("drafts/director/roadmap.md", 10)
        rec, run = self.launch()
        run.assert_not_called()
        self.assertFalse(rec["launched"])
        self.assertIn("drafts/director/INDEX.md 20001 字（上限 20000）", rec["skipped_reason"])
        self.assertNotIn("roadmap.md", rec["skipped_reason"])
        line = director_launch.latest_line(self.cfg)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("drafts/director/INDEX.md 20001 字", line)
        self.assertEqual(json.loads(self.records()[0].read_text(encoding="utf-8"))["launched"], False)

    def test_the_total_over_its_limit_stops_the_launch(self):
        self.budget["total_max_chars"] = 25
        self.doc("drafts/director/INDEX.md", 15)
        self.doc("drafts/director/roadmap.md", 15)
        rec, run = self.launch()
        run.assert_not_called()
        self.assertIn("合計 30 字（上限 25）", rec["skipped_reason"])
        self.assertIn("合計 30 字", director_launch.latest_line(self.cfg))

    def test_a_missing_document_counts_zero_is_recorded_and_does_not_stop(self):
        self.doc("drafts/director/INDEX.md", 5)
        rec, run = self.launch()
        run.assert_called_once()
        by_path = {r["path"]: r for r in rec["docs"]}
        self.assertEqual(len(by_path), len(self.budget["files"]))
        self.assertEqual((by_path["drafts/director/decisions.md"]["chars"], by_path["drafts/director/decisions.md"]["state"]), (0, "無い"))
        self.assertEqual(by_path["drafts/director/INDEX.md"]["chars"], 5)
        saved = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual({r["path"]: r["state"] for r in saved["docs"]}["notes/handoff.md"], "無い")

    def test_within_limits_the_cli_runs_once_in_the_inbox_with_turn_and_ttl_caps(self):
        self.doc("drafts/director/INDEX.md", 20000)
        rec, run = self.launch()
        self.assertEqual(run.call_count, 1)
        args, cwd, ttl = run.call_args.args[:3]
        self.assertEqual(Path(cwd), self.inbox)
        self.assertEqual(ttl, self.budget["ttl_seconds"])
        self.assertEqual(args[args.index("--max-turns") + 1], str(self.budget["max_turns"]))
        self.assertEqual(args[args.index("--model") + 1], MODEL)
        self.assertEqual(run.call_args.kwargs["input"], "今週の計画を立ててください。\n")
        self.assertTrue(rec["launched"])

    def test_without_the_instruction_file_the_cli_is_not_run_and_the_reason_is_recorded(self):
        self.prompt.unlink()
        rec, run = self.launch()
        run.assert_not_called()
        self.assertFalse(rec["launched"])
        self.assertIn("指示のファイルがありません", rec["skipped_reason"])
        self.assertIn("指示のファイルがありません", director_launch.latest_line(self.cfg))
        self.assertIn("指示のファイルがありません", self.records()[0].read_text(encoding="utf-8"))

    def test_a_normal_run_records_time_end_reason_and_usage(self):
        rec, _ = self.launch(result=(0, cli_out(tokens=(100, 50, 30, 20)), ""))
        saved = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["at"], "2026-10-19T03:00:00")
        self.assertEqual(saved["end"], "正常")
        self.assertEqual(saved["usage"]["total_tokens"], 200)
        self.assertEqual(saved["usage"]["num_turns"], 3)
        self.assertFalse(saved["token_excess"])
        self.assertEqual(rec, saved)
        self.assertTrue(list((self.out / "director").glob("*.log")))
        self.assertNotIn("超過", director_launch.latest_line(self.cfg))

    def test_a_turn_cutoff_is_recorded_as_such(self):
        rec, _ = self.launch(result=(1, cli_out(turns=40, subtype="error_max_turns", models=[]), ""))
        self.assertTrue(rec["launched"])
        self.assertEqual(rec["end"], "ターンの上限")
        self.assertEqual(rec["usage"]["num_turns"], 40)

    def test_ttl_and_cli_errors_are_recorded(self):
        rec, _ = self.launch(result=(124, "", "TTL超過 (3600s): 総監督"))
        self.assertEqual(rec["end"], "TTL")
        rec, _ = self.launch(result=(1, "", "boom"), at=NOW + datetime.timedelta(seconds=1))
        self.assertEqual(rec["end"], "CLI の誤り")
        rec, _ = self.launch(result=(0, cli_out(models=["claude-sonnet-5-5"]), ""), at=NOW + datetime.timedelta(seconds=2))
        self.assertEqual(rec["end"], "CLI の誤り")

    def test_usage_over_max_tokens_is_marked_in_the_record_and_one_line_of_report(self):
        self.budget["max_tokens"] = 1000
        rec, _ = self.launch(result=(0, cli_out(tokens=(900, 200, 0, 0)), ""))
        self.assertTrue(rec["token_excess"])
        self.assertEqual(rec["end"], "正常")
        line = director_launch.latest_line(self.cfg)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("超過", line)
        st = {"items": {}, "awaiting_pr": None, "skipped": []}
        with mock.patch.object(hline_report, "line_status", return_value="x"), \
                mock.patch.object(hline_report, "northstar_section", return_value=""), \
                mock.patch.object(hline_report, "failure_section", return_value=""):
            text = hline_report.h_section(self.cfg | {"integration_branch": "b"}, st)
        self.assertIn(line, text.splitlines())

    def test_main_entry_runs_the_gate_with_the_given_config(self):
        path = self.tmp / "hline.json"
        base = json.loads(hline_base.CONFIG.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**base, **self.cfg}), encoding="utf-8")
        self.doc("drafts/director/INDEX.md", 20001)
        with mock.patch("builtins.print"):
            self.assertEqual(director_launch.main(["--config", str(path)]), 1)
        self.assertEqual(len(self.records()), 1)


if __name__ == "__main__":
    unittest.main()
