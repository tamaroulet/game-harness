"""計画役の道具（harness/planner.py）の検査。計画役は偽物で、一時ディレクトリの受信箱に偽の出力を書かせる。
本物の CLI も非公開オラクルの置き場も触らない。"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import planner  # noqa: E402

AGENT = {"cli": "claude", "instruction": "docs/prompts/planner.md", "ttl_seconds": 5}
SPEC = "# 仕様\nREQ-01: 足し算をする\n"


def what(tid):
    return f"# 題 {tid}\nマイルストーン: bench\nタスク: {tid}\n要件: REQ-01\n\n## What\n本文\n"


def task(tid, **drop):
    t = {"id": tid, "title": f"題 {tid}", "depends_on": [], "acceptance": ["足せる"],
         "files": ["calc/__init__.py"], "oracle": [{"path": "tests/test_calc_add.py", "from": "self"}],
         "verification": {"command": "python -m unittest tests.test_calc_add", "expected_exit_code": 0}}
    return {k: v for k, v in t.items() if k not in drop}


def good_output(inbox, whats=("P-01", "P-02"), tasks=None):
    for tid in whats:
        (inbox / f"{tid}.md").write_text(what(tid), encoding="utf-8")
    (inbox / "ledger.yaml").write_text(
        yaml.safe_dump(tasks if tasks is not None else [task(t) for t in whats], allow_unicode=True), encoding="utf-8")


class PlannerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.inbox = self.root / "inbox"
        self.spec = self.root / "spec.md"
        self.spec.write_text(SPEC, encoding="utf-8")
        self.cfg = {"planner": AGENT, "out": str(self.root / "out")}
        self.prompts = []

    def run_plan(self, writer):
        def fake(agent, wt, ttl, label, prompt, log):
            self.prompts.append(prompt)
            self.assertEqual(Path(wt), self.inbox)
            writer(self.inbox)
            return 0, [], ""
        with mock.patch.object(planner, "run_agent", fake):
            return planner.plan(self.cfg, self.spec, "calc", self.inbox)

    def result(self):
        return json.loads((self.inbox / "planner.json").read_text(encoding="utf-8"))

    def test_the_input_has_the_instruction_inbox_package_and_spec(self):
        self.run_plan(good_output)
        prompt = self.prompts[0]
        instruction = (planner.ROOT / "docs" / "prompts" / "planner.md").read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith(instruction))
        self.assertIn(f"受信箱: {self.inbox}", prompt)
        self.assertIn("パッケージ: calc", prompt)
        self.assertTrue(prompt.endswith(SPEC))

    def test_a_correct_output_passes_and_the_hashes_match(self):
        self.assertEqual(self.run_plan(good_output), [])
        res = self.result()
        self.assertEqual(res["problems"], [])
        self.assertEqual(set(res["files"]), {"P-01.md", "P-02.md", "ledger.yaml"})
        for name, digest in res["files"].items():
            self.assertEqual(digest, hashlib.sha256((self.inbox / name).read_bytes()).hexdigest())

    def test_a_task_without_files_fails(self):
        self.run_plan(lambda i: good_output(i, tasks=[task("P-01"), task("P-02", files=0)]))
        self.assertTrue(any("P-02" in p and "files" in p for p in self.result()["problems"]))

    def test_a_task_without_a_what_fails(self):
        def writer(i):
            good_output(i, tasks=[task("P-01"), task("P-02")])
            (i / "P-02.md").unlink()
        problems = self.run_plan(writer)
        self.assertTrue(any("P-02" in p for p in problems))
        self.assertEqual(self.result()["problems"], problems)

    def test_p05_fails(self):
        problems = self.run_plan(lambda i: good_output(i, whats=tuple(f"P-0{n}" for n in range(1, 6))))
        self.assertTrue(any("P-05" in p for p in problems))

    def test_a_missing_ledger_fails(self):
        def writer(i):
            good_output(i)
            (i / "ledger.yaml").unlink()
        problems = self.run_plan(writer)
        self.assertTrue(any("ledger.yaml" in p for p in problems))
        self.assertNotIn("ledger.yaml", self.result()["files"])

    def test_a_gap_in_the_numbers_fails(self):
        problems = self.run_plan(lambda i: good_output(i, whats=("P-01", "P-03")))
        self.assertTrue(any("欠けず" in p for p in problems))

    def test_main_returns_the_exit_code_from_the_problems(self):
        cfg = {"planner": {**AGENT, "model_flag": "--model", "model": "m", "auxiliary_models": ["x-"]},
               "out": self.root / "out"}
        for writer, expected in ((good_output, 0), (lambda i: None, 1)):
            with self.subTest(expected=expected), mock.patch.object(planner.hline_base, "load_config", return_value=cfg), \
                    mock.patch.object(planner, "run_agent", lambda a, wt, t, lb, p, lg: (writer(Path(wt)), (0, [], ""))[1]), \
                    contextlib.redirect_stdout(io.StringIO()):
                argv = ["--spec", str(self.spec), "--package", "calc", "--inbox", str(self.inbox)]
                self.assertEqual(planner.main(argv), expected)
                for p in self.inbox.glob("*"):
                    p.unlink()


if __name__ == "__main__":
    unittest.main()
