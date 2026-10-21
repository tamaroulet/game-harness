"""README に書いたリポジトリの中のパスと python -m harness.<名前> のモジュールが実在することを確かめる（W-109）。"""
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIXES = ("harness/", "config/", "docs/", "tests/", "bench/")
FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`([^`\n]+)`")
TOKEN = re.compile(r"[A-Za-z0-9_./<>*{}\-]+")
MODULE = re.compile(r"python -m harness\.([A-Za-z0-9_]+)")


def mentioned_paths(text):
    """バッククォートで囲んだ範囲から、リポジトリの中のパスを集める（<>・* {} を含む雛形は除く）。"""
    found = []
    for span in INLINE.findall(FENCE.sub("", text)):
        for token in TOKEN.findall(span):
            if not token.startswith(PREFIXES) or re.search(r"[<>*{}]", token):
                continue
            found.append(token.rstrip("."))
    return found


def mentioned_modules(text):
    return MODULE.findall(text)


def missing(text, root):
    """README の文字列の中で、root の下に実在しないパスとモジュールを返す。"""
    root = Path(root)
    out = [p for p in mentioned_paths(text) if not (root / p.rstrip("/")).exists()]
    for name in mentioned_modules(text):
        if not ((root / "harness" / (name + ".py")).is_file() or (root / "harness" / name).is_dir()):
            out.append("python -m harness." + name)
    return out


class ReadmePaths(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_every_path_and_module_exists(self):
        self.assertEqual(missing(self.text, ROOT), [])

    def test_paths_are_actually_found(self):
        self.assertGreater(len(mentioned_paths(self.text)), 10)
        self.assertGreater(len(mentioned_modules(self.text)), 3)

    def test_missing_path_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "harness").mkdir()
            (root / "harness" / "real.py").write_text("", encoding="utf-8")
            text = "`harness/real.py` と `harness/gone.py` と `python -m harness.real` と `python -m harness.vanished`"
            got = missing(text, root)
        self.assertEqual(got, ["harness/gone.py", "python -m harness.vanished"])

    def test_placeholders_and_fences_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            text = "`harness/hline_*.py` `projects/<id>/` \n```\n`harness/inside_fence.py`\n```\n"
            self.assertEqual(missing(text, d), [])

    def test_current_structure_only(self):
        self.assertNotIn("harness/audit.py", self.text)
        for needed in ("harness/hline.py", "config/hline.json", "harness/bench_regress.py"):
            self.assertIn(needed, self.text)


if __name__ == "__main__":
    unittest.main()
