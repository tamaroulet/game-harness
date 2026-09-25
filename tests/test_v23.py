"""v2.3（docs/design/v2_2_architecture_self_critique.md §4.1 の案 1）：狭い文脈の原則 N1・N2・N4・N7 の最初の適用。

    python -m unittest tests.test_v23

**なぜ要るか**: v2 のハーネスは、門は決定論的なのに、実装役に渡す文面が深読みを誘っていた（自己批判 §2）。
- F1・N1：自然言語の要求が、性質の無い仕様 ID を名指していた（56 のうち 13）。T1 は保存則（RL-36）を性質なしで求めた
- F3・N2：性質が使う関数（fits・moved・rotated・kick）の定義を渡していなかった
- F4・N4：interface の描画が、中身を埋め込む既存の型と契約を「作る型」として二重に並べていた
- F5：同じ趣旨の指示が 4 か所に散り、答え方も揺れていた
ここでは、agy を使わずに次を縛る。
- 実装役の prompt は、型・性質・参照データと、関数の定義だけで閉じる（語彙の閉包を v2prep が機械で検査する）
- interface は、中身を埋め込まない型だけを描く
- 指示は作業場所の決まり（tool_policy）の 1 か所
- v2-smoke-05 の門（smoke_gate）は、健全性と呼び出しの終わり方を見る
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "experiments" / "v2" / "tools"))

import implementer_context  # noqa: E402
import propgen  # noqa: E402
import smoke_gate  # noqa: E402
import testgen  # noqa: E402
import tool_policy  # noqa: E402
import unit_schema  # noqa: E402
from ab import v2prep  # noqa: E402

V2 = ROOT / "experiments" / "v2"


def units():
    return {t: json.loads((V2 / "units" / f"{t}.json").read_text(encoding="utf-8")) for t in ("T1", "T2", "T3", "T4", "T5")}


def props():
    return json.loads((V2 / "properties.json").read_text(encoding="utf-8"))["properties"]


class Vocabulary(unittest.TestCase):
    def test_every_function_has_one_definition_that_passes_the_prompt_gate(self):
        self.assertEqual(set(propgen.FUNC_DOCS), set(propgen.FUNCS))
        for text in list(propgen.FUNC_DOCS.values()) + [propgen.EXPR_VARS]:
            for tok in unit_schema.PROMPT_FORBIDDEN:
                self.assertNotIn(tok, text)

    def test_renders_only_the_functions_the_properties_use(self):
        text = propgen.render_vocabulary(["fits(moved(before.ActiveMino, -1, 0))", "after.ActiveMino.X == 1"])
        self.assertIn(propgen.FUNC_DOCS["fits"], text)
        self.assertIn(propgen.FUNC_DOCS["moved"], text)
        self.assertNotIn(propgen.FUNC_DOCS["kick"], text)
        self.assertIn(propgen.EXPR_VARS, text)

    def test_closure_check_finds_dangling_ids_and_undefined_functions(self):
        shown = [{"rule": "RL-16", "given": "fits(before.ActiveMino)", "then": "true"}]
        self.assertEqual(v2prep.vocabulary_problems("RL-16 と RL-36", shown),
                         ["性質の無い仕様 ID RL-36", "定義の無い関数 fits"])
        self.assertEqual(v2prep.vocabulary_problems("RL-16 " + propgen.FUNC_DOCS["fits"], shown), [])


class Units(unittest.TestCase):
    """v2prep が書いた単位定義（experiments/v2/units）が閉じていること。"""

    def test_the_prompts_are_closed_and_carry_no_natural_language_requirement(self):
        ps = props()
        for t, u in units().items():
            n = int(t[1:])
            shown = [p for p in ps if int(p["task"][1:]) <= n]
            self.assertEqual(v2prep.vocabulary_problems(u["prompt"], shown), [], t)
            self.assertTrue(u["prompt"].startswith(f"## タスク：{u['title']}"), t)
            for heading in ("## 要求", "## 満足の基準", "範囲に含めるもの", "保存則"):
                self.assertNotIn(heading, u["prompt"], t)

    def test_t1_no_longer_names_rl36(self):
        self.assertNotIn("RL-36", units()["T1"]["prompt"])

    def test_the_a_templates_leave_instructions_to_the_protocol(self):
        initial = (V2 / "templates" / "initial.md").read_text(encoding="utf-8")
        for dup in ("## 受入", "## 書き換えてよいファイル", "計画・実装案", "変更したファイルの一覧"):
            self.assertNotIn(dup, initial)
        self.assertIn("計画・実装案・確認は返しません", tool_policy.text())
        self.assertIn("変更したファイルの一覧だけを答えて", tool_policy.text())


class InterfaceScope(unittest.TestCase):
    UNIT = {"whitelist": ["Core/GameState.cs"], "interface": {"types": [
        {"kind": "enum", "name": "MinoType", "values": ["I", "O"]},
        {"kind": "struct", "name": "Cell", "members": []},
        {"kind": "class", "name": "GameState", "members": [{"kind": "method", "name": "Tick", "type": "void",
                                                             "params": []}]},
        {"kind": "class", "name": "Fresh", "members": []}]}}

    def test_types_whose_file_is_embedded_are_not_drawn_twice(self):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "Core"
            core.mkdir()
            (core / "Cell.cs").write_text(implementer_context.CONTRACT_MARKER + "\nstruct Cell {}", encoding="utf-8")
            (core / "MinoType.cs").write_text("enum MinoType { I, O }", encoding="utf-8")
            (core / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            only = implementer_context.interface_scope(d, self.UNIT, "Core")
        self.assertEqual(only, {"GameState", "Fresh"}, "書き換えてよい型と、ファイルの無い型だけ")
        text = testgen.render_interface(self.UNIT, only=only)
        self.assertIn("class GameState", text)
        self.assertIn("class Fresh", text)
        self.assertNotIn("MinoType", text)
        self.assertNotIn("struct Cell", text)
        self.assertIn("MinoType", testgen.render_interface(self.UNIT), "only が無ければ従来どおり全部")


def agy_ok(tool="write_to_file", error=False):
    lines = [json.dumps({"event": "step_update", "step_update": {"step_index": 1, "step_type": "tool_call", "tool_name": tool,
                                                                 "state": "DONE", "usage": {"output_tokens": 10,
                                                                                            "thinking_tokens": 5}}})]
    if error:
        lines.append(json.dumps({"event": "step_update", "step_update": {"step_index": 2, "step_type": "error_message",
                                                                         "state": "DONE"}}))
    lines.append(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "", "conversation_id": "c"}}))
    return "\n".join(lines)


class SmokeGate(unittest.TestCase):
    def build(self, d, accepted=True, error=False):
        m = {"tasks": [{"id": "T1"}]}
        (Path(d) / "tasks.json").write_text(json.dumps(m), encoding="utf-8")
        row = {"task": "T1", "accepted": accepted, "p2p_broken": 0, "tests_tampered": [],
               "invariants": {"failures": 0, "build_ok": True}}
        for cond in ("A", "B"):
            c = Path(d) / "r" / cond
            c.mkdir(parents=True)
            (c / "metrics.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (Path(d) / "r" / "A" / "T1_a1.implementer.log").write_text(
            f"# prompt\nx\n\n# stdout\n{agy_ok(error=error)}\n\n# stderr\n", encoding="utf-8")
        (Path(d) / "r" / "B" / "T1").mkdir()
        (Path(d) / "r" / "B" / "T1" / "implementer_attempt_1.log").write_text(
            f"=== プロンプト ===\nx\n\n=== stdout ===\n{agy_ok()}\n\n=== stderr ===\n", encoding="utf-8")
        return smoke_gate.problems("r", out_root=d, manifest=Path(d) / "tasks.json")

    def test_passes_a_healthy_run_and_records_the_first_tool(self):
        with tempfile.TemporaryDirectory() as d:
            found, notes = self.build(d)
        self.assertEqual(found, [])
        self.assertIn("A: 最初の道具が編集だった呼び出し 1/1", notes)

    def test_fails_on_rejection_and_on_error_message_steps(self):
        with tempfile.TemporaryDirectory() as d:
            found, _ = self.build(d, accepted=False, error=True)
        self.assertIn("A/T1: 受入に通っていません", found)
        self.assertIn("A/T1 呼び出し 1: error_message の手番 1", found)


if __name__ == "__main__":
    unittest.main()
