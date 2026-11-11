"""箱の走りの手間の削り（v3 §15 の 11-12 その 2）：箱の確かめ一式は起動ごとに 1 回（box.check_cached）、ベンチの走行は
受信箱の報告を書かない（line_config の report）、会話の形の実装役はホームを名前つきボリュームにして会話を続ける（box.agent の home・
hline_agy.in_box）。Docker と CLI は呼ばない（差し替える）。"""
import argparse
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import box  # noqa: E402
import hline_agy  # noqa: E402
import hline_report  # noqa: E402

CFG = {"limits": {"memory": "8g", "pids": 1024, "tmp": "1g"}, "proxy_name": "p", "proxy_port": 1, "network": "n", "token": "/t",
       "image": "img", "distro": "D"}


class CheckCached(unittest.TestCase):
    def test_a_pass_is_remembered_and_a_failure_is_not(self):
        memo = {}
        with patch.object(box, "check", side_effect=[["中継がありません"], [], []]) as check:
            self.assertEqual(box.check_cached(CFG, memo), ["中継がありません"])
            self.assertEqual(box.check_cached(CFG, memo), [])
            self.assertEqual(box.check_cached(CFG, memo), [])
        self.assertEqual(check.call_count, 2)

    def test_without_a_memo_every_call_checks(self):
        with patch.object(box, "check", return_value=[]) as check:
            box.check_cached(CFG)
            box.check_cached(CFG)
        self.assertEqual(check.call_count, 2)

    def test_chain_stages_of_one_start_share_the_memo(self):
        with tempfile.TemporaryDirectory() as tmp:
            memo, seen = {}, []
            ns = argparse.Namespace(task="B07", run_id="t-s1", repo=tmp, base="HEAD", out=tmp, sessions=tmp, plan=None, implementer=None)
            for _ in range(2):
                st = bench_chain.ChainStage(ns, "S", Path(tmp) / "spec.md", "pkg", memo)
                with patch.object(box, "settings", return_value=CFG), patch.object(box, "require", return_value="sha256:i"), \
                        patch.object(box, "check", side_effect=lambda cfg: seen.append(1) or []), \
                        patch.object(bench_chain.ChainStage, "effective", return_value={}):
                    self.assertTrue(st.guard({}))
        self.assertEqual(len(seen), 1)


class Report(unittest.TestCase):
    def test_bench_runs_do_not_write_the_inbox_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            cfg = bench_run.line_config({"implementer": {"cli": "x"}}, work, work / "inbox", "abc", "br")
            self.assertIs(cfg["report"], False)
            (work / "inbox").mkdir()
            with patch.object(hline_report, "goal_section") as goal:
                hline_report.write_report(cfg, {"items": {}})
            self.assertEqual(list((work / "inbox").iterdir()), [])
            self.assertEqual(goal.call_count, 0)


class AgentHome(unittest.TestCase):
    def run_agent(self, home):
        calls = []

        def docker(cfg, *args, data=None, ttl=box.TTL):
            calls.append(args)
            return b""

        with tempfile.TemporaryDirectory() as tmp, patch.object(box, "docker", side_effect=docker), \
                patch.object(box, "_call", return_value=(0, b"out", "")), patch.object(box, "_quiet"), \
                patch.object(box.box_tree, "receive", return_value={"written": [], "removed": [], "dropped": []}):
            box.agent(CFG, ["agy", "-p="], tmp, 60, "", home=home)
        return next(a for a in calls if a[0] == "create")

    def test_a_chat_call_mounts_the_chain_home_and_removes_the_token_after(self):
        create = list(self.run_agent("box-chat-t-b07"))
        self.assertIn(f"box-chat-t-b07:{box.HOME}", create)
        self.assertFalse(any(str(x).startswith(f"{box.HOME}:") for x in create))
        script = create[create.index("-c") + 1]
        self.assertEqual(script, box.START_KEEP)
        self.assertIn('rm -f "$HOME/.gemini/antigravity-cli/antigravity-oauth-token"', script)
        self.assertNotIn('exec "$@"', script)

    def test_other_calls_keep_the_throwaway_home(self):
        create = list(self.run_agent(None))
        self.assertTrue(any(str(x).startswith(f"{box.HOME}:size=") for x in create))
        self.assertEqual(create[create.index("-c") + 1], box.START)

    def test_the_home_volume_is_made_once_and_dropped(self):
        seen = []
        with patch.object(box, "_call", side_effect=lambda args, *a, **k: seen.append(args[-3:]) or (1, b"", "")), \
                patch.object(box, "docker", side_effect=lambda cfg, *args, **k: seen.append(list(args)) or b""), \
                patch.object(box, "_quiet", side_effect=lambda cfg, *args: seen.append(list(args))):
            self.assertEqual(box.home_volume(CFG, "v"), "v")
            box.drop_home(CFG, "v")
        self.assertEqual(seen, [["volume", "inspect", "v"], ["volume", "create", "v"], ["volume", "rm", "-f", "v"]])


class InBox(unittest.TestCase):
    def test_the_conversation_and_the_home_reach_agy_and_the_box(self):
        got = {}

        def agent(cfg, args, wt, ttl, stdin, allowed=None, home=None):
            got.update(args=args, home=home)
            return 0, "", "", {"dropped": []}

        imp = {"cli": "agy", "model_flag": "--model", "model_name": "m", "headless_flag": "-p", "auto_approve_flag": "--yes",
               "output_format_args": ["--output-format", "stream-json"], "extra_flags": []}
        host = types.SimpleNamespace()
        with patch.object(hline_agy.model_pin, "require_implementer"), patch.object(box, "settings", return_value=CFG), \
                patch.object(box, "require"), patch.object(box, "ensure_net"), patch.object(box, "agent", side_effect=agent):
            hline_agy.in_box(host, imp, {"package": "pkg", "home": "v", "conversation": "c1"}, "wt", 60, "頼み")
            self.assertEqual(got["home"], "v")
            self.assertEqual(got["args"][got["args"].index("--conversation") + 1], "c1")
            hline_agy.in_box(host, imp, {"package": "pkg"}, "wt", 60, "頼み")
            self.assertIsNone(got["home"])
            self.assertNotIn("--conversation", got["args"])


if __name__ == "__main__":
    unittest.main()
