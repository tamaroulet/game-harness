"""H ライン：実装役の断念・同じ差分の繰り返しで試行を打ち切る内側の遮断（hline_abort）の検査。
git は一時ディレクトリの小さなリポジトリだけ。host は小さな偽物で、push・PR・モデルの呼び出しは起こさない。"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_abort  # noqa: E402
import hline_prompt  # noqa: E402
import hline_respec  # noqa: E402
import hline_task  # noqa: E402

MARKER, WHY = hline_abort.MARKER, "編集境界の外の harness/hline.py を変えないと実装できない"
WHAT = "# T-010-a\n\n## What\n本文の合格条件\n"
CFG = {"max_attempts": 3, "ttl_seconds": {"git": 60}, "gate_tail_chars": 2000}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class Repo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.wt, self.out = self.root / "wt", self.root / "out"
        for p in (self.wt, self.out):
            p.mkdir()
        git(self.wt, "init", "-q")
        (self.wt / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        git(self.wt, "add", "tracked.py")
        git(self.wt, "commit", "-q", "-m", "base")

    def write(self, name, text):
        (self.wt / name).write_text(text, encoding="utf-8")


class Digest(Repo):
    def digest(self):
        return hline_abort.patch_digest(self.wt, 60)

    def test_the_same_change_gives_the_same_value_and_another_change_another(self):
        self.write("new.py", "a\n")
        first = self.digest()
        self.assertEqual(self.digest(), first)
        self.write("new.py", "b\n")
        self.assertNotEqual(self.digest(), first)
        self.write("tracked.py", "x = 2\n")
        self.assertNotEqual(self.digest(), first)

    def test_an_untracked_file_counts_and_nothing_is_staged(self):
        empty = self.digest()
        self.write("new.py", "a\n")
        self.assertNotEqual(self.digest(), empty)
        self.assertEqual(git(self.wt, "diff", "--cached"), "")
        self.assertEqual((self.wt / "new.py").read_text(encoding="utf-8"), "a\n")
        self.assertEqual(len(empty), 64)


class Pure(unittest.TestCase):
    def log(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "implementer.log"
        p.write_text(text, encoding="utf-8")
        return p

    def test_abandon_reason_takes_the_first_marker_line(self):
        self.assertIsNone(hline_abort.abandon_reason(self.log(f"途中に {MARKER} があるだけ\n")))
        self.assertEqual(hline_abort.abandon_reason(self.log(f"作業中\n{MARKER}   {WHY}  \n{MARKER} 2 件目\n")), WHY)
        out = json.dumps({"result": f"できません\n{MARKER} {WHY}", "num_turns": 3})  # CLI の JSON の result も見る
        self.assertEqual(hline_abort.abandon_reason(self.log(out + "\n--- stderr ---\n")), WHY)

    def test_abandon_reason_is_none_without_a_declaration_or_a_readable_log(self):
        self.assertIsNone(hline_abort.abandon_reason(self.log("普通のログ\n")))
        self.assertIsNone(hline_abort.abandon_reason(Path(tempfile.gettempdir()) / "no-such-dir-h-line" / "x.log"))

    def test_abort_reason_prefers_the_declaration_and_skips_the_same_diff_check_on_the_first_try(self):
        said, quiet_log = self.log(f"{MARKER} {WHY}\n"), self.log("何も言わない\n")
        self.assertEqual(hline_abort.abort_reason(said, "d", "d"), WHY)
        self.assertIsNone(hline_abort.abort_reason(quiet_log, "d", None))
        self.assertIn("同じ差分", hline_abort.abort_reason(quiet_log, "d", "d"))
        self.assertIsNone(hline_abort.abort_reason(quiet_log, "d", "e"))
        self.assertTrue(hline_abort.abort_reason(self.log(f"{MARKER}\n"), "d", None))

    def test_unconverged_reason_is_the_abort_of_the_last_try(self):
        self.assertIsNone(hline_abort.unconverged_reason([]))
        self.assertIsNone(hline_abort.unconverged_reason([{"abort": "古い"}, {"gate": False}]))
        self.assertEqual(hline_abort.unconverged_reason([{"gate": False}, {"abort": WHY}]), WHY)

    def test_the_instructions_are_in_the_prompt_before_the_what_body(self):
        text = hline_abort.instructions()
        self.assertIn(MARKER, text)
        prompt = hline_prompt.build_prompt(WHAT, "前回の出力", "# 目次X")
        head, tail = prompt.split("\n---\n", 1)
        self.assertIn(text, head)
        self.assertNotIn(MARKER, tail)
        self.assertEqual(tail.split("\n---\n")[0].strip(), WHAT.strip())


class Flow(Repo):
    def host(self, writes, logs=None):
        self.implemented, self.gated = [], []

        def implement(cfg, wt, what, feedback, log):
            n = len(self.implemented)
            self.implemented.append(feedback)
            self.write("new.py", writes[min(n, len(writes) - 1)])
            Path(log).write_text((logs or {}).get(n, "ふつうのログ\n"), encoding="utf-8")
            return 0, ["claude-sonnet-5-5"], None, None, 3

        def gate(cfg, wt, paths, spec=None, task=None, **kw):
            self.gated.append(paths)
            return False, f"GATE-OUT-{len(self.gated)}"

        return types.SimpleNamespace(new_worktree=mock.Mock(), implement=implement, gate=gate,
                                     changed_paths=lambda w, c: ["new.py"])

    def run_task(self, host):
        return quiet(hline_task.run_task, host, CFG, "t", WHAT, self.out, (self.wt, "b"))

    def test_the_same_diff_twice_stops_before_the_second_gate(self):
        wt, branch, tries = self.run_task(self.host(["same\n"]))
        self.assertEqual((wt, branch), (None, None))
        self.assertEqual((len(self.implemented), len(self.gated), len(tries)), (2, 1, 2))
        self.assertEqual(("abort" in tries[0], tries[1]["gate"]), (False, False))
        self.assertIn("同じ差分", tries[1]["abort"])
        self.assertEqual(hline_abort.unconverged_reason(tries), tries[1]["abort"])

    def test_changing_diffs_use_every_attempt_and_keep_the_record_shape(self):
        wt, branch, tries = self.run_task(self.host(["a\n", "b\n", "c\n"]))
        self.assertEqual((wt, len(self.gated), len(tries)), (None, 3, 3))
        for t in tries:
            self.assertEqual(sorted(t), sorted(["run", "attempt", "cli_exit", "models", "gate", "usage", "cutoff", "turns",
                                                "flaky", "warnings"]))

    def test_a_declaration_stops_the_first_try_without_gate_1(self):
        wt, branch, tries = self.run_task(self.host(["a\n"], {0: f"諦めます\n{MARKER} {WHY}\n"}))
        self.assertEqual((wt, branch, self.gated, len(tries)), (None, None, [], 1))
        self.assertEqual((tries[0]["abort"], tries[0]["gate"]), (WHY, False))
        summary = hline_respec.failure_summary(CFG, self.out, tries)
        self.assertIn(WHY, summary)
        for part in ("## Gate 1 の出力", "## 実装役の終了コード", "## 実装役のログの末尾", "諦めます"):
            self.assertIn(part, summary)

    def test_a_summary_without_an_abort_has_no_abort_section(self):
        _, _, tries = self.run_task(self.host(["a\n", "b\n", "c\n"]))
        self.assertNotIn("打ち切り・断念", hline_respec.failure_summary(CFG, self.out, tries))

    def test_a_worktree_that_is_not_a_git_one_is_not_compared(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(hline_task.digest_of(CFG, d))


class Process(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = {"out": str(Path(tmp.name) / "out"), "respecs": 0, "infra_retry": {"max_retries": 0, "wait_seconds": 0}}
        what = Path(tmp.name) / "what.md"
        what.write_text("# T\n本文\n", encoding="utf-8")
        self.st = {"items": {"010-a": {"title": "T", "task": None}}}
        self.host = mock.Mock(what_path=lambda c, n: what, slug=lambda n: "a", today=lambda: "2026-10-06",
                              new_worktree=mock.Mock(return_value=(Path(tmp.name), "b")))

    def run_process(self, tries):
        self.host.run_task = mock.Mock(return_value=(None, None, tries))
        return quiet(hline_task.process, self.host, self.cfg, self.st, "010-a"), self.st["items"]["010-a"]

    def test_an_abandon_with_no_respecs_left_is_unconverged_with_the_reason(self):
        done, item = self.run_process([{"run": 0, "attempt": 1, "gate": False, "abort": WHY}])
        self.assertEqual((done, item["status"], item["reason"]), (False, "unconverged", WHY))
        self.host.run_task.assert_called_once()   # 作り直し（再分解）は無い（C5）

    def test_without_an_abort_the_reason_is_the_old_sentence(self):
        _, item = self.run_process([{"run": 0, "attempt": 1, "gate": False}])
        self.assertEqual(item["reason"], "1 回の試行で Gate 1 に通らず、パッチを捨てた")


if __name__ == "__main__":
    unittest.main()
