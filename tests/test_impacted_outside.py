"""影響テストの起点を harness/ の外のパッケージ（testbed/ など）に広げた分（harness/impacted.py）の検査。作業ツリーは一時ディレクトリの合成。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import impacted  # noqa: E402

FILES = {
    "testbed/__init__.py": "",
    "testbed/sim.py": "def run():\n    pass\n",
    "testbed/other.py": "def go():\n    pass\n",
    "harness/target.py": "def f():\n    pass\n",
    "scripts/tool.py": "x = 1\n",
    "tests/test_pkg.py": "import testbed\n",
    "tests/test_from_sim.py": "from testbed.sim import run\n",
    "tests/test_from_pkg.py": "from testbed import run\n",
    "tests/test_cli.py": "CMD = ['python', '-m', 'testbed.sim']\n",
    "tests/test_unrelated.py": "from testbed.other import go\nimport json\n",
    "tests/test_none.py": "import json\n",
    "tests/test_harness.py": "import target\n",
}


class Outside(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = Path(tmp.name)
        for rel, text in FILES.items():
            (self.wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / rel).write_text(text, encoding="utf-8")

    def pick(self, *paths):
        return set(impacted.test_modules(self.wt, paths=paths))

    def test_outside_modules(self):
        got = impacted.outside_modules(self.wt, ["testbed\\sim.py", "./testbed/sim.py", "harness/target.py", "tests/test_pkg.py",
                                                  "scripts/tool.py", "README.md", "testbed/data.txt", 3])
        self.assertEqual(got, ("testbed/sim.py",))

    def test_module_change_selects_importers_and_callers(self):
        self.assertEqual(self.pick("testbed/sim.py"),
                         {"tests.test_pkg", "tests.test_from_sim", "tests.test_from_pkg", "tests.test_cli"})

    def test_package_init_change(self):
        got = self.pick("testbed/__init__.py")
        self.assertIn("tests.test_pkg", got)
        self.assertIn("tests.test_from_pkg", got)
        self.assertNotIn("tests.test_none", got)
        self.assertNotIn("tests.test_from_sim", got)

    def test_harness_change_unchanged(self):
        self.assertEqual(self.pick("harness/target.py"), {"tests.test_harness"})
        self.assertEqual(impacted.changed_modules(["harness/target.py", "testbed/sim.py"]), ("harness/target.py",))

    def test_nothing_to_run(self):
        self.assertEqual(self.pick("scripts/tool.py", "docs/x.md"), set())
        self.assertEqual(self.pick(), set())

    def test_test_file_change_unchanged(self):
        self.assertEqual(set(impacted.test_modules(self.wt, changed=["tests.test_none"], paths=["tests/test_none.py"])),
                         {"tests.test_none"})


if __name__ == "__main__":
    unittest.main()
