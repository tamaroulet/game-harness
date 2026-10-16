"""自己検査 [A] の復元段が、機能追加の単位でも成立すること（harness/pipeline.py）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 既存の実装がある単位では、自己検査 [A] は「自分でスタブに戻して赤を確認し、
復元して緑に戻ること」を確かめる。復元側は、赤がスタブのせいであって恒久的な故障ではない、
と示すための対照である。

ところが**機能追加の単位では、復元しても緑に戻らない**。この単位の受入テストが、まだ
書かれていない API を呼ぶからである。2026-09-19 の Issue #14 はこれで 4.3 秒で ABORT した。

```
NG   復元後の高速検査  -- 検査系故障: TRX が生成されませんでした（ビルド失敗の可能性）
```

実測のコンパイルエラーは全件が新しい受入テスト 2 ファイル由来で、既存テストからは 1 件も
出ていなかった（`SevenBagTests.cs: error CS0246: 'XorShift32' が見つかりません` など）。

`establish_base` が base の測定で同じ問題に当たり、同じ扱い（受入テストを除いて測り直す）を
既に採っている。ここはその再利用で、**新しい妥協ではない**。

**測れなくなるもの**も併せて固定する（下の test_it_does_not_prove_a_full_green を見よ）。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402

BUILD_ERR = "検査系故障: TRX が生成されませんでした (rc=1): error CS0246: 'XorShift32'"


class RestoredWithoutNewTestsTests(unittest.TestCase):
    def setUp(self):
        self.removed = []
        self.reset_calls = []
        self.c = SimpleNamespace(
            unit={"acceptance": {"required_tests": ["SevenBagTests", "FirstSpawnTests"]}},
        )

    def call(self, new_files=("SevenBagTests.cs", "FirstSpawnTests.cs"),
             isolated=({"Core.Tests.BoardGeometryTests.Width": "Passed"}, None)):
        with mock.patch.object(pipeline, "new_test_files",
                               return_value=[Path(f) for f in new_files]), \
             mock.patch.object(pipeline.fileops, "unlink",
                               side_effect=lambda p: self.removed.append(p.name)), \
             mock.patch.object(pipeline, "run_fast_tests", return_value=isolated), \
             mock.patch.object(pipeline, "sandbox_reset",
                               side_effect=lambda c: self.reset_calls.append(1)), \
             mock.patch("builtins.print"):
            return pipeline.restored_without_new_tests(self.c, BUILD_ERR)

    # ---- 退行そのもの

    def test_a_feature_unit_passes_once_its_own_tests_are_excluded(self):
        """これが Issue #14 を 4.3 秒で止めていた経路。"""
        fast, err = self.call()
        self.assertIsNone(err)
        self.assertEqual(fast, {"Core.Tests.BoardGeometryTests.Width": "Passed"})
        self.assertEqual(sorted(self.removed), ["FirstSpawnTests.cs", "SevenBagTests.cs"])

    def test_the_excluded_tests_are_put_back(self):
        """除いたままにすると、続く [B] 以降が別の理由で崩れる。"""
        self.call()
        self.assertEqual(len(self.reset_calls), 1, "成功時はサンドボックスを戻す")

    # ---- 緩めすぎていないこと

    def test_a_real_environment_failure_is_still_an_abort(self):
        """受入テストを除いてもビルドが壊れるなら、それは本物の故障。"""
        fast, err = self.call(isolated=(None, "SDK が見つかりません"))
        self.assertIsNone(fast)
        self.assertIn("受入テスト以外の故障", err)
        self.assertIn("SDK が見つかりません", err)
        self.assertEqual(self.reset_calls, [], "故障時は証拠を残したまま返す")

    def test_it_aborts_when_the_excluded_tests_still_ran(self):
        """除外が効いていないのに緑を信じない（健全性検査）。"""
        fast, err = self.call(isolated=({"Core.Tests.SevenBagTests.Order": "Passed"}, None))
        self.assertIsNone(fast)
        self.assertIn("除いたのに実行されています", err)
        self.assertIn("SevenBagTests", err)

    def test_it_keeps_the_original_error_when_there_is_nothing_to_exclude(self):
        """除くべきファイルが無いなら、元のビルド失敗がそのまま故障。"""
        fast, err = self.call(new_files=())
        self.assertIsNone(fast)
        self.assertIn(BUILD_ERR, err)
        self.assertIn("見つかりません", err)

    # ---- 測れなくなったことの明文化

    def test_it_does_not_prove_a_full_green(self):
        """**この門が測らなくなったもの。**

        「復元すれば完全に緑に戻る」は機能追加の単位では原理的に成立しないので、
        証明できるのは「この単位の受入テストを除けば緑」まで。黙って緩めると、
        後から「復元後は完全に緑のはず」と誤読される。
        """
        fast, err = self.call()
        self.assertIsNone(err)
        self.assertNotIn("SevenBagTests", " ".join(fast.keys()),
                         "除いた受入テストの結果は含まれない")

    # 「直接のビルドが通っているときは除外パスへ落ちない」ことは、呼び出し側の `if err:` が
    # 構造的に保証している。それを検査するテストは条件式の書き写しにしかならず、何も測らない
    # ので置かない（この表現自体が、変異で赤にならないテストの典型）。


if __name__ == "__main__":
    unittest.main()
