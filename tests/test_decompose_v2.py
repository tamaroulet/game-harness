"""分解役の v2 出力と自己検査（harness/decompose.py、docs/design/mechanical_barriers.md §5 手順 4）。

    python -m unittest discover -s tests -v

**なぜ要るか**: スキーマ門（PR 20）が入ったので、v1 の単位定義は既存の凍結分しか通らない。
分解役が v2 を出し、書き出す前にパイプラインと同じ門とテスト生成に通すことを固定する。
門に落ちた出力は、問題の一覧を添えて出し直させ、それでも落ちれば何も書かない。
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import decompose  # noqa: E402
from test_testgen import UNIT  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402

# 分解役が返す形（ハーネスが足す値は含めない）
GOOD = {k: v for k, v in copy.deepcopy(UNIT).items()
        if k not in ("schema", "impl_files", "required_symbols")}
GOOD["acceptance"].pop("required_tests")
BAD = copy.deepcopy(GOOD)
BAD["acceptance"]["cases"][0]["expect"]["GameState.TickCount"] = 42   # 手計算のリテラル


class DecomposeV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "issue.md").write_text("Ready 初期状態を作る", encoding="utf-8")
        self.addCleanup(setattr, decompose, "CFG", decompose.CFG)
        self.addCleanup(setattr, decompose, "ROOT", decompose.ROOT)
        self.args = SimpleNamespace(project="falling-blocks", repo_dir=str(self.tmp / "repo"),
                                    issue=None, file=str(self.tmp / "issue.md"), id="issue_12")
        reader = lambda which: SPEC if which == "spec" else GDD
        self.enterContext(mock.patch.object(decompose, "base_reader", return_value=reader))

    def run_with(self, *responses):
        calls = mock.Mock(side_effect=[json.dumps(r, ensure_ascii=False) for r in responses])
        with mock.patch.object(decompose, "call_claude", calls):
            rc = decompose.decompose(self.args)
        return rc, calls

    def written(self):
        repo = self.tmp / "repo"
        return sorted(str(p.relative_to(repo)).replace("\\", "/") for p in repo.rglob("*") if p.is_file()) \
            if repo.exists() else []

    def test_good_output_is_written_with_generated_tests(self):
        rc, calls = self.run_with(GOOD)
        self.assertEqual((rc, calls.call_count), (0, 1))
        self.assertEqual(self.written(), ["tests/Core.Tests/Generated/Issue12Cases.cs", "tools/units/issue_12.json"])
        unit = json.loads((self.tmp / "repo/tools/units/issue_12.json").read_text(encoding="utf-8"))
        self.assertEqual(unit["schema"], 2)
        self.assertEqual(unit["acceptance"]["required_tests"], ["Issue12Cases"])
        self.assertIn("GameState.InjectSeed", unit["required_symbols"])
        self.assertNotIn("GameState.GameState", unit["required_symbols"])

    def test_rejected_output_is_retried_with_the_problems(self):
        rc, calls = self.run_with(BAD, GOOD)
        self.assertEqual((rc, calls.call_count), (0, 2))
        second_prompt = calls.call_args_list[1].args[0]
        self.assertIn("門で拒絶されました", second_prompt)
        self.assertIn("GDD から引けないリテラル", second_prompt)

    def test_nothing_is_written_when_retries_run_out(self):
        rc, calls = self.run_with(BAD, BAD)
        self.assertEqual((rc, calls.call_count), (1, 2))
        self.assertEqual(self.written(), [])

    def test_handwritten_tests_are_refused(self):
        rc, _ = self.run_with(dict(GOOD, test_files=[{"path": "tests/X.txt", "content": "x"}]),
                              dict(GOOD, test_files=[]))
        self.assertEqual(rc, 1)
        self.assertEqual(self.written(), [])


if __name__ == "__main__":
    unittest.main()
