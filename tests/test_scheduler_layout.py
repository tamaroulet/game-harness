"""scheduler の試験の分け方（tests/test_scheduler*.py）が、並列に走れる大きさで、過不足なく 1 回ずつ走ること。

    python -m unittest tests.test_scheduler_layout
"""
import ast
import collections
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LIMIT = 18
SELF = "test_scheduler_layout"


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def key(test):
    return f"{type(test).__name__}.{test._testMethodName}"


def modules():
    return sorted(p.stem for p in TESTS_DIR.glob("test_scheduler*.py") if p.stem != SELF)


def defined():
    import tests.test_scheduler as base
    loader = unittest.defaultTestLoader
    names = set()
    for name, obj in vars(base).items():
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj.__module__ == base.__name__:
            names.update(f"{name}.{m}" for m in loader.getTestCaseNames(obj))
    return names


def loaded(module):
    return list(flatten(unittest.defaultTestLoader.loadTestsFromName(f"tests.{module}")))


def on_scaffold(test):
    # 試験クラスは別名の test_scheduler から読み込まれるので、クラスの同一性ではなく名前で Base を探す
    return any(c.__name__ == "Base" and c.__module__.endswith("test_scheduler") for c in type(test).__mro__)


class Layout(unittest.TestCase):
    def test_the_definitions_are_the_83_tests(self):
        self.assertEqual(len(defined()), 83)

    def test_each_module_holds_at_most_18_tests_built_on_the_git_scaffold(self):
        for module in modules():
            self.assertLessEqual(len([t for t in loaded(module) if on_scaffold(t)]), LIMIT, module)

    def test_the_modules_together_run_every_test_exactly_once(self):
        keys = [key(t) for module in modules() for t in loaded(module)]
        self.assertEqual([k for k, n in collections.Counter(keys).items() if n > 1], [])
        self.assertEqual(set(keys), defined())
        self.assertEqual(len(keys), 83)

    def test_discover_finds_the_same_83_tests_once_each(self):
        suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_scheduler*.py",
                                                    top_level_dir=str(TESTS_DIR))
        found = [t for t in flatten(suite) if type(t).__module__.startswith("test_scheduler")
                 and type(t).__module__ != SELF]
        keys = [key(t) for t in found]
        self.assertEqual([k for k, n in collections.Counter(keys).items() if n > 1], [])
        self.assertEqual(set(keys), defined())
        self.assertEqual(len(keys), 83)

    def test_the_quiet_check_does_not_run_a_scheduler_module_in_a_child(self):
        tree = ast.parse((TESTS_DIR / "test_quiet_output.py").read_text(encoding="utf-8"))
        names = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("tests.test_")]
        self.assertTrue(names)
        self.assertEqual([n for n in names if n.startswith("tests.test_scheduler")], [])

    def test_the_unused_copy_of_the_scaffold_is_gone(self):
        self.assertFalse((TESTS_DIR / "scheduler_support.py").exists())


if __name__ == "__main__":
    unittest.main()
