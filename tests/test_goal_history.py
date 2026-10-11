"""目的までの距離の履歴（harness/goal_distance.py）の検査：最初の行・完了が増えた行・増えないときの無記録・後退の行・取得失敗を比べないこと・直近 5 行の表示。
距離と台帳は差し替え、履歴は一時ディレクトリに書く。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import goal_distance as gd  # noqa: E402


def task(tid, status="completed"):
    return {"id": tid, "title": tid, "status": status, "depends_on": []}


def bodies(a=0, b=0, c=(0, 0, "なし"), r3=(0, "なし"), r4=0):
    return {"R3-a": f"判定役の照合が通る課題 {a}／2", "R3-b": f"判定役の照合が通る課題 {b}／1",
            "R3-c": f"床の検査が合格の課題 {c[0]}／10、両条件の記録がそろう課題 {c[1]}／10、凍結 {c[2]}",
            "R3": f"凍結の後の記録 {r3[0]}／60、判定 {r3[1]}", "R4": f"続いた週次の周期 {r4}／4"}


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = {"out": str(self.tmp / "out")}
        self.clock = 0

    def run_section(self, done, **kw):
        tasks = [task(i) for i in done] + [task("P", "pending")]
        with mock.patch.object(gd, "measure", return_value=(tasks, bodies(**kw))), \
                mock.patch.object(gd, "now_text", side_effect=lambda: f"2026-10-11 10:{self.clock:02d}"):
            self.clock += 1
            return gd.section(self.cfg)

    def rows(self):
        return gd.read_history(self.cfg)

    def test_the_first_run_writes_the_first_row_with_no_added_tasks(self):
        text = self.run_section(["A"], a=1)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["added"], [])
        self.assertEqual(rows[0]["done"], ["A"])
        self.assertIsNone(rows[0]["before"])
        self.assertEqual(rows[0]["after"]["R3-a 課題"], 1)
        self.assertIn(gd.HISTORY_TITLE, text)
        self.assertIn("最初の記録", text)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("- "))

    def test_a_grown_completed_set_adds_a_row_whoever_completed_it(self):
        self.run_section(["A"], a=1)
        self.run_section(["A", "W-1"], a=2)          # H ラインが完了にした
        self.run_section(["A", "W-1", "W-2", "W-3"], a=2, b=1)   # 操縦士がまとめて完了にした
        rows = self.rows()
        self.assertEqual([r["added"] for r in rows], [[], ["W-1"], ["W-2", "W-3"]])
        self.assertEqual(rows[1]["before"]["R3-a 課題"], 1)
        self.assertEqual(rows[1]["after"]["R3-a 課題"], 2)
        self.assertEqual(rows[2]["before"]["R3-b 課題"], 0)
        self.assertEqual(rows[2]["after"]["R3-b 課題"], 1)
        self.assertEqual(rows[2]["done"], ["A", "W-1", "W-2", "W-3"])
        self.assertTrue(all(isinstance(r["time"], str) and r["time"] for r in rows))

    def test_no_row_is_added_when_nothing_grew_or_the_set_only_shrank(self):
        self.run_section(["A", "B"], a=1)
        self.run_section(["A", "B"], a=1)
        self.run_section(["A"], a=1)                  # 減っただけ
        self.assertEqual(len(self.rows()), 1)

    def test_a_smaller_amount_prints_a_regression_line_with_values_and_the_last_added_task(self):
        self.run_section(["A"], a=2, r4=1)
        self.run_section(["A", "W-9"], a=2, r4=1)
        text = self.run_section(["A", "W-9"], a=1, r4=1)    # 完了は増えない。R3-a だけ減った
        lines = [x for x in text.splitlines() if x.startswith(gd.REGRESSION)]
        self.assertEqual(len(lines), 1, text)
        self.assertIn("R3-a", lines[0])
        self.assertIn("2 → 1", lines[0])
        self.assertIn("W-9", lines[0])
        self.assertLess(text.index(lines[0]), text.index(gd.HISTORY_TITLE))
        self.assertEqual(len(self.rows()), 2)

    def test_a_regression_is_also_marked_when_the_same_run_adds_a_row(self):
        self.run_section(["A"], r3=(5, "なし"))
        text = self.run_section(["A", "W-7"], r3=(3, "なし"))
        lines = [x for x in text.splitlines() if x.startswith(gd.REGRESSION)]
        self.assertEqual(len(lines), 1, text)
        self.assertIn("R3", lines[0])
        self.assertIn("5 → 3", lines[0])
        self.assertIn("W-7", lines[0])

    def test_no_regression_when_amounts_do_not_fall(self):
        self.run_section(["A"], a=1)
        text = self.run_section(["A", "W-1"], a=2)
        self.assertNotIn(gd.REGRESSION, text)

    def test_an_unreadable_quantity_is_not_compared(self):
        self.run_section(["A"], a=2, b=1)
        failed = bodies(a=0, b=1)
        failed["R3-a"] = "取得失敗：git が落ちた"
        tasks = [task("A")]
        with mock.patch.object(gd, "measure", return_value=(tasks, failed)):
            text = gd.section(self.cfg)
        self.assertNotIn(gd.REGRESSION, text)
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn("R3-a 課題", gd.amounts(failed))

    def test_the_freeze_and_the_verdict_count_as_amounts(self):
        self.run_section(["A"], c=(1, 1, "あり"), r3=(2, "あり"))
        text = self.run_section(["A"], c=(1, 1, "なし"), r3=(2, "あり"))
        lines = [x for x in text.splitlines() if x.startswith(gd.REGRESSION)]
        self.assertEqual(len(lines), 1, text)
        self.assertIn("R3-c", lines[0])
        self.assertIn("1 → 0", lines[0])

    def test_the_prerequisite_count_is_not_an_amount(self):
        self.assertNotIn("前提", " ".join(gd.amounts(bodies())))
        self.assertEqual(sorted(gd.amounts(bodies())), sorted([
            "R3-a 課題", "R3-b 課題", "R3-c 床", "R3-c 両条件", "R3-c 凍結", "R3 記録", "R3 判定", "R4 周期"]))

    def test_only_the_newest_five_rows_are_shown_newest_first_with_added_ids_and_changes(self):
        done = ["A"]
        for n in range(7):
            self.run_section(list(done), a=n)
            done.append(f"W-{n}")
        text = self.run_section(list(done), a=7)
        shown = text.split(gd.HISTORY_TITLE)[1].strip().splitlines()
        self.assertEqual(len(shown), 5, shown)
        self.assertIn("W-6", shown[0])
        self.assertIn("6 → 7", shown[0])
        self.assertIn("W-2", shown[4])
        self.assertIn("2 → 3", shown[4])
        self.assertEqual(len(self.rows()), 8)

    def test_an_unwritable_history_still_returns_the_section(self):
        blocker = self.tmp / "out"
        blocker.write_text("file", encoding="utf-8")    # out がファイルで、履歴を書けない
        text = self.run_section(["A"], a=1)
        self.assertTrue(text.startswith("## 目的までの距離\n- R3-a:"), text)
        self.assertIn(gd.HISTORY_TITLE, text)
        self.assertIn("取得失敗", text)

    def test_a_corrupt_history_still_returns_the_section(self):
        p = gd.history_path(self.cfg)
        p.parent.mkdir(parents=True)
        p.write_text("{not json\n", encoding="utf-8")
        text = self.run_section(["A"], a=1)
        self.assertIn("## 目的までの距離", text)
        self.assertIn("取得失敗", text)

    def test_rows_are_json_lines_in_the_out_directory(self):
        self.run_section(["A"], a=1)
        lines = gd.history_path(self.cfg).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(set(json.loads(lines[0])), {"time", "added", "before", "after", "done"})
        self.assertEqual(gd.history_path(self.cfg).parent, Path(self.cfg["out"]))


if __name__ == "__main__":
    unittest.main()
