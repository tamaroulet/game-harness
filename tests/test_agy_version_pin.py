"""実装役（agy）の版の固定：呼び出しごとの照合と自動更新の停止（harness/ab/driver.py の agy_call）。

    python -m unittest tests.test_agy_version_pin

**なぜ要るか**: v2r-r1-01 は、起動時の照合（1.2.12）の 33 秒後に agy が自分を 1.2.14 に置き換え、38 回の呼び出しのうち
37 回が固定値と違う版で走った。起動時の 1 回の照合では、走行の途中の版の変化を止められない。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import common, driver  # noqa: E402

IMP = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "--yes", "model_flag": "--model",
       "model_name": "m", "output_format_args": ["--output-format", "json"], "usage_format": "agy"}
PINNED = {"cli": {"agy": "1.2.12"}}


class VersionGuard(unittest.TestCase):
    def test_the_pinned_version_passes_and_is_returned(self):
        self.assertEqual(driver.cli_version_guard(IMP, measure=lambda name: "1.2.12", pinned=PINNED), "1.2.12")

    def test_another_version_or_an_unreadable_one_stops(self):
        for got in ("1.2.14", None):
            with self.subTest(got=got), self.assertRaises(common.ABError):
                driver.cli_version_guard(IMP, measure=lambda name: got, pinned=PINNED)
        with self.assertRaises(common.ABError):
            driver.cli_version_guard(IMP, measure=lambda name: "1.2.12", pinned={"cli": {}})

    def test_each_call_checks_first_disables_auto_update_and_records_the_version(self):
        seen = {}

        def runner(args, cwd, ttl, label, env=None, input=None):
            seen["env"] = env
            return 0, '{"usage": {}}', ""
        r = driver.agy_call(IMP, "p", ".", None, 60, runner=runner, guard=lambda imp: "1.2.12")
        self.assertEqual(seen["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"], "true", "\"1\" では止まらなかった（実測）")
        self.assertEqual(r["cli_version"], "1.2.12")
        self.assertIn("cli_version", driver.CALL_RECORD, "metrics の呼び出しの要約に残す")

    def test_a_failed_check_does_not_call_the_implementer(self):
        called = []

        def guard(imp):
            raise common.ABError("版が違う")
        with self.assertRaises(common.ABError):
            driver.agy_call(IMP, "p", ".", None, 60, runner=lambda *a, **k: called.append(1), guard=guard)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
