"""Gate 1 の後・統合の前に、変えたパッケージの全件テストを走らせる（harness/hline_suite.py と hline_task.run_task）。"""
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_suite  # noqa: E402
import hline_task  # noqa: E402

PASS = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
FAIL = "import unittest\n\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail('boom-marker')\n"
CFG = {"max_attempts": 2, "ttl_seconds": {"git": 5, "gate": 120}, "out": "."}


def put(root, rel, text=""):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Repo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        put(self.root, "testbed/__init__.py")
        put(self.root, "testbed/core.py")
        put(self.root, "harness/x.py")
        put(self.root, "config/c.json", "{}")
        put(self.root, "docs/a.md")
        put(self.root, "tests/__init__.py")
        for name in ("test_testbed", "test_testbed_more", "test_other", "test_misc"):
            put(self.root, f"tests/{name}.py", PASS)


class Select(Repo):
    def test_only_testbed_selects_the_testbed_group(self):
        self.assertEqual(hline_suite.select(self.root, ["testbed/core.py"]), ("tests.test_testbed", "tests.test_testbed_more"))

    def test_harness_or_config_selects_the_unaffiliated_tests(self):
        for path in ("harness/x.py", "config/c.json"):
            self.assertEqual(hline_suite.select(self.root, [path]), ("tests.test_misc", "tests.test_other"))

    def test_a_document_selects_nothing(self):
        self.assertEqual(hline_suite.select(self.root, ["docs/a.md", "README.md"]), ())
        self.assertIsNone(hline_suite.run(self.root, ["docs/a.md"]))

    def test_a_changed_test_file_selects_its_own_group(self):
        self.assertEqual(hline_suite.select(self.root, ["tests/test_testbed_more.py"]), ("tests.test_testbed", "tests.test_testbed_more"))
        self.assertEqual(hline_suite.select(self.root, ["tests/test_misc.py"]), ("tests.test_misc", "tests.test_other"))

    def test_groups_are_unioned(self):
        got = hline_suite.select(self.root, ["testbed/core.py", "harness/x.py"])
        self.assertEqual(got, ("tests.test_misc", "tests.test_other", "tests.test_testbed", "tests.test_testbed_more"))

    def test_a_non_python_file_in_a_package_selects_nothing(self):
        self.assertEqual(hline_suite.select(self.root, ["testbed/data.json"]), ())


class Run(Repo):
    def test_passing_group_returns_modules_and_seconds(self):
        ok, text, rec = hline_suite.run(self.root, ["testbed/core.py"], 120)
        self.assertTrue(ok)
        self.assertEqual(rec["modules"], ["tests.test_testbed", "tests.test_testbed_more"])
        self.assertIn("壁時計:", text)
        self.assertIsInstance(rec["seconds"], float)

    def test_a_failing_member_fails_the_group(self):
        put(self.root, "tests/test_testbed_more.py", FAIL)
        ok, text, rec = hline_suite.run(self.root, ["testbed/core.py"], 120)
        self.assertFalse(ok)
        self.assertIn("boom-marker", text)


class Stack(Repo):
    def host(self, paths, gate_ok=True):
        out = Path(self.root) / "out"
        out.mkdir(exist_ok=True)
        self.out = out
        self.feedbacks = []

        def implement(cfg, wt, what, feedback, log):
            self.feedbacks.append(feedback)
            return 0, [], None, None, None

        return types.SimpleNamespace(new_worktree=lambda c, t: (self.root, "b"), implement=implement,
                                     changed_paths=lambda w, c: list(paths), gate=lambda *a, **k: (gate_ok, "Gate 1 ok"),
                                     hline_suite=hline_suite)

    def go(self, host):
        return hline_task.run_task(host, CFG, "t", "# T", self.out, (self.root, "b"))

    def test_a_failure_in_the_group_is_not_stacked_and_feeds_the_next_attempt(self):
        put(self.root, "tests/test_testbed.py", FAIL)
        wt, branch, tries = self.go(self.host(["testbed/core.py"]))
        self.assertIsNone(wt)
        self.assertEqual(len(tries), 2)
        self.assertFalse(tries[0]["gate"])
        self.assertIn("boom-marker", self.feedbacks[1])

    def test_a_passing_group_is_stacked_and_recorded(self):
        wt, branch, tries = self.go(self.host(["testbed/core.py"]))
        self.assertEqual((wt, branch), (self.root, "b"))
        self.assertTrue(tries[0]["gate"])
        self.assertEqual(tries[0]["suite"]["modules"], ["tests.test_testbed", "tests.test_testbed_more"])
        self.assertIn("seconds", tries[0]["suite"])

    def test_a_document_only_change_runs_nothing(self):
        wt, branch, tries = self.go(self.host(["docs/a.md"]))
        self.assertEqual(wt, self.root)
        self.assertNotIn("suite", tries[0])


if __name__ == "__main__":
    unittest.main()
