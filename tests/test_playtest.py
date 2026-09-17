"""プレイ確認（H2）用のビルド（Step 6）。

    python -m unittest discover -s tests -v

git は一時ディレクトリの本物、エンジンは偽物（Unity は起動しない）。
- 専用ワークツリーを PR の head SHA に合わせ、同じ SHA のビルドは作り直さないこと
- ビルドがワークツリーの追跡中のファイルを変えたら止まること（非破壊の確認）
- ビルドの失敗（rc≠0・exe 無し）は rc=1、SHA が取れない等は rc=2
- Unity アダプタが、設定のビルド関数と出力先を渡して起動すること
- 分解役の playtest の宣言を検査すること
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import adapters  # noqa: E402
import decompose  # noqa: E402
import playtest  # noqa: E402
import project  # noqa: E402
import telemetry  # noqa: E402
from adapters import unity  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


class FakeEngine:
    """build_player だけを持つ偽のエンジン。mode でビルドの結果を変える。"""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    def build_player(self, c, root, exe, log):
        self.calls.append((Path(root), Path(exe)))
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text("log", encoding="utf-8")
        if self.mode in ("ok", "dirty"):
            Path(exe).parent.mkdir(parents=True, exist_ok=True)
            Path(exe).write_bytes(b"MZ")
        if self.mode == "dirty":
            (Path(root) / "Game" / "ProjectSettings.asset").write_text("rewritten", encoding="utf-8")
        if self.mode == "fail":
            return 1, "compile error"
        return 0, ""


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        (self.repo / "Game").mkdir()
        (self.repo / "Game" / "ProjectSettings.asset").write_text("original", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.sha = git(self.repo, "rev-parse", "HEAD")
        self.wt = self.tmp / "wt" / "p-playtest"
        self.out = self.tmp / "out"
        cfg = {"paths": {"playtest_worktree": str(self.wt), "playtest_out": str(self.out)},
               "project": {"id": "p", "unity_project_subdir": "Game"}}
        self.c = SimpleNamespace(cfg=cfg, ttl={"git": 60, "playtest_build": 10}, repo=self.repo)
        self.addCleanup(lambda: subprocess.run(["git", "worktree", "prune"], cwd=str(self.repo),
                                               capture_output=True, timeout=60))

    def test_builds_once_per_sha_and_syncs_the_worktree(self):
        engine = FakeEngine("ok")
        rc, msg, info = playtest.build(self.c, engine, 7, self.sha, sleep=lambda s: None)
        self.assertEqual(rc, 0, msg)
        self.assertFalse(info["skipped"])
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.sha)
        meta, _ = telemetry.read(self.out / "p" / f"pr7-{self.sha[:8]}" / "build.json")
        self.assertEqual(meta["sha"], self.sha)
        self.assertTrue(Path(meta["exe"]).exists())

        rc, msg, info = playtest.build(self.c, engine, 7, self.sha, sleep=lambda s: None)
        self.assertEqual((rc, info["skipped"]), (0, True))
        self.assertEqual(len(engine.calls), 1, "同じ SHA は作り直さない")

    def test_new_sha_moves_the_worktree(self):
        playtest.build(self.c, FakeEngine("ok"), 7, self.sha, sleep=lambda s: None)
        (self.repo / "Game" / "Player.cs").write_text("// v2", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "v2")
        sha2 = git(self.repo, "rev-parse", "HEAD")
        (self.wt / "stray.txt").write_text("前回の残り", encoding="utf-8")
        rc, msg, _ = playtest.build(self.c, FakeEngine("ok"), 7, sha2, sleep=lambda s: None)
        self.assertEqual(rc, 0, msg)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), sha2)
        self.assertFalse((self.wt / "stray.txt").exists())

    def test_build_that_rewrites_tracked_files_is_an_environment_stop(self):
        with self.assertRaises(playtest.Stop) as ctx:
            playtest.build(self.c, FakeEngine("dirty"), 7, self.sha, sleep=lambda s: None)
        self.assertEqual(ctx.exception.rc, 2)
        self.assertIn("ProjectSettings.asset", str(ctx.exception))
        self.assertFalse((self.out / "p" / f"pr7-{self.sha[:8]}" / "build.json").exists())

    def test_failed_build_is_rc1(self):
        for mode in ("fail", "no_exe"):
            with self.subTest(mode):
                rc, msg, _ = playtest.build(self.c, FakeEngine(mode), 7, self.sha, sleep=lambda s: None)
                self.assertEqual(rc, 1, msg)
                self.assertIn("ログ", msg)

    def test_unknown_sha_that_cannot_be_fetched_stops(self):
        with self.assertRaises(playtest.Stop) as ctx:
            playtest.build(self.c, FakeEngine("ok"), 7, "0" * 40, sleep=lambda s: None)
        self.assertEqual(ctx.exception.rc, 2)

    def test_main_rejects_a_malformed_sha_and_still_writes_telemetry(self):
        tel = self.tmp / "t.json"
        with mock.patch("sys.stdout"):
            rc = playtest.main(["--project", "unity-2d", "--pr", "1", "--sha", "abc", "--telemetry", str(tel)])
        self.assertEqual(rc, 2)
        self.assertEqual(telemetry.read(tel)[0]["exit_code"], 2)


class UnityBuildPlayerTests(unittest.TestCase):
    def ctx(self, **cfg):
        base = {"unity_exe": "Unity.exe", "playtest_build_method": "Game.EditorScripts.PlaytestBuild.BuildWindows",
                "project": {"unity_project_subdir": "Game"}}
        base.update(cfg)
        return SimpleNamespace(cfg=base, ttl={"playtest_build": 5})

    def test_passes_the_configured_method_and_output(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(unity, "run", return_value=(0, "", "")) as run:
            rc, why = unity.build_player(self.ctx(), Path(d), Path(d) / "o" / "Playtest.exe", Path(d) / "l.log")
        args = run.call_args[0][0]
        self.assertEqual(rc, 0)
        self.assertIn("-batchmode", args)
        self.assertEqual(args[args.index("-executeMethod") + 1], "Game.EditorScripts.PlaytestBuild.BuildWindows")
        self.assertTrue(args[args.index("-playtestOutput") + 1].endswith("Playtest.exe"))
        self.assertEqual(args[args.index("-buildTarget") + 1], "Win64")
        self.assertTrue(args[args.index("-projectPath") + 1].endswith("Game"))
        self.assertEqual(run.call_args[0][2], 5, "TTL は ttl_seconds.playtest_build")

    def test_missing_configuration_is_an_adapter_error(self):
        for cfg in ({"playtest_build_method": ""}, {"project": {}}):
            with self.subTest(cfg), self.assertRaises(adapters.AdapterError):
                unity.build_player(self.ctx(**cfg), Path("."), Path("x.exe"), Path("x.log"))

    def test_real_project_config_has_playtest_keys(self):
        cfg = project.pipeline_config(project.load("unity-2d"))
        self.assertTrue(cfg["playtest_build_method"])
        for key in ("playtest_worktree", "playtest_out"):
            self.assertIn(key, cfg["paths"])
        self.assertIn("playtest_build", cfg["ttl_seconds"])


class DecomposePlaytestDeclarationTests(unittest.TestCase):
    def unit(self, **over):
        d = {"id": "u", "title": "t", "prompt": "p", "whitelist": ["Game/Assets/Core/X.cs"],
             "required_symbols": ["X"], "acceptance": {"required_tests": ["XTests"]},
             "human_check_point": "見る点", "playtest": "none",
             "test_files": [{"path": "tests/Core.Tests/XTests.cs",
                             "content": "[Test] public void A() { Assert.AreEqual(1, X.One()); }"}]}
        d.update(over)
        return d

    def problems(self, d):
        with mock.patch.object(decompose, "CFG", project.config("decompose")):
            return decompose.validate(d)

    def test_valid_declarations(self):
        for value in ("none", "required"):
            with self.subTest(value):
                self.assertEqual(self.problems(self.unit(playtest=value)), [])

    def test_missing_or_invalid_declaration_is_rejected(self):
        missing = self.unit()
        del missing["playtest"]
        self.assertTrue(any("playtest" in p for p in self.problems(missing)))
        for value in ("None", "yes", True, ""):
            with self.subTest(value):
                self.assertTrue(any("playtest" in p for p in self.problems(self.unit(playtest=value))))


if __name__ == "__main__":
    unittest.main()
