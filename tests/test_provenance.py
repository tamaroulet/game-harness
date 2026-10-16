"""走行の来歴（harness/provenance.py）。

    python -m unittest tests.test_provenance

**なぜ要るか**: 第三者が同じ条件を組み立てられるように、ハーネスのコミット・実装役の全体設定・環境変数を走行ごとに残す。
コミットしていないハーネスで走った記録は、どの版で走ったかを戻せないので、起動させない。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import provenance  # noqa: E402
from ab import driver  # noqa: E402


def fake_git(status=""):
    def git(args, cwd=None):
        return "abc123\n" if args[0] == "rev-parse" else status
    return git


class Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "GEMINI.md").write_text("規則", encoding="utf-8")
        (self.tmp / "oauth_creds.json").write_text("秘密", encoding="utf-8")

    def test_records_hashes_and_names_but_not_contents_or_credentials(self):
        rec = provenance.collect(git=fake_git(), home=self.tmp,
                                 environ={"HARNESS_PROPERTY_SEEDS": "1,2", "GEMINI_API_KEY": "k", "PATH": "p"},
                                 implementer={"cli": "agy", "model_name": "m", "_note": "注"})
        self.assertEqual(rec["harness"], {"commit": "abc123", "dirty": []})
        self.assertEqual(rec["agy_global"]["GEMINI.md"], provenance.sha256_of(self.tmp / "GEMINI.md"))
        self.assertIsNone(rec["agy_global"]["settings.json"], "無いファイルは None")
        self.assertEqual(rec["env"], {"HARNESS_PROPERTY_SEEDS": "1,2"}, "HARNESS_ の変数だけ。鍵は書かない")
        self.assertEqual(rec["implementer"], {"cli": "agy", "model_name": "m"})
        text = json.dumps(rec, ensure_ascii=False)
        self.assertNotIn("規則", text)
        self.assertNotIn("秘密", text)
        self.assertNotIn("oauth_creds", text)

    def test_a_dirty_harness_refuses_to_start_but_the_record_is_written(self):
        out = self.tmp / "p.json"
        with self.assertRaises(provenance.ProvenanceError):
            provenance.require(out, git=fake_git(" M harness/ab/driver.py\n?? experiments/v2r/x.json\n"), home=self.tmp)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["harness"]["dirty"],
                         ["experiments/v2r/x.json", "harness/ab/driver.py"])

    def test_the_manifest_is_recorded_with_its_sha256(self):
        m = ROOT / "experiments" / "v2r" / "tasks.json"
        rec = provenance.collect(manifest=m, git=fake_git(), home=self.tmp, environ={})
        self.assertEqual(rec["manifest"], {"path": "experiments/v2r/tasks.json", "sha256": provenance.sha256_of(m)})

    def test_the_driver_refuses_to_start_from_a_dirty_harness(self):
        with mock.patch.object(driver.envcheck, "require"), \
                mock.patch.object(driver.provenance, "require", side_effect=provenance.ProvenanceError("汚れ")), \
                mock.patch.object(driver, "run_condition") as run, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = driver.main(["run", "--condition", "A0", "--run-id", "x"])
        self.assertEqual(rc, driver.exitcode.ABORT)
        run.assert_not_called()
        self.assertIn("汚れ", out.getvalue())


if __name__ == "__main__":
    unittest.main()
