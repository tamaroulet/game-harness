"""関門ごとの流れの節（harness/gate_flow.py）の検査：現在の関門だけが詳しく出る・purpose は着手可の行だけ・待ちの行は未完了の前段・60 行に収まる・読めないとき 1 行。
台帳は小さな偽の辞書で渡し、本物の git は呼ばない。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import gate_flow  # noqa: E402
import goal_distance  # noqa: E402


def task(tid, deps=(), by="hline", status="pending", rung=None):
    t = {"id": tid, "title": f"題-{tid}", "status": status, "by": by, "depends_on": list(deps), "purpose": f"{tid}-PURPOSE"}
    if rung:
        t["rung"] = rung
    return t


def small():
    return {"tasks": [
        task("M1", by="pilot"),
        task("T1", ["M1"]),
        task("G1", ["T1"], rung="R3-a"),
        task("G2", ["G1"], rung="R3-b"),
    ]}


class Flow(unittest.TestCase):
    def test_only_the_current_gate_is_detailed(self):
        text = "\n".join(gate_flow.render(small()))
        self.assertTrue(text.startswith("## 関門ごとの流れ"), text)
        self.assertIn("現在の関門 G1", text)
        self.assertIn("中間関門 M1", text)
        other = [x for x in text.splitlines() if "G2" in x]
        self.assertEqual(len(other), 1, text)
        self.assertIn("済み 0・着手可 0・待ち 1", other[0])
        self.assertNotIn("G2-PURPOSE", text)

    def test_purpose_and_successors_only_on_ready_lines(self):
        text = "\n".join(gate_flow.render(small()))
        ready = [x for x in text.splitlines() if x.startswith("- M1 ")]
        self.assertEqual(len(ready), 1, text)
        self.assertIn("M1-PURPOSE", ready[0])
        self.assertIn("by: pilot", ready[0])
        self.assertIn("後段: T1", ready[0])
        self.assertNotIn("T1-PURPOSE", text)

    def test_a_waiting_line_names_the_unfinished_prerequisites(self):
        text = "\n".join(gate_flow.render(small()))
        waiting = [x for x in text.splitlines() if x.startswith("- T1 ")]
        self.assertEqual(len(waiting), 1, text)
        self.assertIn("未完了の前段: M1", waiting[0])

    def test_a_big_ledger_fits_in_sixty_lines(self):
        tasks = [task("M1", by="pilot")] + [task(f"T{i}", ["M1"]) for i in range(70)]
        tasks.append(task("G1", [f"T{i}" for i in range(70)], rung="R3-a"))
        tasks += [task(f"G{n}", ["G1"], rung="R3-b") for n in range(2, 40)]
        lines = gate_flow.render({"tasks": tasks})
        self.assertLessEqual(len(lines), 60)
        self.assertIn("待ち 71 件", "\n".join(lines))

    def test_an_unreadable_ledger_gives_a_one_line_reason(self):
        with mock.patch.object(goal_distance, "load_ledger", side_effect=goal_distance.Unreadable("git が無い")):
            text = gate_flow.section({})
        self.assertEqual(len(text.splitlines()), 2, text)
        self.assertTrue(text.startswith("## 関門ごとの流れ\n- 取得失敗"), text)
        self.assertIn("git が無い", text)

    def test_the_section_is_rendered_from_the_loaded_ledger(self):
        with mock.patch.object(goal_distance, "load_ledger", return_value=("ref", small())):
            text = gate_flow.section({})
        self.assertIn("現在の関門 G1", text)


if __name__ == "__main__":
    unittest.main()
