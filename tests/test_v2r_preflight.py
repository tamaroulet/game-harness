"""走行の前の検定（原則 P3。docs/design/v2r_instrument_redesign.md §6 の 3）。

    python -m unittest tests.test_v2r_preflight

**なぜ要るか**: 前提が作れない性質・凍結した版と違う生成物・閉じていない文面は、実装役を呼ばずに分かる。v2r-dry-01・02 は
それを有料の走行で見つけた。検定が本物のマニフェストで通ること、1 つでも崩れたら落ちること、ドライバが記録の無い
マニフェストで起動しないことを確かめる。
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import project  # noqa: E402
from ab import common, driver, v2r_preflight  # noqa: E402

REAL = ROOT / "experiments" / "v2r" / "tasks.json"
HAVE_GAME = Path(project.load("falling-blocks")["repo_dir"]).exists()


def copy_experiment(d):
    """experiments/v2r を一時ディレクトリに写す（壊して検定を落とすため）。"""
    dst = Path(d) / "v2r"
    shutil.copytree(REAL.parent, dst, ignore=shutil.ignore_patterns("results"))
    return dst / "tasks.json"


class RealManifest(unittest.TestCase):
    @unittest.skipUnless(HAVE_GAME, "ゲームのリポジトリが無い（構造化仕様・GDD を base commit から読む）")
    def test_the_frozen_v2r_manifest_passes(self):
        with tempfile.TemporaryDirectory() as d:
            rec = v2r_preflight.run(REAL, d)
            self.assertTrue(rec["ok"], [c for c in rec["checks"] if c["problems"]])
            self.assertEqual(len(rec["checks"]), 7)
            self.assertTrue(v2r_preflight.stamp_path(REAL, d).exists())
            v2r_preflight.require(REAL, d)


class Breaks(unittest.TestCase):
    def failed(self, manifest, **kw):
        res = v2r_preflight.checks(manifest, **kw)
        return [n for n, ps in res if ps]

    def test_a_changed_frozen_input_fails_first(self):
        with tempfile.TemporaryDirectory() as d:
            m = copy_experiment(d)
            p = m.parent / "properties.json"
            p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
            self.assertEqual(self.failed(m), ["マニフェストの凍結した入力"])

    @unittest.skipUnless(HAVE_GAME, "ゲームのリポジトリが無い")
    def test_an_outcome_conditioned_given_fails(self):
        """前提に after を足すと、P1 の項目で落ちる（sha256 も合わせて、凍結の検査は通す）。"""
        with tempfile.TemporaryDirectory() as d:
            m = copy_experiment(d)
            p = m.parent / "properties.json"
            decl = json.loads(p.read_text(encoding="utf-8"))
            decl["properties"][0]["given"] += " && after.LockedMinoCount == before.LockedMinoCount"
            p.write_text(json.dumps(decl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            man = json.loads(m.read_text(encoding="utf-8"))
            man["properties_sha256"] = common.sha256_file(p)
            m.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            failed = self.failed(m)
            self.assertIn("前提に after を使わない（P1）", failed)
            self.assertIn("生成物がマニフェストの版と同じ", failed, "宣言が変われば生成物も変わる")

    def test_a_v2_manifest_is_refused(self):
        res = v2r_preflight.checks(ROOT / "experiments" / "v2" / "tasks.json")
        self.assertIn("マニフェストの種類", [n for n, ps in res if ps])


class Gate(unittest.TestCase):
    def test_the_driver_refuses_a_v2r_manifest_without_a_passing_record(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "tasks.json"
            m.write_text(json.dumps({"kind": "v2r"}), encoding="utf-8")
            with self.assertRaises(common.ABError) as ctx:
                v2r_preflight.require(m, d)
            self.assertIn("v2r_preflight", str(ctx.exception))
            stamp = v2r_preflight.stamp_path(m, d)
            stamp.parent.mkdir(parents=True)
            stamp.write_text(json.dumps({"ok": False}), encoding="utf-8")
            with self.assertRaises(common.ABError):
                v2r_preflight.require(m, d)
            stamp.write_text(json.dumps({"ok": True}), encoding="utf-8")
            v2r_preflight.require(m, d)
            m.write_text(json.dumps({"kind": "v2r", "x": 1}), encoding="utf-8")
            with self.assertRaises(common.ABError, msg="マニフェストが変われば記録も別"):
                v2r_preflight.require(m, d)

    def test_a_v2_manifest_needs_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "tasks.json"
            m.write_text(json.dumps({"kind": "v2"}), encoding="utf-8")
            v2r_preflight.require(m, d)

    def test_driver_main_stops_before_running(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "tasks.json"
            m.write_text(json.dumps({"kind": "v2r"}), encoding="utf-8")
            out, real = io.StringIO(), v2r_preflight.require
            with mock.patch.object(driver.envcheck, "require"), mock.patch.object(driver.provenance, "require"), \
                    mock.patch.object(driver.v2r_preflight, "require", side_effect=lambda man: real(man, d)), \
                    mock.patch.object(driver, "run_condition") as run, contextlib.redirect_stdout(out):
                rc = driver.main(["run", "--condition", "A0", "--run-id", "x", "--manifest", str(m)])
            self.assertEqual(rc, driver.exitcode.ABORT)
            run.assert_not_called()
            self.assertIn("走行の前の検定", out.getvalue())


if __name__ == "__main__":
    unittest.main()
