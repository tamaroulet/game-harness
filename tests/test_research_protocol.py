"""調べ方の規則（W-141、docs/design/research_protocol.md）：問いの形・段ごとの調べ役・出典の格と分野と検索語・索引（知識の台帳）。

    python -m unittest tests.test_research_protocol -v

ネットワークにも実物の CLI にも出ない。CLI は偽の run、ページは偽の fetch で差し替える。根拠の強さは tests/test_premises.py。
"""
import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))
import quiet  # noqa: E402
import research  # noqa: E402
import test_research_verify as trv  # noqa: E402

NOW = trv.NOW
MAP = ("# 自律開発の地図\n\n## 段\nmap\n\n## 決めたい判断\n上位の設計を決める\n\n## 判断を変えうるもの\n別の系統が主流なら\n\n"
       "## 分野\nfrontier_labs\nacademia\n\n## 問い\nいまの手法の系統を集める。\n")


def question(**over):
    sec = {"段": "compare", "決めたい判断": "d", "判断を変えうるもの": "c", "候補": "x", "問い": "q"}
    sec.update(over)
    return "# t\n\n" + "".join(f"## {k}\n{v}\n\n" for k, v in sec.items() if v is not None)


class Rules(unittest.TestCase):
    def test_the_protocol_names_the_same_sectors_stages_and_tiers_as_the_code_and_the_schema(self):
        doc = (ROOT / "docs" / "design" / "research_protocol.md").read_text(encoding="utf-8")
        for name in research.SECTORS + research.STAGES + research.TIERS:
            self.assertIn(name, doc)
        schema = json.loads((ROOT / "config" / "research_schema.json").read_text(encoding="utf-8"))
        item = schema["properties"]["claims"]["items"]
        self.assertEqual(tuple(item["required"]), research.CLAIM_KEYS)
        self.assertEqual(tuple(item["properties"]["sector"]["enum"]), research.SECTORS)
        self.assertEqual(tuple(item["properties"]["source_tier"]["enum"]), research.TIERS)
        self.assertIn("queries", schema["required"])
        prompt = (ROOT / "docs" / "prompts" / "researcher.md").read_text(encoding="utf-8")
        for name in research.SECTORS + ("source_tier", "sector", "approach", "queries"):
            self.assertIn(name, prompt)

    def test_every_stage_has_a_pinned_backend(self):
        cfg = research.load_config()
        self.assertEqual(set(cfg["stages"]), set(research.STAGES))
        for stage, name in cfg["stages"].items():
            self.assertIn(name, cfg["backends"], stage)


class QuestionShape(unittest.TestCase):
    def problems(self, text):
        return research.question_problems(research.sections(text))

    def test_good_questions_pass(self):
        self.assertEqual(self.problems(MAP), [])
        self.assertEqual(self.problems(question()), [])

    def test_missing_decision_or_changers_is_rejected(self):
        self.assertTrue(self.problems(question(**{"決めたい判断": None})))
        self.assertTrue(self.problems(question(**{"判断を変えうるもの": None})))

    def test_map_needs_known_sectors_and_no_candidates(self):
        self.assertIn("map には「## 分野」が要る", self.problems(MAP.replace("## 分野\nfrontier_labs\nacademia\n", "")))
        self.assertTrue(any("blogs" in p for p in self.problems(MAP.replace("academia", "blogs"))))
        self.assertIn("map には「## 候補」を書かない（自分の案を俯瞰に入れない）",
                      self.problems(MAP.replace("## 問い", "## 候補\n今の案\n\n## 問い")))

    def test_compare_and_refute_need_candidates_and_the_stage_must_be_known(self):
        self.assertIn("refute には「## 候補」が要る", self.problems(question(段="refute", 候補=None)))
        self.assertTrue(any("段 'guess'" in p for p in self.problems(question(段="guess"))))


class Ask(quiet.Quiet, unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = research.load_config()
        self.cfg.update(records=str(self.tmp / "records"), queue=str(self.tmp / "queue"), work=str(self.tmp / "work"),
                        index=str(self.tmp / "index.json"), public_records=str(self.tmp / "public"))
        self.calls = []
        mock.patch.object(research.agy_pinned, "require", return_value=None).start()
        self.addCleanup(mock.patch.stopall)

    def run_cli(self, code, out):
        def run(args, cwd, ttl, label, env=None, input=None):
            self.calls.append({"args": args, "ttl": ttl})
            return code, out, ""
        return run

    def ask(self, text, run, backend=None):
        q = self.tmp / "q.md"
        q.write_text(text, encoding="utf-8")
        return research.ask(self.cfg, q, backend, None, trv.pages({"https://example.org/a": (200, trv.PAGE, None)}),
                            run, lambda name: [name], NOW)

    def test_a_malformed_question_is_rejected_before_calling_the_researcher(self):
        rc = self.ask("# t\n\n何でも調べる。\n", self.run_cli(0, ""))
        self.assertEqual(rc, research.REJECTED)
        self.assertEqual(self.calls, [], "利用枠を使わない")
        self.assertIn("決めたい判断", self.written())

    def test_map_goes_to_the_deep_backend_with_its_own_ttl(self):
        out = trv.claude_out(json.dumps(trv.ANSWER), models=("claude-sonnet-5-5",))
        self.assertEqual(self.ask(MAP, self.run_cli(0, out)), research.RECORDED)
        args = self.calls[0]["args"]
        self.assertIn("claude-sonnet-5-5", args)
        self.assertEqual(self.calls[0]["ttl"], self.cfg["backends"]["claude-deep"]["ttl_seconds"])
        rec = json.loads((self.tmp / "records" / "R-20261023-q.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["stage"], rec["decision"], rec["sectors"]), ("map", "上位の設計を決める", ["frontier_labs", "academia"]))
        self.assertEqual(rec["queries"], ["agent review tiers"])
        self.assertEqual(rec["claims"][0]["source_tier"], "primary")
        md = (self.tmp / "records" / "R-20261023-q.md").read_text(encoding="utf-8")
        self.assertIn("## 検索語", md)
        self.assertIn("| 系統 | 分野 | 格 |", md)

    def test_an_answer_without_tiers_sectors_or_queries_is_not_recorded(self):
        for broken in ({**trv.ANSWER, "queries": None},
                       {**trv.ANSWER, "claims": [{**trv.claim(), "source_tier": "blog"}]},
                       {**trv.ANSWER, "claims": [{**trv.claim(), "sector": "somewhere"}]}):
            out = trv.claude_out(json.dumps(broken), models=("claude-sonnet-5-5",))
            self.assertEqual(self.ask(MAP, self.run_cli(0, out)), research.REJECTED, broken)
        self.assertFalse((self.tmp / "records").exists())


class Index(quiet.Quiet, unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = {"records": str(self.tmp / "local"), "public_records": str(self.tmp / "public"),
                    "index": str(self.tmp / "out" / "index.json"), "recheck_days": 30}
        for d, rid, asked, claim in (
                ("local", "R-20260901-old", "2026-09-01T10:00:00+09:00", trv.claim(approach="sandbox-vm")),
                ("public", "R-20261023-new", "2026-10-23T10:00:00+09:00",
                 trv.claim(url="https://arxiv.org/abs/1", approach="spec-first")),
                ("public", "R-20260901-old", "2026-09-01T10:00:00+09:00", trv.claim(approach="duplicate"))):
            (self.tmp / d).mkdir(exist_ok=True)
            rec = {"id": rid, "title": rid, "stage": "map", "decision": "上位の設計", "asked_at": asked,
                   "claims": [{"id": "C1", **claim, "verify": {"status": "verified"}}]}
            (self.tmp / d / f"{rid}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def test_the_index_merges_both_places_once_and_marks_the_recheck_date(self):
        rows = research.build_index(self.cfg)
        self.assertEqual([r["ref"] for r in rows], ["R-20260901-old#C1", "R-20261023-new#C1"])
        self.assertEqual(rows[0]["approach"], "sandbox-vm", "同じ ID は Git の外の記録を先に取る")
        self.assertEqual((rows[0]["recheck_by"], rows[1]["domain"]), ("2026-10-01", "arxiv.org"))
        path = research.write_index(self.cfg, rows)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), rows)
        self.assertIn("R-20261023-new#C1", path.with_name("INDEX.md").read_text(encoding="utf-8"))

    def test_search_matches_all_words_and_stale_lists_only_overdue_rows(self):
        rows = research.build_index(self.cfg)
        self.assertEqual([r["ref"] for r in research.search(rows, ["SPEC-first"])], ["R-20261023-new#C1"])
        self.assertEqual(research.search(rows, ["spec-first", "sandbox"]), [])
        self.assertEqual([r["ref"] for r in research.search(rows, [], stale_on="2026-10-23")], ["R-20260901-old#C1"])

    def test_the_cli_writes_and_searches_the_index(self):
        with mock.patch.object(research, "load_config", return_value=self.cfg):
            self.assertEqual(research.main(["index"]), 0)
            self.assertEqual(research.main(["search", "sandbox"]), 0)
            self.assertEqual(research.main(["search", "--stale"]), 0)
        self.assertIn("R-20260901-old#C1", self.written())
        self.assertTrue((self.tmp / "out" / "INDEX.md").exists())


if __name__ == "__main__":
    unittest.main()
