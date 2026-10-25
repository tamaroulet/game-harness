"""継ぎ目 B-1 の照合（`drafts/director/pipeline.md`）：課題の仕様を、要件の網羅の定義の読みに実物で通す。

    checks/join-spec-reqcov.json が、この試験を tests/join_spec_reqcov_judge.py に重ねて、ref の木で走らせる。

**なぜ要るか**: 計画役の監修のループ（W-43）は、課題の仕様に harness.reqcov を掛ける。仕様の書式と reqcov の読む形が
食い違えば、計画役の出力は網羅の検査で必ず落ちる。ファイルがあることではなく、前段の実物の出力を後段の実物に通して確かめる。

docs/bench/*/spec.md ごとに、reqcov の definitions が例外なく 1 件以上の ID を返すことを確かめ、課題ごとの件数を出す。
"""
import sys
import unittest
from pathlib import Path

from harness import reqcov

ROOT = Path(__file__).resolve().parent.parent


class BenchSpecsReadByReqcov(unittest.TestCase):
    def test_every_bench_spec_defines_requirements_reqcov_can_read(self):
        sys.stdout.reconfigure(encoding="utf-8")   # report.md に写る出力を UTF-8 にする（既定の cp932 だと文字化けする）
        specs = sorted((ROOT / "docs" / "bench").glob("*/spec.md"))
        found = [] if specs else ["docs/bench/*/spec.md が無い"]
        for spec in specs:
            name = spec.parent.name
            try:
                ids = reqcov.definitions(spec)
            except reqcov.InputError as e:
                found.append(f"{name}: {e}")
                continue
            print(f"{name}: 要件 {len(ids)} 件", flush=True)
        for line in found:
            print(line, flush=True)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
