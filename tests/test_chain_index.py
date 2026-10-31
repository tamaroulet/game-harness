"""鎖の索引（chain_score.item_lines）の最後の手番。文の無い応答の手番を数えないこと（10-31 の煙の試しで、30 手番のうち 15 が文なし）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import chain_score  # noqa: E402


def step(i, kind, text="", tool=None):
    su = {"step_index": i, "state": "DONE", "step_type": kind, "text_delta": text}
    if tool:
        su["tool_info"] = {"name": tool, "parameters": {"AbsolutePath": f"f{i}.py"}, "output": "ok"}
    return json.dumps({"event": "step_update", "step_update": su})


class TestIndexTail(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name) / "item"
        self.d.mkdir()

    def write_log(self, events):
        body = "\n".join([json.dumps({"event": "init", "init": {"cwd": str(self.d)}}), *events])
        text = json.dumps({"summary": 1}) + bench_run.LOG_SPLIT + body + "\n" + bench_run.LOG_SPLIT + "stderr\n"
        (self.d / "implementer-0-1.log").write_text(text, encoding="utf-8")

    def tail(self):
        lines = chain_score.item_lines(self.d, {})
        at = next(i for i, x in enumerate(lines) if x.startswith("### implementer-0-1.log"))
        return [x for x in lines[at + 1:] if x.startswith("- ")]

    def test_tail_skips_responses_without_text(self):
        """道具を呼ぶだけの文の無い応答を間に挟んでも、最後の手番は道具か文のある手番だけで TAIL 個になる。"""
        events = []
        for i in range(10):
            events.append(step(2 * i, "agent_response"))
            events.append(step(2 * i + 1, "tool", tool=f"view_{i}"))
        events.append(step(20, "agent_response", "直しました"))
        self.write_log(events)
        tail = self.tail()
        self.assertEqual(len(tail), chain_score.TAIL)
        self.assertNotIn("- agent_response：", tail)
        self.assertEqual(tail[-1], "- agent_response：直しました")
        self.assertEqual([x.split("（")[0] for x in tail[:-1]], [f"- 道具 view_{i}" for i in range(5, 10)])

    def test_tail_skips_whitespace_only_text(self):
        """空白だけの文の応答も文の無い応答として数えない。手番が TAIL 個に満たなければ、ある分だけ出す。"""
        self.write_log([step(0, "agent_response", " \n"), step(1, "tool", tool="view_a"), step(2, "agent_response", "見ます")])
        self.assertEqual(self.tail(), ["- 道具 view_a（f1.py）→ ok", "- agent_response：見ます"])


if __name__ == "__main__":
    unittest.main()
