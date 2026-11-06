"""実装役とゲートの生ログを、提案役の読む根の下に入れる（v3 §15 の 11-05 その 5。L7 の前の残り）。
chain_score の finish が段の走りの items/ を索引と同じ置き場の s<k>/ に写し、索引の「生ログ」の節に並べる。
提案役の起こし方は、索引ごとの写しが無ければ起こさず、材料に生ログの置き場を書く。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import bench_run  # noqa: E402
import chain_score  # noqa: E402
import proposer_launch as pl  # noqa: E402
from test_chain_excused import git, view, write_log  # noqa: E402
from test_proposer_launch import Base, stream  # noqa: E402

SECTION = "## 生ログ（この索引の置き場からの相対パス）"


def tree(root):
    """root の下のファイルの {相対パス: 中身のバイト}。"""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


class CopyLogs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rundir, self.priv = self.root / "run", self.root / "priv"
        for rel, body in (("w1/implementer-0-1.log", "log"), ("w1/implementer-0-1.prompt.txt", "in"),
                          ("w1/gate-0-1.log", "FAIL: test_x"), ("w2/what.md", "タスク: w2")):
            (self.rundir / "items" / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.rundir / "items" / rel).write_text(body, encoding="utf-8")

    def test_items_are_copied_whole_and_listed_from_the_index_folder(self):
        got = chain_score.copy_logs(self.rundir, self.priv / "s3")
        self.assertEqual(got, ["s3/w1/gate-0-1.log", "s3/w1/implementer-0-1.log", "s3/w1/implementer-0-1.prompt.txt", "s3/w2/what.md"])
        self.assertEqual(tree(self.priv / "s3"), tree(self.rundir / "items"))

    def test_a_copy_again_drops_files_no_longer_in_items(self):
        chain_score.copy_logs(self.rundir, self.priv / "s3")
        (self.priv / "s3" / "w9").mkdir()
        (self.priv / "s3" / "w9" / "old.log").write_text("old", encoding="utf-8")
        chain_score.copy_logs(self.rundir, self.priv / "s3")
        self.assertEqual(tree(self.priv / "s3"), tree(self.rundir / "items"))

    def test_no_items_makes_an_empty_folder(self):
        self.assertEqual(chain_score.copy_logs(self.root / "none", self.priv / "s1"), [])
        self.assertTrue((self.priv / "s1").is_dir())


class IndexSection(unittest.TestCase):
    def index(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            return chain_score.index_text("B07", 1, "t-s1", Path(tmp), {}, [], **kw)

    def test_the_section_lists_the_copies_or_says_none(self):
        self.assertIn(f"{SECTION}\n\n- s1/w1/gate-0-1.log\n- s1/w1/implementer-0-1.log\n",
                      self.index(logs=["s1/w1/gate-0-1.log", "s1/w1/implementer-0-1.log"]))
        self.assertIn(f"{SECTION}\n\n- 無し\n", self.index(logs=[]))

    def test_without_logs_there_is_no_section(self):
        self.assertNotIn(SECTION, self.index())


class FinishStage(unittest.TestCase):
    """finish_stage を通すと、段の items/（下請けの記録の写しを含む）が s<k>/ に写り、索引に並ぶ。非公開の記録の欄は変えない。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chains, self.out, self.priv, self.local = (self.root / n for n in ("chains", "out", "priv", "local"))
        self.priv.mkdir()
        for k, body in ((0, "    def test_s0(self):\n        self.assertGreaterEqual(f(), 1)\n"),
                        (1, "    def test_a(self):\n        self.assertEqual(f(), 2)\n")):
            p = self.chains / "B07" / f"s{k}" / "hidden" / ("test_hidden_B07.py" if k == 0 else "test_chain_B07_s1.py")
            p.parent.mkdir(parents=True)
            p.write_text("import unittest\nfrom pkg.mod import f\n\n\nclass T(unittest.TestCase):\n" + body, encoding="utf-8")
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
        own = bench_run.work_dir(self.out, "B07", "t-s1")
        write_log(self.rundir / "items" / "w1" / "implementer-0-1.log", own, [view(own / "pkg" / "mod.py")])
        (self.rundir / "items" / "w1" / "gate-0-1.log").write_text("OK\n", encoding="utf-8")

    def finish(self):
        rec, st, row = {"base": "B07", "package": "pkg"}, {"k": 1, "run_id": "t-s1", "start": self.start}, {}
        cfg = {"out": str(self.out), "oracles": str(self.chains.parent), "inbox": str(self.root / "inbox")}
        with patch.object(chain_score.machine, "path", return_value=self.local):
            chain_score.finish_stage(rec, st, row, self.out, self.chains, cfg, self.priv, {})
        return row, json.loads((self.priv / "s1.json").read_text(encoding="utf-8")), (self.priv / "s1.index.md").read_text(encoding="utf-8")

    def test_the_stage_items_are_copied_next_to_the_index_and_listed(self):
        row, priv, index = self.finish()
        self.assertEqual(tree(self.priv / "s1"), tree(self.rundir / "items"))
        self.assertIn(f"{SECTION}\n\n- s1/w1/gate-0-1.log\n- s1/w1/implementer-0-1.log\n", index)
        self.assertEqual(row["new"], [1, 1])
        self.assertEqual(list(priv), ["base", "k", "run_id", "results", "replaced", "violations", "strays", "web", "leaks", "at_start"])

    def test_finishing_again_gives_the_same_copy_and_index(self):
        _, _, first = self.finish()
        copy = tree(self.priv / "s1")
        _, _, again = self.finish()
        self.assertEqual((tree(self.priv / "s1"), again), (copy, first))


class Launch(Base):
    def test_an_index_without_its_raw_logs_launches_nothing(self):
        (self.chains / "B01" / "s2").rmdir()
        rec, run = self.launch()
        run.assert_not_called()
        self.assertFalse(rec["launched"])
        self.assertEqual(rec["skipped_reason"], "生ログの写しが無い段が 1 あります（今の chain_score で finish をやり直す）: "
                         + str((self.chains / "B01" / "s2").absolute()))

    def test_many_missing_are_counted_and_the_first_three_named(self):
        for k in (3, 4, 5, 6):
            (self.chains / "B01" / f"s{k}.index.md").write_text("索引\n", encoding="utf-8")
        rec, run = self.launch()
        run.assert_not_called()
        self.assertIn("生ログの写しが無い段が 4 あります", rec["skipped_reason"])
        self.assertTrue(rec["skipped_reason"].endswith("s5、…"))

    def test_the_material_says_where_the_raw_logs_are_and_reading_them_is_inside_the_roots(self):
        log = self.chains / "B01" / "s2" / "w1" / "implementer-0-1.log"
        rec, run = self.launch(result=(0, stream(uses=self.reads() + [("Read", {"file_path": str(log)})]), ""))
        material = run.call_args.kwargs["input"].split("## 材料", 1)[1]
        self.assertIn("- 生ログ（What ごとの実装役の stream-json とその入力・門の出力・What）: 索引と同じ置き場の s<k>/ の下。", material)
        self.assertEqual(rec["path_hits"], [])
        self.assertTrue(pl.ok(rec))


if __name__ == "__main__":
    unittest.main()
