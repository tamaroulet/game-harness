"""変異表（tests/mutate.py）が古くなっていないこと。

    python -m unittest discover -s tests -v

変異は、置換元がソースにちょうど 1 か所あるときだけ意味を持つ。0 か所なら何も壊せず、2 か所以上なら
どちらを壊したのか分からない。mutate.py を全件回すのは時間がかかる（1 件あたり全テスト 1 周）ので、
置換元の一致だけを通常のテストで確かめる（処理を複製したときに、既存の変異が 2 か所に当たるようになった実例がある）。
"""
import os
import sys
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
            n = (mutate.T / fn).read_text(encoding="utf-8").count(old)
            if n != 1:
                stale.append(f"{name}: {fn} に {n} か所")
        self.assertEqual(stale, [], "変異表が古い。置換元を直すか、文脈を足して 1 か所にする")

    def test_names_are_unique(self):
        ids = [name.split()[0] for name, *_ in mutate.M]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
