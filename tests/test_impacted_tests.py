"""影響テスト（harness/impacted.py）の検査。作業ツリーは一時ディレクトリ、proc.run・proc.resolve_cli は差し替え（git・gh・CLI・モデルは呼ばない）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import gate_order  # noqa: E402
import hline  # noqa: E402
import impacted  # noqa: E402

CFG = hline.load_config()
VERIFY = "python -m unittest tests.test_new"
SPEC = {"target_symbols": [{"module": "harness/target.py", "kind": "function", "name": "f"}],
        "edit_boundary": {"allowed_files": ["harness/target.py", "tests/test_new.py"], "forbidden_files": [], "max_diff_lines": 300},
        "test_oracle": {"verification_command": VERIFY}}
WANT = ("tests.test_new", "tests.test_imp", "tests.test_cli")
FILES = {"harness/target.py": "def f():\n    pass\n", "harness/other.py": "def g():\n    pass\n",
         "tests/test_imp.py": "import target\n", "tests/test_cli.py": "CMD = ['python', '-m', 'harness.target', 'x']\n",
         "tests/test_other.py": "import other\nCMD = 'python -m harness.other'\n", "tests/helper.py": "import target\n",
         "tests/sub/test_deep.py": "import target\n"}


class Tree(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = Path(tmp.name)
        for rel, text in FILES.items():
            (self.wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / rel).write_text(text, encoding="utf-8")
        (self.wt / "docs").mkdir()
        state = {"tasks": [{"id": "T1", "verification": {"command": VERIFY, "expected_exit_code": 0}}]}
        (self.wt / "docs" / "progress.yaml").write_text(yaml.safe_dump(state), encoding="utf-8")


class Pure(unittest.TestCase):
    def test_dotted_and_command_modules_and_from_spec(self):
        self.assertEqual((impacted.dotted("tests/test_x.py"), impacted.dotted("tests\\test_x.py")), ("tests.test_x",) * 2)
        for bad in ("harness/x.py", "tests/helper.py", "tests/sub/test_c.py", "tests/*.py"):
            self.assertIsNone(impacted.dotted(bad))
        self.assertEqual(impacted.command_modules("python -m unittest tests.test_a tests.test_b -v"), ("tests.test_a", "tests.test_b"))
        for text in ("python -m harness.fastsuite discover -s tests", "python -m harness.model_pin", "python -m unittest discover -s tests", None, ""):
            self.assertEqual(impacted.command_modules(text), ())
        self.assertEqual(impacted.from_spec(SPEC), ("tests.test_new",))
        for bad in (None, {}, {"edit_boundary": 3, "test_oracle": []}, {"edit_boundary": {"allowed_files": "x"}}):
            self.assertEqual(impacted.from_spec(bad), ())

    def test_allowed_tools_note_and_stage_command(self):
        self.assertEqual(impacted.allowed_tools(["tests.test_a", "tests.test_b"]),
                         ("Bash(python -m unittest tests.test_a:*)", "Bash(python -m unittest tests.test_b:*)"))
        self.assertEqual((impacted.allowed_tools([]), impacted.prompt_note([])), ((), ""))
        for want in ("tests.test_a", "tests.test_b", "python -m unittest <モジュール>"):
            self.assertIn(want, impacted.prompt_note(["tests.test_a", "tests.test_b"]))
        v = {"command": VERIFY, "expected_exit_code": 0}
        self.assertEqual(impacted.stage_command(CFG["gate_command"], ["tests.test_new"], v), (VERIFY, VERIFY.split(), 0))
        text, args, code = impacted.stage_command(CFG["gate_command"], ["tests.test_new", "tests.test_imp"], dict(v, expected_exit_code=2))
        self.assertEqual((args, code, text), (VERIFY.split() + ["tests.test_imp"], 2, VERIFY + " tests.test_imp"))
        other = {"command": "python -m harness.model_pin", "expected_exit_code": 0}
        self.assertEqual(impacted.stage_command(CFG["gate_command"], ["tests.test_a"], other)[1], ["python", "-m", "harness.model_pin"])
        cmd = impacted.stage_command(CFG["gate_command"], ["tests.test_a"])
        self.assertEqual((cmd[1], cmd[2]), (hline.base_whitelist.unittest_command(CFG["gate_command"], ["tests.test_a"]), 0))
        self.assertIsNone(impacted.stage_command(CFG["gate_command"], []))

    def test_scoped_agent_replaces_only_the_unittest_item_and_leaves_the_config_alone(self):
        agent = CFG["implementer"]
        before = json.dumps(agent, sort_keys=True)
        flags = impacted.scoped_agent(agent, ["tests.test_a"])["extra_flags"]
        self.assertEqual(json.dumps(agent, sort_keys=True), before)
        self.assertEqual(flags[flags.index("--allowedTools") + 1], "Read,Grep,Glob,Edit,Write,Bash(python -m unittest tests.test_a:*)")
        flags = impacted.scoped_agent(agent, [])["extra_flags"]
        self.assertEqual(flags[flags.index("--allowedTools") + 1], "Read,Grep,Glob,Edit,Write")
        self.assertEqual(impacted.scoped_agent(plain := {"extra_flags": ["--x"], "model": "m"}, ["tests.test_a"]), plain)


class Modules(Tree):
    def test_the_importers_and_the_python_m_callers_are_in_and_unrelated_tests_are_not(self):
        self.assertEqual(impacted.test_modules(self.wt, SPEC), WANT)
        got = impacted.test_modules(self.wt, SPEC, {"command": "python -m unittest tests.test_v"}, ["tests.test_c"])
        self.assertEqual(got, ("tests.test_c", "tests.test_v") + WANT)

    def test_nothing_known_gives_nothing_and_a_bare_root_does_not_raise(self):
        self.assertEqual(impacted.test_modules(self.wt), ())
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(impacted.test_modules(d, SPEC), ("tests.test_new",))
            self.assertEqual(impacted.test_modules(Path(d) / "missing", {"target_symbols": [{"module": "harness/x.py"}]}), ())


class Run:
    def __init__(self, first=(0, "", "")):
        self.first, self.commands, self.labels, self.inputs = first, [], [], []

    def __call__(self, args, cwd, ttl, label, env=None, input=None):
        self.commands.append(list(args))
        self.labels.append(label)
        self.inputs.append(input)
        return self.first if label != "Gate 1" else (0, "", "")


class GateStage(Tree):
    def gate(self, run):
        counts = {"added": 1, "deleted": 0, "deleted_files": ()}
        with mock.patch.object(hline.proc, "run", side_effect=run), mock.patch.object(hline, "diff_counts", return_value=counts), \
                mock.patch.object(hline.size_limits, "head_source", return_value=lambda path: None):
            return hline.gate(CFG, self.wt, ["harness/target.py", "tests/test_new.py"], SPEC, "T1")

    def test_a_failing_first_stage_runs_only_the_impacted_tests_and_never_the_full_suite(self):
        run = Run(first=(1, "STAGE-OUT", ""))
        ok, msg = self.gate(run)
        self.assertFalse(ok)
        self.assertIn("STAGE-OUT", msg)
        self.assertEqual(run.commands, [["python", "-m", "unittest", *WANT]])
        self.assertEqual(run.commands[0][3:], list(impacted.test_modules(self.wt, SPEC, {"command": VERIFY}, ("tests.test_new",))))
        self.assertNotIn("Gate 1", run.labels)
        self.assertNotIn(CFG["gate_command"], run.commands)

    def test_a_passing_first_stage_is_the_whole_verdict(self):
        """影響テストが通れば、そこで合格（全件テストは内側のループで走らせない。C3）。"""
        run = Run()
        self.assertTrue(self.gate(run)[0])
        self.assertEqual((len(run.commands), run.commands[-1]), (1, ["python", "-m", "unittest", *WANT]))
        self.assertNotIn("Gate 1", run.labels)

    def test_without_a_spec_the_stage_is_what_it_was(self):
        run = Run(first=(1, "o", ""))
        with mock.patch.object(gate_order.proc, "run", side_effect=run):
            ok, msg = gate_order.focused(CFG, self.wt, ["harness/x.py", "tests/test_a.py"], "T1")
            self.assertEqual(run.commands[-1], VERIFY.split())
            self.assertIn(f"`{VERIFY}` が終了コード 1（期待 0）", msg)
            gate_order.focused(CFG, self.wt, ["harness/x.py", "tests/test_a.py"], None)
            self.assertEqual(run.commands[-1], hline.base_whitelist.unittest_command(CFG["gate_command"], ["tests.test_a"]))
            self.assertIsNone(gate_order.focused(CFG, self.wt, ["harness/x.py"], None))


class Implement(Tree):
    def test_the_permission_and_the_input_name_the_impacted_tests_and_nothing_else(self):
        run = Run(first=(0, json.dumps({"result": "x", "modelUsage": {CFG["implementer"]["model"]: {}}, "num_turns": 1}), ""))
        before = json.dumps(CFG["implementer"], sort_keys=True)
        with mock.patch.object(hline.proc, "run", side_effect=run), mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]):
            hline.implement(CFG, str(self.wt), SPEC, "FB", self.wt / "log")
        self.assertEqual(json.dumps(CFG["implementer"], sort_keys=True), before)
        args = run.commands[0]
        tools = args[args.index("--allowedTools") + 1].split(",")
        self.assertEqual([t for t in tools if t.startswith("Bash")], list(impacted.allowed_tools(WANT)))
        self.assertNotIn("Bash(python -m unittest:*)", tools)
        self.assertNotIn(" ".join(CFG["gate_command"]), " ".join(tools))
        self.assertEqual(args[args.index("--max-turns") + 1], str(CFG["implementer"]["budget"]["max_turns"]))
        head, rest = run.inputs[0].split("\n---\n", 1)
        for name in WANT:
            self.assertIn(name, head)
        self.assertIn(impacted.prompt_note(WANT), head)
        self.assertEqual(json.loads(rest.split("\n---\n")[0]), SPEC)
        self.assertNotIn("tests.test_other", run.inputs[0])


if __name__ == "__main__":
    unittest.main()
