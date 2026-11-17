"""E-1 の段 a の道具（harness/fb_ground.py・harness/fb_gate_probe.py）のうち、箱が無くても確かめられる所。

    python -m unittest tests.test_fb_gate -v
"""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import fb_gate_probe as probe  # noqa: E402
import fb_ground as ground  # noqa: E402
import fb_replay as replay  # noqa: E402

MASK = textwrap.dedent("""\
    diff --git a/pkg/stats.py b/pkg/stats.py
    --- a/pkg/stats.py
    +++ b/pkg/stats.py
    @@ -1,9 +1,3 @@
     class ECDF:
         def __call__(self, x):
             return self._eval_univariate(x)
    -
    -    def _eval_univariate(self, x):
    -        return x
    -
    -def helper_table(x):
    -    return x
    -LIMIT = 3
    -    def __repr__(self):
    diff --git a/pkg/tests/test_stats.py b/pkg/tests/test_stats.py
    --- a/pkg/tests/test_stats.py
    +++ b/pkg/tests/test_stats.py
    @@ -1,2 +1,1 @@
    -def removed_in_test():
     pass
    """)

TEST_PATCH = textwrap.dedent("""\
    diff --git a/pkg/tests/test_stats.py b/pkg/tests/test_stats.py
    deleted file mode 100644
    index 1111111..0000000
    --- a/pkg/tests/test_stats.py
    +++ /dev/null
    @@ -1,3 +0,0 @@
    -import pytest
    -def test_a():
    -    assert 1 == 1
    """)

TESTS = textwrap.dedent("""\
    import pytest
    from pkg.stats import ECDF, helper_table

    @pytest.fixture
    def data():
        return helper_table([1])

    def build():
        return ECDF()

    def test_message():
        with pytest.raises(ValueError, match="bins must be a positive integer"):
            ECDF()(0)

    def test_hidden(data):
        assert data == [1]

    def test_referred():
        assert build()._eval_univariate(1) == 1

    def test_plain():
        assert ECDF() is not None

    class TestThing:
        def setup_method(self):
            self.x = 1

        def test_repr(self):
            assert repr(ECDF()) == "<ECDF object at 0x0>"
    """)

STATEMENT = "Implement ECDF. Path: `/testbed/pkg/stats.py`. Raise ValueError when bins is wrong."


class GroundTest(unittest.TestCase):
    def test_removed_names_skip_tests_and_dunders(self):
        self.assertEqual(ground.removed_names(MASK), {"_eval_univariate", "helper_table", "LIMIT"})
        self.assertEqual(ground.mask_files(MASK), {"pkg/stats.py"})

    def test_original_tests(self):
        self.assertEqual(ground.original_tests(TEST_PATCH),
                         {"pkg/tests/test_stats.py": "import pytest\ndef test_a():\n    assert 1 == 1\n"})

    def test_function_key(self):
        self.assertEqual(ground.function_key("a/test_x.py::TestA::test_b[1-2]"), "TestA::test_b")
        self.assertEqual(ground.function_key("a/test_x.py::test_c"), "test_c")

    def test_scopes_include_fixtures_helpers_and_setup(self):
        scopes = ground.test_scopes(TESTS)
        self.assertEqual(sorted(scopes), ["TestThing::test_repr", "test_hidden", "test_message", "test_plain", "test_referred"])
        names = {n.name for n in scopes["test_hidden"]}
        self.assertEqual(names, {"test_hidden", "data"})
        self.assertIn("build", {n.name for n in scopes["test_referred"]})
        self.assertIn("setup_method", {n.name for n in scopes["TestThing::test_repr"]})

    def test_classify_rules(self):
        removed = ground.removed_names(MASK)
        scopes = ground.test_scopes(TESTS)
        src_names = {"_eval_univariate"}          # 残ったコードが呼んでいる
        got = {k: ground.classify(STATEMENT, removed, src_names, set(), v)[0] for k, v in scopes.items()}
        self.assertEqual(got["test_message"], "隠し：文言")
        self.assertEqual(got["test_hidden"], "隠し：名")
        self.assertEqual(got["test_referred"], "根拠：参照")
        self.assertEqual(got["test_plain"], "根拠：文")
        self.assertEqual(got["TestThing::test_repr"], "隠し：文言")

    def test_literal_found_in_source_is_grounded(self):
        removed = ground.removed_names(MASK)
        nodes = ground.test_scopes(TESTS)["test_message"]
        label, _ = ground.classify(STATEMENT, removed, set(), {"bins must be a positive integer"}, nodes)
        self.assertEqual(label, "根拠：文")

    def test_literal_in_statement_is_grounded(self):
        nodes = ground.test_scopes(TESTS)["test_message"]
        label, _ = ground.classify("bins must be a positive integer", set(), set(), set(), nodes)
        self.assertEqual(label, "根拠：文")

    def test_unregex(self):
        self.assertEqual(ground.unregex(r"^Value \(x\) must be > 0$"), "Value (x) must be > 0")

    def test_node_id_strips_display_prefix(self):
        self.assertEqual(ground.node_id("| MEM 1.2/15.5 GB | DISK 84.6/1006.9 GB tests/m/test_a.py::test_b"),
                         "tests/m/test_a.py::test_b")
        self.assertEqual(ground.node_id("pkg/tests/test_x.py::TestK::test_c[a b-1]"), "pkg/tests/test_x.py::TestK::test_c[a b-1]")
        self.assertEqual(ground.node_id("not a node"), "not a node")

    def test_inherited_tests_use_subclass_name_and_setup_chain(self):
        src = textwrap.dedent("""\
            class TestBase:
                def setup_class(self):
                    self.d = make("base")

                def test_shape(self):
                    assert self.d.shape == (3,)

            class TestSub(TestBase):
                def setup_method(self):
                    self.extra = "sub setup message text"

                def test_own(self):
                    assert True

            class TestOverride(TestBase):
                def test_shape(self):
                    assert True
            """)
        scopes = ground.test_scopes(src)
        self.assertEqual(sorted(scopes), ["TestBase::test_shape", "TestOverride::test_shape", "TestSub::test_own",
                                          "TestSub::test_shape"])
        names = [getattr(n, "name", "") for n in scopes["TestSub::test_shape"]]
        self.assertEqual(names, ["test_shape", "setup_method", "setup_class"])   # 子の準備が先、基底の準備も入る
        self.assertEqual([getattr(n, "name", "") for n in scopes["TestOverride::test_shape"]], ["test_shape", "setup_class"])


def write(base, rel, text):
    p = Path(base) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write(self.root, "pkg/__init__.py", "")
        write(self.root, "pkg/base.py", """\
            class Base:
                def run(self):
                    return self.step()          # 子が定義する（mixin・抽象の形）

            class Child(Base):
                def step(self):
                    return 1
            """)
        write(self.root, "pkg/stats.py", """\
            from .base import Base
            import numpy as np

            class ECDF:
                def __call__(self, x):
                    return self._eval_univariate(x)

            class KDE(Base):
                def step(self):
                    self.cache = 1
                    return self.cache

            class External(np.ndarray):
                def f(self):
                    return self.not_known
            """)
        write(self.root, "pkg/tests/test_stats.py", "from pkg.stats import ECDF\n")
        write(self.root, "tests/test_other.py", "import pkg.base\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_module_name_and_test_files(self):
        self.assertEqual(probe.module_name("pkg/a/__init__.py"), "pkg.a")
        self.assertEqual(probe.module_name("pkg/a/b.py"), "pkg.a.b")
        self.assertIsNone(probe.module_name("pkg/a/__main__.py"))
        self.assertTrue(probe.is_test_file("pkg/tests/x.py"))
        self.assertTrue(probe.is_test_file("pkg/test_x.py"))
        self.assertFalse(probe.is_test_file("pkg/_testing.py"))
        self.assertEqual(probe.source_files(self.root, ["pkg"]), ["pkg/__init__.py", "pkg/base.py", "pkg/stats.py"])

    def test_attrs_flags_missing_method_only(self):
        res = probe.check_attrs(self.root, probe.source_files(self.root, ["pkg"]))
        flagged = {(f["class"], f["attr"]) for f in res["flags"]}
        self.assertEqual(flagged, {("ECDF", "_eval_univariate")})   # Base.step は子が持つ。External は辿れないので見ない
        self.assertGreaterEqual(res["classes_skipped"], 1)

    def test_select_tests(self):
        own, picked, want = probe.select_tests(self.root, ["pkg/stats.py"], [], set())
        self.assertEqual(picked, ["pkg/tests/test_stats.py"])
        own, picked, _ = probe.select_tests(self.root, [], ["pkg/base.py", "tests/test_new.py"], set())
        self.assertEqual(own, ["tests/test_new.py"])
        self.assertEqual(picked, ["tests/test_other.py"])
        own, picked, _ = probe.select_tests(self.root, ["pkg/stats.py"], [], {"pkg/tests/test_stats.py"})
        self.assertEqual(picked, [])

    def test_junit_messages_match_summary_nodes(self):
        xml = Path(self.root) / "j.xml"
        xml.write_text(textwrap.dedent("""\
            <testsuites><testsuite>
            <testcase classname="pkg.tests.test_stats" name="test_a[1]"><failure message="AttributeError: no _eval_univariate">tb</failure></testcase>
            <testcase classname="pkg.tests.test_stats.TestK" name="test_b"><error message="NameError: name 'x' is not defined">tb</error></testcase>
            <testcase classname="pkg.tests.test_stats" name="test_ok"/>
            </testsuite></testsuites>
            """), encoding="utf-8")
        msgs = probe.junit_messages(str(xml))
        self.assertEqual(msgs[probe.node_key("pkg/tests/test_stats.py::test_a[1]")], "AttributeError: no _eval_univariate")
        self.assertEqual(msgs[probe.node_key("pkg/tests/test_stats.py::TestK::test_b")], "NameError: name 'x' is not defined")
        self.assertEqual(len(msgs), 2)

    def test_refs(self):
        res = probe.check_refs(self.root, probe.source_files(self.root, ["pkg"]), ["_eval_univariate", "gone"],
                               ["return  SELF.step()", "absent text"])
        self.assertEqual(res["names"], ["_eval_univariate"])
        self.assertEqual(res["literals"], ["return  SELF.step()"])


LOADER_OLD = """\
def install(names, path, cmd, verbose):
    pass

install(
    {'pandas'},
    '/testbed/build/cp311',
    ['/opt/env/bin/ninja'],
    True,
)
"""

LOADER_NEW = """\
install('pkg', {'pkg'}, '/w/build/cp312', ['/usr/bin/ninja', '-C', '/w/build/cp312'], False)
"""


class BuildGuardTest(unittest.TestCase):
    def test_parse_loader(self):
        self.assertEqual(probe.parse_loader(LOADER_OLD), {"build": "/testbed/build/cp311", "cmd": ["/opt/env/bin/ninja"]})
        self.assertEqual(probe.parse_loader(LOADER_NEW),
                         {"build": "/w/build/cp312", "cmd": ["/usr/bin/ninja", "-C", "/w/build/cp312"]})
        self.assertIsNone(probe.parse_loader("x = 1\n"))
        self.assertIsNone(probe.parse_loader("def (:\n"))

    def test_editable_builds_one_per_build_dir(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            write(a, "_pandas_editable_loader.py", LOADER_OLD)
            write(b, "_pandas_editable_loader.py", LOADER_OLD)      # 同じ置き場を 2 つの site から見ても 1 つ
            write(b, "_other_editable_loader.py", "nothing here\n")
            write(b, "pandas-editable.pth", "import _pandas_editable_loader\n")
            got = probe.editable_builds([a, b])
        self.assertEqual([(x["build"], x["loader"]) for x in got], [("/testbed/build/cp311", "_pandas_editable_loader.py")])

    def test_compile_steps(self):
        out = textwrap.dedent("""\
            [1/1] Generating write_version_file with a custom command
            """)
        self.assertEqual(probe.compile_steps(out), [])
        out = textwrap.dedent("""\
            [1/154] Generating pandas/_libs/algos_take_helper_pxi with a custom command
            [2/154] Compiling Cython source /testbed/pandas/_libs/algos.pyx
            [3/154] Compiling C object pandas/_libs/algos.cpython-311-x86_64-linux-gnu.so.p/meson-generated_algos.c.o
            [154/154] Linking target pandas/_libs/interval.cpython-311-x86_64-linux-gnu.so
            ninja: no work to do.
            """)
        self.assertEqual(len(probe.compile_steps(out)), 3)
        self.assertEqual(len(probe.compile_steps("[1/1] Regenerating build files.\n")), 1)

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc (runs in the task box and on Linux)")
    def test_run_session_kills_grandchildren(self):
        code, out, killed = probe.run_session(
            ["bash", "-c", "sleep 300 >/dev/null 2>&1 & sleep 300 >/dev/null 2>&1 & echo started"], 30)
        self.assertEqual(code, 0)
        self.assertIn(b"started", out)
        self.assertEqual(killed, 2)

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc (runs in the task box and on Linux)")
    def test_run_session_timeout(self):
        code, _, killed = probe.run_session(["bash", "-c", "sleep 300"], 1)
        self.assertEqual(code, 124)
        self.assertEqual(killed, 0)       # 時間切れで止めた後、残りは無い

    def test_deep_stack_runs_and_reraises(self):
        def nest(n):
            return 0 if n == 0 else 1 + nest(n - 1)
        self.assertEqual(probe.deep_stack(lambda: nest(20000)), 20000)
        with self.assertRaises(KeyError):
            probe.deep_stack(lambda: {}["missing"])


class ReplayTest(unittest.TestCase):
    def test_commit_sha_only_from_stdout(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(replay.commit_sha(sha + "\n"), sha)
        for bad in ("", "The file will have its original line endings in your working directory\n",
                    sha + "\nwarning: CRLF will be replaced by LF in x.csv.\n", sha[:-1] + "\n"):
            with self.assertRaises(RuntimeError):
                replay.commit_sha(bad)


class R0SelectTest(unittest.TestCase):
    """R0 のテストの選び方と、落ちの原因の型（実験と計画 §5-6 の 2・4）。"""

    IMPORTS = {"pkg/tests/test_a.py": ["pkg", "pkg.stats", "pkg.stats.ECDF"],
               "pkg/tests/test_b.py": ["pkg"],                      # 最上位の名だけ：選ばない
               "tests/test_c.py": ["pkg.base"],
               "tests/test_d.py": ["pkg.base", "pkg.stats"]}

    def test_groups_order_and_top_level_dropped(self):
        sel = probe.select_tests_r0(self.IMPORTS, ["pkg/stats.py"],
                                    ["pkg/__init__.py", "pkg/base.py", "tests/test_new.py", "data/x.csv"])
        self.assertEqual(sel["own"], ["tests/test_new.py"])
        self.assertEqual(sel["ledger"], ["pkg/tests/test_a.py", "tests/test_d.py"])
        self.assertEqual(sel["changed"], ["tests/test_c.py"])          # test_d は群 2 に入ったので重ねない
        self.assertEqual(sel["top_level_dropped"], ["pkg"])
        self.assertEqual(sel["want_changed"], ["pkg.base"])

    def test_test_imports_reads_ast_only(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "pkg/__init__.py", "")
            write(root, "pkg/tests/test_x.py", "import pkg.base\nfrom pkg.stats import ECDF\n")
            write(root, "pkg/tests/test_broken.py", "def (:\n")
            res = probe.test_imports(root)
        self.assertEqual(res["files"]["pkg/tests/test_x.py"], ["pkg.base", "pkg.stats", "pkg.stats.ECDF"])
        self.assertEqual(res["unparsed"], ["pkg/tests/test_broken.py"])

    def test_failure_class(self):
        cases = [
            ('failed on setup with "ModuleNotFoundError: No module named \'pyspark\'"', ["mlflow"], "E2"),
            ("ModuleNotFoundError: No module named 'fish_licence.halibut'", ["sphinx"], "E2"),
            ("ModuleNotFoundError: No module named 'pandas.io.iceberg'", ["pandas"], "target"),
            ('failed on setup with "AttributeError: module \'moto\' has no attribute \'mock_s3\'"', ["mlflow"], "E3"),
            ("AttributeError: module 'pandas' has no attribute 'read_iceberg'", ["pandas"], "target"),
            ("ImportError: cannot import name 'get_handle' from 'pandas.io.common'", ["pandas"], "target"),
            ("ImportError: cannot import name 'mock_s3' from 'moto'", ["mlflow"], "E3"),
            ('failed on teardown with "FileNotFoundError: [Errno 2] No such file or directory: \'/root/.mlflow/envs\'"',
             ["mlflow"], "E4"),
            ("FileNotFoundError: [Errno 2] No such file or directory: '/testbed/pkg/data.csv'", ["pkg"], "target"),
            ("AssertionError: lualatex exited with return code 1", ["sphinx"], "E5"),
            ("Failed: Timeout (>10.0s) from pytest-timeout.", ["sympy"], "E6"),
            ("astropy.utils.exceptions.AstropyWarning: leap-second auto-update failed due to the following exception: "
             "IERSStaleWarning('leap-second file is expired.')", ["astropy"], "E1"),
            ("URLError: <urlopen error [Errno -3] Temporary failure in name resolution>", ["astropy"], "E1"),
            ("", ["pandas"], "U"),
            ("AssertionError: Iterable are different", ["pandas"], "target"),
            ("NameError: name 'get_series_repr_params' is not defined", ["pandas"], "target"),
        ]
        for msg, pkgs, want in cases:
            self.assertEqual(probe.failure_class(msg, pkgs), want, msg)


class R0ReplayTest(unittest.TestCase):
    """R0 の再集計の部品（実験と計画 §5-6 の数え方）。"""

    def test_plan_budget(self):
        records = {"a.py": {"rc": 0, "secs": 100}, "b.py": {"rc": 1, "secs": 250},
                   "c.py": {"rc": 0, "secs": 1}, "t.py": {"rc": 124, "secs": 60, "timeout": True}}
        plan, used = replay.plan_budget(["a.py", "gap.py", "t.py", "b.py", "c.py"], records)
        self.assertEqual(plan, {"a.py": "done", "gap.py": "no_record", "t.py": "timeout", "b.py": "cut",
                                "c.py": "not_started"})
        self.assertEqual(used, 300.0)
        plan, used = replay.plan_budget(["a.py", "c.py"], records)
        self.assertEqual(plan, {"a.py": "done", "c.py": "done"})
        self.assertEqual(used, 101.0)

    def test_existing_test_edits(self):
        patch = textwrap.dedent("""\
            diff --git a/pkg/tests/test_new.py b/pkg/tests/test_new.py
            new file mode 100644
            --- /dev/null
            +++ b/pkg/tests/test_new.py
            @@ -0,0 +1 @@
            +def test_x(): pass
            diff --git a/pkg/tests/test_old.py b/pkg/tests/test_old.py
            --- a/pkg/tests/test_old.py
            +++ b/pkg/tests/test_old.py
            @@ -1 +1 @@
            -assert f() == 1
            +assert True
            diff --git a/pkg/tests/conftest.py b/pkg/tests/conftest.py
            deleted file mode 100644
            --- a/pkg/tests/conftest.py
            +++ /dev/null
            @@ -1 +0,0 @@
            -import pytest
            diff --git a/pkg/stats.py b/pkg/stats.py
            --- a/pkg/stats.py
            +++ b/pkg/stats.py
            @@ -1 +1 @@
            -x = 1
            +x = 2
            diff --git a/pkg/tests/data/table.csv b/pkg/tests/data/table.csv
            --- a/pkg/tests/data/table.csv
            +++ b/pkg/tests/data/table.csv
            @@ -1 +1 @@
            -a
            +b
            """)
        self.assertEqual(replay.existing_test_edits(patch),
                         [{"path": "pkg/tests/test_old.py", "kind": "modified"},
                          {"path": "pkg/tests/conftest.py", "kind": "deleted"}])
        self.assertEqual(replay.existing_test_edits(""), [])

    def test_state_tests_merges_gold_retest(self):
        d = {"gold_retest": {"tests": {"files": {"t2.py": {"rc": 1, "secs": 2}, "t1.py": {"rc": 1, "secs": 9}},
                                       "failed": [{"node": "t2.py::b", "kind": "FAILED", "msg": "x"},
                                                  {"node": "t1.py::z", "kind": "FAILED", "msg": "dup"}]}}}
        c = {"name": "gold", "tests": {"files": {"t1.py": {"rc": 1, "secs": 1}},
                                       "failed": [{"node": "t1.py::a", "kind": "FAILED", "msg": "y"}]}}
        files, failed = replay.state_tests(d, c)
        self.assertEqual(files, {"t1.py": {"rc": 1, "secs": 1}, "t2.py": {"rc": 1, "secs": 2}})
        self.assertEqual([x["node"] for x in failed["t1.py"]], ["t1.py::a"])   # 重なったファイルは先のものだけ
        self.assertEqual([x["node"] for x in failed["t2.py"]], ["t2.py::b"])

    def test_r0_state_pushes_target_new_static_and_edits_only(self):
        start = {"undefined": [{"file": "pkg/a.py", "kind": "UndefinedName", "name": "old_noise"}],
                 "imports": {"failed": []}, "attrs": {"flags": []}}
        cand = {"name": "A", "resolved": False, "changed": ["pkg/stats.py"],
                "undefined": [{"file": "pkg/a.py", "kind": "UndefinedName", "name": "old_noise"},
                              {"file": "pkg/stats.py", "kind": "UndefinedName", "name": "gone_helper"}],
                "imports": {"failed": []}, "attrs": {"flags": []}}
        imports_t0 = {"pkg/tests/test_stats.py": ["pkg.stats"], "pkg/tests/test_env.py": ["pkg.stats"]}
        records = {"pkg/tests/test_stats.py": {"rc": 1, "secs": 5}, "pkg/tests/test_env.py": {"rc": 1, "secs": 5}}
        failed = {"pkg/tests/test_stats.py": [{"node": "pkg/tests/test_stats.py::t", "kind": "FAILED",
                                               "msg": "NameError: name 'gone_helper' is not defined"}],
                  "pkg/tests/test_env.py": [{"node": "pkg/tests/test_env.py::e", "kind": "ERROR",
                                             "msg": "ModuleNotFoundError: No module named 'pyspark'"}]}
        keys = replay.keysets(start)
        s = replay.r0_state(cand, keys, imports_t0, ["pkg/stats.py"], ["pkg"], {"gone_helper"}, {"pkg/stats.py"},
                            records, failed, [])
        self.assertTrue(s["pushed"])
        self.assertTrue(s["on_target"])
        self.assertEqual(s["static_new"]["undefined"], 1)           # t=0 からあった old_noise は押し返さない
        self.assertEqual(s["tests_target"], 1)
        self.assertEqual(s["test_classes"], {"target": 1, "E2": 1})
        # 環境の型の落ちと t=0 からの静的な検出だけなら、押し返さない
        quiet = dict(cand, undefined=start["undefined"])
        env_only = {"pkg/tests/test_env.py": failed["pkg/tests/test_env.py"]}
        s = replay.r0_state(quiet, keys, imports_t0, ["pkg/stats.py"], ["pkg"], {"gone_helper"}, {"pkg/stats.py"},
                            records, env_only, [])
        self.assertFalse(s["pushed"])
        # テストの改変は、それだけで押し返す（的には数えない）
        s = replay.r0_state(quiet, keys, imports_t0, ["pkg/stats.py"], ["pkg"], set(), set(), records, env_only,
                            [{"path": "pkg/tests/test_stats.py", "kind": "modified"}])
        self.assertTrue(s["pushed"])
        self.assertFalse(s["on_target"])

    def test_r0_lines(self):
        def st(name, resolved, pushed, on):
            return {"state": name, "resolved": resolved, "pushed": pushed, "on_target": on,
                    "pushed_with_unknown": pushed, "on_target_with_unknown": on, "record_gap_files": 0}
        results = [{"id": "o__r.c.t%d.h.lv1" % i, "states": [st("gold", None, i < 3, False),
                                                           st("A", False, i < 5, i < 5)]} for i in range(10)]
        lines = replay.r0_lines(results)
        self.assertEqual(len(lines["gold_pushed"]), 3)
        self.assertFalse(lines["line1_gold_le_2"])
        self.assertTrue(lines["line2_caught_ge_5"])
        self.assertFalse(lines["pass"])


if __name__ == "__main__":
    unittest.main()
