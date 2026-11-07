"""改善ループの PR の本文のひな形（harness/cycle_pr.py）の検査。数のほかの文が公開に出ないことを見る。

    python -m unittest tests.test_cycle_pr -v
"""
import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import cycle_pr as cp  # noqa: E402


def run(passed=16, new=(121, 124), kept=(404, 404), tok=23739632):
    return {"passed": passed, "stages": 18, "new": list(new), "kept": list(kept), "tokens": tok, "invalid": 0}


REC = {"id": "c1", "rev": "4ac2ed3", "head": "abc1234", "dev": run(), "dev_base": [run(), run(passed=15)],
       "held": run(passed=14, new=(87, 96), kept=(348, 349)), "held_base": [run(passed=12, new=(83, 96), kept=(343, 343))],
       "pair": {"stages": ["B08:4", "B09:6"], "fixed": 1, "broke": 0, "same": 1, "skipped": 0}}


class PublicProblems(unittest.TestCase):
    def test_a_clean_text_has_none(self):
        self.assertEqual(cp.public_problems("a.json", '{"base": "B07", "reason": "前の段（s3）が無効"}', {"test_secret"}, {"secretpkg"}), [])

    def test_local_paths_are_found_with_the_line_only(self):
        for line in ("C:\\src\\.local\\x", "c:/users/bench/y", "${local}/z", "/home/u/.local/bin"):
            got = cp.public_problems("a.json", f"ok\n{line}\n")
            self.assertEqual(got, ["ローカルのパス: a.json:2"], line)

    def test_hidden_names_and_packages_are_found_without_showing_them(self):
        got = cp.public_problems("a.json", "x test_secret y\nSecretPkg_step\nsecretpkg\n", {"test_secret"}, {"secretpkg"})
        self.assertEqual(got, ["漏れ: a.json:1", "漏れ: a.json:2", "漏れ: a.json:3"])

    def test_the_real_public_records_are_clean_of_paths(self):
        """L6 までの公開の集計に当てて、当たりにならないことを見る（当たるなら、機械が記録の PR を止めてしまう）。"""
        root = HERE.parent / "bench" / "records"
        files = [*root.glob("chains/*/*.json"), *root.glob("pairs/*.json")]
        self.assertTrue(files)
        hits = [h for p in files for h in cp.public_problems(p.name, p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])


class Body(unittest.TestCase):
    def test_the_numbers_are_in_the_body(self):
        body = cp.surface_body(REC)
        for text in ("4ac2ed3", "abc1234", "B08:4", "直した 1", "壊した 0", "| 開発用：新版 | 16/18 | 121/124 | 404/404 | 2,374 万 |",
                     "| held-out：新版 | 14/18 | 87/96 | 348/349 |", "Argon の事後の評価はこれから", cp.GENERATED):
            self.assertIn(text, body)

    def test_the_argon_verdict_goes_in_as_one_line(self):
        body = cp.surface_body(REC, ("LOW", "判断点の\n文"))
        self.assertIn("判定 LOW：判断点の 文", body)
        with self.assertRaises(cp.TemplateError):
            cp.surface_body(REC, ("たぶん良い", "x"))

    def test_free_text_in_any_field_stops_the_body(self):
        """欄に文が紛れたら（機械の記録の誤り）、本文を作らない。"""
        for path, value in ((("id",), "直す所は x"), (("rev",), "not a sha"), (("pair", "stages"), ["B08 の題"]), (("pair", "fixed"), "1 件"),
                            (("dev", "passed"), "十六"), (("dev", "new"), [1]), (("held", "tokens"), True), (("pair", "fixed"), -1)):
            rec = copy.deepcopy(REC)
            node = rec
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.assertRaises(cp.TemplateError, msg=str(path)):
                cp.surface_body(rec)

    def test_nothing_but_the_template_comes_from_the_record(self):
        """記録に余分な欄（提案の文）があっても、本文に入らない。"""
        rec = {**REC, "proposal": "狙う失敗の型の文", "dev": {**run(), "note": "試験の名 test_secret"}}
        for body in (cp.surface_body(rec), cp.argon_focus(rec), cp.records_body(rec, "取り込まない")):
            self.assertNotIn("狙う失敗の型の文", body)
            self.assertNotIn("test_secret", body)

    def test_held_out_has_totals_only(self):
        body = cp.surface_body(REC)
        self.assertNotIn("B05", body)
        self.assertNotIn("B06", body)
        self.assertNotIn("B10", body)

    def test_the_argon_material_asks_the_three_questions(self):
        focus = cp.argon_focus(REC)
        self.assertIn(cp.ARGON_ASK, focus)
        self.assertIn("直した 1", focus)

    def test_the_records_body_names_the_cycle_and_the_outcome(self):
        body = cp.records_body(REC, "取り込まない")
        self.assertIn("周期 c1", body)
        self.assertIn("結果：取り込まない", body)


if __name__ == "__main__":
    unittest.main()
