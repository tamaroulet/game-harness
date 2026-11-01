"""提案役の起こし方（harness/proposer_launch.py）：関所・起こす引数・出力の読み取り・道の検め・提案の形・モデルの固定の検査。

本物の CLI・記録は使わない。一時ディレクトリの偽の設定・記録・指示と、proc.run・resolve_cli の差し替えだけで書く。
"""
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
import hline_base  # noqa: E402
import model_pin  # noqa: E402
import proposer_launch as pl  # noqa: E402

REAL = json.loads((HERE.parent / "config" / "proposer.json").read_text(encoding="utf-8"))
MODEL = REAL["model"]
NOW = datetime.datetime(2026, 10, 19, 3, 0, 0)
GOOD = ("前置きの文\n\n## 狙う失敗の型\n型の説明\n## 出た段\n- B01 s2\n## 直し\n直しの説明\n## 予想\n予想の説明\n"
        "## 補う弱さ\n弱さの説明\n## 外す条件\n条件の説明\n")


def stream(text=GOOD, models=None, turns=3, subtype="success", tokens=(100, 50), uses=(), model_usage=True, result=True):
    rows = [{"type": "system", "subtype": "init", "model": MODEL, "cwd": "x", "tools": ["Read", "Grep", "Glob"]}]
    rows += [{"type": "assistant", "message": {"model": MODEL, "content": [
        {"type": "text", "text": "読みます"}, {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}
        for i, (name, inp) in enumerate(uses)]
    end = {"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": turns, "result": text,
           "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1], "cache_read_input_tokens": 0,
                     "cache_creation_input_tokens": 0}}
    if model_usage:
        end["modelUsage"] = {m: {} for m in (models if models is not None else [MODEL])}
    rows += [end] if result else []
    return "\n".join(["読めない行"] + [json.dumps(r, ensure_ascii=False) for r in rows])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root, self.out, self.oracles = self.tmp / "repo", self.tmp / "out", self.tmp / "oracles"
        self.chains = self.out / "chains" / "v1"
        for d in (self.root, self.oracles, self.chains / "B01"):
            d.mkdir(parents=True)
        (self.chains / "B01" / "s2.json").write_text("{}", encoding="utf-8")
        (self.chains / "B01" / "s2.index.md").write_text("索引\n", encoding="utf-8")
        self.prompt = self.tmp / "proposer.md"
        self.prompt.write_text("提案を書いてください。\n", encoding="utf-8")
        patch = mock.patch.object(pl, "PROMPT", self.prompt)
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = {"out": str(self.out), "oracles": str(self.oracles)}
        self.agent = json.loads(json.dumps(REAL))

    def launch(self, result=None, tag="v1", extra=(), at=NOW):
        result = result if result is not None else (0, stream(), "")
        with mock.patch.object(pl.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(pl.proc, "run", return_value=result) as run:
            rec = pl.launch(self.cfg, self.agent, tag, extra, now=at, root=self.root)
        return rec, run

    def saved(self):
        return json.loads(sorted((self.out / "proposer").glob("*.json"))[-1].read_text(encoding="utf-8"))


class Gates(Base):
    def assert_skipped(self, rec, run, words):
        run.assert_not_called()
        self.assertFalse(rec["launched"])
        self.assertIn(words, rec["skipped_reason"])
        self.assertEqual(self.saved()["skipped_reason"], rec["skipped_reason"])
        self.assertFalse(pl.ok(rec))

    def test_without_the_instruction_file_nothing_is_launched(self):
        self.prompt.unlink()
        self.assert_skipped(*self.launch(), "指示のファイルがありません")

    def test_without_the_dev_chain_record_nothing_is_launched(self):
        self.assert_skipped(*self.launch(tag="v9"), "開発の組の記録がありません")

    def test_a_root_under_oracles_is_refused(self):
        self.assert_skipped(*self.launch(extra=[self.oracles / "chains"]), "oracles と重なります")

    def test_a_root_containing_oracles_is_refused(self):
        self.assert_skipped(*self.launch(extra=[self.tmp]), str(self.tmp))

    def test_a_missing_model_stops_before_launch_and_is_recorded(self):
        del self.agent["model"]
        with self.assertRaises(model_pin.ModelPinError):
            self.launch()
        self.assertIn("モデルの固定の誤り", self.saved()["skipped_reason"])
        self.assertFalse(self.saved()["launched"])

    def test_a_model_in_auxiliary_models_stops(self):
        self.agent["auxiliary_models"] = ["claude-sonnet-"]
        with self.assertRaises(model_pin.ModelPinError):
            self.launch()


class Args(Base):
    def test_caps_model_tools_and_add_dir_for_each_root_with_root_as_cwd(self):
        extra = self.tmp / "notes"
        rec, run = self.launch(extra=[extra])
        args, cwd, ttl = run.call_args.args[:3]
        self.assertEqual(Path(cwd), self.root)
        self.assertEqual(ttl, REAL["proposer_budget"]["ttl_seconds"])
        self.assertEqual(args[args.index("--max-turns") + 1], str(REAL["proposer_budget"]["max_turns"]))
        self.assertEqual(args[args.index("--model") + 1], MODEL)
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read,Grep,Glob")
        self.assertIn("Bash", args[args.index("--disallowedTools") + 1].split(","))
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--permission-mode", args)
        dirs = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--add-dir"]
        self.assertEqual(dirs, [self.chains, extra])
        self.assertEqual([Path(r) for r in rec["roots"]], [self.root, self.chains, extra])

    def test_the_prompt_is_the_instruction_then_the_material_and_is_saved(self):
        rec, run = self.launch()
        text = run.call_args.kwargs["input"]
        self.assertTrue(text.startswith("提案を書いてください。\n"))
        material = text[text.index("## 材料"):]
        self.assertIn("- tag: v1", material)
        self.assertIn(str(self.chains / "B01" / "s2.index.md"), material)
        self.assertIn(str(self.root), material)
        for h in pl.HEADINGS:
            self.assertIn(h, material)
        saved = list((self.out / "proposer").glob("*.prompt.txt"))
        self.assertEqual(saved[0].read_text(encoding="utf-8"), text)
        self.assertTrue(list((self.out / "proposer").glob("*.log")))


class Output(Base):
    def test_a_normal_run_reads_usage_and_model_from_the_result_line_and_writes_the_proposal(self):
        rec, _ = self.launch()
        self.assertEqual((rec["end"], rec["usage"]["total_tokens"], rec["usage"]["num_turns"]), ("正常", 150, 3))
        self.assertEqual(rec["models"], [MODEL])
        self.assertEqual((rec["path_hits"], rec["shape_errors"]), ([], []))
        body = Path(rec["proposal"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("## 狙う失敗の型\n"))
        self.assertNotIn("前置き", body)
        self.assertTrue(pl.ok(rec))
        self.assertEqual(self.saved(), rec)
        self.assertEqual(len(pl.line_of(rec).splitlines()), 1)

    def assert_cli_error(self, out):
        rec, _ = self.launch(result=(0, out, ""))
        self.assertEqual(rec["end"], "CLI の誤り")
        self.assertIsNone(rec["proposal"])
        self.assertFalse(pl.ok(rec))
        return rec

    def test_no_model_usage_stops_as_a_cli_error(self):
        self.assertIn("modelUsage", self.assert_cli_error(stream(model_usage=False))["error"])

    def test_no_result_line_stops_as_a_cli_error(self):
        self.assertIn("result の行", self.assert_cli_error(stream(result=False))["error"])

    def test_a_different_model_stops_as_a_cli_error(self):
        self.assert_cli_error(stream(models=["claude-opus-5-5"]))

    def test_a_turn_cutoff_is_recorded_as_such(self):
        rec, _ = self.launch(result=(1, stream(turns=60, subtype="error_max_turns", models=[]), ""))
        self.assertEqual(rec["end"], "ターンの上限")
        self.assertEqual(rec["usage"]["num_turns"], 60)
        self.assertIsNone(rec["proposal"])

    def test_ttl_is_recorded_as_such(self):
        rec, _ = self.launch(result=(124, stream(result=False), "TTL超過 (3600s): 提案役"))
        self.assertEqual(rec["end"], "TTL")
        self.assertFalse(pl.ok(rec))

    def test_usage_over_max_tokens_is_marked(self):
        self.agent["proposer_budget"]["max_tokens"] = 100
        rec, _ = self.launch()
        self.assertTrue(rec["token_excess"])
        self.assertEqual(rec["end"], "正常")
        self.assertIn("超過", pl.line_of(rec))


class PathAudit(Base):
    def hits(self, uses):
        rec, _ = self.launch(result=(0, stream(uses=uses), ""))
        if rec["path_hits"]:
            self.assertIsNone(rec["proposal"])
            self.assertFalse(pl.ok(rec))
        return rec["path_hits"]

    def test_reads_inside_the_roots_have_no_hits(self):
        inside = str(self.root / "harness" / "a.py")
        self.assertEqual(self.hits([
            ("Read", {"file_path": inside}),
            ("Read", {"file_path": inside.replace("/", "\\")}),
            ("Read", {"file_path": inside.upper()}),
            ("Read", {"file_path": str(self.chains / "B01" / "s2.json")}),
            ("Read", {"file_path": "docs/a.md"}),
            ("Grep", {"pattern": "def run"}),
            ("Glob", {"pattern": "**/*.json", "path": str(self.chains)}),
        ]), [])

    def test_a_read_outside_the_roots_is_a_hit(self):
        got = self.hits([("Read", {"file_path": str(self.tmp / "elsewhere" / "a.txt")})])
        self.assertEqual(len(got), 1)
        self.assertIn("読む根の外", got[0]["why"])

    def test_a_path_escaping_with_dotdot_is_a_hit(self):
        got = self.hits([("Grep", {"pattern": "x", "path": str(self.chains) + "/../../../oracles"}),
                         ("Read", {"file_path": "../oracles/s1.json"})])
        self.assertEqual(len(got), 2)

    def test_an_absolute_pattern_outside_the_roots_is_a_hit(self):
        got = self.hits([("Glob", {"pattern": str(self.oracles) + "/**/*.json"})])
        self.assertEqual([h["name"] for h in got], ["Glob"])

    def test_a_relative_glob_escaping_with_dotdot_is_a_hit_and_grep_regex_is_not_a_path(self):
        got = self.hits([("Glob", {"pattern": "../oracles/**/*.json"}), ("Grep", {"pattern": "/chains/"})])
        self.assertEqual([h["name"] for h in got], ["Glob"])

    def test_a_tool_outside_allowed_tools_is_a_hit(self):
        got = self.hits([("Bash", {"command": "ls"})])
        self.assertIn("許していない道具: Bash", got[0]["why"])

    def test_windows_and_linux_forms_are_normalized(self):
        self.assertTrue(pl.under(pl.norm(r"C:\X\Repo\..\repo\a.py"), pl.norm("c:/x/repo")))
        self.assertFalse(pl.under(pl.norm("C:/x/repo/../oracles/a"), pl.norm(r"C:\x\repo")))
        self.assertFalse(pl.under(pl.norm("/x/repo/../oracles"), pl.norm("/x/repo")))
        self.assertFalse(pl.under(pl.norm("/x/repo2/a"), pl.norm("/x/repo")))
        self.assertTrue(pl.under(pl.norm("a/b", r"D:\w"), pl.norm("d:/w")))


class Shape(Base):
    def errors(self, text):
        return pl.shape_errors(text, self.chains)

    def test_a_good_proposal_has_no_errors(self):
        self.assertEqual(self.errors(GOOD), [])

    def test_a_missing_heading(self):
        self.assertIn("見出し ## 予想 が 0 回あります（各 1 回）", self.errors(GOOD.replace("## 予想\n予想の説明\n", "")))

    def test_a_wrong_order(self):
        text = GOOD.replace("## 直し\n直しの説明\n", "").replace("## 外す条件", "## 直し\n直しの説明\n## 外す条件")
        self.assertTrue(any("並びが違います" in e for e in self.errors(text)))

    def test_an_empty_section(self):
        self.assertIn("見出し ## 補う弱さ の中身が空です", self.errors(GOOD.replace("弱さの説明\n", "  \n")))

    def test_a_stage_without_its_record_or_in_a_wrong_form(self):
        errs = self.errors(GOOD.replace("- B01 s2\n", "- B01 s5\n- B01 stage2\n- .. s2\n"))
        self.assertIn("## 出た段 の段の記録がありません: B01 s5", errs)
        self.assertEqual(sum("行の形が違います" in e for e in errs), 2)

    def test_a_bad_shape_discards_the_proposal_and_is_recorded(self):
        rec, _ = self.launch(result=(0, stream(text=GOOD.replace("## 外す条件\n条件の説明\n", "")), ""))
        self.assertEqual(rec["end"], "正常")
        self.assertIn("見出し ## 外す条件 が 0 回あります（各 1 回）", self.saved()["shape_errors"])
        self.assertIsNone(rec["proposal"])
        self.assertFalse(list((self.out / "proposer").glob("*.proposal.md")))


class Main(Base):
    def run_main(self, tag):
        path = self.tmp / "hline.json"
        base = json.loads(hline_base.CONFIG.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**base, **self.cfg}), encoding="utf-8")
        agent = self.tmp / "proposer.json"
        agent.write_text(json.dumps(self.agent), encoding="utf-8")
        with mock.patch.object(pl, "AGENT", agent), mock.patch.object(pl, "ROOT", self.root), \
                mock.patch.object(pl.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(pl.proc, "run", return_value=(0, stream(), "")) as run, \
                mock.patch("builtins.print") as out:
            code = pl.main(["--tag", tag, "--config", str(path)])
        return code, run, out

    def test_main_returns_0_for_a_good_run_and_prints_one_line(self):
        code, run, out = self.run_main("v1")
        self.assertEqual(code, 0)
        self.assertEqual(Path(run.call_args.args[1]), self.root)
        self.assertEqual(len(out.call_args.args[0].splitlines()), 1)

    def test_main_returns_1_when_a_gate_stops(self):
        code, run, _ = self.run_main("v9")
        self.assertEqual(code, 1)
        run.assert_not_called()


class ModelPinCheck(unittest.TestCase):
    def test_model_pin_finds_a_missing_model_in_proposer(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "config").mkdir()
        cfg = {k: v for k, v in REAL.items() if k != "model"}
        (tmp / "config" / "proposer.json").write_text(json.dumps(cfg), encoding="utf-8")
        found = model_pin.problems(tmp)
        self.assertEqual(len(found), 1)
        self.assertIn("config/proposer.json", found[0])
        self.assertIn("model", found[0])

    def test_the_repository_proposer_config_is_checked_and_passes(self):
        self.assertIn("config/proposer.json", [w for w, _, _ in model_pin.agent_configs()])
        self.assertEqual([f for f in model_pin.problems() if "proposer" in f], [])


if __name__ == "__main__":
    unittest.main()
