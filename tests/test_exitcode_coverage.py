"""終了コードの三値（0 成功 / 1 不合格 / 2 環境異常）を、すべてのスクリプトの入口で守る（harness/exitcode.py）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 未捕捉の例外や `sys.exit("…")` は rc=1 になり、実装の不合格（1）と区別できない。
配管の故障を実装の過失と取り違えると、後段（スケジューラ・progress complete・実装役）が正しい実装を
直しにいく。新しいスクリプトを足したときに `exitcode.normalized` を付け忘れないよう、入口を機械で検査する。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import exitcode  # noqa: E402

# 入口はあるが、終了コードを誰も読まないもの（理由を書いて免除する）
EXEMPT = {
    "exitcode.py": "正規化そのもの",
    "adapters/csharp_tests.py": "結果ファイルの要約を表示するだけの診断用。終了コードを返さない",
    "templates/game-repo/.github/scripts/ms4_approval_gate.py": "ゲームのリポジトリへ配る承認門のひな形。harness では動かない（承認機構なのでここでは触らない）",
}
MAIN_RE = re.compile(r'^if __name__ == "__main__":', re.M)


class EveryEntryPointIsNormalized(unittest.TestCase):
    def test_all_scripts(self):
        missing = []
        for p in sorted((ROOT / "harness").rglob("*.py")):
            rel = p.relative_to(ROOT / "harness").as_posix()
            text = p.read_text(encoding="utf-8")
            if rel in EXEMPT or not MAIN_RE.search(text):
                continue
            if "sys.exit(exitcode.normalized(main))" not in text:
                missing.append(rel)
        self.assertEqual(missing, [], "exitcode.normalized を通さない入口があります")

    def test_exempt_list_is_not_stale(self):
        for rel in EXEMPT:
            self.assertTrue((ROOT / "harness" / rel).exists(), rel)


class Normalized(unittest.TestCase):
    def test_uncaught_exception_is_abort(self):
        def boom():
            raise RuntimeError("配管の故障")
        self.assertEqual(exitcode.normalized(boom), exitcode.ABORT)

    def test_reject_stays_one(self):
        self.assertEqual(exitcode.normalized(lambda: 1), 1)


if __name__ == "__main__":
    unittest.main()
