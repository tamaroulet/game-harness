"""内側の道具の TTL が、呼び出し側の TTL の内側に収まっていること。

    python -m unittest discover -s tests -v

**なぜ要るか**: スケジューラは `decompose.py` を TTL つきで起動し、超えたら木ごと殺す。
`decompose.py` も内側で CLI を TTL つきで呼ぶ。内側の予算が外側を超えると、**外側の kill が
先に来て、`decompose.py` は telemetry も分解役のログも書けずに消える**。
つまり TTL を上げただけのつもりで、PR 12 で入れた記録がまるごと失われる。

この関係は 2 つの設定ファイルにまたがっていて、片方だけ見ても正しさが分からない。
だから機械で見る。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

# 外側の kill のあと、decompose.py が telemetry と分解役のログを書き終えるための余白。
# 子プロセスの起動と後始末のぶん。厳密な実測値ではなく、下限として置いている。
MARGIN_SECONDS = 30


def load(name):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


class TtlBudgetTests(unittest.TestCase):
    def setUp(self):
        self.decompose = load("decompose.json")["ttl_seconds"]
        self.scheduler = load("scheduler.json")["ttl_seconds"]

    def test_decompose_fits_inside_the_scheduler_ttl(self):
        """内側（claude + gh）の合計が、外側（scheduler の decompose）より小さいこと。"""
        inner = self.decompose["claude"] + self.decompose["gh"]
        outer = self.scheduler["decompose"]
        self.assertLess(
            inner + MARGIN_SECONDS, outer,
            f"内側の予算が外側に収まっていません: claude {self.decompose['claude']} + "
            f"gh {self.decompose['gh']} = {inner} 秒 に余白 {MARGIN_SECONDS} 秒を足すと、"
            f"外側の {outer} 秒を超えます。外側の kill が先に来ると、telemetry も"
            f"分解役のログも残りません")

    def test_the_claude_ttl_is_the_dominant_term(self):
        """余白の計算が gh 側の値に引きずられていないこと（どちらを直すべきか迷わないため）。"""
        self.assertGreater(self.decompose["claude"], self.decompose["gh"])

    def test_every_ttl_is_a_positive_number(self):
        bad = [f"{name}.{k} = {v!r}"
               for name, d in (("decompose", self.decompose), ("scheduler", self.scheduler))
               for k, v in d.items()
               if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0]
        self.assertEqual(bad, [], "TTL は正の数でなければならない")


if __name__ == "__main__":
    unittest.main()
