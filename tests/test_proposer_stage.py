"""提案役を bench のアカウントで、材料だけの写しで起こす（harness/proposer_stage.py。L7 の前の残り 2、案 A）。
写しの中身（追跡しているハーネスの木・tag ごとの記録と生ログの写し・out を写しに向けた設定・bench のバッチ）、関所、
写しの上での提案役の起動（読む根は写しだけ）、取り込みと写しの消し方。本物の CLI・記録・bench の置き場は使わない。"""
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline_base  # noqa: E402
import proposer_launch as pl  # noqa: E402
import proposer_stage as ps  # noqa: E402
from test_proposer_launch import stream  # noqa: E402

REAL_CFG = json.loads((HERE.parent / "config" / "hline.json").read_text(encoding="utf-8"))
AGENT = json.loads((HERE.parent / "config" / "proposer.json").read_text(encoding="utf-8"))
NOW = datetime.datetime(2026, 11, 7, 3, 0, 0)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True).stdout


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.local = self.tmp / "local"
        self.out, self.oracles = self.local / "out" / "hline", self.local / "oracles"
        self.chains = self.out / "chains" / "v1"
        (self.chains / "B01" / "s2" / "w1").mkdir(parents=True)
        (self.chains / "B01" / "s2.json").write_text("{}", encoding="utf-8")
        (self.chains / "B01" / "s2.index.md").write_text("索引\n", encoding="utf-8")
        (self.chains / "B01" / "s2" / "w1" / "implementer-0-1.log").write_text("log\n", encoding="utf-8")
        self.oracles.mkdir(parents=True)
        (self.local / "bench").mkdir()
        (self.local / "bench" / "bench_env.bat").write_text("@echo off\n", encoding="utf-8")
        self.repo = self.tmp / "repo"
        for rel, text in (("harness/a.py", "x = 1\n"), ("docs/prompts/proposer.md", "提案を書いてください。\n")):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        (self.repo / "untracked.txt").write_text("u\n", encoding="utf-8")
        self.config = self.tmp / "hline.json"
        self.config.write_text(json.dumps({**REAL_CFG, "out": str(self.out), "oracles": str(self.oracles)}), encoding="utf-8")
        real_path = ps.machine.path
        patch = mock.patch.object(ps.machine, "path", lambda name: self.local if name == "local" else real_path(name))
        patch.start()
        self.addCleanup(patch.stop)
        self.dest = self.local / "bench" / "proposer" / "20261107-030000"

    def prepare(self, tags=("v1",)):
        return ps.prepare(self.config, list(tags), now=NOW, root=self.repo)

    def launch_on_copy(self, uses=None):
        dest = self.prepare()
        uses = uses if uses is not None else [("Read", {"file_path": str(dest / "out" / "chains" / "v1" / "B01" / "s2.index.md")}),
                                              ("Read", {"file_path": str(dest / "out" / "chains" / "v1" / "B01" / "s2" / "w1"
                                                                          / "implementer-0-1.log")}),
                                              ("Read", {"file_path": str(dest / "repo" / "harness" / "a.py")})]
        with mock.patch.object(pl.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(pl.proc, "run", return_value=(0, stream(uses=uses), "")) as run:
            rec = pl.launch(hline_base.load_config(dest / "hline.json"), json.loads(json.dumps(AGENT)), ["v1"],
                            now=NOW, root=dest / "repo")
        return dest, rec, run


class Prepare(Base):
    def test_the_copy_has_the_tracked_tree_the_records_and_out_pointing_at_it(self):
        dest = self.prepare()
        self.assertEqual(dest, self.dest)
        self.assertEqual(sorted(tree(dest / "repo")), ["docs/prompts/proposer.md", "harness/a.py"])   # 追跡していないファイルは入らない
        self.assertEqual(tree(dest / "out" / "chains" / "v1"), tree(self.chains))   # 索引・s<k>.json・生ログの写し
        staged = json.loads((dest / "hline.json").read_text(encoding="utf-8"))
        self.assertEqual((staged["out"], staged["oracles"]), (str(dest / "out"), str(self.oracles)))
        man = json.loads((dest / "stage.json").read_text(encoding="utf-8"))
        self.assertEqual((man["tags"], man["rev"], man["files"]), (["v1"], git(self.repo, "rev-parse", "HEAD").decode().strip(), 3))

    def test_the_batch_runs_the_launcher_on_the_copy_in_ascii_and_crlf(self):
        dest = self.prepare()
        raw = (dest / "launch.bat").read_bytes()
        text = raw.decode("ascii")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(f"call {self.local / 'bench' / 'bench_env.bat'}\r\n", text)
        self.assertIn(f"cd /d {dest / 'repo'}\r\n", text)
        self.assertIn(f"python -m harness.proposer_launch --tag v1 --config {dest / 'hline.json'} > {dest / 'launch.out'} 2>&1\r\n", text)
        self.assertIn(f"echo exit=%ERRORLEVEL% >> {dest / 'launch.out'}\r\n", text)

    def test_gates_stop_before_anything_is_written(self):
        shutil.rmtree(self.chains / "B01" / "s2")
        with self.assertRaisesRegex(ps.StageError, "生ログの写しが無い段が 1 あります"):
            self.prepare()
        (self.chains / "B01" / "s2").mkdir()
        for tags, words in ((("v1&x",), "tag の形"), (("v1", "v1"), "重なって"), (("v9",), "開発の組の記録がありません")):
            with self.subTest(tags=tags), self.assertRaisesRegex(ps.StageError, words):
                self.prepare(tags)
        (self.local / "bench" / "bench_env.bat").unlink()
        with self.assertRaisesRegex(ps.StageError, "bench の環境のファイルがありません"):
            self.prepare()
        self.assertFalse((self.local / "bench" / "proposer").exists())

    def test_a_failure_while_copying_leaves_no_copy(self):
        (self.tmp / "notgit").mkdir()
        with self.assertRaisesRegex(ps.StageError, "rev-parse"):
            ps.prepare(self.config, ["v1"], now=NOW, root=self.tmp / "notgit")   # 写しの置き場を作る前に止まる
        self.assertFalse(self.dest.exists())
        with mock.patch.object(ps.shutil, "copytree", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.prepare()   # 木を出した後で落ちる
        self.assertFalse(self.dest.exists())

    def test_an_existing_copy_is_not_overwritten(self):
        self.dest.mkdir(parents=True)
        with self.assertRaisesRegex(ps.StageError, "もうあります"):
            self.prepare()
        self.assertEqual(list(self.dest.iterdir()), [])


class LaunchOnTheCopy(Base):
    def test_the_launcher_reads_only_the_copy_and_writes_its_record_there(self):
        dest, rec, run = self.launch_on_copy()
        self.assertEqual([Path(r) for r in rec["roots"]], [dest / "repo", dest / "out" / "chains" / "v1"])
        self.assertEqual((rec["path_hits"], rec["unread_indexes"]), ([], []))
        self.assertTrue(pl.ok(rec))
        self.assertEqual(Path(rec["proposal"]).parent, dest / "out" / "proposer")
        self.assertIn("--no-session-persistence", run.call_args.args[0])   # 会話の記録を bench の HOME に残さない

    def test_reading_the_original_records_is_a_path_hit(self):
        dest = self.dest
        uses = [("Read", {"file_path": str(dest / "out" / "chains" / "v1" / "B01" / "s2.index.md")}),
                ("Read", {"file_path": str(self.chains / "B01" / "s2.json")})]
        _, rec, _ = self.launch_on_copy(uses)
        self.assertEqual(len(rec["path_hits"]), 1)
        self.assertIsNone(rec["proposal"])


class Collect(Base):
    def finished(self):
        dest, rec, _ = self.launch_on_copy()
        (dest / "launch.out").write_text(pl.line_of(rec) + "\nexit=0\n", encoding="utf-8")
        return dest, rec

    def test_collect_waits_for_the_batch_to_finish(self):
        dest, _, _ = self.launch_on_copy()
        with self.assertRaisesRegex(ps.StageError, "終わっていません"):
            ps.collect(self.config, dest)
        (dest / "launch.out").write_text("途中\n", encoding="utf-8")
        with self.assertRaisesRegex(ps.StageError, "終わっていません"):
            ps.collect(self.config, dest)
        self.assertTrue(dest.is_dir())

    def test_collect_brings_the_record_back_and_removes_the_copy(self):
        dest, rec = self.finished()
        recs = ps.collect(self.config, dest)
        store = self.out / "proposer"
        self.assertFalse(dest.exists())
        stamp = "20261107-030000-000000"
        self.assertEqual(sorted(p.name for p in store.iterdir()),
                         sorted(f"{stamp}{s}" for s in (".json", ".log", ".prompt.txt", ".proposal.md", ".launch.out")))
        self.assertEqual(len(recs), 1)
        self.assertEqual(Path(recs[0]["proposal"]), store / f"{stamp}.proposal.md")
        self.assertEqual(recs[0]["staged"], {"dest": str(dest), "rev": git(self.repo, "rev-parse", "HEAD").decode().strip(),
                                             "account": "bench"})
        self.assertEqual(json.loads((store / f"{stamp}.json").read_text(encoding="utf-8")), recs[0])
        self.assertTrue(pl.ok(recs[0]))

    def test_collect_does_not_overwrite_a_record(self):
        dest, _ = self.finished()
        (self.out / "proposer").mkdir(parents=True)
        (self.out / "proposer" / "20261107-030000-000000.log").write_text("前の記録\n", encoding="utf-8")
        with self.assertRaisesRegex(ps.StageError, "同じ名前の記録"):
            ps.collect(self.config, dest)
        self.assertTrue(dest.is_dir())

    def test_only_copies_are_removed(self):
        stray = self.local / "bench" / "proposer" / "other"
        stray.mkdir(parents=True)
        for target in (self.tmp, stray, self.local / "bench"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ps.StageError, "写しの置き場ではありません"):
                    ps.discard(target)
                with self.assertRaisesRegex(ps.StageError, "写しの置き場ではありません"):
                    ps.collect(self.config, target)
                self.assertTrue(target.is_dir())

    def test_discard_removes_a_copy_that_was_not_launched(self):
        dest = self.prepare()
        ps.discard(dest)
        self.assertFalse(dest.exists())


class Main(Base):
    def test_prepare_prints_the_copy_and_the_runas_line(self):
        with mock.patch.object(ps, "ROOT", self.repo), mock.patch("builtins.print") as out:
            code = ps.main(["prepare", "--tag", "v1", "--config", str(self.config)])
        self.assertEqual(code, 0)
        lines = [c.args[0] for c in out.call_args_list]
        dest = Path(lines[0][len("- 写し: "):])
        self.assertEqual(dest.parent, self.local / "bench" / "proposer")
        self.assertTrue((dest / "launch.bat").is_file())
        self.assertEqual(lines[1], f'- bench で 1 回: runas /user:bench "cmd /c {dest / "launch.bat"}"')

    def test_a_gate_returns_2_and_writes_nothing(self):
        with mock.patch.object(ps, "ROOT", self.repo), mock.patch("sys.stderr"):
            self.assertEqual(ps.main(["prepare", "--tag", "v9", "--config", str(self.config)]), 2)
        self.assertFalse((self.local / "bench" / "proposer").exists())


if __name__ == "__main__":
    unittest.main()
