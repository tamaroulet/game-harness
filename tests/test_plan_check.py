"""計画の受け入れの検査（harness/plan_check.py）の検査。一時ディレクトリに ledger.yaml と P-NN.md を書く。計画役（CLI）は呼ばない。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import plan_check  # noqa: E402


def task(n, deps=(), **over):
    t = {"id": f"P-{n:02d}", "title": f"題 {n}", "depends_on": list(deps), "acceptance": ["a"], "oracle": [],
         "files": [f"pkg/m{n}.py", f"tests/test_pkg_m{n}.py"],
         "verification": {"command": f"python -m unittest tests.test_pkg_m{n}", "expected_exit_code": 0}}
    t.update(over)
    return t


def verify(n):
    return {"command": f"python -m unittest tests.test_pkg_m{n}", "expected_exit_code": 0}


def good():
    return [task(1), task(2, ["P-01"]), task(3, ["P-02"])]


def run(tasks, whats=None, prior=None):
    """(終了コード, plan_check の値, planner.json 全体) を返す。"""
    with tempfile.TemporaryDirectory() as d:
        inbox = Path(d)
        for tid in whats if whats is not None else [t["id"] for t in tasks]:
            (inbox / f"{tid}.md").write_text(f"タスク: {tid}\n", encoding="utf-8")
        (inbox / "ledger.yaml").write_text(yaml.safe_dump(tasks, allow_unicode=True), encoding="utf-8")
        if prior is not None:
            (inbox / "planner.json").write_text(json.dumps(prior), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = plan_check.main([str(inbox)])
        data = json.loads((inbox / "planner.json").read_text(encoding="utf-8"))
        return code, data["plan_check"], data


class PlanCheckTests(unittest.TestCase):
    def broken(self, rule, tasks, task_id=None, whats=None):
        code, found, _ = run(tasks, whats)
        self.assertEqual(code, 1)
        self.assertEqual(found, [{"rule": rule, "task": task_id}] if not isinstance(task_id, list)
                         else [{"rule": rule, "task": i} for i in task_id])

    def test_good(self):
        code, found, _ = run(good())
        self.assertEqual((code, found), (0, []))

    def test_count(self):
        self.broken("タスクの数", [task(1)])
        self.broken("タスクの数", [task(1)] + [task(n, ["P-01"]) for n in range(2, 10)])

    def test_cycle(self):
        tasks = [task(1), task(2, ["P-01", "P-03"]), task(3, ["P-02"])]
        self.broken("依存の循環", tasks, ["P-02", "P-03"])

    def test_unknown_dependency(self):
        self.broken("depends_on が台帳に無い ID", [task(1), task(2, ["P-01"]), task(3, ["P-02", "P-99"])], "P-03")

    def test_unreachable(self):
        tasks = [task(1), task(2, ["P-01"]), task(3, ["P-02"])]
        tasks[0]["depends_on"] = ["P-03"]
        code, found, _ = run(tasks)
        self.assertEqual(code, 1)
        self.assertIn({"rule": "根から届かない", "task": "P-01"}, found)

    def test_depth(self):
        chain = [task(1)] + [task(n, [f"P-{n - 1:02d}"]) for n in range(2, 8)]
        self.broken("依存の深さ", chain, None)
        self.assertEqual(run(chain[:6])[0], 0)

    def test_what_mismatch(self):
        self.broken("What とタスクが 1 対 1 でない", good(), "P-03", whats=["P-01", "P-02"])

    def test_files_count(self):
        self.broken("files の件数", [task(1), task(2, ["P-01"]), task(3, ["P-02"], files=[], verification=verify(2))], "P-03")
        six = ["pkg/c.py", "pkg/c2.py", "pkg/c3.py", "pkg/c4.py", "pkg/c5.py", "tests/test_pkg_m3.py"]
        self.broken("files の件数", [task(1), task(2, ["P-01"]), task(3, ["P-02"], files=six)], "P-03")

    def test_outside_package(self):
        files = ["pkg/m3.py", "tests/test_pkg_m3.py", "tests/other.py"]
        self.broken("files の場所", [task(1), task(2, ["P-01"]), task(3, ["P-02"], files=files)], "P-03")

    def test_package_not_uniform(self):
        files = ["pkg2/m3.py", "tests/test_pkg2_m3.py"]
        ver = {"command": "python -m unittest tests.test_pkg2_m3", "expected_exit_code": 0}
        self.broken("package がそろわない", [task(1), task(2, ["P-01"]), task(3, ["P-02"], files=files, verification=ver)], None)

    def test_acceptance_count(self):
        self.broken("acceptance の項目数", [task(1), task(2, ["P-01"]), task(3, ["P-02"], acceptance=[])], "P-03")
        five = ["a", "b", "c", "d", "e"]
        self.broken("acceptance の項目数", [task(1), task(2, ["P-01"]), task(3, ["P-02"], acceptance=five)], "P-03")

    def test_shared_file_without_dependency(self):
        files = ["pkg/m2.py", "pkg/m3.py", "tests/test_pkg_m3.py"]
        tasks = [task(1), task(2, ["P-01"]), task(3, ["P-01"], files=files)]
        self.broken("同じファイルを持つタスクが依存し合わない", tasks, "P-03")
        tasks[2]["depends_on"] = ["P-02"]
        self.assertEqual(run(tasks)[0], 0)

    def test_oracle_module_origin(self):
        verification = {"command": "python -m unittest tests.test_pkg_zzz", "expected_exit_code": 0}
        self.broken("検証の試験のモジュールの出どころ",
                    [task(1), task(2, ["P-01"]), task(3, ["P-02"], verification=verification)], "P-03")
        own = {"command": "python -m unittest tests.test_pkg_m1", "expected_exit_code": 0}
        self.assertEqual(run([task(1), task(2, ["P-01"]), task(3, ["P-02"], verification=own)])[0], 0)

    def test_keeps_existing_keys(self):
        prior = {"files": {"P-01.md": "abc"}, "problems": ["x"]}
        _, _, data = run(good(), prior=prior)
        self.assertEqual((data["files"], data["problems"], data["plan_check"]), (prior["files"], prior["problems"], []))

    def test_unreadable_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(plan_check.main([d]), 1)
                self.assertEqual(plan_check.main([str(Path(d) / "none")]), 1)
            data = json.loads((Path(d) / "planner.json").read_text(encoding="utf-8"))
            self.assertEqual(data["plan_check"], [{"rule": "ledger.yaml を読めない", "task": None}])


if __name__ == "__main__":
    unittest.main()
