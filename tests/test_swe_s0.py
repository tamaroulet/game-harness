"""S0（SWE-EVO 版の事前再生ゲート。harness/swe_s0.py）のうち、コンテナが無くても確かめられる所。

    python -m unittest tests.test_swe_s0 -v
"""
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import fb_gate_probe as probe  # noqa: E402
import swe_s0  # noqa: E402

EVAL = textwrap.dedent("""\
    #!/bin/bash
    set -uxo pipefail
    source /opt/miniconda3/bin/activate
    conda activate testbed
    cd /testbed
    git status
    git -c core.fileMode=false diff 853a6b0db11c17c6b36a54057fbd9b240b747919
    source /opt/miniconda3/bin/activate
    conda activate testbed
    python -m pip install --no-deps -e .
    pip install 'distributed==2023.6.0'

    : '>>>>> Start Test Output'
    pytest -rA dask/tests/test_core.py
    : '>>>>> End Test Output'
    """)

PATCH = textwrap.dedent("""\
    diff --git a/dask/core.py b/dask/core.py
    --- a/dask/core.py
    +++ b/dask/core.py
    @@ -1 +1 @@
    -a
    +b
    diff --git a/dask/tests/test_core.py b/dask/tests/test_core.py
    --- a/dask/tests/test_core.py
    +++ b/dask/tests/test_core.py
    @@ -1 +1 @@
    -c
    +d
    diff --git a/docs/source/changelog.rst b/docs/source/changelog.rst
    --- a/docs/source/changelog.rst
    +++ b/docs/source/changelog.rst
    @@ -1 +1 @@
    -e
    +f
    """)


class S0Test(unittest.TestCase):
    def test_setup_lines_between_the_diff_and_the_test_output(self):
        self.assertEqual(swe_s0.setup_lines(EVAL), [
            "source /opt/miniconda3/bin/activate", "conda activate testbed",
            "python -m pip install --no-deps -e .", "pip install 'distributed==2023.6.0'"])
        with self.assertRaises(ValueError):
            swe_s0.setup_lines("#!/bin/bash\necho hi\n")

    def test_split_patch_drops_test_files_only(self):
        code, dropped, files = swe_s0.split_patch(PATCH)
        self.assertEqual(dropped, ["dask/tests/test_core.py"])
        self.assertEqual(files, ["dask/core.py", "docs/source/changelog.rst"])
        self.assertIn("diff --git a/dask/core.py b/dask/core.py", code)
        self.assertNotIn("test_core.py", code)
        self.assertTrue(all(not probe.is_test_file(f) for f in files))

    def test_exception_type(self):
        self.assertEqual(swe_s0.exception_type("assert False\n + where False = f()"), "AssertionError")
        self.assertEqual(swe_s0.exception_type("mlflow.exceptions.MlflowException: no key"), "MlflowException")
        self.assertEqual(swe_s0.exception_type("ModuleNotFoundError: No module named 'x'"), "ModuleNotFoundError")
        self.assertEqual(swe_s0.exception_type("Failed: DID NOT RAISE <class 'ValueError'>"), "Failed")
        self.assertEqual(swe_s0.exception_type(""), "")

    def test_decide_excuses_only_the_same_item_and_type_at_t0(self):
        gold = [{"node": "t.py::a", "msg": "KeyError: 'x'"}, {"node": "t.py::b", "msg": "assert 1 == 2"},
                {"node": "t.py::c", "msg": "NameError: name 'gone' is not defined"},
                {"node": "u.py", "msg": "ModuleNotFoundError: No module named 'dspy'"}]
        t0 = [{"node": "t.py::a", "msg": "KeyError: 'x'"}, {"node": "t.py::b", "msg": "TypeError: bad"},
              {"node": "u.py", "msg": "ModuleNotFoundError: No module named 'dspy'"}]
        pushed, excused = swe_s0.decide(gold, t0)
        self.assertEqual([x["node"] for x in excused], ["t.py::a", "u.py"])
        self.assertEqual([x["node"] for x in pushed], ["t.py::b", "t.py::c"])   # 型が違う・t=0 で落ちていない


if __name__ == "__main__":
    unittest.main()
