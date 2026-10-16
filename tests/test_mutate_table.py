"""変異表（tests/mutate.py）が古くなっていないこと。

    python -m unittest discover -s tests -v

変異は、置換元がソースにちょうど 1 か所あるときだけ意味を持つ。0 か所なら何も壊せず、2 か所以上なら
どちらを壊したのか分からない。mutate.py を全件回すのは時間がかかる（1 件あたり全テスト 1 周）ので、
置換元の一致だけを通常のテストで確かめる（処理を複製したときに、既存の変異が 2 か所に当たるようになった実例がある）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mutate  # noqa: E402


@unittest.skipIf(os.environ.get("HARNESS_MUTATING"),
                 "変異の実行中はソースが書き換わっていて必ず赤になる。"
                 "飛ばさないと、どの変異も無条件に『検出』になり、変異テストの信号が消える")
class MutateTableTests(unittest.TestCase):
    def test_every_mutation_matches_exactly_once(self):
        stale = []
        for name, fn, old, _ in mutate.M:
            n = mutate.read_source(mutate.T / fn).count(old)
            if n != 1:
                stale.append(f"{name}: {fn} に {n} か所")
        self.assertEqual(stale, [], "変異表が古い。置換元を直すか、文脈を足して 1 か所にする")

    def test_names_are_unique(self):
        ids = [name.split()[0] for name, *_ in mutate.M]
        self.assertEqual(len(ids), len(set(ids)))


class NewlineTests(unittest.TestCase):
    """CRLF のファイルでも、数える側と当てる側が一致すること。

    変異は「置換元がちょうど 1 か所」のときだけ意味を持つ。数える側（上の検査）が改行を
    正規化して読み、当てる側（mutate.main）がバイト列のまま読んでいたため、CRLF のファイルでは
    **表が緑なのに変異が一度も実行できない**状態になっていた。実測で 6 件が該当した
    （M53 / M54 / M55 / M61 / M71 / M115。いずれも harness/pipeline.py で、CRLF だった）。

    ここは harness のソースを見ない（変異の実行中でも成立する）。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.crlf = self.tmp / "crlf.py"
        self.crlf.write_bytes(b"def f(x):\r\n    if x:\r\n        return 1\r\n    return 0\r\n")

    def test_a_multi_line_pattern_is_found_in_a_crlf_file(self):
        """これが退行そのもの。バイト列のまま数えると 0 か所になり、変異が実行できない。"""
        old = "    if x:\n        return 1\n"
        self.assertEqual(self.crlf.read_bytes().decode("utf-8").count(old), 0)
        self.assertEqual(mutate.read_source(self.crlf).count(old), 1)

    def test_a_pattern_that_is_really_absent_is_still_zero(self):
        """正規化しただけで何でも当たるようになっては意味が無い。"""
        self.assertEqual(mutate.read_source(self.crlf).count("    while x:\n"), 0)

    def test_write_source_restores_crlf(self):
        text = mutate.read_source(self.crlf).replace("return 1", "return 2")
        mutate.write_source(self.crlf, text, crlf=True)
        raw = self.crlf.read_bytes()
        self.assertIn(b"return 2", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_write_source_keeps_lf_when_the_file_had_lf(self):
        lf = self.tmp / "lf.py"
        lf.write_bytes(b"def f(x):\n    return 1\n")
        mutate.write_source(lf, mutate.read_source(lf).replace("return 1", "return 2"), crlf=False)
        self.assertNotIn(b"\r", lf.read_bytes())

    def test_a_round_trip_leaves_a_crlf_file_byte_identical(self):
        """変異の finally は元のバイト列で戻すが、当てる側が改行を変えないことも確かめる。"""
        before = self.crlf.read_bytes()
        mutate.write_source(self.crlf, mutate.read_source(self.crlf), crlf=True)
        self.assertEqual(self.crlf.read_bytes(), before)


class AppendSectionTests(unittest.TestCase):
    """新しい変異の追記先が、対象ファイルごとに分かれていること。

    M の末尾に足すと、**並行した PR が同じ 1 行を触って必ず競合する。**
    2026-09-19 に 2 度踏んだ（#23 × #24、#28 × #29）。対象ファイルが違えば
    追記位置も分かれるので、競合しない。

    ここは harness のソースを見ない（変異の実行中でも成立する）。
    """

    BUCKETS = {
        "M_PIPELINE": "pipeline.py",
        "M_SCHEDULER": "scheduler.py",
        "M_DECOMPOSE": "decompose.py",
        "M_DOTNET": "adapters/dotnet.py",
    }

    def test_each_bucket_only_holds_its_own_target(self):
        """別の対象を混ぜると、その追記先がまた競合点になる。"""
        bad = [f"{bucket} に {fn} の {name}"
               for bucket, want in self.BUCKETS.items()
               for name, fn, *_ in getattr(mutate, bucket)
               if fn != want]
        self.assertEqual(bad, [], "追記先は対象ファイルごとに分ける")

    def test_no_bucket_is_forgotten(self):
        """新しい追記先を作ったら、この表にも載せる（載っていないものは検査されない）。"""
        found = {n for n in dir(mutate)
                 if n.startswith("M_") and isinstance(getattr(mutate, n), list)}
        self.assertEqual(found, set(self.BUCKETS), "新しい追記先は BUCKETS にも登録する")

    def test_every_bucket_reaches_M(self):
        """**追記先に足したのに M へ繋ぎ忘れる**と、その変異は一度も実行されない。

        「表にはあるのに走らない」は、PR 9 で 6 件見つけたのと同じ形の失敗である。
        """
        in_m = {name for name, *_ in mutate.M}
        missing = [name
                   for bucket in self.BUCKETS
                   for name, *_ in getattr(mutate, bucket)
                   if name not in in_m]
        self.assertEqual(missing, [], "追記先のリストを M へ足し忘れている")


if __name__ == "__main__":
    unittest.main()
