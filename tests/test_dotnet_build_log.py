"""ビルドが壊れて TRX が出なかったとき、理由が残ること（harness/adapters/dotnet.py）。

    python -m unittest discover -s tests -v

**なぜ要るか**: `run_tests` は `run()` の戻り値を**受け取ってすらいなかった**。TRX が無いときに
返るのは「検査系故障: TRX が生成されませんでした（ビルド失敗の可能性）」という推測だけで、
コンパイラが何を言ったのかはどこにも残らなかった。

2026-09-19 の Issue #14 は自己検査の復元段でこれに当たった。原因（新しい受入テストが
まだ無い API を呼んでいる）を突き止めるには、サンドボックスで `dotnet build` を手で回すしか
なかった。下の DOTNET_OUTPUT はそのとき実際に出た文面。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from adapters import dotnet  # noqa: E402

# 実測（2026-09-19 の Issue #14）。同じエラーが何十行も並ぶので、重複を畳めることも確かめる。
DOTNET_OUTPUT = """MSBuild のバージョン 17.11.4+37eb419ad
  復元対象のプロジェクトを決定しています...
C:\\...\\SevenBagTests.cs(48,27): error CS0246: 型または名前空間の名前 'XorShift32' が見つかりませんでした
C:\\...\\SevenBagTests.cs(50,33): error CS0103: 現在のコンテキストに 'XorShift32' という名前は存在しません
C:\\...\\SevenBagTests.cs(61,27): error CS0246: 型または名前空間の名前 'XorShift32' が見つかりませんでした
C:\\...\\FirstSpawnTests.cs(70,19): error CS1061: 'GameState' に 'Tick' の定義が含まれておらず
"""


class BuildFailureDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = SimpleNamespace(
            out=self.tmp / "out",
            sandbox=self.tmp / "sandbox",
            unit={"fast_test_project": "tests/Core.Tests/Core.Tests.csproj"},
            ttl={"fast_tests": 300},
            metrics={},
        )

    def call(self, rc=1, out=DOTNET_OUTPUT, err=""):
        """TRX は作らせない（＝ビルドが壊れた状況）。"""
        with mock.patch.object(dotnet, "run", return_value=(rc, out, err)):
            return dotnet.run_tests(self.c, "self_fast_restored")

    def log(self):
        return (self.c.out / "self_fast_restored.dotnet.log").read_text(encoding="utf-8")

    def test_the_first_error_lines_reach_the_caller(self):
        """これが退行そのもの。メッセージだけ見て原因が分かること。"""
        results, err = self.call()
        self.assertIsNone(results)
        self.assertIn("error CS0246", err)
        self.assertIn("XorShift32", err)
        self.assertIn("rc=1", err)

    def test_duplicate_error_lines_are_collapsed(self):
        """同じ 'XorShift32 が見つかりません' が何十行も並ぶ。畳まないと読めない。"""
        _, err = self.call()
        self.assertEqual(err.count("SevenBagTests.cs(48,27)"), 1)
        self.assertLessEqual(err.count("error CS"), 3, "先頭の数件だけ見せる")

    def test_the_full_output_is_kept_on_disk(self):
        _, err = self.call()
        body = self.log()
        self.assertIn("FirstSpawnTests.cs(70,19)", body, "先頭に載らなかった行も残す")
        self.assertIn("- rc: 1", body)
        self.assertIn(str(self.c.out / "self_fast_restored.dotnet.log"), err, "全文の場所を示す")

    def test_stderr_is_kept_too(self):
        _, err = self.call(out="", err="dotnet: コマンドが失敗しました")
        self.assertIn("dotnet: コマンドが失敗しました", self.log())

    def test_output_without_recognisable_errors_still_reports(self):
        """エラー行を見つけられなくても、黙らない。"""
        _, err = self.call(rc=124, out="なにも分からない出力")
        self.assertIn("rc=124", err)
        self.assertIn("エラー行を見つけられませんでした", err)
        self.assertIn("なにも分からない出力", self.log())

    def test_a_failed_log_write_still_returns_the_error(self):
        """観測のための処理。書けなくても、判定用のメッセージは返す。"""
        with mock.patch("pathlib.Path.write_text", side_effect=OSError("掴まれています")), \
             mock.patch.object(dotnet, "run", return_value=(1, DOTNET_OUTPUT, "")):
            results, err = dotnet.run_tests(self.c, "self_fast_restored")
        self.assertIsNone(results)
        self.assertIn("error CS0246", err)
        self.assertIn("ログを書けませんでした", err)

    def test_nothing_is_written_when_the_trx_exists(self):
        """正常時は何も足さない。"""
        self.c.out.mkdir(parents=True, exist_ok=True)
        trx = self.c.out / "ok.trx"
        trx.write_text("<x/>", encoding="utf-8")
        with mock.patch.object(dotnet, "run", return_value=(0, "", "")), \
             mock.patch.object(dotnet, "parse_results", return_value=({}, {"total": 0})), \
             mock.patch.object(dotnet.fileops, "unlink"):
            results, err = dotnet.run_tests(self.c, "ok")
        self.assertIsNone(err)
        self.assertFalse((self.c.out / "ok.dotnet.log").exists())


if __name__ == "__main__":
    unittest.main()
