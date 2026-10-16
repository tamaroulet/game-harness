"""A/B 実験の走行の監視（harness/ab/guard.py）と、呼び出しの終わり方の記録（agy_stream.outcome）。

    python -m unittest tests.test_guard

**なぜ要るか**: v2-smoke-02 では監視の知らせを総監督が受けられず、止めるべき後に 2 タスクが走った。v2-smoke-03・04 は
監視が自分で止める形にした（総監督のスクラッチの道具）。本走の前にハーネスに入れ、止める線引き（2026-09-25 の裁定）を
テストで縛る。あわせて、長い呼び出しの原因だった「思考だけの手番 → error_message → やり直し」と agy の ERROR を
呼び出しの記録に残す。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import agy_stream  # noqa: E402
from ab import guard  # noqa: E402

WD = r"C:\Temp\harness-narrow\aaa"


def tool(i, name, **params):
    return json.dumps({"event": "step_update", "step_update": {
        "step_index": i, "state": "DONE", "step_type": "tool", "tool_name": name,
        "tool_info": {"name": name, "parameters": params, "output": "x"}}})


class Classify(unittest.TestCase):
    def test_stop_on_reading_outside_or_building(self):
        text = "\n".join([tool(1, "view_file", AbsolutePath=r"C:\src\falling-blocks\tests\T.cs"),
                          tool(2, "run_command", CommandLine=r'Get-Content "C:\Temp\other\GameState.cs"'),
                          tool(3, "run_command", CommandLine="dotnet test"),
                          tool(4, "run_command", CommandLine=r'Get-ChildItem "C:\Temp"'),
                          tool(5, "view_file", AbsolutePath=WD + r"\Core\GameState.cs")])
        stops, notes = guard.classify(text, WD)
        self.assertEqual(len(stops), 3, "外の中身を読む 2 つと、ビルド・テスト 1 つ")
        self.assertEqual(len(notes), 1, "外の一覧は記録だけ")

    def test_own_conversation_record_is_not_outside(self):
        own = agy_stream.AGY_BRAIN / "conv-1"
        text = tool(1, "view_file", AbsolutePath=str(own / ".system_generated" / "logs" / "t.md"))
        self.assertEqual(guard.classify(text, WD, (own,)), ([], []))


class Outcome(unittest.TestCase):
    def step(self, i, stype, think=None, out=None):
        su = {"step_index": i, "state": "DONE", "step_type": stype}
        if out is not None:
            su["usage"] = {"input_tokens": 10, "output_tokens": out, "thinking_tokens": think}
        return json.dumps({"event": "step_update", "step_update": su})

    def test_thinking_only_step_error_step_and_status_are_recorded(self):
        text = "\n".join([self.step(1, "agent_response", 13122, 13122), self.step(2, "error_message"),
                          self.step(3, "agent_response", 500, 900),
                          json.dumps({"event": "result", "result": {
                              "status": "ERROR", "error": "Your previous response was cut off\nRetries remaining: 3",
                              "usage": {"input_tokens": 20}}})])
        oc = agy_stream.parse(text)["outcome"]
        self.assertEqual(oc, {"status": "ERROR", "error": "Your previous response was cut off",
                              "error_steps": 1, "thinking_only_steps": 1})

    def test_cut_call_has_no_status(self):
        self.assertIsNone(agy_stream.parse(self.step(1, "agent_response", 5, 9))["outcome"]["status"])


if __name__ == "__main__":
    unittest.main()
