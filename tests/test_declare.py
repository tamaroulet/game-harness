"""declare（V2-4）：分解役に契約と性質の宣言を書かせ、事前投資を測る。

    python -m unittest tests.test_declare

**なぜ要るか**: 事前投資を小さくするため、分解役には道具を与えず、必要なものをすべてプロンプトで渡す
（v1 は探索のキャッシュ読みで 8.45305 USD かかった）。書いた JSON は contractgen と propgen が実際に生成できる
ところまで検査し、落ちたら同じ会話に問題の一覧だけを送って 1 回だけ出し直させる。ここでは Claude を呼ばずに、
その流れと記録の形を確かめる。
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import declare  # noqa: E402
from test_propgen import DECL, GDD, INTERFACE, SPEC  # noqa: E402

GOOD = {"interface": INTERFACE, "properties": DECL}


def usage(cost):
    return {"format": "claude", "input_tokens": 10, "output_tokens": 100, "cache_read_tokens": 0,
            "cache_creation_tokens": 50, "cost_usd": cost}


class Extract(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        text = json.dumps(GOOD)
        self.assertEqual(declare.extract(text)[0], GOOD)
        self.assertEqual(declare.extract("```json\n" + text + "\n```")[0], GOOD)

    def test_wrong_shape_is_reported(self):
        self.assertIn("interface と properties", declare.extract(json.dumps({"interface": {}}))[1])
        self.assertIn("JSON として読めません", declare.extract("{ broken")[1])


class Check(unittest.TestCase):
    def test_valid_declaration_generates(self):
        self.assertEqual(declare.check(GOOD, SPEC, GDD, "tests", "impl"), [])

    def test_contract_and_property_problems_are_reported(self):
        bad = copy.deepcopy(GOOD)
        bad["properties"]["properties"][0]["then"] = "after.Nope == 1"
        self.assertTrue(any("Nope" in p for p in declare.check(bad, SPEC, GDD, "tests", "impl")))
        bad = copy.deepcopy(GOOD)
        bad["interface"]["types"].append({"name": "GamePhase", "kind": "enum", "values": ["X"]})
        self.assertTrue(any("重複" in p for p in declare.check(bad, SPEC, GDD, "tests", "impl")))


class Prompt(unittest.TestCase):
    def test_prompt_carries_everything_and_no_tools_are_given(self):
        text = declare.build_prompt(SPEC, GDD)
        for needle in ("道具は使えません", "IsOccupied", "IReadOnlyList<Cell>", "PR-17..PR-51", "kick(m, dir)",
                       "T1：", "T5：", "## 5. 外部パラメーター", "then が「左辺 == 右辺」"):
            self.assertIn(needle, text)
        self.assertNotIn("L4", text.split("# 構造化仕様", 1)[1], "根拠の行番号は渡さない")
        seen = {}

        def fake_run(args, **kw):
            seen["args"], seen["input"] = args, kw.get("input")
            return mock.Mock(returncode=0, stdout=json.dumps({"result": "{}", "session_id": "s1",
                                                              "total_cost_usd": 0.1, "usage": {},
                                                              "modelUsage": seen.get("models", MODELS)}))
        with mock.patch.object(declare.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(declare, "_resolve", return_value="claude"):
            got = declare.call_claude("P", CFG)
        self.assertEqual(seen["args"][:4], ["claude", "-p", "--tools", ""], "道具を 1 つも与えない")
        self.assertEqual(seen["args"][4:6], ["--model", "claude-opus-5"], "モデルを明示して呼ぶ")
        self.assertEqual(seen["input"], "P", "プロンプトは標準入力で渡す")
        self.assertEqual(got[5], ["claude-haiku-4-5-20251001", "claude-opus-5"], "使われたモデルを記録する")
        # 報告が無い・固定したモデルが使われていないなら止める
        for models in ({}, {"claude-sonnet-5": {}}):
            seen["models"] = models
            with mock.patch.object(declare.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(declare, "_resolve", return_value="claude"), \
                    self.assertRaises(SystemExit):
                declare.call_claude("P", CFG)


CFG = {"cli": "claude", "headless_flag": "-p", "usage_format": "claude", "response_key": "result",
       "output_format_args": ["--output-format", "json"], "model_flag": "--model", "model": "claude-opus-5",
       "auxiliary_models": ["claude-haiku-"]}
MODELS = {"claude-opus-5": {"outputTokens": 1}, "claude-haiku-4-5-20251001": {"outputTokens": 1}}


class Flow(unittest.TestCase):
    def run_declare(self, replies):
        calls = []

        def fake_call(prompt, cfg, session=None):
            calls.append((prompt, session))
            text, cost = replies[len(calls) - 1]
            return 0, text, usage(cost), 12.5, "s1", ["claude-opus-5"]
        out = Path(tempfile.mkdtemp())
        reader = {"docs/spec/spec.md": SPEC, "docs/gdd/source.md": GDD}
        with mock.patch.object(declare, "call_claude", side_effect=fake_call), \
                mock.patch.object(declare.unit_schema, "git_reader",
                                  return_value=lambda p: reader[p]), \
                mock.patch.object(declare.project, "config",
                                  side_effect=lambda n: {"spec_path": "docs/spec/spec.md",
                                                         "gdd_path": "docs/gdd/source.md"} if n == "unit_schema" else CFG), \
                mock.patch.object(declare, "build_prompt", return_value="PROMPT"):
            rec = declare.declare("falling-blocks", out)
        return rec, calls, out

    def test_first_reply_passes(self):
        rec, calls, out = self.run_declare([(json.dumps(GOOD), 0.4)])
        self.assertTrue(rec["ok"])
        self.assertEqual((rec["total"]["calls"], rec["total"]["cost_usd"], rec["total"]["seconds"]), (1, 0.4, 12.5))
        self.assertEqual(json.loads((out / "properties.json").read_text(encoding="utf-8")), DECL)
        self.assertEqual(json.loads((out / "contract.json").read_text(encoding="utf-8")), INTERFACE)
        self.assertEqual(json.loads((out / "results" / "precost.json").read_text(encoding="utf-8"))["total"]["calls"], 1)
        pre = json.loads((out / "results" / "precost.json").read_text(encoding="utf-8"))
        self.assertEqual((pre["model_requested"], pre["models_used"]), ("claude-opus-5", ["claude-opus-5"]),
                         "事前投資の記録に、要求したモデルと使われたモデルを残す")

    def test_retry_sends_only_the_problems_in_the_same_conversation(self):
        bad = copy.deepcopy(GOOD)
        bad["properties"]["properties"][0]["then"] = "after.Nope == 1"
        rec, calls, out = self.run_declare([(json.dumps(bad), 0.4), (json.dumps(GOOD), 0.1)])
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["total"]["cost_usd"], 0.5)
        self.assertEqual(calls[1][1], "s1", "--resume で同じ会話に送る")
        self.assertIn("Nope", calls[1][0])
        self.assertNotIn("PROMPT", calls[1][0], "最初のプロンプトを送り直さない")

    def test_second_failure_writes_no_declaration(self):
        bad = copy.deepcopy(GOOD)
        bad["properties"]["properties"][0]["then"] = "after.Nope == 1"
        rec, calls, out = self.run_declare([(json.dumps(bad), 0.4), (json.dumps(bad), 0.1)])
        self.assertFalse(rec["ok"])
        self.assertEqual(len(calls), declare.RETRIES + 1)
        self.assertFalse((out / "properties.json").exists())


if __name__ == "__main__":
    unittest.main()
