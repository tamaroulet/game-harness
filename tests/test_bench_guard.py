"""bench_guard の検査。本物のアカウントや ACL は作らず、一時ディレクトリと open の差し替えで確かめる。"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import bench_guard  # noqa: E402

DOC = HERE.parent / "docs" / "ops" / "bench_account.md"


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = bench_guard.main(argv)
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = {k: str(self.root / k) for k in bench_guard.PLACES}
        for k in bench_guard.PLACES:
            (self.root / k).mkdir()

    def config(self, cfg=None):
        p = self.root / "cfg.json"
        p.write_text(json.dumps(cfg or self.cfg), encoding="utf-8")
        return ["--config", str(p)]


class Check(Base):
    def test_zero_when_nothing_opens(self):
        (self.root / "inbox" / "a.txt").write_text("x")
        real = open

        def deny(path, *a, **k):
            if str(path).startswith(str(self.root)):
                raise PermissionError(path)
            return real(path, *a, **k)

        with mock.patch("builtins.open", deny):
            self.assertEqual(run(["check"] + self.config())[0], 0)

    def test_one_when_a_file_opens_and_prints_its_path(self):
        f = self.root / "out" / "sub" / "log.txt"
        f.parent.mkdir()
        f.write_text("x")
        code, out = run(["check"] + self.config())
        self.assertEqual(code, 1)
        self.assertIn(str(f), out)

    def test_zero_for_missing_and_empty_dirs(self):
        (self.root / "oracles").rmdir()
        self.assertEqual(run(["check"] + self.config())[0], 0)

    def test_two_when_a_key_is_missing(self):
        for k in bench_guard.PLACES:
            cfg = {x: v for x, v in self.cfg.items() if x != k}
            self.assertEqual(run(["check"] + self.config(cfg))[0], 2)
            self.assertEqual(run(["scan", str(self.root)] + self.config(cfg))[0], 2)


class Scan(Base):
    def setUp(self):
        super().setUp()
        c = self.root / "oracles" / "bench" / "t1"
        c.mkdir(parents=True)
        (c / "canary.txt").write_text("  CANARY-123 \n")
        self.logs = self.root / "logs"
        self.logs.mkdir()

    def scan(self):
        return run(["scan", str(self.logs)] + self.config())

    def test_zero_when_clean(self):
        (self.logs / "a.log").write_text("nothing here\n")
        self.assertEqual(self.scan()[0], 0)

    def test_canary_is_found_with_file_and_line(self):
        f = self.logs / "a.log"
        f.write_text("ok\nsaw CANARY-123 here\n")
        code, out = self.scan()
        self.assertEqual(code, 1)
        self.assertIn(f"{f}:2", out)

    def test_oracles_path_in_any_separator_or_case(self):
        base = self.cfg["oracles"]
        for i, text in enumerate([base, base.replace("\\", "/"), base.upper(), base.replace("\\", "\\\\")]):
            f = self.logs / f"p{i}.log"
            f.write_text(f"x\nopen {text}{'/' if i else ''}bench\n")
            code, out = self.scan()
            self.assertEqual(code, 1)
            self.assertRegex(out, re.escape(f"{f}:2"))
            f.unlink()


class Doc(unittest.TestCase):
    def test_five_headings_and_the_four_words(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^## ", text, re.M)), 5)
        for w in ("New-LocalUser", "icacls", "runas", "python -m harness.bench_guard check"):
            self.assertIn(w, text)


if __name__ == "__main__":
    unittest.main()
