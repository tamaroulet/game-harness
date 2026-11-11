"""素のエージェント A の会話の形（bench_solo run --chat。v3 §15 の 11-12 その 2）：鎖ごとに 1 つの会話を続ける。
2 段目からは前置きを外した次の頼みを同じ会話に送り、走り直しは同じ頼みの送り直しと書く。会話の ID は呼び出しが落ちても run に残り、
鎖のホーム（名前つきボリューム）は鎖を書き終えたら消す。箱と実装役は呼ばない（差し替える）。"""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import bench_solo  # noqa: E402
import hline_agy  # noqa: E402
from hline_base import Infra, save_prompt  # noqa: E402

MODEL = "model-under-test"
AGENT = {"cli": "agy", "model": MODEL, "model_name": MODEL}
HEAD = bench_solo.PROMPT.read_text(encoding="utf-8").strip()
FULL = f"{HEAD}\n\n---\n# 題\n- 検証コマンド: python -m unittest\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def ctx_of(out, **kw):
    return types.SimpleNamespace(tag="t", out=out, base="B07", tip="T", rec={"stages": []}, chat=True, arm=bench_solo.CHAT_ARM,
                                 home="box-chat-t-b07", conversation=None, **kw)


class Ask(unittest.TestCase):
    def test_the_first_ask_has_the_preamble_and_later_asks_continue_the_conversation(self):
        asked = []

        def solo(ctx, run_id, start, ask):
            asked.append(ask)
            return {"base": "T", "start": "S", "head": "H", "stop": "done", "tries": 1, "tokens": {"total": 5}, "conversation": "c1"}

        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_of(Path(tmp))
            with patch.object(bench_solo, "stage_inputs", return_value=("S", FULL)), patch.object(bench_solo, "solo_stage", solo):
                bench_solo.run_step(ctx, 1)
                bench_solo.run_step(ctx, 2)
        self.assertEqual(asked[0], FULL)
        self.assertTrue(asked[1].startswith(bench_solo.FOLLOWUP))
        self.assertNotIn(HEAD, asked[1])
        self.assertIn("# 題", asked[1])
        self.assertEqual(ctx.conversation, "c1")

    def test_a_retry_resends_the_same_ask_in_the_same_conversation(self):
        asked, results = [], [{"invalid": "REQ-22", "reason": "途中で切れた", "start": "S", "head": None, "conversation": "c1"},
                              {"base": "T", "start": "S", "head": "H", "stop": "done", "tries": 1, "tokens": {"total": 5}, "conversation": "c1"}]

        def solo(ctx, run_id, start, ask):
            asked.append(ask)
            return results[len(asked) - 1]

        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_of(Path(tmp), )
            with patch.object(bench_solo, "stage_inputs", return_value=("S", FULL)), patch.object(bench_solo, "solo_stage", solo):
                st = bench_solo.run_step(ctx, 1)
        self.assertEqual(st["run_id"], "t-s1-r1")
        self.assertEqual(asked[0], FULL)
        self.assertTrue(asked[1].startswith(bench_solo.RETRY_ASK))

    def test_resuming_takes_the_conversation_from_the_left_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            left = bench_run.run_dir(out, "B07", "t-s1") / "factory" / "run.json"
            left.parent.mkdir(parents=True)
            left.write_text(json.dumps({"start": "S", "head": "H", "stop": "done", "tries": 1, "tokens": {"total": 5}, "conversation": "c9"}),
                            encoding="utf-8")
            ctx = ctx_of(out)
            with patch.object(bench_solo, "stage_inputs", return_value=("S", FULL)), patch.object(bench_solo, "solo_stage") as solo:
                bench_solo.run_step(ctx, 1)
        self.assertEqual((ctx.conversation, solo.call_count), ("c9", 0))

    def test_the_solo_arm_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = types.SimpleNamespace(tag="t", out=Path(tmp), base="B07", tip="T", rec={"stages": []})
            done = {"base": "T", "start": "S", "head": "H", "stop": "done", "tries": 1, "tokens": {"total": 5}, "conversation": "c1"}
            with patch.object(bench_solo, "stage_inputs", return_value=("S", FULL)), \
                    patch.object(bench_solo, "solo_stage", return_value=done) as solo:
                bench_solo.run_step(ctx, 1)
                bench_solo.run_step(ctx, 2)
        self.assertEqual([c.args[3] for c in solo.call_args_list], [FULL, FULL])


class Call(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out = self.root / "repo", self.root / "out"
        (self.repo / "pkg").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "start")
        self.start = git(self.repo, "rev-parse", "HEAD")
        spec = self.root / "spec.md"
        spec.write_text("# 仕様\n", encoding="utf-8")
        self.ctx = types.SimpleNamespace(out=self.out, base="B07", tip=self.start, repo=self.repo, agent=AGENT, pkg="pkg", k=2,
                                         what="# 題\n", spec=spec, chat=True, arm=bench_solo.CHAT_ARM, home="box-chat-t-b07",
                                         conversation="c1", box_memo={})
        for name, value in (("settings", {}), ("require", "sha256:image"), ("check", []), ("agy_version", "v")):
            p = patch.object(bench_solo.box, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def implement(self, seen, raises=None):
        def fake(host, cfg, wt, prompt, log):
            seen.append(cfg)
            host.conversation = "c1"
            head = {"session_id": "agy-x", "modelUsage": {MODEL: {}}, "commands": [],
                    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 3}}
            Path(log).write_text(json.dumps(head) + hline_agy.LOG_SPLIT + "{}" + hline_agy.LOG_SPLIT, encoding="utf-8")
            save_prompt(log, prompt)
            if raises:
                raise raises
            (Path(wt) / "pkg" / "mod.py").write_text("def f(): return 2\n", encoding="utf-8")
            return 0, [MODEL], {}, None, 1
        return fake

    def test_the_call_continues_the_conversation_in_the_chain_home(self):
        seen = []
        with patch.object(bench_solo.hline_agy, "implement", self.implement(seen)):
            run = bench_solo.solo_stage(self.ctx, "t-s2", self.start, "頼み")
        self.assertEqual(seen[0]["box"], {"package": "pkg", "home": "box-chat-t-b07", "conversation": "c1"})
        self.assertEqual((run["arm"], run["conversation"], run["invalid"]), ("chat", "c1", None))
        env = json.loads((bench_run.run_dir(self.out, "B07", "t-s2") / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(env["arm"], "chat")

    def test_a_failed_call_still_records_its_conversation(self):
        seen = []
        with patch.object(bench_solo.hline_agy, "implement", self.implement(seen, Infra("途中で切れた"))):
            run = bench_solo.solo_stage(self.ctx, "t-s2", self.start, "頼み")
        self.assertEqual((run["invalid"], run["conversation"], run["head"]), ("REQ-22", "c1", None))

    def test_the_box_check_runs_once_per_start(self):
        with patch.object(bench_solo.box, "check", return_value=[]) as check, \
                patch.object(bench_solo.hline_agy, "implement", self.implement([])):
            bench_solo.solo_stage(self.ctx, "t-s2", self.start, "頼み")
            bench_solo.solo_stage(self.ctx, "t-s3", self.start, "頼み")
        self.assertEqual(check.call_count, 1)


class ChainHome(unittest.TestCase):
    def test_the_home_is_made_for_the_chain_and_dropped_when_it_is_written(self):
        made, dropped = [], []

        def step(ctx, k):
            ctx.conversation = "c1"
            return {"run_id": f"t-s{k}"}

        with tempfile.TemporaryDirectory() as tmp:
            ctx = types.SimpleNamespace(tag="t", repo=Path(tmp), out=Path(tmp), staged=Path(tmp), base="B07", pkg="pkg",
                                        entry={"stages": 2}, chat=True, arm=bench_solo.CHAT_ARM, home=None, conversation=None)
            with patch.object(bench_solo.box, "settings", return_value={}), \
                    patch.object(bench_solo.box, "home_volume", side_effect=lambda cfg, name: made.append(name) or name), \
                    patch.object(bench_solo.box, "drop_home", side_effect=lambda cfg, name: dropped.append(name)), \
                    patch.object(bench_chain, "s0_commit", return_value="S0"), patch.object(bench_chain, "fetch_tip", return_value="T"), \
                    patch.object(bench_solo, "run_step", side_effect=step):
                rec = bench_solo.run_chain(ctx, "R", "dev")
        self.assertEqual((made, dropped), (["box-chat-t-b07"], ["box-chat-t-b07"]))
        self.assertEqual((rec["arm"], rec["conversation"], rec["stopped"]), ("chat", "c1", None))

    def test_a_stop_inside_the_chain_keeps_the_home_for_the_rerun(self):
        dropped = []
        with tempfile.TemporaryDirectory() as tmp:
            ctx = types.SimpleNamespace(tag="t", repo=Path(tmp), out=Path(tmp), staged=Path(tmp), base="B07", pkg="pkg",
                                        entry={"stages": 2}, chat=True, arm=bench_solo.CHAT_ARM, home=None, conversation=None)
            with patch.object(bench_solo.box, "settings", return_value={}), \
                    patch.object(bench_solo.box, "home_volume", side_effect=lambda cfg, name: name), \
                    patch.object(bench_solo.box, "drop_home", side_effect=lambda cfg, name: dropped.append(name)), \
                    patch.object(bench_chain, "s0_commit", return_value="S0"), \
                    patch.object(bench_solo, "run_step", side_effect=bench_solo.QuotaStop("尽きた")):
                with self.assertRaises(bench_solo.QuotaStop):
                    bench_solo.run_chain(ctx, "R", "dev")
        self.assertEqual(dropped, [])

    def test_the_home_name_is_a_valid_volume_name(self):
        self.assertEqual(bench_solo.box.home_name("e5-a-chat1", "B07"), "box-chat-e5-a-chat1-b07")

    def test_the_command_line_has_the_chat_switch(self):
        with patch.object(bench_solo, "run", side_effect=lambda a: a) as run:
            a = bench_solo.main(["run", "--tag", "t", "--repo", "r", "--base", "b", "--staged", "s", "--implementer", "i", "--chat"])
        self.assertTrue(a.chat)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
