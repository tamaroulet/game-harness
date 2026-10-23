"""失敗の分類の計器（harness/failure_classes.py）（進捗のタスク G0-4）。

    python -m unittest tests.test_failure_classes -v

合成の Gate 1 の出力と試行の記録で、分類・先に当たる順・落ちたモジュールとテストの名前・未収束の failure_class・
日ごとの件数の表・同じ入力で同じ表になること・計器が止まっても report.md が書かれることを確かめる。
"""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import failure_classes as fc  # noqa: E402
import hline_abort  # noqa: E402
import hline_report  # noqa: E402

SUITE_FAIL = ("===== 失敗: tests.test_a（終了コード 1）=====\nFAIL: test_x (tests.test_a.A.test_x)\n"
              "ERROR: test_y (tests.test_a.A.test_y)\n走ったテスト: 3 件（2 モジュール）\n落ちたモジュール: tests.test_a, tests.test_b\n")
HEAD = "検証コマンド `python -m unittest tests.test_a` が終了コード 1（期待 0）\n"


class Classify(unittest.TestCase):
    def test_each_class(self):
        cases = [
            ({"abort": "実装できない"}, None, "abandon"),
            ({"abort": hline_abort.REPEAT + "。繰り返し"}, None, "repeat"),
            ({}, "変えてはならないパスを変えています: docs/progress.yaml。元に戻してください。", "protected"),
            ({}, "検証コマンド `x` が終了コード 124（期待 0）\nTTL超過 (60s): Gate 1", "timeout"),
            ({"suite": {"modules": ["tests.test_a"]}}, SUITE_FAIL, "full_suite"),
            ({}, HEAD + "FAIL: test_x (tests.test_a.A.test_x)", "test"),
            ({"cutoff": "ターン数の上限 30 で打ち切られました"}, "作業ツリーに変更がありません。", "cutoff"),
            ({}, "作業ツリーに変更がありません。", "other"),
        ]
        for rec, text, want in cases:
            with self.subTest(want=want):
                self.assertEqual(fc.classify(rec, text), want)

    def test_earlier_class_wins(self):
        full = {"suite": {}, "cutoff": "x"}
        self.assertEqual(fc.classify({**full, "abort": "断念"}, SUITE_FAIL), "abandon")
        self.assertEqual(fc.classify(full, "変えてはならないパスを変えています: a。TTL超過"), "protected")
        self.assertEqual(fc.classify(full, SUITE_FAIL + "TTL超過"), "timeout")
        self.assertEqual(fc.classify(full, SUITE_FAIL), "full_suite")
        self.assertEqual(fc.classify({"cutoff": "x"}, SUITE_FAIL), "test")

    def test_names_are_taken_from_the_output(self):
        d = fc.describe({"suite": {}}, SUITE_FAIL)
        self.assertEqual(d["modules"], ["tests.test_a", "tests.test_b"])
        self.assertEqual(d["tests"], ["test_x (tests.test_a.A.test_x)", "test_y (tests.test_a.A.test_y)"])
        self.assertEqual(fc.describe({"abort": "x"}, None), {"class": "abandon", "modules": [], "tests": []})
        self.assertEqual(fc.describe({}, "落ちたモジュール: なし")["modules"], [])

    def test_last_class_of_an_item(self):
        item = {"tries": [{"failure": {"class": "test"}}, {"failure": {"class": "repeat"}}]}
        self.assertEqual(fc.last_class(item), "repeat")
        self.assertEqual(fc.last_class({"tries": [{}]}), "other")
        self.assertEqual(fc.last_class({"tries": []}), "other")


def bad(cls=None):
    return {"gate": False, **({"failure": {"class": cls}} if cls else {})}


ITEMS = {
    "a": {"status": "unconverged", "at": "2026-10-10", "failure_class": "test", "tries": [bad("test"), bad("test"), bad("test")]},
    "b": {"status": "unconverged", "at": "2026-10-10", "failure_class": "infra", "tries": []},
    "c": {"status": "unconverged", "at": "2026-10-09", "tries": [bad()]},                  # 古い記録
    "d": {"status": "done", "done_at": "2026-10-10T09:00:00+09:00", "tries": [bad("cutoff"), {"gate": True}]},
    "e": {"status": "unconverged", "tries": [bad("test")]},                                # 日付なし
    "f": {"status": "waiting", "deps": [], "tries": [bad("test")]},
}


def cells(text, title, day):
    block = text.split(title, 1)[1].split("\n\n", 2)[1]
    row = next(x for x in block.splitlines() if x.startswith(f"| {day} "))
    return dict(zip(["日付", *fc.COLUMNS], [c.strip() for c in row.strip("|").split("|")]))


class Table(unittest.TestCase):
    def test_counts_per_day(self):
        out = fc.render(ITEMS)
        s = cells(out, "未収束の項目", "2026-10-10")
        self.assertEqual((s["test"], s["infra"], s["不明"]), ("1", "1", "0"))
        self.assertEqual(cells(out, "未収束の項目", "2026-10-09")["不明"], "1")
        t10, t9 = cells(out, "不合格の試行", "2026-10-10"), cells(out, "不合格の試行", "2026-10-09")
        self.assertEqual((t10["test"], t10["cutoff"], t10["不明"]), ("3", "1", "0"))   # 通った試行は数えない
        self.assertEqual(t9["不明"], "1")

    def test_same_queue_gives_same_table_and_is_not_changed(self):
        items = copy.deepcopy(ITEMS)
        self.assertEqual(fc.render(items), fc.render(items))
        self.assertEqual(items, ITEMS)

    def test_main_prints_the_table_without_touching_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "queue.json"
            q.write_text(json.dumps({"items": ITEMS}, ensure_ascii=False), encoding="utf-8")
            before = q.read_bytes()
            outs = []
            for _ in range(2):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(fc.main(["--queue", str(q)]), 0)
                outs.append(buf.getvalue())
            self.assertEqual(outs[0], outs[1])
            self.assertIn("| 2026-10-10 |", outs[0])
            self.assertEqual(q.read_bytes(), before)


class Report(unittest.TestCase):
    def test_section_follows_the_instrument_table(self):
        text = hline_report.h_section({"integration_branch": "i"}, {"items": ITEMS, "awaiting_pr": None, "skipped": []})
        self.assertLess(text.index("## 計器"), text.index("### 失敗の分類"))

    def test_a_broken_instrument_becomes_one_line(self):
        with mock.patch.object(fc, "section", side_effect=RuntimeError("壊れた")):
            out = hline_report.failure_section(ITEMS)
        self.assertEqual(out, "### 失敗の分類\n- 失敗の分類を出せませんでした: RuntimeError: 壊れた\n")



class RunAsModule(unittest.TestCase):
    def test_python_dash_m_finds_the_sibling_modules(self):
        """python -m harness.<名前> では __package__ が "harness" になり、兄弟のモジュールを引けずに落ちていた（2026-10-23）。"""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        for name in ("failure_classes", "northstar"):
            r = subprocess.run([sys.executable, "-m", f"harness.{name}", "--help"], cwd=root, capture_output=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, (name, r.stderr[-400:]))


if __name__ == "__main__":
    unittest.main()
