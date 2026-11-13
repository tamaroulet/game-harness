"""agy の導入先の SHA-256 の照合（harness/agy_pinned.py）。一時ディレクトリで確かめる。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import agy_pinned  # noqa: E402


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


class Static(unittest.TestCase):
    def test_module_does_not_import_subprocess(self):
        self.assertNotIn("subprocess", Path(agy_pinned.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
