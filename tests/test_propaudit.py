"""性質の前提が構成できるかの監査（docs/design/v2r_instrument_redesign.md の原則 P1）と、ファイルのロックの再試行（P5）。

    python -m unittest tests.test_propaudit

**なぜ要るか**: 前提が実装の結果（after）を条件にしていると、性質の空虚さが「実装の欠陥」か「系列の運」かを見分けられない
（v2r-dry-02 の B の T6）。監査が項を正しく分け、数を正しく出すこと。
"""
import errno
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import fileops  # noqa: E402
import propaudit  # noqa: E402
import propgen  # noqa: E402


def terms(given):
    return [propaudit.classify(c) for c in propgen._conjuncts(propgen.parse(given, "t"))]


class Classify(unittest.TestCase):
    def test_after_and_occupied_after_are_outcomes(self):
        self.assertEqual(terms("before.Phase == Playing && after.LinesCleared == before.LinesCleared + 1"),
                         ["start", "outcome"])
        self.assertEqual(terms("!occupied_after(0, 21)"), ["outcome"])

    def test_before_and_input_beyond_the_start_are_constructible(self):
        self.assertEqual(terms("input.HardDrop && before.LinesCleared == 3 && fits(drop(before.ActiveMino))"),
                         ["constructible", "constructible", "start"])

    def test_the_audit_counts_and_strict_mode(self):
        props = [{"id": "P-1", "task": "T1", "given": "input.Left && after.Score == before.Score"},
                 {"id": "P-2", "task": "T1", "given": "before.Phase == Playing"},
                 {"id": "P-3", "task": "T2", "given": "before.Score == 100"}]
        rows = propaudit.audit(props)
        self.assertEqual([(r["start"], r["constructible"], r["outcome"]) for r in rows], [(0, 1, 1), (1, 0, 0), (0, 1, 0)])
        text = propaudit.render(rows)
        self.assertIn("outcome の項を持つ（P1 に反する）：1 件", text)
        self.assertIn("証拠の状態の注入が要る）：1 件", text)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "decl.json"
            path.write_text(json.dumps({"properties": props}), encoding="utf-8")
            with unittest.mock.patch("builtins.print"):
                self.assertEqual(propaudit.main([str(path)]), 0)
                self.assertEqual(propaudit.main([str(path), "--strict"]), 1)
                path.write_text(json.dumps({"properties": props[1:]}), encoding="utf-8")
                self.assertEqual(propaudit.main([str(path), "--strict"]), 0)

    def test_the_v2r_declaration_after_the_first_rewrite(self):
        """記録：v2r-dry-02 の時点では 34 件中 30 件が outcome の項を持っていた。V2R-7 の最初の書き直しで 2 件（T2-01・T2-04。
        隠れたロック猶予タイマーで決まる固定。複数ティックの証拠で書き直す）になった（2026-09-27）。"""
        props = json.loads((ROOT / "experiments" / "v2r" / "properties.json").read_text(encoding="utf-8"))["properties"]
        rows = propaudit.audit(props)
        self.assertEqual((len(rows), [r["id"] for r in rows if r["outcome"]]), (34, ["P-T2-01", "P-T2-04"]))


class RmTree(unittest.TestCase):
    def test_a_locked_tree_is_retried_then_removed(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "x"
            (target / "sub").mkdir(parents=True)
            real, tries, sleeps = fileops.shutil.rmtree, [], []

            def flaky(p):
                tries.append(p)
                if len(tries) < 3:
                    raise PermissionError(errno.EACCES, "locked")
                real(p)
            with unittest.mock.patch.object(fileops.shutil, "rmtree", flaky):
                fileops.rmtree(target, sleep=sleeps.append)
            self.assertFalse(target.exists())
            self.assertEqual(len(sleeps), 2)
            fileops.rmtree(target)  # 無ければ何もしない

    def test_a_tree_that_stays_locked_raises_file_lock_error(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x").mkdir()

            def locked(p):
                raise PermissionError(errno.EACCES, "locked")
            with unittest.mock.patch.object(fileops.shutil, "rmtree", locked):
                with self.assertRaises(fileops.FileLockError):
                    fileops.rmtree(Path(d) / "x", sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
