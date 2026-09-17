"""実装パイプラインのホワイトリストの門（harness/pipeline.py の gate_whitelist、contract.md §5.3）。

    python -m unittest discover -s tests -v

git は一時ディレクトリの本物（サンドボックスに見立てたリポジトリ）。エンジンの付随ファイルの規則は Unity アダプタ。
- 新しいフォルダのファイル・日本語のパスを、まとめたりエスケープしたりせずに照合する
- .meta は、本体が whitelist にあり、かつ新規のものだけを許す
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402
from adapters import unity  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
                       cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout


class GateWhitelistTests(unittest.TestCase):
    def setUp(self):
        self.sb = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.sb, True)
        git(self.sb, "init", "-q", "-b", "main")
        self.write("Game/Assets/Core/Existing.cs", "class Existing {}")
        self.write("Game/Assets/Core/Existing.cs.meta", "guid: 1111")
        self.write("Game/Assets/Scenes/Main.unity.meta", "guid: 2222")
        git(self.sb, "add", ".")
        git(self.sb, "commit", "-q", "-m", "base")
        self.unit = {"whitelist": ["Game/Assets/Core/Existing.cs", "Game/Assets/Core/New/Board.cs",
                                   "Game/Assets/Core/盤面 定数.cs"]}
        self.c = SimpleNamespace(sandbox=self.sb, ttl={"git": 60}, unit=self.unit,
                                 engine=SimpleNamespace(is_companion=unity.is_companion,
                                                        companions=unity.companions))

    def write(self, rel, text):
        p = self.sb / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def gate(self):
        return pipeline.gate_whitelist(self.c)

    def test_whitelisted_file_in_a_new_folder_is_allowed(self):
        self.write("Game/Assets/Core/New/Board.cs", "class Board {}")
        self.assertEqual(self.gate(), [], "新しいフォルダを dir/ にまとめて許可外と誤判定しない")

    def test_non_ascii_and_space_paths_are_matched_exactly(self):
        self.write("Game/Assets/Core/盤面 定数.cs", "class C {}")
        self.assertEqual(self.gate(), [])

    def test_stray_file_in_a_new_folder_is_reported_with_its_full_path(self):
        self.write("Game/Assets/Core/New/Board.cs", "class Board {}")
        self.write("Game/Assets/Core/New/Sneaky.cs", "class Sneaky {}")
        self.assertEqual(self.gate(), ["Game/Assets/Core/New/Sneaky.cs"])

    def test_modifying_a_whitelisted_existing_file_is_allowed(self):
        self.write("Game/Assets/Core/Existing.cs", "class Existing { int x; }")
        self.assertEqual(self.gate(), [])

    def test_new_meta_of_a_whitelisted_file_is_allowed(self):
        self.write("Game/Assets/Core/New/Board.cs", "class Board {}")
        self.write("Game/Assets/Core/New/Board.cs.meta", "guid: 3333")
        self.assertEqual(self.gate(), [])

    def test_meta_of_an_unrelated_file_is_refused(self):
        self.write("Game/Assets/Scenes/Other.unity.meta", "guid: 4444")
        self.assertEqual(self.gate(), ["Game/Assets/Scenes/Other.unity.meta"])

    def test_modifying_or_deleting_an_existing_meta_is_refused(self):
        self.write("Game/Assets/Core/Existing.cs.meta", "guid: 9999")
        self.assertEqual(self.gate(), ["Game/Assets/Core/Existing.cs.meta"], "whitelist の本体の .meta でも既存は不可")
        git(self.sb, "checkout", "--", ".")
        (self.sb / "Game/Assets/Scenes/Main.unity.meta").unlink()
        self.assertEqual(self.gate(), ["Game/Assets/Scenes/Main.unity.meta"])

    def test_staged_meta_counts_as_new_only_when_added(self):
        self.write("Game/Assets/Core/New/Board.cs", "class Board {}")
        self.write("Game/Assets/Core/New/Board.cs.meta", "guid: 3333")
        git(self.sb, "add", "Game/Assets/Core/New/Board.cs.meta")
        self.assertEqual(self.gate(), [])

    def test_rename_reports_both_sides_that_are_not_allowed(self):
        git(self.sb, "mv", "Game/Assets/Core/Existing.cs", "Game/Assets/Core/Renamed.cs")
        self.assertIn("Game/Assets/Core/Renamed.cs", self.gate())
        git(self.sb, "reset", "-q", "--hard")
        self.write("Game/Other.cs", "class O {}")
        git(self.sb, "add", "Game/Other.cs")
        git(self.sb, "commit", "-q", "-m", "other")
        (self.sb / "Game/Assets/Core/New").mkdir(parents=True)
        git(self.sb, "mv", "Game/Other.cs", "Game/Assets/Core/New/Board.cs")
        self.assertEqual(self.gate(), ["Game/Other.cs"], "許可外のファイルを許可内へ改名して持ち込むのも不可")


if __name__ == "__main__":
    unittest.main()
