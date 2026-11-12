"""B の会話の形（bench_chain run --chat。v3 §15 の 11-12 その 3）と、両腕に同じ進め方（What の「## 進め方」）。
段の最後の実装役の呼び出しの会話を、次の段の呼び出しが続ける。鎖のホームは鎖を書き終えたら消す。箱と H ラインは呼ばない（差し替える）。"""
import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import chain_compare  # noqa: E402

INFO = {"title": "題", "kind": "足す", "reqs": [("REQ-01", "REQ-01：何かを足す")]}


def ev(name, body):
    return json.dumps({"event": name, name: body})


def write_log(rundir, conv, name="implementer-0-1.log", other=None):
    log = Path(rundir) / "items" / "t-p-01" / name
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [ev("init", {"conversation_id": conv, "model": "m"})]
    if other:
        lines.append(json.dumps({"event": "step_update", "step_update": {"conversation_id": other, "state": "DONE"}}))
    lines.append(ev("result", {"conversation_id": conv, "status": "SUCCESS"}))
    log.write_text("{}" + bench_run.LOG_SPLIT + "\n".join(lines) + bench_run.LOG_SPLIT, encoding="utf-8")


class Guidance(unittest.TestCase):
    def test_both_arms_get_the_same_way_of_working_in_the_what(self):
        what = bench_chain.what_text("B07", "pkg", 2, INFO, ["pkg/mod.py", "tests/test_pkg_s2.py"])
        head, _, tail = what.partition("## 進め方")
        self.assertIn("## 合格条件", head)
        self.assertIn("`pkg/`", tail)
        self.assertIn("`tests/test_pkg*.py`", tail)
        self.assertIn("1 本のスクリプトにまとめて", tail)
        self.assertIn("読み直さなくてよい", tail)
        for harness_only in ("影響テスト", "次の試行", "判定に通りませんでした"):
            self.assertNotIn(harness_only, what)


class LastConversation(unittest.TestCase):
    def test_the_latest_call_of_the_stage_is_taken(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bench_chain.last_conversation(tmp))
            write_log(tmp, "c-first", "implementer-0-1.log")
            write_log(tmp, "c-second", "implementer-0-2.log", other="c-sub")
            write_log(tmp, "c-old", "implementer-0-0.log")
            self.assertEqual(bench_chain.last_conversation(tmp), "c-second")


class StageMark(unittest.TestCase):
    def test_the_chat_state_reaches_the_line_box_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(task="B07", run_id="t-s2", repo=tmp, base="HEAD", out=tmp, sessions=tmp, plan=None, implementer=None)
            chat = {"home": "box-chat-t-b07", "conversation": "c1"}
            st = bench_chain.ChainStage(ns, "S", Path(tmp) / "spec.md", "pkg", {}, chat)
            self.assertEqual(st.box_mark({"x": 1})["box"], {"package": "pkg", "home": "box-chat-t-b07", "conversation": "c1"})
            plain = bench_chain.ChainStage(ns, "S", Path(tmp) / "spec.md", "pkg")
            self.assertEqual(plain.box_mark({})["box"], {"package": "pkg"})


class Chain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.staged, self.out = self.root / "staged", self.root / "out"
        sha = {}
        for k in (1, 2):
            spec = self.staged / "B07" / f"s{k}" / "spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(f"# 仕様 {k}\n", encoding="utf-8")
            sha[f"s{k}"] = hashlib.sha256(spec.read_bytes()).hexdigest()
        (self.staged / "chain.json").write_text(json.dumps({"box": True}), encoding="utf-8")
        self.entry = {"base": "B07", "package": "pkg", "stages": 2, "spec_sha256": sha}
        self.a = argparse.Namespace(tag="t", implementer=None, sessions=None, chat=True, box_memo={})
        self.seen, self.made, self.dropped = [], [], []

    def run_stage(self, stage):
        self.seen.append(stage.box_mark({})["box"])
        rundir = stage.rundir
        k = len(self.seen)
        write_log(rundir, f"c{k}")
        (rundir / "factory").mkdir(parents=True, exist_ok=True)
        (rundir / "factory" / "run.json").write_text(json.dumps({"head": "H", "start": "S", "stop": "done", "tries": 1}), encoding="utf-8")
        return 0

    def chain(self):
        with patch.object(bench_chain.box, "settings", return_value={}), \
                patch.object(bench_chain.box, "home_volume", side_effect=lambda cfg, name: self.made.append(name) or name), \
                patch.object(bench_chain.box, "drop_home", side_effect=lambda cfg, name: self.dropped.append(name)), \
                patch.object(bench_chain, "s0_commit", return_value="S0"), patch.object(bench_chain, "fetch_tip", return_value="T"), \
                patch.object(bench_chain, "spec_info", return_value=INFO), patch.object(bench_chain, "changeable", return_value=["pkg/m.py"]), \
                patch.object(bench_chain, "start_commit", return_value="S"), \
                patch.object(bench_chain, "write_plan", side_effect=lambda d, *a: d), \
                patch.object(bench_chain, "run_stage", side_effect=self.run_stage):
            return bench_chain.run_chain(self.a, self.root, self.out, self.staged, "R", self.entry, "dev")

    def test_the_next_stage_continues_the_last_conversation_and_the_home_is_dropped(self):
        rec = self.chain()
        self.assertEqual([s.get("conversation") for s in self.seen], [None, "c1"])
        self.assertEqual({s.get("home") for s in self.seen}, {"box-chat-t-b07"})
        self.assertEqual((self.made, self.dropped), (["box-chat-t-b07"], ["box-chat-t-b07"]))
        self.assertEqual((rec["arm"], rec["conversation"]), ("harness-chat", "c2"))

    def test_resuming_takes_the_conversation_from_the_left_stage(self):
        rundir = bench_run.run_dir(self.out, "B07", "t-s1")
        write_log(rundir, "c-left")
        (rundir / "factory").mkdir(parents=True)
        (rundir / "factory" / "run.json").write_text(json.dumps({"head": "H", "start": "S", "stop": "done"}), encoding="utf-8")
        self.chain()
        self.assertEqual([s.get("conversation") for s in self.seen], ["c-left"])

    def test_chat_needs_the_box(self):
        (self.staged / "chain.json").write_text(json.dumps({}), encoding="utf-8")
        with self.assertRaises(bench_chain.BenchError):
            self.chain()

    def test_without_chat_nothing_changes(self):
        self.a.chat = False
        rec = self.chain()
        self.assertEqual(self.seen, [{"package": "pkg"}, {"package": "pkg"}])
        self.assertNotIn("arm", rec)
        self.assertEqual(self.made, [])

    def test_the_command_line_has_the_chat_switch(self):
        with patch.object(bench_chain, "run", side_effect=lambda a: 0 if a.chat else 1):
            self.assertEqual(bench_chain.main(["run", "--tag", "t", "--repo", "r", "--base", "b", "--staged", "s", "--chat"]), 0)


class Arms(unittest.TestCase):
    def test_the_chat_arms_raise_no_warning(self):
        run = {"bases": {"B07": {}}, "set": "dev"}
        a = [{**run, "arm": "solo"}, {**run, "arm": "chat"}]
        b = [{**run, "arm": None}, {**run, "arm": "harness-chat"}]
        self.assertEqual(chain_compare.warnings_of(a, b), [])
        self.assertEqual(len(chain_compare.warnings_of(b, a)), 1)


if __name__ == "__main__":
    unittest.main()
