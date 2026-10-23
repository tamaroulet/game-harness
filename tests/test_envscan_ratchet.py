"""環境の直書きの歯止め（W-125、ADR-004 の束 F）：境界の外で、記録（config/envscan_baseline.json）より直書きを増やせない。

    python -m unittest tests.test_envscan_ratchet -v

減らすのは自由（python -m harness.envscan --update で記録を下げる）。この試験は CI の全件に入るので、直書きを増やす PR は通らない。"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import envscan  # noqa: E402


class Ratchet(unittest.TestCase):
    def test_no_file_outside_the_boundary_has_more_than_recorded(self):
        base = envscan.load_baseline()
        up = envscan.increases(envscan.scan(envscan.ROOT, base["boundary"]), base["counts"])
        self.assertEqual(up, [], "境界の外で直書きが増えました。境界の内側へ移すか、増やさずに書いてください")

    def test_the_record_has_no_file_inside_the_boundary(self):
        base = envscan.load_baseline()
        self.assertEqual([f for f in base["counts"] if envscan.in_boundary(f, base["boundary"])], [])

    def test_the_scanner_and_the_record_are_in_the_boundary(self):
        base = envscan.load_baseline()
        for name in ("harness/envscan.py", "config/envscan_baseline.json"):
            self.assertTrue(envscan.in_boundary(name, base["boundary"]), name)


class Categories(unittest.TestCase):
    SAMPLES = {
        "cli_name": 'run(["git", "status"]); which("agy")',
        "abs_path": r'"C:\\src\\.local\\inbox" and C:/Users/x',
        "os_specific": "subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0; taskkill",
        "encoding": 'data.decode("cp932"); env["PYTHONUTF8"] = "1"; open(p, errors="replace")',
        "exe_suffix": "Unity.exe run.cmd setup.ps1",
        "version_literal": '"2.1.258" "net8.0" "6000.3.23f1"',
        "model_id": "claude-sonnet-5-5 and gemini-3.8-flash-medium",
    }

    def test_each_category_finds_its_samples(self):
        for kind, text in self.SAMPLES.items():
            with self.subTest(kind=kind):
                self.assertGreater(envscan.count(text).get(kind, 0), 0)

    def test_plain_code_counts_nothing(self):
        self.assertEqual(envscan.count("def add(a, b):\n    return a + b  # gitignore と github の語\n"), {})


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "harness").mkdir()
        (self.tmp / "config").mkdir()
        self.mod = self.tmp / "harness" / "m.py"
        self.mod.write_text('A = ["git"]\nB = ["gh"]\n', encoding="utf-8")
        self.base = self.tmp / "config" / "envscan_baseline.json"
        self.base.write_text(json.dumps({"boundary": ["harness/platform/"], "counts": {"harness/m.py": {"cli_name": 2}}}), encoding="utf-8")

    def run_main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = envscan.main([*args, "--baseline", str(self.base), "--root", str(self.tmp)])
        return rc, out.getvalue()

    def test_an_increase_fails_the_check_and_is_not_written(self):
        self.mod.write_text('A = ["git"]\nB = ["gh"]\nC = ["claude"]\n', encoding="utf-8")
        before = self.base.read_text(encoding="utf-8")
        self.assertEqual(self.run_main("--check")[0], 1)
        rc, out = self.run_main("--update")
        self.assertEqual(rc, 1)
        self.assertIn("harness/m.py の cli_name が 2 → 3", out)
        self.assertEqual(self.base.read_text(encoding="utf-8"), before)

    def test_a_new_file_outside_the_boundary_starts_from_zero(self):
        (self.tmp / "harness" / "new.py").write_text('X = "cp932"\n', encoding="utf-8")
        self.assertEqual(self.run_main("--check")[0], 1)

    def test_a_file_inside_the_boundary_is_free(self):
        (self.tmp / "harness" / "platform").mkdir()
        (self.tmp / "harness" / "platform" / "process.py").write_text('X = ["taskkill"]\nY = "cp932"\n', encoding="utf-8")
        self.assertEqual(self.run_main("--check")[0], 0)

    def test_a_decrease_lowers_the_record(self):
        self.mod.write_text('A = ["git"]\n', encoding="utf-8")
        self.assertEqual(self.run_main("--update")[0], 0)
        self.assertEqual(json.loads(self.base.read_text(encoding="utf-8"))["counts"], {"harness/m.py": {"cli_name": 1}})


if __name__ == "__main__":
    unittest.main()
