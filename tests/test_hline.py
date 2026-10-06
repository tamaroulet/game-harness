"""H ライン（harness/hline.py、段 0）の検査。

    python -m unittest tests.test_hline -v

**なぜ要るか**: H ラインは総監督の道具を剥いだ後の、ハーネスの唯一の改修の経路。受信箱の取り方・試行の打ち切り・
総監督の部屋の拒否の規則が崩れると、無菌室が破れるか、ラインが止まる。外部の CLI は呼ばずに検査する。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_base  # noqa: E402

CFG = hline.load_config()


class Inbox(unittest.TestCase):
    def test_takes_the_first_what_by_name_and_skips_the_files_the_line_writes(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("report.md", "TODO.md", "CLAUDE.md", "002-b.md", "001-a.md", "note.txt"):
                (Path(d) / n).write_text("x", encoding="utf-8")
            self.assertEqual(hline.pick(d).name, "001-a.md")

    def test_an_inbox_with_only_line_files_has_nothing_to_take(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "report.md").write_text("x", encoding="utf-8")
            self.assertIsNone(hline.pick(d))

    def test_slug_and_title(self):
        self.assertEqual(hline.slug("001-Add Normalize_Newlines.md"), "001-add-normalize-newlines")
        self.assertEqual(hline.slug("日本語.md"), "task")
        self.assertEqual(hline.title_of("前書き\n# 改行の正規化\n本文"), "改行の正規化")

    def test_a_running_line_is_not_entered_twice_but_a_stale_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            lock = hline.acquire_lock(d, 100)
            self.assertIsNotNone(lock)
            self.assertIsNone(hline.acquire_lock(d, 100))
            self.assertIsNone(hline.acquire_lock(d, 100, now=lock.stat().st_mtime + 101))   # 生きている持ち主は古くても奪わない
            with mock.patch.object(hline_base, "process_token", return_value=None):   # 持ち主が死んだ
                self.assertIsNotNone(hline.acquire_lock(d, 100))   # 更新時刻が新しくても取り直す


class Gate(unittest.TestCase):
    def test_no_change_fails_without_running_the_tests(self):
        with mock.patch.object(hline.proc, "run") as run:
            ok, msg = hline.gate(CFG, Path("."), [])
        self.assertFalse(ok)
        run.assert_not_called()

    def test_protected_paths_fail_without_running_the_tests(self):
        for p in ("docs/progress.yaml", ".claude/settings.json"):
            with self.subTest(p=p), mock.patch.object(hline.proc, "run") as run:
                ok, msg = hline.gate(CFG, Path("."), ["harness/x.py", p])
            self.assertFalse(ok)
            self.assertIn(p, msg)
            run.assert_not_called()

    def test_the_verdict_is_the_exit_code_of_the_test_command(self):
        for code, want in ((0, True), (1, False)):
            with self.subTest(code=code), mock.patch.object(hline.proc, "run", return_value=(code, "out", "err")) as run:
                ok, _ = hline.gate(CFG, Path("."), ["harness/x.py"])
            self.assertEqual(ok, want)
            self.assertEqual(run.call_args.args[0], CFG["gate_command"])


class Implementer(unittest.TestCase):
    def test_the_model_is_pinned_on_the_command_line(self):
        args = hline.implementer_args(CFG["implementer"], ["claude"])
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5")

    def test_a_run_with_another_model_stops_as_an_environment_fault(self):
        out = json.dumps({"modelUsage": {"claude-opus-5-5": {}}})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(hline.proc, "run", return_value=(0, out, "")):
            with self.assertRaises(hline.Infra):
                hline.implement(CFG, Path(d), "# x", None, Path(d) / "log")

    def test_the_pinned_model_is_recorded(self):
        out = json.dumps({"modelUsage": {"claude-sonnet-5-5": {}, "claude-haiku-4-5": {}}})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(hline.proc, "run", return_value=(0, out, "")) as run:
            code, models, *_ = hline.implement(CFG, Path(d), "# 題", "前回の出力", Path(d) / "log")
        self.assertEqual(models, ["claude-haiku-4-5", "claude-sonnet-5-5"])
        prompt = run.call_args.kwargs["input"]
        self.assertIn("# 題", prompt)
        self.assertIn("前回の出力", prompt)


class ImplementerRoom(unittest.TestCase):
    def test_the_worktree_is_readable_by_the_implementer_and_the_rewrite_is_invisible_to_git(self):
        """実測：リポジトリの設定の Read(//c/src/.local/wt/**) が実装役にも効き、自分の作業ツリーを読めなかった。"""
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".claude").mkdir()
            deny = ["Read(//c/src/.local/wt/**)", "Read(**/*.cs)", "Bash(*dotnet test*)"]
            (repo / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": deny}}), encoding="utf-8")
            for args in (["init", "-q"], ["add", "-A"],
                         ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"]):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            hline.implementer_room(repo, 60)
            got = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
            status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        self.assertNotIn("Read(//c/src/.local/wt/**)", got)
        self.assertIn("Read(**/*.cs)", got)
        self.assertIn("Read(//c/src/.local/out/**)", got)
        self.assertEqual(status, "")


class Retries(unittest.TestCase):
    def run_task(self, verdicts):
        verdicts = iter(verdicts)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline, "new_worktree", side_effect=lambda c, t: (Path(d), "b")) as wt, \
                mock.patch.object(hline, "implement", return_value=(0, ["m"], {})), \
                mock.patch.object(hline, "changed_paths", return_value=["harness/x.py"]), \
                mock.patch.object(hline, "gate", side_effect=lambda c, w, p, *rest: (next(verdicts), "out")):
            got = hline.run_task(CFG, "t", "# x", Path(d))
        return got, wt.call_count

    def test_a_pass_on_the_second_attempt_is_delivered_from_the_same_worktree(self):
        (wt, branch, tries), made = self.run_task([False, True])
        self.assertIsNotNone(wt)
        self.assertEqual((len(tries), made), (2, 1))

    def test_non_convergence_gives_up_after_max_attempts_in_the_one_worktree(self):
        n = CFG["max_attempts"]
        (wt, branch, tries), made = self.run_task([False] * n)
        self.assertIsNone(wt)
        self.assertEqual((len(tries), made), (n, 1))


class DirectorRoom(unittest.TestCase):
    def settings(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)
            for p in ("game-harness", "falling-blocks", ".local/inbox", ".local/wt", ".local/out"):
                (src / p).mkdir(parents=True)
            (src / ".local" / "machine.toml").write_text("", encoding="utf-8")
            return hline.director_settings(src / ".local" / "inbox", src_root=src), src

    def test_shell_is_disabled(self):
        (s, _) = self.settings()
        for tool in ("Bash", "PowerShell", "mcp__terminal"):
            self.assertIn(tool, s["permissions"]["deny"])
        self.assertEqual(s["permissions"]["disableBypassPermissionsMode"], "disable")

    def test_everything_outside_the_inbox_and_its_own_settings_are_not_writable(self):
        (s, src) = self.settings()
        deny = s["permissions"]["deny"]
        root = "//" + src.as_posix().replace(":", "", 1).lower()
        for target in ("game-harness/**", "falling-blocks/**", ".local/wt/**", ".local/out/**",
                       ".local/machine.toml", ".local/inbox/.claude/**", ".local/inbox/report.md",
                       ".local/inbox/TODO.md", ".local/inbox/CLAUDE.md"):
            self.assertIn(f"Edit({root}/{target})", deny)
        self.assertIn("Edit(~/.claude/**)", deny)
        self.assertNotIn(f"Edit({root}/.local/inbox/**)", deny)
        self.assertNotIn(f"Edit({root}/.local/**)", deny)

    def test_raw_logs_and_worktrees_are_not_readable(self):
        (s, src) = self.settings()
        root = "//" + src.as_posix().replace(":", "", 1).lower()
        self.assertIn(f"Read({root}/.local/wt/**)", s["permissions"]["deny"])
        self.assertIn(f"Read({root}/.local/out/**)", s["permissions"]["deny"])


if __name__ == "__main__":
    unittest.main()
