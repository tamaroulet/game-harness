"""素のエージェント A の走らせ役（harness/bench_solo.py。v3 §8 の E5）：頼みの形、走りのディレクトリの形と finish がそのまま採点できること、
時間切れ・変更なし・トークンの上限・環境の破れ・利用枠・箱の確かめの扱い、前の起動の記録の使い方、腕の名が公開の集計まで届くこと。
箱と実装役は呼ばない（差し替える）。"""
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_pair  # noqa: E402
import bench_run  # noqa: E402
import bench_solo  # noqa: E402
import chain_score  # noqa: E402
import hline_agy  # noqa: E402
from hline_base import Infra, Quota, save_prompt  # noqa: E402

MODEL = "model-under-test"
AGENT = {"cli": "agy", "model": MODEL, "model_name": MODEL}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def fake_implement(code=0, change=True, raises=None, seen=None):
    """hline_agy.implement の代わり：作業の木を変え、本物と同じ形の生ログ（要約の JSON・標準出力・標準エラー）と頼みを書く。"""
    def implement(host, cfg, wt, prompt, log):
        if seen is not None:
            seen.append({"cfg": cfg, "prompt": prompt})
        if raises is not None:
            raise raises
        if change:
            (Path(wt) / "pkg" / "mod.py").write_text("def f(): return 2\n", encoding="utf-8")
        head = {"session_id": "agy-" + Path(log).stem, "modelUsage": {MODEL: {"inputTokens": 10}}, "commands": [],
                "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5, "output_tokens": 3}}
        Path(log).write_text(json.dumps(head) + hline_agy.LOG_SPLIT + "{}" + hline_agy.LOG_SPLIT, encoding="utf-8")
        save_prompt(log, prompt)
        return code, [MODEL], {}, None, 2
    return implement


class Prompt(unittest.TestCase):
    def test_prompt_is_the_preamble_the_same_what_and_the_command(self):
        info = {"title": "題", "kind": "足す", "reqs": [("REQ-01", "REQ-01：何かを足す")]}
        files = ["pkg/mod.py", "tests/test_pkg_s1.py"]
        rows = bench_chain.ledger_rows("pkg", 1, info, files)
        what = bench_chain.what_text("B07", "pkg", 1, info, files)
        text = bench_solo.prompt_text(what, rows)
        self.assertTrue(text.startswith(bench_solo.PROMPT.read_text(encoding="utf-8").strip()))
        plain = [line for line in what.strip().splitlines() if not line.startswith(bench_solo.HLINE_HEADERS)]
        self.assertIn("\n".join(plain), text)   # H ラインのキューの見出しの行（マイルストーン・タスク）だけを外した、B と同じ What の文
        self.assertEqual(len(plain), len(what.strip().splitlines()) - 2)
        for header in bench_solo.HLINE_HEADERS:
            self.assertNotIn(header, text)
        self.assertIn(f"- 検証コマンド: {rows[0]['verification']['command']}", text)
        for harness_only in ("影響テスト", "次の試行", "判定に通りませんでした"):
            self.assertNotIn(harness_only, text)


class Stage(unittest.TestCase):
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
        self.ctx = types.SimpleNamespace(out=self.out, base="B07", tip=self.start, repo=self.repo, agent=AGENT, pkg="pkg", k=1,
                                         what="# 題\n", spec=spec)
        for name, value in (("settings", {}), ("require", "sha256:image"), ("check", []), ("agy_version", "v")):
            p = patch.object(bench_solo.box, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.rundir = bench_run.run_dir(self.out, "B07", "t-s1")

    def stage(self, implement):
        with patch.object(bench_solo.hline_agy, "implement", implement):
            return bench_solo.solo_stage(self.ctx, "t-s1", self.start, "頼み")

    def finish(self, run):
        chains, priv = self.root / "chains", self.root / "priv"
        for k, name in ((0, "test_hidden_B07.py"), (1, "test_chain_B07_s1.py")):
            p = chains / "B07" / f"s{k}" / "hidden" / name
            p.parent.mkdir(parents=True)
            p.write_text("import unittest\n", encoding="utf-8")
        priv.mkdir()
        st, row = {"k": 1, "run_id": "t-s1", "start": run["start"]}, {}
        cfg = {"out": str(self.out), "oracles": str(chains.parent), "inbox": str(self.root / "inbox"), "implementer": {"model": "other"}}
        with patch.object(chain_score, "grade", return_value={"test_chain_B07_s1.T.test_a": "ok"}), \
                patch.object(chain_score, "start_results", return_value=None), \
                patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            chain_score.finish_stage({"base": "B07", "package": "pkg", "box": True}, st, row, self.out, chains, cfg, priv, {})
        return row

    def test_a_stage_is_recorded_like_b_and_finish_scores_it(self):
        seen = []
        run = self.stage(fake_implement(seen=seen))
        self.assertEqual((run["tries"], run["stop"], run["invalid"], run["arm"], run["box_image"]), (1, "done", None, "solo", "sha256:image"))
        self.assertEqual(run["start"], self.start)
        self.assertEqual(seen[0]["cfg"], {"implementer": AGENT, "box": {"package": "pkg", "docs": "docs/bench/B07/"},
                                          "ttl_seconds": {"implementer": bench_chain.STAGE_SECONDS}})
        self.assertEqual(json.loads((self.rundir / "factory" / "run.json").read_text(encoding="utf-8"))["head"], run["head"])
        self.assertEqual(json.loads((self.rundir / "factory" / "config.json").read_text(encoding="utf-8")), {"implementer": AGENT})
        self.assertEqual((self.rundir / "calls" / "001-implementer" / "prompt.txt").read_text(encoding="utf-8"), "頼み")
        self.assertEqual(run["tokens"]["total"], 18)
        self.assertEqual(bench_run.model_problem(self.rundir, {"implementer": {"model": "other"}}), [])
        self.assertTrue((self.rundir / "inputs" / "solo.md").is_file() and (self.rundir / "env.json").is_file())
        row = self.finish(run)
        self.assertEqual((row.get("invalid"), row["violations"], row["new"]), (None, 0, [1, 1]))

    def test_the_tip_is_a_child_of_the_start(self):
        run = self.stage(fake_implement())
        check = self.root / "check"
        git(self.root, "init", "-q", str(check))
        git(check, "fetch", "-q", str(self.rundir / chain_score.BUNDLE), "+refs/heads/tip:refs/heads/tip")
        self.assertEqual(git(check, "rev-parse", "tip"), run["head"])
        self.assertEqual(git(check, "rev-parse", "tip^"), self.start)

    def test_no_change_leaves_the_start_as_the_tip(self):
        run = self.stage(fake_implement(change=False))
        self.assertEqual((run["head"], run["stop"]), (self.start, "done"))

    def test_time_out_keeps_the_received_tree(self):
        run = self.stage(fake_implement(code=hline_agy.TIMEOUT_EXIT))
        self.assertEqual((run["stop"], run["invalid"]), ("time", None))
        self.assertNotEqual(run["head"], self.start)

    def test_tokens_over_the_stage_cap_are_recorded_as_the_stop(self):
        with patch.object(bench_chain, "STAGE_TOKENS", 10):
            run = self.stage(fake_implement())
        self.assertEqual((run["stop"], run["invalid"]), ("tokens", None))

    def test_infra_failure_is_invalid_without_a_tip(self):
        run = self.stage(fake_implement(raises=Infra("API の失敗")))
        self.assertEqual((run["invalid"], run["head"], run["tries"], run["tokens"]["total"]), ("REQ-22", None, 0, 0))
        self.assertTrue((self.rundir / "factory" / "run.json").is_file())

    def test_quota_writes_the_record_and_stops(self):
        with self.assertRaises(bench_solo.QuotaStop):
            self.stage(fake_implement(raises=Quota("尽きた")))
        self.assertEqual(json.loads((self.rundir / "factory" / "run.json").read_text(encoding="utf-8"))["invalid"], "quota")

    def test_box_check_problems_skip_the_call(self):
        seen = []
        with patch.object(bench_solo.box, "check", return_value=["中継がありません"]):
            run = self.stage(fake_implement(seen=seen))
        self.assertEqual((run["invalid"], run["head"], seen), ("REQ-10", None, []))


class Chain(unittest.TestCase):
    def test_left_records_are_used_and_invalid_runs_are_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            left = bench_run.run_dir(out, "B07", "t-s1") / "factory" / "run.json"
            left.parent.mkdir(parents=True)
            left.write_text(json.dumps({"invalid": "REQ-22", "reason": "箱", "start": "S", "head": None}), encoding="utf-8")
            ctx = types.SimpleNamespace(tag="t", out=out, base="B07", tip="T", rec={"stages": []})
            good = {"base": "T", "start": "S", "head": "H", "stop": "done", "tries": 1, "tokens": {"total": 5}}
            with patch.object(bench_solo, "stage_inputs", return_value=("S", "P")), \
                    patch.object(bench_solo, "solo_stage", return_value=good) as solo:
                st = bench_solo.run_step(ctx, 1)
        self.assertEqual((st["run_id"], st["head"], st["tokens"]), ("t-s1-r1", "H", 5))
        self.assertEqual([(s["run_id"], s["invalid"], s["resumed"]) for s in ctx.rec["stages"]], [("t-s1", "REQ-22", True), ("t-s1-r1", None, False)])
        self.assertEqual(solo.call_count, 1)

    def test_a_runs_only_in_the_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "chain.json").write_text(json.dumps({"tag": "t", "bases": [{"base": "B07"}]}), encoding="utf-8")
            a = types.SimpleNamespace(tag="t", repo=tmp, staged=tmp, out=None, base="HEAD", implementer="x")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(bench_solo.run(a), 2)
        self.assertIn("箱でだけ", err.getvalue())


class Agent(unittest.TestCase):
    def test_the_implementer_config_is_read_like_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "agent.json"
            p.write_text(json.dumps(AGENT), encoding="utf-8")
            self.assertEqual(bench_solo.load_agent(p), AGENT)
            p.write_text("[]", encoding="utf-8")
            with self.assertRaises(bench_solo.BenchError):
                bench_solo.load_agent(p)


class Arm(unittest.TestCase):
    def test_the_arm_reaches_the_public_summary_and_b_records_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "B05" / "s0" / "hidden").mkdir(parents=True)
            recs = {"solo": {"arm": "solo"}, None: {}}
            for want, extra in recs.items():
                rec = {"tag": "t", "set": "held", "base": "B05", "rev": "r", "stages": [], **extra}
                with patch.object(chain_score, "grade", return_value={}):
                    public = chain_score.finish_chain(rec, root, root, {}, root / "priv")
                self.assertEqual(public.get("arm"), want)
                dest = root / f"{want}.json"
                bench_pair.publish(dest, public, root / "priv")
                doc = json.loads(dest.read_text(encoding="utf-8"))
                self.assertEqual((doc.get("arm"), "arm" in doc), (want, want is not None))


if __name__ == "__main__":
    unittest.main()
