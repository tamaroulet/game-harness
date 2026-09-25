"""v2.1d（docs/design/v2_1d_env_protocol.md）：作業場所の決まりを事実として示し、空回りの手番を削る。

    python -m unittest tests.test_v21d

**なぜ要るか**: v2-dry-05c の実装役は 30〜33 手番を回した。道具の引数を調べると、空回りは
- 作業場所の一覧・検索。親の harness-narrow を一覧し、ほかの走行（前の走行と、先に走った条件 A）の作業場所まで読んだ
- 仕様 ID（ST-09 など）の定義探し
- テスト・ログ・csproj・dotnet・git の確認
で、manage_task はすべて直前の端末の操作の完了待ちだった。ここでは、agy を使わずに次を縛る。
- 作業場所は呼び出しの後に消す（ほかの走行から読めない）
- プロンプトに、作業場所の一覧と決まり（何があり、何が無く、ID は何で、検証はどこか）を事実として書く
- 道具の手番に、コマンドの先頭の語・manage_task の Action・作業場所の外を指したかを残す（引数の本文は残さない）
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import agy_stream  # noqa: E402
import contractgen  # noqa: E402
import implementer_context  # noqa: E402
import narrow_dir  # noqa: E402
import pipeline  # noqa: E402
import tool_policy  # noqa: E402

IMP = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y", "model_flag": "--model",
       "model_name": "m", "output_format_args": ["--output-format", "stream-json"]}


def tool_step(i, **params):
    return json.dumps({"event": "step_update", "step_update": {
        "step_index": i, "state": "DONE", "step_type": "tool", "tool_name": "run_command",
        "tool_info": {"name": "run_command", "parameters": params, "output": "中身は残さない"}}})


class Protocol(unittest.TestCase):
    def test_states_facts_about_the_workspace(self):
        text = tool_policy.text()
        for phrase in ("がすべて", "作業場所の外", "dotnet", "性質の出典を示すラベル", "GddReference の定数",
                       "manage_task・schedule も要りません", "そのまま終えてください"):
            self.assertIn(phrase, text)

    def test_embedding_lists_every_file_in_the_workspace_first(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Core").mkdir()
            (Path(d) / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            (Path(d) / "Core" / "IGameState.cs").write_text("interface IGameState {}", encoding="utf-8")
            text = implementer_context.blocks(d, ["Core/GameState.cs", "Core/Board.cs"], ["Core/IGameState.cs"])
        self.assertIn("作業場所のファイル一覧：\n- Core/IGameState.cs\n- Core/GameState.cs\n", text)
        self.assertLess(text.index("作業場所のファイル一覧"), text.index("interface IGameState {}"))


class ToolSummary(unittest.TestCase):
    def test_records_the_verb_the_action_and_whether_it_left_the_workspace(self):
        wd = r"C:\Temp\harness-narrow\aaa"
        out = "\n".join([
            tool_step(1, CommandLine=r'Get-ChildItem "C:\Temp\harness-narrow"'),
            tool_step(2, CommandLine=r'Get-Content "C:\Temp\harness-narrow\aaa\Core\GameState.cs"'),
            tool_step(3, Action="status", TaskId="x/task-2"),
            tool_step(4, AbsolutePath=r"C:\Temp\harness-narrow\bbb\Core\GameState.cs")])
        steps = {s["index"]: s for s in agy_stream.parse(out, wd)["steps"]}
        self.assertEqual((steps[1]["verb"], steps[1]["outside"]), ("Get-ChildItem", True), "親の一覧は外")
        self.assertEqual((steps[2]["verb"], steps[2]["outside"]), ("Get-Content", False))
        self.assertEqual(steps[3]["action"], "status")
        self.assertTrue(steps[4]["outside"], "ほかの走行の作業場所は外")
        self.assertNotIn("中身は残さない", json.dumps(steps, ensure_ascii=False))
        self.assertNotIn("GameState.cs", json.dumps(steps, ensure_ascii=False), "引数の本文は残さない")


class Relocate(unittest.TestCase):
    """v2-smoke-01：再試行の指示に入った作業ツリーのパスをたどって、A が作業場所の外のテストを読んだ。"""

    def test_origin_paths_in_feedback_become_workspace_paths(self):
        with tempfile.TemporaryDirectory() as d:
            origin = Path(d) / "wt"
            dst = Path(d) / narrow_dir.ROOT_NAME / "x"
            text = (f"{origin}\\tests\\T.cs(12,5): error\n{str(origin).replace(chr(92), '/')}/Core/A.cs:3\n"
                    f"{str(origin).upper()}\\X.cs")
            out = narrow_dir.relocate(text, origin, dst)
        self.assertNotIn(str(origin).lower(), out.lower())
        self.assertIn(f"{dst}\\tests\\T.cs(12,5)", out)
        self.assertEqual(out.count(str(dst)), 3)
        self.assertEqual(narrow_dir.relocate("x", origin, None), "x")

    def test_condition_a_retry_prompt_carries_no_worktree_path(self):
        import shutil
        from ab import common, driver
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = common.load_manifest(ROOT / "experiments" / "b4_ab" / "tasks.json")
        unit = dict(common.unit_of(m, m["tasks"][0]), whitelist=["Core/GameState.cs"])
        wt = tmp / "wt"
        (wt / "Core").mkdir(parents=True)
        (wt / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
        v2 = json.loads((ROOT / "experiments" / "v2" / "tasks.json").read_text(encoding="utf-8"))
        ctx = {"m": m, "wt": wt, "out": tmp / "out", "ttl": 60, "impl_dir": "Core", "imp": IMP,
               "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
               "templates": {k: (ROOT / "experiments" / "v2" / v2["templates"][k]).read_text(encoding="utf-8")
                             for k in ("initial", "retry")}}
        ctx["out"].mkdir(parents=True)
        prompts = []

        def call(imp, prompt, cwd, cid, ttl):
            prompts.append(prompt)
            return {"rc": 0, "seconds": 1.0, "conversation_id": "c", "usage": {}, "out": "", "err": ""}

        # V2-6 からは、再試行の知らせは判定（pipeline.judge）の知らせ。そこに出るパスも置き換える
        def judge(n):
            return ("SUCCESS", "") if n >= 2 else ("INNER", f"{wt}\\tests\\Core.Tests\\T.cs(10,1): error")
        driver.run_task_a(ctx, m["tasks"][0], unit, {}, call=call, judge=judge)
        self.assertEqual(len(prompts), 2)
        self.assertNotIn(str(wt), prompts[1])
        self.assertIn(str(narrow_dir.path_for(wt)), prompts[1])


class OwnState(unittest.TestCase):
    """v2-smoke-02：実装役が自分の会話の記録を読むのは外に数えない。ほかの会話の記録は外。"""

    def test_own_conversation_record_is_not_outside(self):
        wd = r"C:\Temp\harness-narrow\aaa"
        own = agy_stream.AGY_BRAIN / "conv-1" / ".system_generated" / "logs" / "t.md"
        other = agy_stream.AGY_BRAIN / "conv-2" / ".system_generated" / "logs" / "t.md"
        out = "\n".join([
            json.dumps({"event": "init", "conversation_id": "conv-1"}),
            tool_step(1, AbsolutePath=str(own)), tool_step(2, AbsolutePath=str(other))])
        steps = {s["index"]: s for s in agy_stream.parse(out, wd)["steps"]}
        self.assertFalse(steps[1]["outside"])
        self.assertTrue(steps[2]["outside"])


class DiscardAfterCall(unittest.TestCase):
    def test_pipeline_removes_the_workspace_after_writing_back(self):
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            sb = Path(d)
            (sb / "Core").mkdir()
            (sb / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            (sb / "Core" / "IGameState.cs").write_text(contractgen.HEADER + "\ninterface IGameState {}",
                                                       encoding="utf-8")
            c = SimpleNamespace(unit={"prompt": "作る", "whitelist": ["Core/GameState.cs"]}, sandbox=sb,
                                sb=lambda rel: sb / rel, cfg={"implementer": IMP, "project": {"impl_dir": "Core"}},
                                ttl={"implementer": 300}, metrics={}, cur={})

            def fake_run(args, cwd, ttl, label, input=None):
                seen["cwd"], seen["prompt"] = Path(cwd), json.loads(input)["message"]["content"]
                (Path(cwd) / "Core" / "GameState.cs").write_text("class GameState : IGameState {}", encoding="utf-8")
                return 0, "", ""
            with mock.patch.object(pipeline, "run", side_effect=fake_run), \
                    mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                    mock.patch.object(pipeline, "write_implementer_log"):
                pipeline.call_implementer(c, feedback=f"{sb}\\tests\\T.cs(3,1): error")
            self.assertEqual((sb / "Core" / "GameState.cs").read_text(encoding="utf-8"),
                             "class GameState : IGameState {}", "書き戻してから消す")
        self.assertFalse(seen["cwd"].exists(), "呼び出しの後に作業場所を残さない")
        self.assertIn(tool_policy.PROTOCOL, seen["prompt"])
        self.assertIn("- Core/IGameState.cs", seen["prompt"])
        self.assertNotIn(str(sb), seen["prompt"], "反例・出力のサンドボックスのパスも作業場所に置き換える")
        self.assertIn(f"{seen['cwd']}\\tests\\T.cs(3,1)", seen["prompt"])

    def test_discard_refuses_other_directories(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                narrow_dir.discard(Path(d))


if __name__ == "__main__":
    unittest.main()
