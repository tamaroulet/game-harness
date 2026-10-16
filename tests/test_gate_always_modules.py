"""Gate 1 が gate_always_modules を毎回走らせること（harness/gate_order.py の focused）の検査。

    python -m unittest tests.test_gate_always_modules -v

proc.run を差し替え、実際のテストは走らせない。作業ツリーは一時ディレクトリに作る。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import gate_order  # noqa: E402

GATE = ["python", "-m", "harness.fastsuite", "discover", "-s", "tests"]
ALWAYS = ["tests.test_isolation", "tests.test_ledger_double_check"]
FAILED = ("=" * 70 + "\nFAIL: test_scan (tests.test_isolation.Scan)\n" + "-" * 70
          + "\nTraceback (most recent call last):\n  File \"x.py\", line 3, in test_scan\n"
          + "AssertionError: 1 != 2\n\n" + "-" * 70 + "\nRan 1 tests in 0.001s\n\nFAILED (failures=1)\n")


def cfg(**extra):
    return {"gate_command": GATE, "gate_tail_chars": 4000, "ttl_seconds": {"gate": 10}, **extra}


def worktree(d, tests=("test_a", "test_isolation", "test_ledger_double_check")):
    (Path(d) / "tests").mkdir()
    for name in tests:
        (Path(d) / "tests" / f"{name}.py").write_text("", encoding="utf-8")
    return Path(d)


class Run:
    def __init__(self, result=(0, "", "")):
        self.result, self.commands = result, []

    def __call__(self, args, cwd, ttl, label, env=None, input=None):
        self.commands.append(list(args))
        return self.result


def focused(config, paths=(), tests=("test_a", "test_isolation", "test_ledger_double_check"), result=(0, "", "")):
    run = Run(result)
    with tempfile.TemporaryDirectory() as d, mock.patch.object(gate_order.proc, "run", side_effect=run):
        out = gate_order.focused(config, worktree(d, tests), list(paths), None)
    return out, run.commands


class TestAlwaysModules(unittest.TestCase):
    def test_always_modules_join_the_impacted_command(self):
        out, commands = focused(cfg(gate_always_modules=ALWAYS), ["tests/test_a.py"])
        self.assertEqual(commands, [GATE[:3] + ["tests.test_a", *ALWAYS]])
        self.assertEqual(out[0], True)

    def test_modules_missing_from_the_worktree_are_dropped(self):
        _, commands = focused(cfg(gate_always_modules=ALWAYS), ["tests/test_a.py"],
                              tests=("test_a", "test_isolation"))
        self.assertEqual(commands, [GATE[:3] + ["tests.test_a", "tests.test_isolation"]])

    def test_runs_without_impacted_tests(self):
        out, commands = focused(cfg(gate_always_modules=ALWAYS))
        self.assertEqual(commands, [GATE[:3] + ALWAYS])
        self.assertEqual(out[0], True)

    def test_module_already_impacted_is_not_repeated(self):
        _, commands = focused(cfg(gate_always_modules=ALWAYS), ["tests/test_isolation.py"])
        self.assertEqual(commands, [GATE[:3] + ["tests.test_isolation", "tests.test_ledger_double_check"]])

    def test_failure_fails_gate_and_returns_failed_test_output(self):
        out, _ = focused(cfg(gate_always_modules=ALWAYS), result=(1, "", FAILED))
        self.assertFalse(out[0])
        self.assertIn("FAIL: test_scan (tests.test_isolation.Scan)", out[1])
        self.assertIn("AssertionError: 1 != 2", out[1])

    def test_missing_or_empty_field_keeps_todays_command(self):
        for config in (cfg(), cfg(gate_always_modules=[])):
            out, commands = focused(config, ["tests/test_a.py"])
            self.assertEqual(commands, [GATE[:3] + ["tests.test_a"]])
            self.assertEqual(focused(config)[0], None)


    def test_repository_config_has_the_first_values(self):
        path = Path(__file__).resolve().parent.parent / "config" / "hline.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded.get("gate_always_modules"), ALWAYS)


if __name__ == "__main__":
    unittest.main()
