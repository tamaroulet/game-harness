"""H ライン：収束しなかった What の最後の試行の変更と失敗の出力を残し、置き直したときの最初の試行をそこから始める（W-28）。

    python -m unittest tests.test_hline_resume -v

**なぜ要るか**: 1 か所の誤りで What の全部をやり直すと、実装役の仕事が毎回捨てられる。残した変更が今の統合ブランチに当たるときは、
その変更を載せた作業ツリーと前回の失敗の出力から続ける。当たらないときは空から始め、理由を試行の記録（queue.json の tries）に残す。
git は一時ディレクトリのローカルのリポジトリだけを使い、実装役と Gate 1 は差し替える。
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_git  # noqa: E402
import hline_queue  # noqa: E402
import hline_resume  # noqa: E402
import hline_task  # noqa: E402

NAME = "W-28"
FAILURE = "FAILED (failures=1): test_x"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class Resume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-q")
        (self.origin / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "base")
        self.cfg = json.loads(json.dumps(hline.load_config()))
        self.cfg.update(out=str(self.tmp / "out"), max_attempts=1, infra_retry={"max_retries": 0, "wait_seconds": []})
        self.cfg["ttl_seconds"] = {**self.cfg["ttl_seconds"], "git": 60}
        self.count = 0
        self.calls, self.gate_ok, self.integrated = [], False, []
        self.edit = lambda wt: (Path(wt) / "b.txt").write_text("new file\n", encoding="utf-8")
        self.host = SimpleNamespace(
            what_path=hline_queue.what_path, slug=hline.slug, mark=lambda *a, **k: None, today=lambda: "2026-10-11",
            new_worktree=self.new_worktree, self_change=lambda paths: [], changed_paths=hline_git.changed_paths,
            apply_patch=hline_git.apply_patch, implement=self.implement, gate=self.gate,
            integrate=lambda *a, **k: self.integrated.append(a[2]), save_state=lambda *a: None)
        self.host.run_task = lambda cfg, tid, what, outdir, first=None, task=None, on_stage=None, run=0: \
            hline_task.run_task(self.host, cfg, tid, what, outdir, first, task, on_stage, run)

    def new_worktree(self, cfg, tid):
        self.count += 1
        wt = self.tmp / f"wt{self.count}"
        git(self.tmp, "clone", "-q", str(self.origin), str(wt))
        return wt, f"b{self.count}"

    def implement(self, cfg, wt, what, feedback, log):
        self.calls.append({"wt": Path(wt), "feedback": feedback, "had_b": (Path(wt) / "b.txt").exists()})
        self.edit(wt)
        return 0, ["claude-sonnet-5-5"], None, None, 1

    def gate(self, cfg, wt, paths, spec=None, task=None, extended=(), warnings=None, verify=None, milestone=None):
        return (True, "") if self.gate_ok else (False, FAILURE)

    def state(self, replaced):
        return {"items": {NAME: {"status": "waiting", "title": "t", "task": None, "replaced": replaced, "deps": []}}}

    def process(self, st):
        with contextlib.redirect_stdout(io.StringIO()):
            ok = hline_task.process(self.host, self.cfg, st, NAME)
        return ok, st["items"][NAME]

    def put_what(self):
        hline_queue.what_path(self.cfg, NAME).parent.mkdir(parents=True, exist_ok=True)
        hline_queue.what_path(self.cfg, NAME).write_text("# t\nマイルストーン: M\nタスク: W-28\n", encoding="utf-8")

    def unconverged_first(self):
        self.put_what()
        ok, item = self.process(self.state(False))
        self.assertFalse(ok)
        self.assertEqual(item["status"], "unconverged")

    def test_an_unconverged_what_keeps_its_last_changes_and_failure(self):
        self.unconverged_first()
        kept = hline_resume.resume_dir(self.cfg, NAME)
        self.assertIn("b.txt", (kept / hline_resume.PATCH).read_text(encoding="utf-8"))
        self.assertIn(FAILURE, (kept / hline_resume.FAILURE).read_text(encoding="utf-8"))

    def test_a_replaced_what_starts_from_the_kept_changes_with_the_previous_failure(self):
        self.unconverged_first()
        self.assertEqual((self.calls[0]["feedback"], self.calls[0]["had_b"]), (None, False))   # 置き直しでない最初の実行は今と同じ
        self.gate_ok = True
        self.edit = lambda wt: None
        ok, item = self.process(self.state(True))
        self.assertTrue(ok)
        second = self.calls[1]
        self.assertTrue(second["had_b"])   # 前の変更が作業ツリーに載っている
        self.assertNotEqual(second["wt"], self.calls[0]["wt"])
        self.assertIn(FAILURE, second["feedback"])   # 前の失敗の出力が実装役に渡る
        self.assertEqual(item["tries"][0]["resume"], {"applied": True, "reason": None})
        self.assertFalse(hline_resume.resume_dir(self.cfg, NAME).exists())   # 済みになったら残さない

    def test_changes_that_do_not_apply_start_from_empty_and_the_reason_is_recorded(self):
        self.unconverged_first()
        git(self.origin, "checkout", "-q", "-b", "other")
        (self.origin / "b.txt").write_text("conflicting\n", encoding="utf-8")   # 今の統合ブランチには同名のファイルが既にある
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "conflict")
        self.edit = lambda wt: None
        ok, item = self.process(self.state(True))
        second = self.calls[1]
        self.assertIsNone(second["feedback"])   # 前の失敗の出力は渡らない
        self.assertEqual(Path(second["wt"], "b.txt").read_text(encoding="utf-8"), "conflicting\n")   # 残した変更は載っていない
        resume = item["tries"][0]["resume"]
        self.assertFalse(resume["applied"])
        self.assertIn("当たらない", resume["reason"])

    def test_nothing_kept_starts_from_empty_and_the_reason_is_recorded(self):
        self.put_what()
        self.edit = lambda wt: None
        _, item = self.process(self.state(True))
        self.assertIsNone(self.calls[0]["feedback"])
        self.assertEqual(item["tries"][0]["resume"]["applied"], False)
        self.assertIn("残した変更が無い", item["tries"][0]["resume"]["reason"])

    def test_a_what_that_is_not_replaced_has_no_resume_record(self):
        self.unconverged_first()
        _, item = self.process(self.state(False))
        self.assertNotIn("resume", item["tries"][0])
        self.assertIsNone(self.calls[1]["feedback"])
        self.assertFalse(self.calls[1]["had_b"])


if __name__ == "__main__":
    unittest.main()
