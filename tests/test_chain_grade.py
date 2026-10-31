"""鎖の採点の側の直し（10-31、L4 の間。Argon の事後の検め）。
- 隠しテストのファイルが読み込みで落ちたら、宣言した試験の数だけの落ちに開き、段の分け方と分母を保つ。
- 段を無効にする理由の順：漏れ・呼び出しの記録・モデル（凍結の finish と同じ）・道の検め。
- 前の段が無効なら、この段の理由より先に「前の段が無効」とする。
- queue.json に無い What のディレクトリ（延長の前など）の索引の見出しに、What のタスクを出す。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import chain_score  # noqa: E402

BROKEN = '''import unittest
from pkg import missing


class T(unittest.TestCase):
    def test_a(self):
        missing()

    def test_b(self):
        missing()


class U(unittest.TestCase):
    def test_c(self):
        missing()
'''


class Grade(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "tree" / "pkg").mkdir(parents=True)
        (self.root / "tree" / "pkg" / "__init__.py").write_text("def present():\n    return 1\n", encoding="utf-8")
        (self.root / "hidden").mkdir()

    def hidden(self, name, text):
        p = self.root / "hidden" / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_import_failure_expands_to_declared_tests(self):
        """読み込みで落ちた段 2 の試験のファイルは、宣言した 3 件の落ちになり、どれも段 2 に数える。"""
        ok = self.hidden("test_hidden_B07.py", "import unittest\nfrom pkg import present\n\n\nclass S(unittest.TestCase):\n"
                                               "    def test_s0(self):\n        self.assertEqual(present(), 1)\n")
        res = chain_score.grade(self.root / "tree", [ok, self.hidden("test_chain_B07_s2.py", BROKEN)], set())
        self.assertEqual(res["test_hidden_B07.S.test_s0"], "ok")
        broken = {t: v for t, v in res.items() if t.startswith("test_chain_B07_s2.")}
        self.assertEqual(sorted(broken), ["test_chain_B07_s2.T.test_a", "test_chain_B07_s2.T.test_b", "test_chain_B07_s2.U.test_c"])
        self.assertTrue(all(v.startswith("ERROR") and "ImportError" in v for v in broken.values()), broken)
        self.assertEqual({chain_score.stage_of(t, "B07") for t in broken}, {2})
        self.assertFalse(any(t.startswith(chain_score.FAILED_IMPORT) for t in res))

    def test_import_failure_still_drops_replaced_tests(self):
        """置き換えた試験の名前は、開いた後でも除く。"""
        res = chain_score.grade(self.root / "tree", [self.hidden("test_chain_B07_s2.py", BROKEN)], {"test_b"})
        self.assertEqual(sorted(res), ["test_chain_B07_s2.T.test_a", "test_chain_B07_s2.U.test_c"])

    def test_syntax_error_counts_once_in_its_stage(self):
        """構文の誤りで宣言を読めないファイルは、そのモジュールの 1 件として、その段に数える。"""
        res = chain_score.grade(self.root / "tree", [self.hidden("test_chain_B07_s3.py", "def broken(:\n")], set())
        self.assertEqual(list(res), ["test_chain_B07_s3.<読み込みの誤り>"])
        self.assertEqual(chain_score.stage_of(next(iter(res)), "B07"), 3)


class InvalidReason(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rundir = Path(tmp.name)
        (self.rundir / "factory").mkdir()
        (self.rundir / "factory" / "config.json").write_text(
            json.dumps({"implementer": {"cli": "agy", "model_name": "m-ok"}}), encoding="utf-8")
        self.cfg = {"implementer": {"model": "m-ok"}}

    def call(self, model):
        d = self.rundir / "calls" / "001-implementer"
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps({"modelUsage": {model: {"inputTokens": 1}}}), encoding="utf-8")

    def reason(self, hits=(), found=()):
        why = chain_score.invalid_reason(self.rundir, self.cfg, list(hits), list(found))
        return why[0] if why else None

    def test_order_of_reasons(self):
        """漏れ → 呼び出しの記録 → モデル → 道の検めの順で、最初に当たったものが理由になる。"""
        self.assertEqual(self.reason(hits=[("x", 1)]), "REQ-11")
        self.assertEqual(self.reason(found=[("log", "view_file", "C:/x")]), "REQ-22")
        self.call("m-bad")
        self.assertEqual(self.reason(found=[("log", "view_file", "C:/x")]), "REQ-02")
        self.call("m-ok")
        self.assertEqual(self.reason(found=[("log", "view_file", "C:/x")]), "stray")
        self.assertIsNone(self.reason())


class Taint(unittest.TestCase):
    def test_previous_invalid_stage_comes_first(self):
        """前の段が無効なら、この段そのものの理由があっても「前の段が無効」とし、この段の理由は文に添える。"""
        row = {"invalid": "REQ-22", "reason": "呼び出しの記録がありません"}
        chain_score.taint(row, "s1")
        self.assertEqual(row["invalid"], "tainted")
        self.assertEqual(row["reason"], "前の段（s1）が無効。この段も REQ-22：呼び出しの記録がありません")

    def test_valid_stage_after_invalid_one(self):
        row = {"invalid": False, "reason": None}
        chain_score.taint(row, "s1")
        self.assertEqual((row["invalid"], row["reason"]), ("tainted", "前の段（s1）が無効"))

    def test_no_previous_invalid_stage_leaves_row(self):
        row = {"invalid": "REQ-02", "reason": "r"}
        chain_score.taint(row, None)
        self.assertEqual(row, {"invalid": "REQ-02", "reason": "r"})


class IndexHeading(unittest.TestCase):
    def test_item_missing_in_queue_shows_task_from_what(self):
        """queue.json に無い What のディレクトリの見出しは、What の「タスク:」の行から作る（None と出さない）。"""
        with tempfile.TemporaryDirectory() as tmp:
            rundir = Path(tmp)
            (rundir / "factory").mkdir()
            (rundir / "factory" / "queue.json").write_text(json.dumps({"items": {}}), encoding="utf-8")
            d = rundir / "items" / "20261031-2035-p-01"
            d.mkdir(parents=True)
            (d / "what.md").write_text("# 題\nマイルストーン: bench\nタスク: P-01\n", encoding="utf-8")
            text = chain_score.index_text("B07", 1, "t1-s1", rundir, {}, [])
        self.assertIn("## What P-01（20261031-2035-p-01）：queue.json に無い", text)
        self.assertNotIn("What None", text)


if __name__ == "__main__":
    unittest.main()
