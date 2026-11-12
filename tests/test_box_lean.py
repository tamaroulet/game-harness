"""箱の走りの手間の削り（v3 §15 の 11-12 その 2・その 4）：箱の確かめ一式は起動ごとに 1 回（box.check_cached）、ベンチの走行は
受信箱の報告を書かない（line_config の report）、実装役の箱の作業の場所は課題のパッケージ・試験・仕様だけ（box.task_scope と
box_tree の keep・scope）、A の会話の形はホームと作業の場所を鎖のボリュームにして残す（box.agent の home・work・send、hline_agy.in_box）。
Docker と CLI は呼ばない（差し替える）。"""
import argparse
import io
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import box  # noqa: E402
import box_tree  # noqa: E402
import hline_agy  # noqa: E402
import hline_report  # noqa: E402

CFG = {"limits": {"memory": "8g", "pids": 1024, "tmp": "1g"}, "proxy_name": "p", "proxy_port": 1, "network": "n", "token": "/t",
       "image": "img", "distro": "D"}
MARK = {"package": "pkg", "docs": "docs/bench/B07/"}
TREE = {"pkg/__init__.py": "x = 1\n", "tests/test_pkg_base.py": "import pkg\n", "tests/test_other.py": "import other\n",
        "docs/bench/B07/spec.md": "# 仕様\n", "docs/bench/B07/chain/s1/spec.md": "# 段 1\n", "docs/bench/B08/spec.md": "# 別\n",
        "harness/box.py": "# 走らせ役\n", "README.md": "# 読んで\n"}
SCOPED = ["docs/bench/B07/chain/s1/spec.md", "docs/bench/B07/spec.md", "pkg/__init__.py", "tests/test_pkg_base.py"]


def write_tree(root, files=TREE):
    for rel, text in files.items():
        (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(root) / rel).write_bytes(text.encode("utf-8"))   # 改行を LF のまま（箱から返る中身と比べる）


def names(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return sorted(m.name for m in tar if m.isfile())


def box_tar(files):
    """docker cp が返す形（頭に work/）の tar。"""
    return box_tree.pack(None, {f"{box.WORK_NAME}/{rel}": text.encode() for rel, text in files.items()})


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


class Scope(unittest.TestCase):
    def test_the_task_scope_is_the_package_its_tests_and_its_specs(self):
        keep = box.task_scope(MARK)
        self.assertEqual(sorted(r for r in TREE if keep(r)), SCOPED)
        self.assertIsNone(box.task_scope({"package": "pkg"}))   # docs の無い印（前の走り）は木の全部
        self.assertIsNone(box.task_scope(None))

    def test_pack_sends_only_the_kept_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp)
            self.assertEqual(names(box_tree.pack(tmp, keep=box.task_scope(MARK))), SCOPED)
            self.assertEqual(len(names(box_tree.pack(tmp))), len(TREE))

    def test_receive_leaves_the_files_outside_the_scope_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp)
            back = {"pkg/__init__.py": "x = 2\n", "tests/test_pkg_s1.py": "import pkg\n", "docs/bench/B07/spec.md": "# 仕様\n",
                    "docs/bench/B07/chain/s1/spec.md": "# 段 1\n", "scratch.py": "print(1)\n"}   # test_pkg_base.py は箱の中で消した
            got = box_tree.receive(box_tar(back), tmp, box.new_files_rule("pkg"), strip=box.WORK_NAME, scope=box.task_scope(MARK))
            self.assertEqual(got["written"], ["pkg/__init__.py", "tests/test_pkg_s1.py"])
            self.assertEqual(got["removed"], ["tests/test_pkg_base.py"])
            self.assertEqual(got["dropped"], [["scratch.py", "許されたパスの外の新しいファイル"]])   # B は次の試行で知らせる
            for rel in ("harness/box.py", "README.md", "tests/test_other.py", "docs/bench/B08/spec.md"):
                self.assertTrue((Path(tmp) / rel).is_file(), rel)

    def test_a_kept_place_leaves_its_scratch_in_the_box_without_counting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp)
            back = {**{r: TREE[r] for r in SCOPED}, "scratch.py": "print(1)\n", "notes/run.txt": "1\n"}
            got = box_tree.receive(box_tar(back), tmp, box.new_files_rule("pkg"), strip=box.WORK_NAME, scope=box.task_scope(MARK), keep_out=True)
            self.assertEqual(got, {"written": [], "removed": [], "dropped": []})
            self.assertFalse((Path(tmp) / "scratch.py").exists())


class Agent(unittest.TestCase):
    def run_agent(self, **kw):
        calls, got = [], {}

        def docker(cfg, *args, data=None, ttl=box.TTL):
            calls.append(args)
            if args[0] == "cp" and data is not None:
                got["sent"] = names(data)
            return b""

        def receive(data, wt, allowed, strip=None, scope=None, keep_out=False):
            got.update(scope=scope, keep_out=keep_out)
            return {"written": [], "removed": [], "dropped": []}

        with tempfile.TemporaryDirectory() as tmp, patch.object(box, "docker", side_effect=docker), \
                patch.object(box, "_call", return_value=(0, b"out", "")), patch.object(box, "_quiet"), \
                patch.object(box.box_tree, "receive", side_effect=receive):
            write_tree(tmp)
            box.agent(CFG, ["agy", "-p="], tmp, 60, "", **kw)
        return list(next(a for a in calls if a[0] == "create")), got

    def test_b_gets_a_throwaway_box_with_only_the_task(self):
        create, got = self.run_agent(scope=box.task_scope(MARK))
        self.assertEqual(got["sent"], SCOPED)
        self.assertEqual(create[create.index("-c") + 1], box.START)
        self.assertTrue(any(str(x).startswith(f"{box.HOME}:size=") for x in create))
        self.assertIn(box.WORK, create)
        self.assertFalse(got["keep_out"])
        self.assertIsNotNone(got["scope"])

    def test_without_a_scope_the_whole_tree_goes_in(self):
        _, got = self.run_agent()
        self.assertEqual(len(got["sent"]), len(TREE))
        self.assertIsNone(got["scope"])

    def test_a_chat_call_mounts_the_chain_home_and_place_and_adds_only_the_new_spec(self):
        create, got = self.run_agent(home="box-chat-t-b07", work="box-work-t-b07", scope=box.task_scope(MARK),
                                     send={"docs/bench/B07/chain/s1/spec.md"})
        self.assertIn(f"box-chat-t-b07:{box.HOME}", create)
        self.assertIn(f"box-work-t-b07:{box.WORK}", create)
        self.assertFalse(any(str(x).startswith(f"{box.HOME}:") for x in create))
        self.assertEqual(got["sent"], ["docs/bench/B07/chain/s1/spec.md"])
        self.assertTrue(got["keep_out"])
        script = create[create.index("-c") + 1]
        self.assertEqual(script, box.START_STAY)
        self.assertIn("[ -d .git ] ||", script)   # 箱の中の git は最初の呼び出しだけ作る
        self.assertIn('rm -f "$HOME/.gemini/antigravity-cli/antigravity-oauth-token"', script)
        self.assertNotIn('exec "$@"', script)

    def test_a_home_without_a_place_keeps_the_old_start(self):
        create, _ = self.run_agent(home="box-chat-t-b07")
        self.assertEqual(create[create.index("-c") + 1], box.START_KEEP)

    def test_the_chain_volumes_are_made_once_and_dropped(self):
        seen = []
        with patch.object(box, "_call", side_effect=lambda args, *a, **k: seen.append(args[-3:]) or (1, b"", "")), \
                patch.object(box, "docker", side_effect=lambda cfg, *args, **k: seen.append(list(args)) or b""), \
                patch.object(box, "_quiet", side_effect=lambda cfg, *args: seen.append(list(args))):
            self.assertEqual(box.chain_volume(CFG, "v"), "v")
            box.drop_volume(CFG, "v")
        self.assertEqual(seen, [["volume", "inspect", "v"], ["volume", "create", "v"], ["volume", "rm", "-f", "v"]])


class InBox(unittest.TestCase):
    def test_the_mark_reaches_agy_and_the_box(self):
        got = {}

        def agent(cfg, args, wt, ttl, stdin, allowed=None, **kw):
            got.update(args=args, **kw)
            return 0, "", "", {"dropped": []}

        imp = {"cli": "agy", "model_flag": "--model", "model_name": "m", "headless_flag": "-p", "auto_approve_flag": "--yes",
               "output_format_args": ["--output-format", "stream-json"], "extra_flags": []}
        host = types.SimpleNamespace()
        with patch.object(hline_agy.model_pin, "require_implementer"), patch.object(box, "settings", return_value=CFG), \
                patch.object(box, "require"), patch.object(box, "ensure_net"), patch.object(box, "agent", side_effect=agent):
            hline_agy.in_box(host, imp, {**MARK, "home": "h", "work": "w", "conversation": "c1", "send": ["docs/bench/B07/chain/s2/spec.md"]},
                             "wt", 60, "頼み")
            self.assertEqual((got["home"], got["work"], got["send"]), ("h", "w", {"docs/bench/B07/chain/s2/spec.md"}))
            self.assertEqual(got["args"][got["args"].index("--conversation") + 1], "c1")
            self.assertTrue(got["scope"]("pkg/x.py") and not got["scope"]("harness/box.py"))
            hline_agy.in_box(host, imp, dict(MARK), "wt", 60, "頼み")   # B：記憶を持たない呼び出し・課題の範囲だけ
            self.assertEqual((got["home"], got["work"], got["send"]), (None, None, None))
            self.assertNotIn("--conversation", got["args"])
            self.assertIsNotNone(got["scope"])
            hline_agy.in_box(host, imp, {"package": "pkg"}, "wt", 60, "頼み")   # docs の無い印（前の走り）は木の全部
            self.assertIsNone(got["scope"])


if __name__ == "__main__":
    unittest.main()
