"""目的までの距離（harness/goal_distance.py）の検査：5 つの横木の量・前提タスクの推移的な数え方・取得失敗の行・report.md の先頭。
git と照合の結果は差し替え、本物の git・gh は呼ばない。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import gate_flow  # noqa: E402
import goal_distance as gd  # noqa: E402
import hline_report  # noqa: E402

CFG = {"integration_branch": "hline/integration", "base": "origin/main", "ttl_seconds": {"git": 5}}


def task(tid, status="completed", deps=(), rung=None, oracle=()):
    t = {"id": tid, "title": tid, "status": status, "depends_on": list(deps), "oracle": [{"path": p} for p in oracle]}
    if rung:
        t["rung"] = rung
    return t


LEDGER = {"tasks": [
    task("A"), task("B", "pending", ["A"]), task("C", "pending", ["B", "A"]),
    task("X1", "completed", ["A"], "R3-a", ["tests/launcher_judge.py"]),
    task("X2", "pending", ["C", "X1"], "R3-a", ["tests/other_judge.py"]),
    task("Y", "pending", ["X1"], "R3-b", ["tests/third.py"]),
    task("W-59", "pending"),
    task("Z", "pending", ["ghost"], "R3-c"),
]}


class Fake:
    """台帳・記録・照合・周期を差し替える。files は bench/records の下のパス → JSON。"""

    def __init__(self, tasks=None, files=None, passing=(), cycles=0, ledger_fails=0, paths_error=None):
        self.ledger = {"tasks": LEDGER["tasks"] if tasks is None else tasks}
        self.files, self.passing, self.cycles, self.ledger_fails = files or {}, set(passing), cycles, ledger_fails
        self.refs, self.paths_error = [], paths_error

    def show(self, cfg, ref, path):
        self.refs.append(ref)
        if path == "docs/progress.yaml":
            if self.ledger_fails and ref == "origin/hline/integration":
                raise gd.Unreadable("無い")
            import yaml
            return yaml.safe_dump(self.ledger, allow_unicode=True)
        if path not in self.files:
            raise gd.Unreadable(f"{path} が無い")
        return json.dumps(self.files[path])

    def paths(self, cfg, ref):
        if self.paths_error:
            raise gd.Unreadable(self.paths_error)
        return sorted(self.files)

    def ok(self, cfg, n):
        return n <= self.cycles

    def __enter__(self):
        self.ps = [mock.patch.object(gd, "show", self.show), mock.patch.object(gd, "record_paths", self.paths),
                   mock.patch.object(gd, "passing_modules", lambda cfg: self.passing),
                   mock.patch.object(gd, "cycles_ok", self.ok)]
        [p.start() for p in self.ps]
        return self

    def __exit__(self, *a):
        [p.stop() for p in self.ps]


def lines_of(text):
    return {x.split(":")[0][2:]: x for x in text.splitlines() if x.startswith("- R")}


R = "bench/records"


class Distance(unittest.TestCase):
    def test_every_rung_is_zero_without_records_or_checks(self):
        with Fake():
            text = gd.render(CFG)
        self.assertEqual(text.splitlines()[0], "## 目的までの距離")
        got = lines_of(text)
        self.assertEqual(list(got), ["R3-a", "R3-b", "R3-c", "R3", "R4"])
        self.assertIn("判定役の照合が通る課題 0／2", got["R3-a"])
        self.assertIn("判定役の照合が通る課題 0／1", got["R3-b"])
        self.assertIn("凍結の後の記録 0／60、判定 なし", got["R3"])
        self.assertIn("続いた週次の周期 0／4", got["R4"])

    def test_r3_a_and_r3_b_count_oracle_items_whose_module_passes(self):
        with Fake(passing={"tests.launcher_judge", "tests.third"}):
            got = lines_of(gd.render(CFG))
        self.assertIn("判定役の照合が通る課題 1／2", got["R3-a"])
        self.assertIn("判定役の照合が通る課題 1／1", got["R3-b"])

    def test_prerequisites_are_counted_transitively_without_the_rungs_own_tasks(self):
        with Fake():
            got = lines_of(gd.render(CFG))
        # R3-a: X1・X2 の依存を A・B・C までたどる（X1 は自身なので含めない）。完了は A だけ
        self.assertTrue(got["R3-a"].endswith("（前提タスク 1／3）"), got["R3-a"])
        # R3-b: Y → X1 → A。X1 は別の横木のタスクだが前提に数える
        self.assertTrue(got["R3-b"].endswith("（前提タスク 2／2）"), got["R3-b"])
        # 台帳に無い依存先は未完了の前提に数える
        self.assertTrue(got["R3-c"].endswith("（前提タスク 0／1）"), got["R3-c"])
        self.assertTrue(got["R4"].endswith("（前提タスク 0／0）"), got["R4"])

    def test_r3_c_counts_floor_both_conditions_and_the_freeze(self):
        files = {f"{R}/B01/floor.json": {"passed": True}, f"{R}/B02/floor.json": {"passed": False},
                 f"{R}/B01/chat-1.json": {}, f"{R}/B01/factory-1.json": {"invalid": ""},
                 f"{R}/B02/chat-1.json": {"invalid": "x"}, f"{R}/B02/factory-1.json": {},
                 f"{R}/B03/chat-1.json": {}, f"{R}/B03/chat-2.json": {}}
        with Fake(files=files):
            line = lines_of(gd.render(CFG))["R3-c"]
        self.assertIn("床の検査が合格の課題 1／10", line)
        self.assertIn("両条件の記録がそろう課題 1／10", line)
        self.assertIn("凍結 なし", line)
        files[f"{R}/freeze.json"] = {"base": "b1"}
        with Fake(files=files):
            self.assertIn("凍結 あり", lines_of(gd.render(CFG))["R3-c"])

    def test_r3_counts_valid_records_with_the_freeze_base_and_the_verdict(self):
        files = {f"{R}/freeze.json": {"base": "b1"}, f"{R}/B01/chat-1.json": {"base": "b1"},
                 f"{R}/B01/factory-1.json": {"base": "b1", "invalid": ["x"]}, f"{R}/B02/chat-1.json": {"base": "b0"},
                 f"{R}/B02/factory-1.json": {"base": "b1", "invalid": ""}}
        with Fake(files=files):
            self.assertIn("凍結の後の記録 2／60、判定 なし", lines_of(gd.render(CFG))["R3"])
        files[f"{R}/verdict.json"] = {}
        with Fake(files=files):
            self.assertIn("判定 あり", lines_of(gd.render(CFG))["R3"])

    def test_r4_is_zero_until_w59_is_done_then_the_longest_run_that_holds(self):
        tasks = [task("W-59", "pending")]
        with Fake(tasks=tasks, cycles=4):
            self.assertIn("続いた週次の周期 0／4", lines_of(gd.render(CFG))["R4"])
        tasks = [task("W-59", "completed")]
        for cycles, want in ((4, 4), (2, 2), (0, 0)):
            with Fake(tasks=tasks, cycles=cycles):
                self.assertIn(f"続いた週次の周期 {want}／4", lines_of(gd.render(CFG))["R4"])

    def test_an_unreadable_quantity_says_so_and_the_other_rungs_still_print(self):
        with Fake(paths_error="ls-tree が落ちた"):   # bench/records が読めない
            got = lines_of(gd.render(CFG))
        self.assertIn("取得失敗", got["R3-c"])
        self.assertIn("ls-tree が落ちた", got["R3-c"])
        self.assertIn("取得失敗", got["R3"])
        self.assertNotIn("取得失敗", got["R3-a"])
        self.assertTrue(got["R3-c"].endswith("（前提タスク 0／1）"), got["R3-c"])

    def test_the_ledger_is_read_from_the_integration_branch_then_the_base(self):
        with Fake(ledger_fails=1) as fake:
            gd.render(CFG)
        self.assertEqual(fake.refs[0], "origin/hline/integration")
        self.assertEqual(fake.refs[1], "origin/main")

    def test_an_unreadable_ledger_gives_a_failure_section(self):
        with mock.patch.object(gd, "show", side_effect=gd.Unreadable("git が無い")):
            text = gd.section(CFG)
        self.assertTrue(text.startswith("## 目的までの距離\n- 取得失敗"), text)
        self.assertIn("git が無い", text)


class ReportHead(unittest.TestCase):
    def test_the_section_leads_report_md_before_the_progress_tree(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = {"inbox": str(tmp), "out": str(tmp / "out"), "integration_branch": "hline/integration", "base": "origin/main",
               "ttl_seconds": {"git": 5}}
        with mock.patch.object(gd, "section", return_value="## 目的までの距離\n- R3-a: x\n"), \
                mock.patch.object(gate_flow, "section", return_value="## 関門ごとの流れ\n- G: y\n"), \
                mock.patch.object(hline_report, "progress_report", return_value="## 進捗ツリー\n- 人間作業: NONE\n"), \
                mock.patch.object(hline_report, "h_section", return_value="## H ライン\n"), \
                mock.patch.object(hline_report.hline_probe, "safe_section", return_value="## probe\n"), \
                mock.patch.object(hline_report.hline_checks, "safe_section", return_value="## 照合\n宣言なし\n"):
            hline_report.write_report(cfg, {"items": {}, "awaiting_pr": None})
        text = (tmp / "report.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## 目的までの距離\n- R3-a: x\n\n## 関門ごとの流れ\n- G: y\n\n## 進捗ツリー\n"), text)

    def test_a_raising_section_still_writes_report_md(self):
        with mock.patch.object(gd, "section", side_effect=RuntimeError("boom")):
            text = hline_report.goal_section({})
        self.assertTrue(text.startswith("## 目的までの距離\n- 取得失敗"), text)
        self.assertIn("boom", text)


if __name__ == "__main__":
    unittest.main()
