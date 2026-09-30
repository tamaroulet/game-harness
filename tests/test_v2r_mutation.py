"""走行のタスクごとの終わりの実装に、変異の死滅率を一括で当てる（harness/ab/v2r_mutation.py）。

    python -m unittest tests.test_v2r_mutation

**なぜ要るか**: v2r-dry-03 の天井（A0 の欠陥 0 件）を、「系列が易しい」か「オラクルが弱い」かで見分ける診断を、
タスクの終わりのコミットと、走行の測定と同じ非公開シードで、取り違えずに回せること。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import common, v2r_mutation  # noqa: E402

PROJ = {"repo_dir": "repo", "impl_dir": "Game/Assets/Core", "fast_test_project": "tests/Core.Tests/Core.Tests.csproj"}


def report(statuses):
    return {"files": {"A.cs": {"source": "コード", "mutants": [{"mutatorName": "E", "status": s} for s in statuses]}}}


class RunAll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out" / "r1" / "A0"
        self.out.mkdir(parents=True)
        rows = [{"task": "T1", "resume": {"end_commit": "c1" * 20}}, {"task": "T2", "resume": {"end_commit": "c2" * 20}}]
        (self.out / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.git_calls, self.stryker_calls = [], []
        patches = [mock.patch.object(common, "load_manifest", return_value={"measure_hidden_seeds": 3, "base_commit": "b0" * 20}),
                   mock.patch.object(v2r_mutation.project, "load", return_value=PROJ)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def git(self, args, cwd, label, check=True):
        self.git_calls.append(args)
        if args[0] == "ls-tree":
            return "Game/Assets/Core/Board.cs\nGame/Assets/Core/GameState.cs\nGame/Assets/Core/Core.asmdef\n"
        if args[0] == "diff":   # T1 は GameState.cs だけを変え、T2 は .cs を変えていない
            return "Game/Assets/Core/GameState.cs\n" if args[3] == "b0" * 20 else ""
        return ""

    def stryker(self, wt, test_project, files, dest, seeds, project=None):
        self.stryker_calls.append({"files": files, "seeds": seeds, "project": project, "test": test_project})
        (Path(dest) / "reports").mkdir(parents=True, exist_ok=True)
        path = Path(dest) / "reports" / "mutation-report.json"
        path.write_text(json.dumps(report(["Killed"] * 9 + ["Survived"])), encoding="utf-8")
        return path

    def run_all(self, **kw):
        return v2r_mutation.run_all("r1", "A0", "m.json", wt_root=self.tmp / "wt", out_root=self.tmp / "out",
                                    stryker=self.stryker, git=self.git, **kw)

    def test_each_task_is_mutated_at_its_end_commit_with_the_measurement_seeds(self):
        done = self.run_all()
        self.assertEqual(sorted(done), ["T1", "T2"])
        adds = [a for a in self.git_calls if a[:2] == ["worktree", "add"]]
        self.assertEqual([a[-1] for a in adds], ["c1" * 20, "c2" * 20], "タスクの終わりのコミットから")
        self.assertEqual(self.stryker_calls[0]["files"], ["Game/Assets/Core/Board.cs", "Game/Assets/Core/GameState.cs"],
                         "変異を入れるのは impl_dir の .cs だけ")
        self.assertEqual(self.stryker_calls[0]["seeds"], common.hidden_seeds("r1", "T1", 3), "走行の測定と同じ非公開シード")
        self.assertEqual(self.stryker_calls[1]["seeds"], common.hidden_seeds("r1", "T2", 3))
        self.assertEqual(self.stryker_calls[0]["project"], "Core.csproj")
        removes = [a for a in self.git_calls if a[:2] == ["worktree", "remove"]]
        self.assertGreaterEqual(len(removes), 2, "一時の worktree を片付ける")

    def test_tasks_with_a_report_are_skipped_unless_forced(self):
        self.run_all(tasks=["T1"])
        self.run_all()
        self.assertEqual(len(self.stryker_calls), 2, "T1 は飛ばし、T2 だけ測る")
        self.run_all(tasks=["T1"], force=True)
        self.assertEqual(len(self.stryker_calls), 3, "--force なら測り直す")

    def test_the_task_scope_mutates_only_the_files_the_task_changed(self):
        self.run_all(scope="task", tasks=["T2"])
        self.run_all(scope="task")
        diffs = [a for a in self.git_calls if a[0] == "diff"]
        self.assertEqual([a[3:5] for a in diffs][:2], [["c1" * 20, "c2" * 20], ["b0" * 20, "c1" * 20]],
                         "前のタスクの終わりから（絞っても T2 の始まりは T1 の終わり）、T1 は base_commit から")
        self.assertEqual([c["files"] for c in self.stryker_calls], [["Game/Assets/Core/GameState.cs"]],
                         "T2 は .cs を変えていないので測らない")
        rows, missing = v2r_mutation.summarize(self.out, scope="task")
        self.assertEqual([r["task"] for r in rows], ["T1"])
        self.assertEqual(missing, [], "測らなかったタスクは報告の無いタスクに数えない")
        self.assertFalse(v2r_mutation.mutation_dir(self.out).exists(), "範囲 all の出力と混ぜない")

    def test_the_environment_check_skips_the_implementer_clis_only(self):
        seen = {}

        def measure(clis, tools, csproj):
            seen["clis"] = clis
            return {"os": "o", "python": "p", "python_packages": {"pyyaml": "6.0.3"}, "cli": {"dotnet": "8.0.413"},
                    "tools": {"dotnet-stryker": "4.16.0"}, "test_packages": {"NUnit": "3.14.0"}}
        pinned = {"os": "o", "python": "p", "python_packages": {"pyyaml": "6.0.3"},
                  "cli": {"agy": "1.2.11", "claude": "2.1.258", "dotnet": "8.0.413"},
                  "tools": {"dotnet-stryker": "4.16.0"}, "test_packages": {"NUnit": "3.14.0"}}
        with mock.patch.object(v2r_mutation.envcheck, "load_pinned", side_effect=lambda: json.loads(json.dumps(pinned))), \
                mock.patch.object(v2r_mutation.envcheck, "game_csproj", return_value="x.csproj"):
            rec = v2r_mutation.env_require(self.tmp / "env.json", measure=measure)
            self.assertEqual(seen["clis"], ("dotnet",))
            self.assertEqual(rec["problems"], [], "agy の版は照合しない")
            with self.assertRaises(v2r_mutation.envcheck.EnvError):
                v2r_mutation.env_require(self.tmp / "env.json",
                                         measure=lambda **k: {**measure(**k), "tools": {"dotnet-stryker": "5.0.0"}})

    def test_an_unknown_task_is_rejected(self):
        with self.assertRaises(common.ABError):
            self.run_all(tasks=["T9"])

    def test_rows_without_an_end_commit_are_rejected(self):
        (self.out / "metrics.jsonl").write_text(json.dumps({"task": "T1"}) + "\n", encoding="utf-8")
        with self.assertRaises(common.ABError):
            self.run_all()

    def test_summary_is_written_without_code(self):
        self.run_all()
        (v2r_mutation.mutation_dir(self.out) / "T3").mkdir()
        rows, missing = v2r_mutation.summarize(self.out)
        self.assertEqual([(r["task"], r["score"], r["oracle_ok"]) for r in rows], [("T1", 0.9, True), ("T2", 0.9, True)])
        self.assertEqual(missing, ["T3"])
        text = (v2r_mutation.mutation_dir(self.out) / "summary.md").read_text(encoding="utf-8")
        self.assertIn("T3", text)
        self.assertNotIn("コード", (v2r_mutation.mutation_dir(self.out) / "summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
