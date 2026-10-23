"""外部の調べもの（W-123、ADR-004 の K-15）：出典の照合（harness/research_verify.py）と、調べ役の呼び出しから記録まで（harness/research.py）。

    python -m unittest tests.test_research_verify -v

ネットワークにも実物の CLI にも出ない。ページは偽の fetch、CLI は偽の run で差し替える。"""
import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quiet  # noqa: E402
import research  # noqa: E402
import research_verify as rv  # noqa: E402

NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
PAGE = "<html><head><script>var x = 'not a quote';</script></head><body><p>Agents\nship “tiered” reviews.</p></body></html>"


def pages(table):
    return lambda url: table.get(url, (None, None, "名前を解決できない"))


def claim(url="https://example.org/a", quote='ship "tiered" reviews', **kw):
    return {"claim": "c", "source_url": url, "source_title": "t", "published": "2026-10-01", "quote": quote,
            "kind": "fact", "confidence": "high", "source_tier": "primary", "sector": "frontier_labs", "approach": "a", **kw}


class Normalize(unittest.TestCase):
    def test_width_quotes_space_and_case_are_ignored(self):
        self.assertEqual(rv.normalize("Ａgents  ship\n“TIERED”"), rv.normalize('agents ship "tiered"'))

    def test_script_and_style_are_not_text(self):
        text = rv.html_text(PAGE)
        self.assertIn("Agents", text)
        self.assertNotIn("not a quote", text)


class Verify(unittest.TestCase):
    def test_a_quote_on_the_page_is_verified(self):
        got = rv.verify(claim(), pages({"https://example.org/a": (200, PAGE, None)}), NOW)
        self.assertEqual(got["status"], "verified")
        self.assertEqual(got["http_status"], 200)
        self.assertEqual(len(got["sha256"]), 64)
        self.assertEqual(got["fetched_at"], "2026-10-23T12:00:00+09:00")

    def test_a_quote_not_on_the_page_is_not_verified(self):
        got = rv.verify(claim(quote="ship tiered reviews daily"), pages({"https://example.org/a": (200, PAGE, None)}), NOW)
        self.assertEqual(got["status"], "quote_not_found")

    def test_a_text_inside_a_script_does_not_count(self):
        got = rv.verify(claim(quote="not a quote"), pages({"https://example.org/a": (200, PAGE, None)}), NOW)
        self.assertEqual(got["status"], "quote_not_found")

    def test_a_page_that_cannot_be_fetched_is_unreachable_not_false(self):
        got = rv.verify(claim(), pages({"https://example.org/a": (404, None, "HTTP 404")}), NOW)
        self.assertEqual((got["status"], got["http_status"], got["reason"]), ("unreachable", 404, "HTTP 404"))

    def test_no_url_or_no_quote_is_no_source(self):
        for c in (claim(url=""), claim(url="file:///etc/passwd"), claim(quote="  ")):
            self.assertEqual(rv.verify(c, pages({}), NOW)["status"], "no_source")

    def test_the_same_url_is_fetched_once(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            return 200, PAGE, None
        got = rv.verify_all([claim(), claim(quote="Agents"), claim(url="https://example.org/b")], fetcher, NOW)
        self.assertEqual([g["status"] for g in got], ["verified", "verified", "verified"])
        self.assertEqual(calls, ["https://example.org/a", "https://example.org/b"])


ANSWER = {"claims": [claim(), claim(quote="made up sentence")], "queries": ["agent review tiers"],
          "unknowns": ["一次の出典が見つからない"]}
QUESTION = ("# 承認の層\n\n## 段\ncompare\n\n## 決めたい判断\n承認の層を分けるか\n\n## 判断を変えうるもの\n失敗の事後検証\n\n"
            "## 候補\n層を分ける\n\n## 問い\nいまの実践を調べる。\n")


def agy_out(text, model=None):
    model = model or research.load_config()["backends"]["agy"]["model_name"]   # 設定の固定と同じモデルを報告する
    return "\n".join(json.dumps(e) for e in ({"event": "init", "init": {"model": model, "conversation_id": "c1"}},
                                             {"event": "result", "result": {"status": "SUCCESS", "response": text,
                                                                            "usage": {"input_tokens": 10, "output_tokens": 5}}}))


def claude_out(text, models=("claude-haiku-5-5",)):
    return json.dumps({"result": text, "modelUsage": {m: {} for m in models}, "usage": {"input_tokens": 3}})


class Ask(quiet.Quiet, unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = research.load_config()
        self.cfg.update(records=str(self.tmp / "records"), queue=str(self.tmp / "queue"), work=str(self.tmp / "work"))
        self.q = self.tmp / "review-tiers.md"
        self.q.write_text(QUESTION, encoding="utf-8")
        self.calls = []
        mock.patch.object(research.agy_pinned, "require", return_value=None).start()
        self.addCleanup(mock.patch.stopall)

    def runner(self, code, out, err=""):
        def run(args, cwd, ttl, label, env=None, input=None):
            self.calls.append({"args": args, "cwd": cwd, "input": input})
            return code, out, err
        return run

    def ask(self, backend, run, fetch=None):
        return research.ask(self.cfg, self.q, backend, None, fetch or pages({"https://example.org/a": (200, PAGE, None)}),
                            run, lambda name: [name], NOW)

    def test_agy_records_claims_with_marks_and_ids(self):
        rc = self.ask("agy", self.runner(0, agy_out("前置き\n```json\n" + json.dumps(ANSWER) + "\n```")))
        self.assertEqual(rc, research.RECORDED)
        rec = json.loads((self.tmp / "records" / "R-20261023-review-tiers.json").read_text(encoding="utf-8"))
        self.assertEqual([c["id"] for c in rec["claims"]], ["C1", "C2"])
        self.assertEqual([c["verify"]["status"] for c in rec["claims"]], ["verified", "quote_not_found"])
        self.assertEqual(rec["models"], [research.load_config()["backends"]["agy"]["model_name"]])
        self.assertEqual(rec["counts"]["verified"], 1)
        md = (self.tmp / "records" / "R-20261023-review-tiers.md").read_text(encoding="utf-8")
        self.assertIn("| C2 |", md)
        self.assertIn("一次の出典が見つからない", md)
        # docs/research/ に写すので LF で書く（Windows の write_text の既定の CRLF にしない）
        for ext in ("md", "json"):
            self.assertNotIn(b"\r\n", (self.tmp / "records" / f"R-20261023-review-tiers.{ext}").read_bytes())

    def test_agy_is_called_read_only_with_the_schema_and_the_pinned_model(self):
        self.ask("agy", self.runner(0, agy_out(json.dumps(ANSWER))))
        args = self.calls[0]["args"]
        for flag in ("--mode", "plan", "--sandbox", "--json-schema", "--model", research.load_config()["backends"]["agy"]["model_name"]):
            self.assertIn(flag, args)
        self.assertTrue(args[args.index("--json-schema") + 1].endswith("research_schema.json"))
        self.assertIn("調べ役への指示", json.loads(self.calls[0]["input"])["message"]["content"])

    def test_claude_is_an_alternative_with_the_same_record(self):
        rc = self.ask("claude", self.runner(0, claude_out(json.dumps(ANSWER))))
        self.assertEqual(rc, research.RECORDED)
        rec = json.loads((self.tmp / "records" / "R-20261023-review-tiers.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["models"], ["claude-haiku-5-5"])
        self.assertEqual(rec["backend"], "claude")
        self.assertIn("WebSearch,WebFetch", self.calls[0]["args"])

    def test_a_different_model_is_an_abort(self):
        with self.assertRaises(research.Abort):
            self.ask("agy", self.runner(0, agy_out(json.dumps(ANSWER), model="gemini-other")))
        with self.assertRaises(research.Abort):
            self.ask("claude", self.runner(0, claude_out(json.dumps(ANSWER), models=("claude-sonnet-5-5",))))
        self.assertFalse((self.tmp / "records").exists())

    def test_a_failed_cli_is_an_abort(self):
        with self.assertRaises(research.Abort):
            self.ask("claude", self.runner(1, claude_out(""), "boom"))

    def test_a_broken_shape_is_rejected_and_nothing_is_recorded(self):
        for text in ("答えはありません", json.dumps({"claims": [{"claim": "x"}], "unknowns": []}),
                     json.dumps({**ANSWER, "advice": "use tiers"})):
            with self.subTest(text=text[:20]):
                self.assertEqual(self.ask("agy", self.runner(0, agy_out(text))), research.REJECTED)
        self.assertFalse((self.tmp / "records").exists())
        self.assertTrue(any((self.tmp / "work").glob("*/raw.txt")))

    def test_an_exhausted_quota_queues_the_question(self):
        rc = self.ask("agy", self.runner(1, "", "RESOURCE_EXHAUSTED: quota exceeded"))
        self.assertEqual(rc, research.QUEUED)
        self.assertTrue((self.tmp / "queue" / "review-tiers.md").is_file())
        self.assertFalse((self.tmp / "records").exists())
        rc = self.ask("claude", self.runner(1, json.dumps({"result": "You've hit your limit · resets 5pm"})))
        self.assertEqual(rc, research.QUEUED)

    def test_the_queue_is_drained_in_order_and_stops_at_the_quota(self):
        queue = self.tmp / "queue"
        queue.mkdir()
        for name in ("b.md", "a.md"):
            (queue / name).write_text(QUESTION, encoding="utf-8")
        ok = self.runner(0, agy_out(json.dumps(ANSWER)))
        kw = dict(fetcher=pages({}), run=ok, resolve=lambda name: [name], now=NOW)
        self.assertEqual(research.run_queue(self.cfg, "agy", None, **kw), research.RECORDED)
        self.assertEqual(sorted(p.name for p in (self.tmp / "records").glob("*.json")), ["R-20261023-a.json", "R-20261023-b.json"])
        self.assertEqual(list(queue.glob("*.md")), [])
        (queue / "c.md").write_text(QUESTION, encoding="utf-8")
        kw["run"] = self.runner(1, "", "RESOURCE_EXHAUSTED")
        self.assertEqual(research.run_queue(self.cfg, "agy", None, **kw), research.QUEUED)
        self.assertTrue((queue / "c.md").is_file())


class Config(unittest.TestCase):
    def test_every_backend_pins_an_exact_model(self):
        cfg = research.load_config()
        self.assertIn(cfg["default"], cfg["backends"])
        for name, b in cfg["backends"].items():
            with self.subTest(backend=name):
                self.assertEqual(b["model_flag"], "--model")
                self.assertRegex(b["model"], r"^(claude|gemini)-[a-z0-9.-]+$")

    def test_the_schema_and_the_shape_check_agree_on_the_keys(self):
        schema = json.loads((research.ROOT / research.load_config()["schema"]).read_text(encoding="utf-8"))
        item = schema["properties"]["claims"]["items"]
        self.assertEqual(tuple(item["required"]), research.CLAIM_KEYS)
        self.assertEqual(tuple(item["properties"]["kind"]["enum"]), research.KINDS)
        self.assertEqual(tuple(item["properties"]["confidence"]["enum"]), research.CONFIDENCE)


if __name__ == "__main__":
    unittest.main()
