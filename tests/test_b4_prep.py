"""無人連続走行の準備（B4-PREP）。CI の観測と赤の区別・門ごとの所要時間・高速検査の復元省略・
不変条件の作業場所の再利用・anchor の hook 出力。

    python -m unittest discover -s tests -v

**なぜ要るか**: B4-RUN は人間が見ていない間に 5 機能を続けて回す。配管の故障（CI を観測できない、
復元の省略で落ちた）を実装の過失として差し戻すと、正しい実装が壊される。どこで時間を使ったかが
記録に残らないと、ボトルネックを数字で選べない。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import invrun  # noqa: E402
import pipeline  # noqa: E402
import progress  # noqa: E402
from adapters import dotnet  # noqa: E402


class ClassifyCi(unittest.TestCase):
    """watch が落ちたときに、赤（1）と観測できない（2）を分ける。2 のときは差し戻さない。"""

    def test_completed_failure_is_red(self):
        rc, _ = pipeline.classify_ci(json.dumps({"status": "completed", "conclusion": "failure"}), "9")
        self.assertEqual(rc, 1)

    def test_completed_success_is_green_even_if_watch_failed(self):
        rc, _ = pipeline.classify_ci(json.dumps({"status": "completed", "conclusion": "success"}), "9")
        self.assertEqual(rc, 0)

    def test_unreadable_or_unfinished_is_infrastructure(self):
        for viewed in ("", "not json", json.dumps({"status": "in_progress", "conclusion": ""})):
            with self.subTest(viewed):
                rc, msg = pipeline.classify_ci(viewed, "9", "network")
                self.assertEqual(rc, 2)
                self.assertIn("差し戻しません", msg)


class GateTiming(unittest.TestCase):
    """門が変わるたびに、前の門にいた時間を試行の記録へ足す。判定には使わない。"""

    def test_stages_accumulate_per_gate(self):
        c = pipeline.Ctx.__new__(pipeline.Ctx)
        clock = iter([0.0, 1.0, 4.0, 4.5, 6.0])
        with mock.patch.object(pipeline.time, "monotonic", lambda: next(clock)):
            c._gate, c._gate_t0, c._gate_sink = None, 0.0, None
            c.cur = {}
            c.gate = "implementer"      # t=0
            c.gate = "fast"             # t=1 → implementer 1.0
            c.gate = "implementer"      # t=4 → fast 3.0
            c.gate = "fast"             # t=4.5 → implementer 0.5
            c.flush_gate()              # t=6 → fast 1.5（門はそのまま）
        self.assertEqual(c.gate, "fast")
        self.assertEqual(c.cur["stages"], {"implementer": 1.5, "fast": 4.5})


class FastRestore(unittest.TestCase):
    """2 回目以降は --no-restore。省いたせいで落ちたら、復元ありで 1 回だけやり直す。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = SimpleNamespace(out=self.tmp, sandbox=self.tmp, unit={"fast_test_project": "t.csproj"},
                                 ttl={"fast_tests": 60}, metrics={})
        self.calls = []

    def fake_run(self, fail_first_no_restore):
        trx_empty = ('<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
                     '<ResultSummary><Counters total="0" passed="0" failed="0" notExecuted="0"/></ResultSummary></TestRun>')

        def run(args, cwd, ttl, label):
            self.calls.append("--no-restore" in args)
            tag = next(a for a in args if a.startswith("trx;LogFileName=")).split("=", 1)[1]
            if "--no-restore" in args and fail_first_no_restore:
                return 1, "error NETSDK1004: Assets file 'project.assets.json' not found", ""
            (self.tmp / tag).write_text(trx_empty, encoding="utf-8")
            return 0, "", ""
        return run

    def test_second_run_skips_restore(self):
        with mock.patch.object(dotnet, "run", self.fake_run(False)):
            dotnet.run_tests(self.c, "a")
            dotnet.run_tests(self.c, "b")
        self.assertEqual(self.calls, [False, True])

    def test_falls_back_to_restore_when_assets_are_missing(self):
        self.c.fast_restored = True
        with mock.patch.object(dotnet, "run", self.fake_run(True)):
            results, err = dotnet.run_tests(self.c, "a")
        self.assertIsNone(err)
        self.assertEqual(self.calls, [True, False])


class InvariantsWorkdir(unittest.TestCase):
    def test_obj_and_bin_survive_other_files_are_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            work, sb = Path(d) / "out" / "work", Path(d) / "sb"
            sb.mkdir()
            (work / "obj").mkdir(parents=True)
            (work / "obj" / "project.assets.json").write_text("{}", encoding="utf-8")
            (work / "Old.txt").write_text("stale", encoding="utf-8")
            invrun.materialize(work, sb, "Core", {"New.txt": "x"})
            self.assertTrue((work / "obj" / "project.assets.json").exists())
            self.assertFalse((work / "Old.txt").exists())
            self.assertTrue((work / "New.txt").exists())


class AnchorHook(unittest.TestCase):
    def test_hook_output_carries_the_anchor_as_additional_context(self):
        state = progress.load(ROOT / progress.REL_PATH)
        out = json.loads(progress.hook_output(state))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], progress.anchor(state))


if __name__ == "__main__":
    unittest.main()
