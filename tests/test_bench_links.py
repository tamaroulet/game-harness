"""bench の書いた置き場を操縦士のアカウントで辿るときの守り（v3 §7-9、L7 の前の Argon の指摘）：リンクとジャンクションを辿らない・
ログの中のパスをそのまま読まない。守れないと、bench の読めない所（隠しテスト）の中身を bench の読める所へ運ぶことになる。

    python -m unittest tests.test_bench_links -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402
import chain_score  # noqa: E402
import proposer_stage as ps  # noqa: E402


def make_link(target, link):
    """Windows はジャンクション（管理者の権限が要らない。bench も作れる）、ほかはシンボリックリンク。"""
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True, timeout=30)
    else:
        os.symlink(target, link, target_is_directory=True)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.secret = self.root / "oracles" / "chains"
        self.secret.mkdir(parents=True)
        (self.secret / "hidden.py").write_text("THE_SECRET = 1\n", encoding="utf-8")


class LinksIn(Base):
    def test_a_plain_tree_has_none(self):
        d = self.root / "run" / "items"
        d.mkdir(parents=True)
        (d / "a.log").write_text("x", encoding="utf-8")
        self.assertEqual(bench_guard.links_in(self.root / "run"), [])

    def test_a_missing_place_has_none(self):
        self.assertEqual(bench_guard.links_in(self.root / "none"), [])

    def test_a_link_anywhere_is_found_and_not_entered(self):
        d = self.root / "run" / "items" / "w1"
        d.mkdir(parents=True)
        make_link(self.secret, d / "x")
        got = bench_guard.links_in(self.root / "run")
        self.assertEqual([Path(p) for p in got], [d / "x"])

    def test_the_place_itself_being_a_link_is_found(self):
        make_link(self.secret, self.root / "run")
        self.assertEqual([Path(p) for p in bench_guard.links_in(self.root / "run")], [self.root / "run"])


class Adopt(Base):
    def setUp(self):
        super().setUp()
        self.home = self.root / "bench"
        self.brain = self.home / ".gemini" / "antigravity-cli" / "brain"
        self.own = self.root / "bench" / "work"
        self.item = self.root / "bench" / "run" / "items" / "w1"
        self.item.mkdir(parents=True)
        self.own.mkdir(parents=True)

    def adopt(self, path):
        log = self.item / "implementer-0-1.log"
        steps = [{"subagents": [{"conversation_id": "sub-1", "log_uri": Path(path).as_uri() if Path(path).is_absolute() else path}]}]
        return chain_score.adopt(log, steps, self.own, self.home)

    def copied(self):
        return list(self.item.glob("subagent-*"))

    def test_the_agents_own_record_is_read(self):
        p = self.brain / "sub-1" / "transcript.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{}\n", encoding="utf-8")
        subs, missing = self.adopt(p)
        self.assertEqual((len(subs), missing), (1, []))
        self.assertEqual(self.copied()[0].read_text(encoding="utf-8"), "{}\n")

    def test_a_path_outside_the_bench_place_is_not_read_and_is_a_hit(self):
        subs, missing = self.adopt(self.secret / "hidden.py")
        self.assertEqual(subs, [])
        self.assertEqual(missing, [("implementer-0-1.log", chain_score.SUB_TOOL, chain_score.SUB_MISSING)])
        self.assertEqual(self.copied(), [])

    def test_the_shape_of_the_record_place_outside_home_is_not_enough(self):
        """操縦士のアカウントの agy の会話の記録も、同じ形のパスにある。"""
        other = self.root / "tamar" / ".gemini" / "antigravity-cli" / "brain" / "sub-1" / "transcript.jsonl"
        other.parent.mkdir(parents=True)
        other.write_text("mine\n", encoding="utf-8")
        self.assertEqual(self.adopt(other)[0], [])
        self.assertEqual(self.copied(), [])

    def test_inside_home_but_not_the_record_place_is_not_read(self):
        p = self.home / "other.txt"
        p.write_text("x", encoding="utf-8")
        self.assertEqual(self.adopt(p)[0], [])

    def test_climbing_out_with_dots_is_not_read(self):
        (self.brain / "sub-1").mkdir(parents=True)
        path = str(self.brain / "sub-1" / ".." / ".." / ".." / ".." / ".." / "oracles" / "chains" / "hidden.py")
        self.assertEqual(self.adopt(path)[0], [])
        self.assertEqual(self.copied(), [])

    def test_a_link_inside_the_record_place_pointing_out_is_not_followed(self):
        self.brain.mkdir(parents=True)
        make_link(self.secret, self.brain / "sub-1")
        self.assertEqual(self.adopt(self.brain / "sub-1" / "hidden.py")[0], [])
        self.assertEqual(self.copied(), [])


class CopyLogs(Base):
    def test_skip_copies_nothing(self):
        items = self.root / "run" / "items" / "w1"
        items.mkdir(parents=True)
        (items / "a.log").write_text("x", encoding="utf-8")
        self.assertEqual(chain_score.copy_logs(self.root / "run", self.root / "priv" / "s1", skip=True), [])
        self.assertTrue((self.root / "priv" / "s1").is_dir())


class FinishStage(Base):
    """リンクのある走りは、辿らずに無効にする（本物の採点の並びで）。"""

    def setUp(self):
        super().setUp()
        self.chains, self.out, self.priv = self.root / "chains", self.root / "out", self.root / "priv"
        self.priv.mkdir()
        for stage, name in (("s0", "test_hidden_B07.py"), ("s1", "test_chain_B07_s1.py")):
            hidden = self.chains / "B07" / stage / "hidden" / name
            hidden.parent.mkdir(parents=True)
            hidden.write_text("import unittest\nfrom pkg.mod import f\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(f(), 2)\n", encoding="utf-8")
        self.rundir = bench_run.run_dir(self.out, "B07", "t-s1")
        (self.rundir / "git").mkdir(parents=True)
        (self.rundir / "items" / "w1").mkdir(parents=True)
        (self.rundir / "calls" / "c1").mkdir(parents=True)
        (self.rundir / "calls" / "c1" / "result.json").write_text("{}", encoding="utf-8")
        repo = self.root / "repo"
        (repo / "pkg").mkdir(parents=True)
        git = ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q"], check=True, capture_output=True, timeout=30)
        (repo / "pkg" / "mod.py").write_text("def f(): return 2\n", encoding="utf-8")
        subprocess.run([*git, "add", "-A"], check=True, capture_output=True, timeout=30)
        subprocess.run([*git, "commit", "-q", "-m", "tip"], check=True, capture_output=True, timeout=30)
        subprocess.run([*git, "branch", "tip"], check=True, capture_output=True, timeout=30)
        self.start = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        subprocess.run([*git, "bundle", "create", str(self.rundir / "git" / "run.bundle"), "tip"], check=True, capture_output=True, timeout=30)

    def finish(self):
        rec, st, row = {"base": "B07", "package": "pkg"}, {"k": 1, "run_id": "t-s1", "start": self.start}, {}
        cfg = {"out": str(self.out), "oracles": str(self.root / "oracles"), "inbox": str(self.root / "inbox")}
        with patch.object(chain_score.machine, "path", return_value=self.root / "local"), patch.object(chain_score.bench_run, "model_problem", return_value=[]):
            chain_score.finish_stage(rec, st, row, self.out, self.chains, cfg, self.priv, {})
        return row

    def test_without_links_the_stage_is_graded_as_before(self):
        (self.rundir / "items" / "w1" / "a.log").write_text("x", encoding="utf-8")
        row = self.finish()
        self.assertFalse(row.get("invalid"))
        self.assertEqual(row["new"], [1, 1])
        self.assertTrue((self.priv / "s1" / "w1" / "a.log").is_file())

    def test_a_link_in_the_run_invalidates_the_stage_and_nothing_is_carried(self):
        make_link(self.secret, self.rundir / "items" / "w1" / "x")
        row = self.finish()
        self.assertEqual(row["invalid"], "stray")
        self.assertEqual(row["strays"], 1)
        self.assertFalse(list((self.priv / "s1").rglob("hidden.py")))
        priv = json.loads((self.priv / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(priv["strays"][0][1:], [chain_score.LINK_TOOL, chain_score.LINK_HIT])
        self.assertIn(chain_score.LINK_HIT, (self.priv / "s1.index.md").read_text(encoding="utf-8"))


class Collect(Base):
    def test_collect_refuses_a_copy_with_a_link(self):
        dest = self.root / "stage"
        dest.mkdir()
        make_link(self.secret, dest / "out")
        with patch.object(ps, "checked", return_value=dest):
            with self.assertRaisesRegex(ps.StageError, "ジャンクション"):
                ps.collect("unused", dest)
        self.assertTrue((self.secret / "hidden.py").is_file())


if __name__ == "__main__":
    unittest.main()
