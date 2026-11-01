"""鎖の採点の直し 3 つ：開始の木での新しい試験の通過（new_at_start・at_start・索引の節）、実装役の下請けの記録の写しと道の検め、
自分の下請けの記録を読む呼び出しは道の外としないこと。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import chain_score  # noqa: E402

MARKS = ("B07", "pkg", "docs/bench", "chain/s")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)


def hidden(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("import unittest\nfrom pkg.mod import f\n\n\nclass T(unittest.TestCase):\n" + body, encoding="utf-8")


class AtStart(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chains, self.out, self.priv = (self.root / n for n in ("chains", "out", "priv"))
        self.priv.mkdir()
        hidden(self.chains / "B07" / "s0" / "hidden" / "test_hidden_B07.py", "    def test_s0(self):\n        self.assertGreaterEqual(f(), 1)\n")
        hidden(self.chains / "B07" / "s1" / "hidden" / "test_chain_B07_s1.py",
               "    def test_a(self):\n        self.assertGreaterEqual(f(), 1)\n\n    def test_b(self):\n        self.assertEqual(f(), 2)\n")
        self.rundir = bench_run.run_dir(self.out, "B07", "t-s1")
        (self.rundir / "git").mkdir(parents=True)
        repo = self.root / "repo"
        (repo / "pkg").mkdir(parents=True)
        git(repo, "init", "-q")
        (repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "start")
        self.start = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        (repo / "pkg" / "mod.py").write_text("def f(): return 2\n", encoding="utf-8")
        git(repo, "commit", "-q", "-am", "tip")
        git(repo, "branch", "tip")
        git(repo, "bundle", "create", str(self.rundir / "git" / "run.bundle"), "tip")

    def finish(self, start):
        rec = {"base": "B07", "package": "pkg"}
        st = {"k": 1, "run_id": "t-s1", "start": start}
        row = {}
        cfg = {"out": str(self.out), "oracles": str(self.chains.parent), "inbox": str(self.root / "inbox")}
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"):
            chain_score.finish_stage(rec, st, row, self.out, self.chains, cfg, self.priv, {})
        return row, json.loads((self.priv / "s1.json").read_text(encoding="utf-8")), (self.priv / "s1.index.md").read_text(encoding="utf-8")

    def test_new_tests_passing_at_start(self):
        """開始の木では段 1 の試験 2 件のうち 1 件が通り、先端では 2 件が通る。既存の項目は変わらない。"""
        row, priv, index = self.finish(self.start)
        self.assertEqual(row["new_at_start"], [1, 2])
        self.assertEqual(row["new"], [2, 2])
        self.assertEqual((row["old"], row["violations"], row["leaks"], row["strays"], row["web"]), ([1, 1], 0, 0, 0, 0))
        self.assertEqual(sorted(priv["at_start"]), ["test_chain_B07_s1.T.test_a", "test_chain_B07_s1.T.test_b"])
        self.assertEqual(priv["at_start"]["test_chain_B07_s1.T.test_a"], "ok")
        self.assertTrue(priv["at_start"]["test_chain_B07_s1.T.test_b"].startswith("FAIL"))
        self.assertIn("## 開始の木で通っていた新しい試験\n\n- 1/2\n- `test_a`\n\n## 道の検め", index)

    def test_start_commit_missing_is_null(self):
        """開始のコミットが束から取り出せなければ null（索引にもそう書く）。"""
        row, priv, index = self.finish("0" * 40)
        self.assertIsNone(row["new_at_start"])
        self.assertIsNone(priv["at_start"])
        self.assertIn("## 開始の木で通っていた新しい試験\n\n- 開始のコミットを取り出せませんでした", index)


class Subagents(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local, self.own = self.root / "local", self.root / "local" / "own"
        self.other, self.work = self.local / "other", self.own / "wt"
        self.item = self.own / "items" / "w1"
        for d in (self.other, self.work, self.item):
            d.mkdir(parents=True)
        self.brain = self.root / "brain"

    def transcript(self, cid, *calls):
        p = self.brain / cid / "transcript.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = ['{"step_index":0,"source":"SYSTEM","type":"SYSTEM_MESSAGE"}', "not json"]
        lines += [json.dumps({"step_index": i, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": n, "args": a}]}) for i, (n, a) in enumerate(calls, 1)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def log(self, launches, reads=()):
        """実装役の生ログ。launches は (id, 記録のパス, 作業の場所)、reads は実装役自身が view_file で読むパス。"""
        subs = [{"conversation_id": c, "log_uri": None if p is None else Path(p).as_uri(), "workspace_uris": [w.as_uri()] if w else []}
                for c, p, w in launches]
        events = [{"event": "init", "init": {"cwd": str(self.own)}},
                  {"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "subagent", "tool_name": "invoke_subagent",
                                                           "subagent_info": {"subagents": subs}}}]
        events += [{"event": "step_update", "step_update": {"step_index": 2 + i, "state": "DONE", "step_type": "tool",
                                                             "tool_info": {"name": "view_file", "parameters": {"AbsolutePath": str(r)}}}}
                   for i, r in enumerate(reads)]
        p = self.item / "implementer-0-1.log"
        p.write_text("{}" + bench_run.LOG_SPLIT + "\n".join(map(json.dumps, events)) + "\n" + bench_run.LOG_SPLIT + "e\n", encoding="utf-8")
        return p

    def check(self, log):
        subs, missing = chain_score.adopt(log, chain_score.steps_of(log)[1], self.own)
        found, web = chain_score.audit([log], self.own, self.local, MARKS, subs)
        return subs, found + missing, web

    def test_copy_and_audit_of_transcript(self):
        """起動を拾い（URI は Windows の形も Linux の形も）、記録を What のディレクトリに写す（同じ中身なら写し直さない）。作業の場所の外を読む呼び出しは当たり。"""
        self.assertEqual(chain_score.uri_path("file:///C:/x/brain/sub-1/t.jsonl"), "C:/x/brain/sub-1/t.jsonl")
        self.assertEqual(chain_score.uri_path("file:///home/u/a%20b/t.jsonl"), "/home/u/a b/t.jsonl")
        bad = self.transcript("sub-1", ("view_file", {"AbsolutePath": str(self.work / "a.py")}), ("view_file", {"AbsolutePath": str(self.other / "b.py")}))
        log = self.log([("sub-1", bad, self.work)])
        subs, found, web = self.check(log)
        copy = self.item / "subagent-sub-1.jsonl"
        self.assertEqual([(c, Path(w)) for c, w in subs], [(copy, self.work)])   # 区切りは OS で違う
        self.assertEqual(copy.read_bytes(), bad.read_bytes())
        self.assertEqual((found, web), ([("subagent-sub-1.jsonl", "view_file", str(self.other / "b.py"))], []))
        mtime = copy.stat().st_mtime_ns
        self.check(log)
        self.assertEqual(copy.stat().st_mtime_ns, mtime)

    def test_network_tool_and_inside_only(self):
        """下請けのネットの道具は実装役と同じ決まり（名指しは当たり、調べものは残る）。作業の場所の中だけなら当たり無し。
        下請けの作業の場所が段の作業の場所の外なら、その中を読んでも当たり。"""
        net = self.transcript("sub-2", ("search_web", {"query": "game-harness chain"}), ("search_web", {"query": "mujoco condim"}))
        _, found, web = self.check(self.log([("sub-2", net, self.work)]))
        self.assertEqual([f[:2] for f in found], [("subagent-sub-2.jsonl", "search_web")])
        self.assertEqual(web, [("subagent-sub-2.jsonl", "search_web", "mujoco condim")])
        ok = self.transcript("sub-3", ("view_file", {"AbsolutePath": str(self.work / "a.py")}))
        self.assertEqual(self.check(self.log([("sub-3", ok, self.work)]))[1:], ([], []))
        far = self.transcript("sub-6", ("view_file", {"AbsolutePath": str(self.other / "c.py")}))
        self.assertEqual([f[2] for f in self.check(self.log([("sub-6", far, self.other)]))[1]], [str(self.other / "c.py")])

    def test_missing_transcript_is_a_hit(self):
        """記録を読めない（ファイルが無い・log_uri が無い）起動は、検められない当たりにする。"""
        log = self.log([("sub-4", self.brain / "none.jsonl", None), ("sub-5", None, None)])
        subs, found, _ = self.check(log)
        self.assertEqual((subs, found), ([], [("implementer-0-1.log", "invoke_subagent", chain_score.SUB_MISSING)] * 2))

    def test_reading_own_transcript_is_not_a_stray(self):
        """実装役が自分の下請けの記録を読んでも当たりにならない。別の id の記録を読めば当たり。中身は検める。"""
        mine = self.transcript("sub-1")
        theirs = self.transcript("sub-9")
        log = self.log([("sub-1", mine, self.work)], reads=[mine, theirs])
        found, _ = chain_score.audit([log], self.own, self.root, MARKS)   # 記録の置き場も local の下にして、外のパスとして数える
        self.assertEqual(found, [("implementer-0-1.log", "view_file", str(theirs))])

    def test_run_without_subagents_is_unchanged(self):
        """下請けの無い走りでは、道の検めの値は今までと同じ（写しも当たりも無い）。"""
        log = self.log([], reads=[self.other / "x.py", self.own / "y.py"])
        subs, found, web = self.check(log)
        self.assertEqual((subs, web, found), ([], [], [("implementer-0-1.log", "view_file", str(self.other / "x.py"))]))
        self.assertEqual(chain_score.strays([log], self.own, self.local), found)
        self.assertEqual(list(self.item.glob("subagent-*")), [])
