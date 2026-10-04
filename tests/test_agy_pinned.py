"""agy の導入先の SHA-256 の照合（harness/agy_pinned.py）と、driver・pipeline への組み込み。一時ディレクトリで確かめる。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import agy_pinned  # noqa: E402
import pipeline  # noqa: E402
from ab import common, driver  # noqa: E402

# 既存のテストは実在の agy の導入先なしに pipeline.call_implementer を回す。雛形の期待値では止まるので、
# このプロセスでは既定の照合を空振りにする（照合そのものはこのファイルが verify を渡して確かめる）。
pipeline.guard_implementer.__defaults__ = (lambda: {},)

IMP = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "--yes", "model_flag": "--model", "model_name": "m"}
PINNED = {"cli": {"agy": "1.2.14"}}


def fail():
    raise agy_pinned.AgyDigestError("照合に落ちた")


class Check(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "agy.exe").write_bytes(b"agy body")
        self.digest = agy_pinned.sha256_file(self.dir / "agy.exe")

    def record(self, **over):
        return {"install_dir": str(self.dir), "files": {"agy.exe": self.digest}, **over}

    def stops(self, **kw):
        with self.assertRaises(agy_pinned.AgyDigestError) as cm:
            agy_pinned.require(**kw)
        return str(cm.exception)

    def test_different_content_stops(self):
        msg = self.stops(record=self.record(files={"agy.exe": "0" * 64}))
        self.assertIn("agy.exe", msg)
        self.assertIn(self.digest, msg)

    def test_missing_unset_or_malformed_stops(self):
        for rec in (self.record(files={"nothing.exe": self.digest}), self.record(files={}), {},
                    {"install_dir": None, "files": {"a": self.digest}}, {"install_dir": "", "files": {}},
                    self.record(files={"agy.exe": self.digest.upper()}), self.record(files={"agy.exe": 5}),
                    self.record(files={"../outside.bin": "0" * 64})):
            with self.subTest(rec=rec):
                self.stops(record=rec)

    def test_load_record_errors(self):
        bad = self.dir / "bad.json"
        self.stops(path=self.dir / "none.json")
        self.assertIsNone(agy_pinned.sha256_file(self.dir / "none"))
        for text in ("not json", "[1]"):
            bad.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(agy_pinned.AgyDigestError):
                agy_pinned.load_record(bad)

    def test_same_content_passes_and_returns_the_record(self):
        rec = self.record()
        self.assertIs(agy_pinned.require(record=rec), rec)

    def test_measure_reads_only_inside_install_dir(self):
        got = agy_pinned.measure(self.dir, ["agy.exe", "none.exe", "../outside", "sub\\x"])
        self.assertEqual(got, {"agy.exe": self.digest, "none.exe": None, "../outside": None, "sub/x": None})
        self.assertEqual(agy_pinned.problems(self.record(files=agy_pinned.measure(self.dir, ["agy.exe"]))), [])


class Wiring(unittest.TestCase):
    def test_guard_checks_before_measuring_the_version(self):
        measured = []
        with self.assertRaises(common.ABError):
            driver.agy_guard(IMP, measure=lambda n: measured.append(n) or "1.2.14", pinned=PINNED, verify=fail)
        self.assertEqual(measured, [])

    def test_guard_returns_the_cli_version_and_other_clis_skip_the_check(self):
        self.assertEqual(driver.agy_guard(IMP, measure=lambda n: "1.2.14", pinned=PINNED, verify=lambda: {}), "1.2.14")
        self.assertEqual(driver.agy_guard(dict(IMP, cli="claude"), measure=lambda n: "2.1.258",
                                          pinned={"cli": {"claude": "2.1.258"}}, verify=fail), "2.1.258")

    def test_a_failed_check_does_not_call_the_runner(self):
        called = []
        with self.assertRaises(common.ABError):
            driver.agy_call(IMP, "p", ".", None, 60, runner=lambda *a, **k: called.append(1),
                            guard=lambda imp: driver.agy_guard(imp, verify=fail))
        self.assertEqual(called, [])

    def test_pipeline_guard(self):
        with self.assertRaises(SystemExit) as cm:
            pipeline.guard_implementer({"cli": "agy"}, verify=fail)
        self.assertTrue(str(cm.exception.code).startswith("ABORT: "))
        self.assertIsNone(pipeline.guard_implementer({"cli": "agy"}, verify=lambda: {}))
        self.assertIsNone(pipeline.guard_implementer({"cli": "claude"}, verify=lambda: self.fail("呼ばれた")))

    def test_call_implementer_guards_first(self):
        src = Path(pipeline.__file__).read_text(encoding="utf-8")
        body = src[src.index("def call_implementer("):]
        self.assertLess(body.index("guard_implementer(imp)"), body.index("prompt = c.unit"))


class Static(unittest.TestCase):
    def test_acl_runbook_has_the_commands_and_sections(self):
        text = (ROOT / "docs" / "ops" / "agy_acl.md").read_text(encoding="utf-8")
        for needle in ("icacls", "/deny", "WD", "AD", "DE", "DC", "/remove:d", "## 適用", "## 確認", "## 外し方"):
            self.assertIn(needle, text)

    def test_module_does_not_import_subprocess(self):
        self.assertNotIn("subprocess", Path(agy_pinned.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
