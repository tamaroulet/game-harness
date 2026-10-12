import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import goal_distance as gd  # noqa: E402


class PassingModulesPlainChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = {"inbox": str(self.tmp)}
        (self.tmp / "checks").mkdir()
        self.status = {}

    def declare(self, name, decl, result="合格"):
        decl = dict(decl, kind="unittest", modules=[f"tests.{name}"])
        (self.tmp / "checks" / f"{name}.json").write_text(json.dumps(decl), encoding="utf-8")
        self.status[name] = result

    def passing(self):
        text = "## 照合\n" + "".join(f"- {n}: {r}（x）\n" for n, r in self.status.items())
        with mock.patch.object(gd.hline_checks, "safe_section", return_value=text):
            return gd.passing_modules(self.cfg)

    def test_counts_only_plain_passing_checks(self):
        self.declare("plain", {})
        self.declare("explicit", {"ref": "origin/main"})
        self.declare("expect_pass", {"expect": "pass"})
        self.declare("repeated", {"repeat": 3})
        self.declare("overlaid", {"overlay": {"a": "b"}})
        self.declare("expect_fail", {"expect": "fail"})
        self.declare("sha", {"ref": "0123456789abcdef0123456789abcdef01234567"})
        self.declare("integration", {"ref": "origin/hline/integration"})
        self.declare("failing", {}, result="不合格")
        self.assertEqual(
            self.passing(),
            {"tests.plain", "tests.explicit", "tests.expect_pass", "tests.repeated"})


if __name__ == "__main__":
    unittest.main()
