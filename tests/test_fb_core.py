"""制御核を FeatureBench の箱に繋ぐ部品（harness/fb_core.py）のうち、FeatureBench が無くても確かめられる所。

    python -m unittest tests.test_fb_core -v

箱と agy は偽物の関数で置き換える（利用枠 0）。FeatureBench に足す所は、WSL で偽物の agy を通して確かめる。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import core  # noqa: E402
import fb_agy  # noqa: E402
import fb_core  # noqa: E402

IMP = fb_agy.agent_settings(fb_agy.load_json(fb_agy.AGENT_CONFIG), "gemini-4-hydrogen-medium")

STATEMENT = """## Task
Implement the hooks.

**NOTE**:
- This test comes from the `pkg` library.
- You are given access to the test file `/testbed/pkg/tests/test_mod.py` for this task (white-box testing). Use this information to help you solve the problem.

## Interface Description
Path: `/testbed/pkg/mod.py`
```python
class Thing(Base):
    def value(self) -> int:
        ...
def helper(x):
    ...
```
Path: `/testbed/pkg/other.py`
```python
def helper(x):
    ...
```
"""

COLLECT = """pkg/tests/test_mod.py::TestThing::test_a
pkg/tests/test_mod.py::TestThing::test_b[1]
pkg/tests/test_mod.py::test_free

3 tests collected in 0.01s
"""

PYTEST_FAIL = """..F
=========================== short test summary info ============================
PASSED pkg/tests/test_mod.py::TestThing::test_a
FAILED pkg/tests/test_mod.py::TestThing::test_b[1] - AssertionError: 1 != 2
ERROR pkg/tests/test_mod.py::test_free - NameError: name 'helper' is not defined
==================== 1 failed, 1 passed, 1 error in 0.12s =====================
"""

STATUS = """ M pkg/mod.py
?? pkg/new.py
 D pkg/old.py
M  pkg/tests/test_mod.py
?? pkg/tests/helper.py
R  pkg/b.py -> pkg/c.py
 M README.md
?? conftest.py
"""

PYFLAKES = """pkg/mod.py:3:1: 'os' imported but unused
pkg/mod.py:10:5: undefined name 'missing_fn'
pkg/new.py:2:7: invalid syntax
"""


class Parsing(unittest.TestCase):
    def test_visible_test_path_comes_from_the_white_box_note(self):
        self.assertEqual(fb_core.visible_test_path(STATEMENT), "pkg/tests/test_mod.py")
        self.assertIsNone(fb_core.visible_test_path("no note here"))

    def test_statement_items_list_the_interface_heads_per_path(self):
        self.assertEqual(fb_core.statement_items(STATEMENT),
                         ["pkg/mod.py: class Thing", "pkg/mod.py: def helper", "pkg/other.py: def helper"])

    def test_collected_nodes_are_grouped_by_class(self):
        nodes = fb_core.collected_nodes(COLLECT)
        self.assertEqual(len(nodes), 3)
        groups = fb_core.group_nodes(nodes)
        self.assertEqual(list(groups), ["pkg/tests/test_mod.py::TestThing", "pkg/tests/test_mod.py"])
        self.assertEqual(len(groups["pkg/tests/test_mod.py::TestThing"]), 2)

    def test_parse_pytest_reads_each_item(self):
        passed, failed, errors, summary = fb_core.parse_pytest(PYTEST_FAIL, 1)
        self.assertEqual(passed, ["pkg/tests/test_mod.py::TestThing::test_a"])
        self.assertEqual(failed, ["pkg/tests/test_mod.py::TestThing::test_b[1]", "pkg/tests/test_mod.py::test_free"])
        self.assertIn("pkg/tests/test_mod.py::TestThing::test_b[1] - AssertionError: 1 != 2", errors)
        self.assertEqual(summary, "1 failed, 1 passed, 1 error in 0.12s")

    def test_parse_pytest_reads_the_quiet_summary_line_without_equals(self):
        out = "FAILED t.py::a - E\n53 failed, 16 passed, 3 skipped in 1.87s\n"
        self.assertEqual(fb_core.parse_pytest(out, 1)[3], "53 failed, 16 passed, 3 skipped in 1.87s")

    def test_parse_pytest_keeps_a_reason_when_nothing_ran(self):
        _, failed, errors, _ = fb_core.parse_pytest("ImportError while loading conftest\nE   boom\n", 4)
        self.assertEqual(failed, [])
        self.assertTrue(errors and errors[0].startswith("pytest exit 4"))

    def test_changed_py_skips_tests_deleted_and_non_python(self):
        self.assertEqual(fb_core.changed_py(STATUS), ["pkg/mod.py", "pkg/new.py", "pkg/c.py"])

    def test_parse_static_counts_only_undefined_names_and_syntax(self):
        self.assertEqual(fb_core.parse_static(PYFLAKES),
                         ["pkg/mod.py:10:5: undefined name 'missing_fn'", "pkg/new.py:2:7: invalid syntax"])

    def test_split_exit_keeps_stderr_after_the_mark(self):
        code, body = fb_core.split_exit("out\n" + fb_core.EXIT_MARK + "1\nstderr line\n")
        self.assertEqual(code, 1)
        self.assertIn("stderr line", body)
        self.assertNotIn(fb_core.EXIT_MARK, body)


class Ledger(unittest.TestCase):
    def test_build_ledger_from_the_statement_and_the_visible_tests(self):
        lg = fb_core.build_ledger("u", STATEMENT, "pkg/tests/test_mod.py", fb_core.collected_nodes(COLLECT))
        self.assertEqual([c.id for c in lg.checks], ["C1", "C2"])
        self.assertTrue(lg.checks[0].required)
        self.assertEqual([r.source for r in lg.requirements], ["statement"] * 3 + ["test"] * 2)
        self.assertEqual(lg.requirements[3].checks, ["C1::pkg/tests/test_mod.py::TestThing::test_a",
                                                     "C1::pkg/tests/test_mod.py::TestThing::test_b[1]"])

    def test_without_a_visible_test_the_test_check_is_not_required(self):
        lg = fb_core.build_ledger("u", "no note", None, [], static=False)
        self.assertEqual(len(lg.checks), 1)
        self.assertFalse(lg.checks[0].required)


class FakeBox:
    def __init__(self, replies):
        self.replies = replies
        self.commands = []

    def exec(self, command, timeout):
        self.commands.append(command)
        for key, (code, out) in self.replies.items():
            if key in command:
                return 0, f"{out}\n{fb_core.EXIT_MARK}{code}\n"
        return 0, f"{fb_core.EXIT_MARK}0\n"


class Adapter(unittest.TestCase):
    def test_verify_runs_the_visible_tests_and_pyflakes_on_changed_files(self):
        box = FakeBox({"-m pytest pkg/tests": (1, PYTEST_FAIL), "git status": (0, STATUS), "pyflakes": (1, PYFLAKES)})
        with tempfile.TemporaryDirectory() as d:
            ad = fb_core.ContainerAdapter(box.exec, d)
            res = ad.verify([core.Check("C1", "tests", ["pkg/tests/test_mod.py"]), core.Check("C2", "static", [])])
            self.assertFalse(res[0].ok)
            self.assertEqual(len(res[0].failed), 2)
            self.assertFalse(res[1].ok)
            self.assertEqual(len(res[1].failed), 2)
            self.assertTrue((Path(d) / res[0].raw).exists())
        pyflakes_cmd = next(c for c in box.commands if "pyflakes" in c)
        self.assertIn(f"PYTHONPATH={fb_core.TOOLS_DIR}", pyflakes_cmd)
        self.assertIn("pkg/mod.py pkg/new.py pkg/c.py", pyflakes_cmd)
        self.assertTrue(all(c.startswith(fb_core.ENV) for c in box.commands))
        pytest_cmd = next(c for c in box.commands if "-m pytest" in c)
        self.assertIn("timeout ", pytest_cmd, "FeatureBench の exec_command は時間を切らないので、自分で切る")

    def test_static_stops_instead_of_passing_when_the_tool_is_missing(self):
        box = FakeBox({"git status": (0, STATUS), "pyflakes": (1, "/usr/bin/python: No module named pyflakes")})
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                fb_core.ContainerAdapter(box.exec, d).verify([core.Check("C2", "static", [])])

    def test_tools_ready_needs_the_package(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(fb_core.tools_ready(d))
            self.assertFalse(fb_core.tools_ready(""))
            Path(d, "pyflakes").mkdir()
            Path(d, "pyflakes", "__init__.py").write_text("", encoding="utf-8")
            self.assertTrue(fb_core.tools_ready(d))

    def test_static_passes_when_nothing_changed(self):
        box = FakeBox({"git status": (0, "")})
        with tempfile.TemporaryDirectory() as d:
            res = fb_core.ContainerAdapter(box.exec, d).verify([core.Check("C2", "static", [])])
        self.assertTrue(res[0].ok)


def stream(conv, model="gemini-4-hydrogen-medium", done=True):
    lines = [{"event": "init", "init": {"model": model, "conversation_id": conv}},
             {"event": "step_update", "step_update": {"step_index": 0, "step_type": "agent_response", "state": "DONE",
                                                      "usage": {"input_tokens": 50, "output_tokens": 5,
                                                                "thinking_tokens": 1, "cache_read_tokens": 500}}}]
    if done:
        lines.append({"event": "result", "result": {"status": "SUCCESS", "conversation_id": conv, "response": "done",
                                                    "usage": {"input_tokens": 50, "output_tokens": 5}}})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


class Port(unittest.TestCase):
    def test_start_then_send_continue_the_same_conversation(self):
        commands, written = [], {}
        with tempfile.TemporaryDirectory() as d:
            def fetch(src, local):
                Path(local).write_text(stream("conv-1"), encoding="utf-8")

            port = fb_core.AgyPort(IMP, write=lambda local, dest: written.update({dest: Path(local).read_text(encoding="utf-8")}),
                                   exec_stream=lambda cmd, ttl: commands.append((cmd, ttl)) or 0, fetch=fetch, log_dir=d)
            t1 = port.start("do the task", 3000)
            t2 = port.send("Harness verification after your last turn: FAILED.", 1500)
            self.assertEqual(Path(d, "agy_prompt_2.txt").read_text(encoding="utf-8"),
                             "Harness verification after your last turn: FAILED.", "送った文を手番ごとに残す")
        self.assertTrue(t1.ok and t2.ok)
        self.assertEqual(t1.model, "gemini-4-hydrogen-medium")
        self.assertEqual(t1.usage["input_tokens"], 50)
        self.assertNotIn("--conversation", commands[0][0])
        self.assertIn("--conversation conv-1", commands[1][0])
        self.assertIn("--model gemini-4-hydrogen-medium", commands[0][0])
        self.assertIn("AGY_CLI_DISABLE_AUTO_UPDATE=1", commands[0][0])
        self.assertEqual(commands[1][1], 1500)
        self.assertIn("do the task", written[f"{fb_core.LOG_DIR}/agy_prompt_1.jsonl"])

    def test_a_turn_without_a_result_is_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            port = fb_core.AgyPort(IMP, write=lambda a, b: None, exec_stream=lambda c, t: 124,
                                   fetch=lambda s, local: Path(local).write_text(stream("c", done=False), encoding="utf-8"),
                                   log_dir=d)
            self.assertFalse(port.start("x", 100).ok)


class Records(unittest.TestCase):
    def test_write_records_keeps_the_plain_a_shape(self):
        rec = {"stop": "pass", "decision": "pass", "rounds": [
            {"turn": {"model": "m", "ok": True, "usage": {"input_tokens": 1, "output_tokens": 2, "model_steps": 3}}},
            {"turn": {"model": "m", "ok": True, "usage": {"input_tokens": 4, "output_tokens": 5, "model_steps": 6}}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "agy_stream_2.jsonl").write_text("b\n", encoding="utf-8")
            Path(d, "agy_stream_1.jsonl").write_text("a\n", encoding="utf-8")
            fb_core.write_records(d, rec, "m")
            usage = json.loads(Path(d, "agy_usage.json").read_text(encoding="utf-8"))
            joined = Path(d, "agy_stream.jsonl").read_text(encoding="utf-8")
        self.assertEqual(usage["reported_model"], "m")
        self.assertEqual(usage["usage"]["input_tokens"], 5)
        self.assertEqual(usage["usage"]["turns"], 2)
        self.assertEqual(joined, "a\nb\n")


class Arms(unittest.TestCase):
    def test_every_arm_in_the_config_gets_its_own_agent_name(self):
        class Base:
            def __init__(self, *a, **k):
                pass

        arms, cfg = fb_core.load_arms()
        agents = fb_core.arm_agents(Base, arms)
        self.assertEqual(set(agents), set(arms))
        for name, cls in agents.items():
            self.assertEqual(cls().name, name)
        self.assertFalse(arms["agy-off"]["gate"] or arms["agy-off"]["show_ledger"], "全部切った腕は素の A と同じ形")


if __name__ == "__main__":
    unittest.main()
