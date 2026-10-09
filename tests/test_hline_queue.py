"""H ライン（段 1）のタスクキュー・統合ブランチ・TaskSpec・Gate A の検査（進捗のタスク S1-1）。

    python -m unittest tests.test_hline_queue -v

**なぜ要るか**: 段 1 で H ラインは「1 件 1 PR」から、「依存の順に人間の操作なしで 1 本の統合ブランチに積み、尽きたら統合 PR を
1 本だけ出して止まる」ループになる。依存の順・マイルストーンの柵・凍結と再開・統合 PR の 1 本・強制終了からの復旧が崩れると、
ラインが二重に走る・What が失われる・人間が呼ばれ続ける。実際の push・PR・モデルの呼び出しは起こさない
（World の setUp が proc.run を差し替え、想定外の呼び出しは落ちる）。git の検査は、ローカルの一時リポジトリで fetch と作業ツリーだけを行う。
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_base  # noqa: E402
import hline_git  # noqa: E402
import hline_queue  # noqa: E402
import hline_report  # noqa: E402
import hline_spec  # noqa: E402
import infra_retry  # noqa: E402
import model_pin  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CFG = hline.load_config()
SCHEMA = hline_spec.load_schema(CFG)
SPEC = {
    "target_symbols": [{"module": "harness/textnorm.py", "kind": "function", "name": "normalize_newlines"}],
    "signatures": [{"symbol": "normalize_newlines", "params": [{"name": "text", "type": "str"}], "returns": "str"}],
    "contracts": {"preconditions": [], "postconditions": ["改行は \\n だけになる"], "invariants": ["入力は変えない"]},
    "edit_boundary": {"allowed_files": ["harness/textnorm.py", "tests/test_textnorm.py"],
                      "forbidden_files": ["docs/progress.yaml"], "max_diff_lines": 100},
    "test_oracle": {"guidance": "CRLF と CR が LF になること"},
}
VERIFY = "python -m unittest tests.test_x"


def claude_json(result, model):
    return json.dumps({"result": result, "modelUsage": {model: {}}})


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def spec_with(**edit):
    spec = json.loads(json.dumps(SPEC))
    for k, v in edit.items():
        spec["edit_boundary"][k] = v
    return spec


# ============================================================ スキーマと Gate A

class Schema(unittest.TestCase):
    def test_a_conforming_taskspec_has_no_violation(self):
        self.assertEqual(hline_spec.gate_a(SCHEMA, SPEC), [])

    def test_each_of_the_five_required_elements_is_required(self):
        self.assertEqual(sorted(SCHEMA["required"]),
                         sorted(["target_symbols", "signatures", "contracts", "edit_boundary", "test_oracle"]))
        for key in SCHEMA["required"]:
            with self.subTest(key=key):
                spec = {k: v for k, v in SPEC.items() if k != key}
                problems = hline_spec.gate_a(SCHEMA, spec)
                self.assertTrue(any(key in p for p in problems), problems)

    def test_the_contract_needs_pre_post_and_invariants(self):
        for key in ("preconditions", "postconditions", "invariants"):
            with self.subTest(key=key):
                spec = json.loads(json.dumps(SPEC))
                del spec["contracts"][key]
                self.assertTrue(hline_spec.gate_a(SCHEMA, spec))

    def test_the_diff_limit_cannot_exceed_300(self):
        self.assertEqual(hline_spec.gate_a(SCHEMA, spec_with(max_diff_lines=300)), [])
        for bad in (301, 0, "300", True):
            with self.subTest(limit=bad):
                self.assertTrue(hline_spec.gate_a(SCHEMA, spec_with(max_diff_lines=bad)))

    def test_wrong_types_unknown_keys_and_empty_lists_are_violations(self):
        for edit in ({"allowed_files": []}, {"allowed_files": "harness/x.py"}, {"forbidden_files": [""]}):
            with self.subTest(edit=edit):
                self.assertTrue(hline_spec.gate_a(SCHEMA, spec_with(**edit)))
        extra = dict(SPEC, note="何か")
        self.assertTrue(hline_spec.gate_a(SCHEMA, extra))
        bad_kind = json.loads(json.dumps(SPEC))
        bad_kind["target_symbols"][0]["kind"] = "variable"
        self.assertTrue(hline_spec.gate_a(SCHEMA, bad_kind))
        self.assertTrue(hline_spec.gate_a(SCHEMA, None))

    def test_a_task_declaring_what_must_carry_the_progress_verification_command(self):
        spec = json.loads(json.dumps(SPEC))
        self.assertTrue(hline_spec.gate_a(SCHEMA, spec, VERIFY))
        spec["test_oracle"]["verification_command"] = "python -m unittest tests.other"
        self.assertTrue(hline_spec.gate_a(SCHEMA, spec, VERIFY))
        spec["test_oracle"]["verification_command"] = VERIFY
        self.assertEqual(hline_spec.gate_a(SCHEMA, spec, VERIFY), [])

    def test_json_is_taken_out_of_a_fenced_reply(self):
        self.assertEqual(hline_spec.extract_json("```json\n{\"a\": 1}\n```")[0], {"a": 1})
        self.assertEqual(hline_spec.extract_json("前置き {\"a\": 1} 後書き")[0], {"a": 1})
        self.assertIsNone(hline_spec.extract_json("JSON ではない")[0])


# ============================================================ 編集境界（Gate 1）

class Boundary(unittest.TestCase):
    def gate(self, paths, lines=10, spec=None, tests=(0, "", ""), warn=None):
        with mock.patch.object(hline, "diff_counts", return_value={"added": lines, "deleted": 0, "deleted_files": ()}), \
                mock.patch.object(hline.size_limits, "head_source", return_value=lambda p: None), mock.patch.object(hline.proc, "run", return_value=tests) as run:
            ok, msg = hline.gate(CFG, Path("."), paths, spec or spec_with(
                allowed_files=["harness/", "tests/*.py"], forbidden_files=["harness/hline.py"], max_diff_lines=100), warnings=warn)
        return ok, msg, run

    def test_changes_inside_the_boundary_run_the_tests(self):
        ok, msg, run = self.gate(["harness/textnorm.py", "tests/test_textnorm.py"])
        self.assertTrue(ok)
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [hline.base_whitelist.unittest_command(CFG["gate_command"], ["tests.test_textnorm"])])   # 影響テストだけ（C3）

    def test_a_file_outside_the_allowed_files_fails_without_running_the_tests(self):
        """編集境界の外は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        warn = []
        ok, msg, run = self.gate(["harness/textnorm.py", "README.md"], warn=warn)
        self.assertTrue(ok)
        self.assertIn("README.md", "\n".join(warn))
        run.assert_called()

    def test_a_forbidden_file_fails_even_when_it_is_allowed_by_a_wider_pattern(self):
        """TaskSpec の forbidden_files も警告だけ（変えてはならないパスは hline_protect が不合格にする）。"""
        warn = []
        ok, msg, run = self.gate(["harness/hline.py"], warn=warn)
        self.assertTrue(ok)
        self.assertIn("変えてはならない", "\n".join(warn))

    def test_exceeding_the_diff_limit_fails(self):
        """差分の行数は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        self.assertTrue(self.gate(["harness/x.py"], lines=100)[0])
        warn = []
        ok, msg, run = self.gate(["harness/x.py"], lines=101, warn=warn)
        self.assertTrue(ok)
        self.assertIn("101", "\n".join(warn))
        run.assert_called()

    def test_failing_tests_still_fail_inside_the_boundary(self):
        self.assertFalse(self.gate(["harness/x.py"], tests=(1, "out", "err"))[0])

    def test_matching_rules(self):
        self.assertTrue(hline_spec.matches("harness/a/b.py", "harness/"))
        self.assertTrue(hline_spec.matches("harness/a/b.py", "harness"))
        self.assertTrue(hline_spec.matches("tests\\test_a.py", "tests/test_*.py"))
        self.assertFalse(hline_spec.matches("harness2/a.py", "harness"))
        self.assertFalse(hline_spec.matches("docs/progress.yaml", "harness/"))

    def test_the_diff_size_counts_added_and_deleted_lines_and_new_files(self):
        calls = []

        def fake_must(args, cwd, ttl, label):
            calls.append(args)
            return "3\t2\ta.py\n-\t-\tlogo.png\n10\t0\tnew.py\n"

        with mock.patch.object(hline_spec, "must", side_effect=fake_must):
            self.assertEqual(hline_spec.diff_counts(CFG, Path(".")), {"added": 13, "deleted": 2, "deleted_files": ()})   # 追加と削除を別に数える
        self.assertEqual(calls[0], ["git", "add", "-A", "-N"])   # 新しいファイルも差分に数える


# ============================================================ 進捗のタスクの検証

class TaskVerification(unittest.TestCase):
    def worktree(self, d, command=VERIFY):
        (Path(d) / "docs").mkdir()
        state = {"tasks": [{"id": "S1-2", "verification": {"command": command, "expected_exit_code": 0}},
                           {"id": "S1-3"}]}
        (Path(d) / "docs" / "progress.yaml").write_text(yaml.safe_dump(state), encoding="utf-8")
        return Path(d)

    def gate(self, wt, verify_code):
        def fake_run(args, cwd, ttl, label, env=None, input=None):
            return (0, "", "") if label == "Gate 1" else (verify_code, "verify-out", "")

        with mock.patch.object(hline.proc, "run", side_effect=fake_run) as run:
            ok, msg = hline.gate(CFG, wt, ["harness/x.py"], None, "S1-2")
        return ok, msg, run

    def test_a_change_failing_the_progress_verification_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg, run = self.gate(self.worktree(d), 1)
        self.assertFalse(ok)
        self.assertIn(VERIFY, msg)
        self.assertEqual([c.args[0] for c in run.call_args_list], [["python", "-m", "unittest", "tests.test_x"]])

    def test_a_change_passing_the_verification_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(self.gate(self.worktree(d), 0)[0])

    def test_a_task_without_a_verification_command_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            wt = self.worktree(d)
            with mock.patch.object(hline.proc, "run", return_value=(0, "", "")):
                ok, msg = hline.gate(CFG, wt, ["harness/x.py"], None, "S1-3")
        self.assertFalse(ok)
        self.assertIn("S1-3", msg)


class Integrate(unittest.TestCase):
    def run_integrate(self, task, complete_code=0):
        calls = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            calls.append(args)
            return (complete_code, "", "") if "complete" in args else (0, "", "")

        with mock.patch.object(hline.proc, "run", side_effect=fake_run):
            try:
                hline.integrate(CFG, Path("."), "120-x", "題", task)
            except hline.Infra:
                return calls, True
        return calls, False

    def test_the_task_is_recorded_as_complete_before_the_commit_and_pushed_to_the_integration_branch_only(self):
        calls, failed = self.run_integrate("S1-2")
        self.assertFalse(failed)
        self.assertEqual(calls[0][-3:], ["harness.progress", "complete", "S1-2"])
        names = [c[1] for c in calls[1:]]
        self.assertEqual(names, ["add", "commit", "push"])
        self.assertIn("HEAD:refs/heads/" + CFG["integration_branch"], calls[-1])
        self.assertTrue(any("H-Line-Item: 120-x" in a for a in calls[2]))
        self.assertFalse([c for c in calls if "pr" in c or any("gh" in str(a) for a in c[:1])])   # タスクごとの PR は作らない

    def test_what_without_a_task_does_not_touch_the_progress(self):
        calls, failed = self.run_integrate(None)
        self.assertFalse([c for c in calls if "harness.progress" in c])
        self.assertEqual([c[1] for c in calls], ["add", "commit", "push"])

    def test_a_failing_complete_stops_before_anything_is_committed_or_pushed(self):
        calls, failed = self.run_integrate("S1-2", complete_code=1)
        self.assertTrue(failed)
        self.assertEqual(len(calls), 1)

    def test_the_report_mirrors_the_progress_of_the_integration_branch(self):
        served = progress.load(REPO / "docs" / "progress.yaml")
        progress.task(served, "S1-1")["status"] = "completed"
        asked = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            asked.append(args[-1])
            return (0, yaml.safe_dump(served, allow_unicode=True), "") if "hline/integration" in args[-1] else (1, "", "")

        with mock.patch.object(hline.proc, "run", side_effect=fake_run):
            text = hline_report.progress_report(CFG)
        self.assertEqual(asked[0], f"origin/{CFG['integration_branch']}:docs/progress.yaml")
        self.assertEqual(text, progress.report(served))


# ============================================================ 統合ブランチの先端から作る作業ツリー（ローカルの git だけ）

class IntegrationTip(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.clone = self.tmp / "origin", self.tmp / "clone"
        self.origin.mkdir()
        self.git(self.origin, "init", "-q")
        self.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.origin / ".claude").mkdir()
        (self.origin / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (self.origin / "base.txt").write_text("main", encoding="utf-8")
        self.git(self.origin, "add", "-A")
        self.git(self.origin, "commit", "-q", "-m", "main")
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))
        self.cfg = json.loads(json.dumps(CFG))
        self.cfg["worktrees"] = str(self.tmp / "wt")
        patcher = mock.patch.object(hline_git, "ROOT", self.clone)
        patcher.start()
        self.addCleanup(patcher.stop)

    def push_to_integration(self, name, text, trailer=None):
        branch = self.cfg["integration_branch"]
        exists = subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=self.origin,
                                capture_output=True).returncode == 0
        self.git(self.origin, "checkout", "-q", *([] if exists else ["-b"]), branch)
        (self.origin / name).write_text(text, encoding="utf-8")
        self.git(self.origin, "add", "-A")
        msg = "feat(hline): 積む" + (f"\n\nH-Line-Item: {trailer}\nCo-Authored-By: T <t@t>\n" if trailer else "")
        self.git(self.origin, "commit", "-q", "-m", msg)
        self.git(self.origin, "checkout", "-q", "main")

    def test_a_worktree_starts_from_main_until_the_integration_branch_exists_and_then_from_its_tip(self):
        wt1, _ = hline_git.new_worktree(self.cfg, "t1")
        self.assertTrue((wt1 / "base.txt").exists())
        self.assertFalse((wt1 / "first.txt").exists())
        self.push_to_integration("first.txt", "1番目のタスクの変更")
        wt2, _ = hline_git.new_worktree(self.cfg, "t2")
        self.assertEqual((wt2 / "first.txt").read_text(encoding="utf-8"), "1番目のタスクの変更")
        self.push_to_integration("second.txt", "2番目")
        wt3, _ = hline_git.new_worktree(self.cfg, "t3")
        self.assertTrue((wt3 / "first.txt").exists() and (wt3 / "second.txt").exists())
        self.assertEqual(len({wt1, wt2, wt3}), 3)

    def test_the_implementer_room_edit_is_invisible_to_git_in_the_new_worktree(self):
        wt, _ = hline_git.new_worktree(self.cfg, "t1")
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_what_already_on_the_integration_branch_is_recognized_by_its_trailer(self):
        self.assertFalse(hline_git.integrated(self.cfg, "abc"))
        self.push_to_integration("a.txt", "a", trailer="abc")
        hline_git.fetch(self.cfg)
        self.assertTrue(hline_git.integrated(self.cfg, "abc"))
        self.assertFalse(hline_git.integrated(self.cfg, "ab"))
        self.assertEqual(hline_git.ahead(self.cfg), 1)

    def test_worktree_creation_names_the_start_point_without_touching_the_network(self):
        starts = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            if args[:3] == ["git", "worktree", "add"]:
                starts.append(args[-1])
            return (0, "", "") if args[1] != "rev-parse" else (1, "", "")

        with mock.patch.object(hline.proc, "run", side_effect=fake_run), \
                mock.patch.object(hline_git, "implementer_room"):
            hline_git.new_worktree(CFG, "t")
            self.assertEqual(starts, [CFG["base"]])


# ============================================================ キューの部品

class QueueParts(unittest.TestCase):
    def test_declarations_are_read_from_the_body(self):
        meta = hline_queue.parse_header("# 題\nマイルストーン: B7.1\n- タスク： S1-2\n依存: 120-a.md, `130-b`、140-c\n本文")
        self.assertEqual(meta, {"milestone": "B7.1", "task": "S1-2", "deps": ["120-a", "130-b", "140-c"]})
        self.assertEqual(hline_queue.parse_header("# 題\n本文"), {"milestone": None, "task": None, "deps": []})

    def item(self, deps=(), status="waiting", milestone="B7.1"):
        return {"status": status, "title": "t", "milestone": milestone, "task": None, "deps": list(deps)}

    def test_freezing_follows_dependencies_transitively_and_unfreezes_when_the_upstream_is_fixed(self):
        st = {"items": {"a": self.item(status="unconverged"), "b": self.item(["a"]), "c": self.item(["b"]),
                        "d": self.item(), "e": self.item(["d"])}}
        hline_queue.refresh(st)
        self.assertEqual(hline_queue.by_status(st, "frozen"), ["b", "c"])
        self.assertEqual(st["items"]["c"]["frozen_by"], ["b"])
        self.assertEqual(hline_queue.next_runnable(st), "d")
        st["items"]["a"]["status"] = "done"
        hline_queue.refresh(st)
        self.assertEqual(hline_queue.by_status(st, "frozen"), [])
        self.assertEqual(hline_queue.next_runnable(st), "b")

    def test_a_what_with_an_unknown_or_unfinished_dependency_is_not_runnable(self):
        st = {"items": {"a": self.item(["ghost"]), "b": self.item(["c"]), "c": self.item(status="processing")}}
        self.assertIsNone(hline_queue.next_runnable(st))


# ============================================================ 走行（外部は差し替える）

class World(unittest.TestCase):
    """受信箱・生ログ・作業ツリーは一時ディレクトリ。git・gh・モデルは差し替え、想定外の呼び出しは落とす。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out, self.wt = self.tmp / "inbox", self.tmp / "out", self.tmp / "wt"
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), worktrees=str(self.wt))
        for p in (self.inbox, self.out, self.wt):
            p.mkdir()
        (self.wt / "docs").mkdir()
        (self.wt / "docs" / "progress.yaml").write_text(yaml.safe_dump(
            {"tasks": [{"id": "S1-2", "verification": {"command": VERIFY, "expected_exit_code": 0}}]}), encoding="utf-8")
        self.implemented, self.integrated, self.created, self.dropped = [], [], [], []
        self.failing, self.pr_state, self.open_pr, self.ahead_n, self.current = set(), "OPEN", None, 0, None; self.diff_list = []
        self.warnings = []   # Gate 1 が不合格にしない指摘（C2）。試行の記録に入り、report の済みの行に件数が出る

        def forbidden(*a, **kw):
            raise AssertionError(f"実際の外部呼び出しが起きました: {a[:1]}")

        self.slept = []
        self.patch(infra_retry, "SLEEP", new=self.slept.append)   # 呼び直しの待ち時間は実際には待たない
        self.patch(hline.proc, "run", side_effect=forbidden)
        self.patch(hline.proc, "resolve_cli", side_effect=forbidden)
        [self.patch(hline, n) for n in ("fetch", "changed_paths")]   # changed_paths は空の反復を返す
        self.patch(hline, "integrated", side_effect=lambda c, n: n in self.integrated)
        self.patch(hline, "new_worktree", side_effect=lambda c, t: (self.wt, "b"))
        self.patch(hline, "integrate", side_effect=self.fake_integrate)
        self.patch(hline, "ahead", side_effect=lambda c: self.ahead_n); self.patch(hline, "diff_files", side_effect=lambda c: self.diff_list)
        self.patch(hline, "open_pr", side_effect=lambda c: self.open_pr)
        self.patch(hline, "create_pr", side_effect=self.fake_create_pr)
        self.patch(hline, "pr_state", side_effect=lambda c, u: self.pr_state)
        self.patch(hline, "drop_merged_branch", side_effect=self.fake_drop)
        self.patch(hline_report, "progress_report", return_value="## 進捗ツリー\n- 人間作業: NONE\n")
        self.patch_agents()

    def patch(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        p.start()
        self.addCleanup(p.stop)

    def patch_agents(self):
        self.patch(hline, "run_task", side_effect=self.fake_run_task)

    def fake_run_task(self, cfg, tid, what, outdir, first=None, task=None, on_stage=None):
        self.current = hline.title_of(what)   # 段は 1 つ。処理中の What は実装役に渡る本文から分かる
        self.implemented.append(self.current)
        ok = self.current not in self.failing
        tries = [{"run": 0, "attempt": 1, "cli_exit": 0, "models": ["claude-sonnet-5-5"], "gate": ok,
                  "warnings": list(self.warnings)}]
        return (self.wt, "b", tries) if ok else (None, None, tries)

    def fake_integrate(self, cfg, wt, name, title, task):
        self.integrated.append(name)
        self.ahead_n += 1

    def fake_create_pr(self, cfg, title, body):
        self.created.append((title, body))
        return f"https://github.com/o/r/pull/{len(self.created)}"

    def fake_drop(self, cfg):
        self.dropped.append(1)
        self.ahead_n = 0

    # --- 道具
    def put(self, name, milestone="B7.1", task=None, deps=(), extra=""):
        lines = [f"# T-{name}"] + ([f"マイルストーン: {milestone}"] if milestone else [])
        lines += [f"タスク: {task}"] if task else []
        lines += ["依存: " + ", ".join(deps)] if deps else []
        (self.inbox / f"{name}.md").write_text("\n".join(lines + ["", "本文", extra]), encoding="utf-8")

    def poll(self):
        return quiet(hline.poll, self.cfg)

    def state(self):
        return hline_queue.load_state(self.cfg)

    def status(self, name):
        return self.state()["items"][name]["status"]

    def report(self):
        return (self.inbox / "report.md").read_text(encoding="utf-8")

    def inbox_names(self):
        return sorted(p.name for p in self.inbox.glob("*.md") if p.name not in hline.RESERVED)


class Ordering(World):
    def test_a_dependent_what_waits_for_its_dependency_even_when_it_sorts_first(self):
        self.put("010-b", deps=["020-a"])
        self.put("020-a")
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.implemented, ["T-020-a", "T-010-b"])

    def test_it_is_not_processed_until_the_dependency_is_done_and_then_continues_without_a_human(self):
        self.put("010-b", deps=["020-a"])
        self.poll()
        self.assertEqual(self.implemented, [])
        self.assertEqual(self.status("010-b"), "waiting")
        self.assertEqual(self.inbox_names(), [])   # 取り込んだ What は受信箱から消える（二度取らない）
        self.put("020-a")
        self.poll()
        self.assertEqual(self.implemented, ["T-020-a", "T-010-b"])
        self.assertEqual((self.status("020-a"), self.status("010-b")), ("done", "done"))

    def test_independent_whats_are_processed_by_name_and_all_in_one_run(self):
        for n in ("030-c", "010-a", "020-b"):
            self.put(n)
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b", "T-030-c"])

    def test_the_queue_survives_between_runs(self):
        self.put("010-b", deps=["020-a"])
        self.poll()
        self.assertTrue((self.out / "queue.json").exists())
        self.assertTrue((self.out / "queue" / "010-b.md").exists())   # 受信箱の外（生ログと同じ場所）


class IntegrationPr(World):
    def test_one_integration_pr_is_made_for_all_the_tasks_and_no_per_task_pr(self):
        for n in ("010-a", "020-b", "030-c"):
            self.put(n)
        self.poll()
        self.assertEqual(self.integrated, ["010-a", "020-b", "030-c"])
        self.assertEqual(len(self.created), 1)
        title, body = self.created[0]
        for n in ("T-010-a", "T-020-b", "T-030-c", "claude-sonnet-5-5"):
            self.assertIn(n, body)
        self.assertEqual(self.state()["awaiting_pr"], "https://github.com/o/r/pull/1")
        self.assertIn("統合 PR 待ち", self.report())
        self.assertIn("REVIEW_REQUIRED https://github.com/o/r/pull/1", self.report())

    def test_the_pr_is_made_only_when_the_waiting_whats_are_gone(self):
        self.put("010-a", deps=["020-ghost"])
        self.put("020-b")
        self.poll()
        self.assertEqual(self.integrated, ["020-b"])
        self.assertEqual(self.created, [])   # 010-a がまだ待ち
        self.put("020-ghost")
        self.poll()
        self.assertEqual(len(self.created), 1)

    def test_no_pr_is_made_when_nothing_was_integrated(self):
        self.failing = {"T-010-a"}
        self.put("010-a")
        self.assertEqual(self.poll(), 1)
        self.assertEqual(self.created, [])
        self.assertIsNone(self.state()["awaiting_pr"])
        self.poll()
        self.assertEqual(self.created, [])

    def test_a_pr_that_is_already_open_is_not_made_again(self):
        self.open_pr = "https://github.com/o/r/pull/9"
        self.put("010-a")
        self.poll()
        self.assertEqual(self.created, [])
        self.assertEqual(self.state()["awaiting_pr"], self.open_pr)

    def test_while_the_pr_is_open_the_inbox_is_not_taken(self):
        self.put("010-a")
        self.poll()
        self.put("020-b")
        self.poll()
        self.assertEqual(self.inbox_names(), ["020-b.md"])
        self.assertEqual(self.implemented, ["T-010-a"])
        self.assertEqual(len(self.created), 1)

    def test_a_closed_pr_releases_the_line_and_the_next_whats_make_the_next_pr(self):
        self.put("010-a")
        self.poll()
        self.pr_state = "CLOSED"
        self.put("020-b")
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.dropped, [])   # マージされていないブランチは消さない

    def test_a_merged_pr_drops_the_integration_branch_and_the_next_cycle_starts_clean(self):
        self.put("010-a")
        self.poll()
        self.pr_state = "MERGED"
        self.poll()
        self.assertEqual(self.dropped, [1])
        self.assertTrue(self.state()["items"]["010-a"]["merged"])
        self.assertEqual(len(self.created), 1)   # 新しい What が無いので、2 本目は作らない
        self.put("020-b", deps=["010-a"])   # 前の周期で済んだ What への依存は満たされている
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])
        self.assertEqual(len(self.created), 2)
        self.assertNotIn("T-010-a", self.created[1][1])   # マージ済みは次の PR に載らない

    def test_the_pr_lists_the_unconverged_and_the_frozen(self):
        self.failing = {"T-010-a"}
        self.put("010-a")
        self.put("020-b", deps=["010-a"])
        self.put("030-c")
        self.poll()
        body = self.created[0][1]
        self.assertIn("未収束（1 件）", body)
        self.assertIn("凍結（1 件）", body)
        self.assertIn("020-b", body.split("## 凍結")[1])


class Fence(World):
    def test_whats_with_a_forbidden_or_missing_milestone_stay_in_the_inbox_with_a_reason(self):
        self.put("010-a", milestone="B8.1")
        self.put("020-b", milestone=None)
        self.put("030-c", milestone="B7.4")
        self.poll()
        self.assertEqual(self.inbox_names(), ["010-a.md", "020-b.md"])
        self.assertEqual(self.implemented, ["T-030-c"])
        report = self.report()
        self.assertIn("010-a.md", report)
        self.assertIn("B8.1", report)
        self.assertIn("020-b.md: マイルストーンの宣言がありません", report)

    def test_the_configured_milestones_are_b7_1_to_b7_5(self):
        self.assertEqual(CFG["milestones"], ["B7.1", "B7.2", "B7.3", "B7.4", "B7.5"])


class Freezing(World):
    def test_the_downstream_of_an_unconverged_what_is_frozen_and_calls_no_agent_while_independent_ones_go_on(self):
        self.failing = {"T-020-a"}
        self.put("020-a")
        self.put("030-b", deps=["020-a"])
        self.put("040-c", deps=["030-b"])
        self.put("050-d")
        self.assertEqual(self.poll(), 1)
        self.assertEqual(self.implemented, ["T-020-a", "T-050-d"])
        self.assertEqual([self.status(n) for n in ("020-a", "030-b", "040-c", "050-d")],
                         ["unconverged", "frozen", "frozen", "done"])
        self.assertIn("020-a", (self.inbox / "TODO.md").read_text(encoding="utf-8"))
        self.assertIn("凍結（2 件）: 030-b", self.report())

    def test_the_counts_of_the_attempts_are_two_tries_and_no_respec(self):
        """段を 1 つにしたので、1 つの作業ツリーで 2 回まで。作り直し（再分解）は無い（C5）。"""
        self.assertEqual((CFG["max_attempts"], CFG["respecs"]), (2, 0))


class Resume(World):
    """未収束が重なってもラインは止まらない（連座を外した）。直した What を同じ名前で置くと記録を差し替えて凍結が解ける。"""

    def two_unconverged(self):
        self.failing = {"T-010-a", "T-020-b"}
        self.put("010-a")
        self.put("020-b")
        self.put("030-c", deps=["010-a"])
        self.put("040-d")
        self.put("050-e", milestone="B7.2")

    def test_unconverged_whats_do_not_stop_the_independent_ones_or_the_pr(self):
        self.two_unconverged()
        self.assertEqual(self.poll(), 1)
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b", "T-040-d", "T-050-e"])
        self.assertEqual([self.status(n) for n in ("010-a", "020-b", "030-c", "040-d", "050-e")],
                         ["unconverged", "unconverged", "frozen", "done", "done"])
        self.assertEqual(len(self.created), 1)   # 積むものが尽きたので統合 PR は出る
        self.assertIn("- 状態: 統合 PR 待ち", self.report())

    def test_a_fixed_what_with_the_same_name_replaces_the_record_unfreezes_and_resumes(self):
        self.two_unconverged()
        self.poll()
        self.pr_state = "MERGED"   # 統合 PR が閉じるまでは受信箱を取らない（その検査は IntegrationPr）
        self.failing = set()
        self.put("010-a", extra="直した版")
        self.put("060-new")
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.implemented[4:], ["T-010-a", "T-030-c", "T-060-new"])
        self.assertEqual([self.status(n) for n in ("010-a", "020-b", "030-c")], ["done", "unconverged", "done"])
        self.assertNotIn("010-a", (self.inbox / "TODO.md").read_text(encoding="utf-8"))
        self.assertIn("020-b", (self.inbox / "TODO.md").read_text(encoding="utf-8"))
        self.assertEqual(len(self.created), 2)


class Crash(World):
    def test_a_killed_run_does_not_redo_the_done_ones_and_does_not_lose_the_one_in_progress(self):
        self.put("010-a")
        self.put("020-b")
        self.put("030-c")
        original = self.fake_run_task

        def die_on_b(cfg, tid, what, outdir, first=None, task=None, on_stage=None):
            if hline.title_of(what) == "T-020-b":
                raise KeyboardInterrupt   # 強制終了の代わり（finally が走らない kill でも、状態の記録は同じ）
            return original(cfg, tid, what, outdir, first, task)

        with mock.patch.object(hline, "run_task", side_effect=die_on_b), self.assertRaises(KeyboardInterrupt):
            self.poll()
        st = self.state()
        self.assertEqual([st["items"][n]["status"] for n in ("010-a", "020-b", "030-c")],
                         ["done", "processing", "waiting"])
        self.assertTrue((self.out / "queue" / "020-b.md").exists())
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b", "T-030-c"])   # 010-a は二度実装されず、020-b は取り直される
        self.assertEqual(self.integrated, ["010-a", "020-b", "030-c"])   # 010-a は二度積まれない

    def test_the_one_already_on_the_integration_branch_is_not_processed_again(self):
        self.put("010-a")
        self.put("020-b")
        self.poll()
        st = self.state()
        st["awaiting_pr"] = None
        st["items"]["020-b"]["status"] = "processing"   # push の後、済みの記録の前に落ちた
        for i in st["items"].values():
            i.pop("pr", None)
        hline_queue.save_state(self.cfg, st)
        self.poll()
        self.assertEqual(self.status("020-b"), "done")
        self.assertEqual(self.implemented, ["T-010-a", "T-020-b"])

    def test_a_leftover_lock_from_a_killed_run_is_taken_over_after_the_expiry(self):
        lock = self.inbox / ".hline.lock"
        lock.write_text("9999", encoding="utf-8")
        old = time.time() - self.cfg["ttl_seconds"]["lock_stale"] - 10
        import os
        os.utime(lock, (old, old))
        self.put("010-a")
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a"])
        self.assertFalse(lock.exists())

    def test_an_environment_fault_keeps_the_what_and_the_next_run_takes_it_again(self):
        self.put("010-a")
        with mock.patch.object(hline, "run_task", side_effect=hline.Infra("gh が落ちた")) as task:
            self.assertEqual(self.poll(), 2)
        self.assertEqual(task.call_count, 1 + CFG["infra_retry"]["max_retries"])   # 呼び直しの後に止まる
        self.assertTrue((self.out / "queue" / "010-a.md").exists())
        self.assertEqual(self.status("010-a"), "waiting")
        self.assertFalse((self.inbox / ".hline.lock").exists())
        self.poll()
        self.assertEqual(self.implemented, ["T-010-a"])
        self.assertEqual(self.status("010-a"), "done")

    def test_the_exit_code_of_an_environment_fault_is_2(self):
        with mock.patch.object(hline, "load_config", return_value=self.cfg), \
                mock.patch.object(hline, "poll", side_effect=hline.Infra("x")):
            self.assertEqual(quiet(hline.main, ["poll"]), 2)


STAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"


class ReportEachStep(World):
    """走行の途中でも report.md が今の状態を映す。途中の中身は、差し替えた run_task の中で控える。"""

    def run_with_snapshots(self):
        snaps = []

        def staged_run_task(cfg, tid, what, outdir, first=None, task=None, on_stage=None):
            title = hline.title_of(what)
            for stage in ("実装 1-1", "Gate 1 1-1"):
                on_stage(stage)
                snaps.extend([(stage, self.report()), (f"{stage} {title}", self.report())])
            return self.fake_run_task(cfg, tid, what, outdir, first, task)

        with mock.patch.object(hline, "run_task", side_effect=staged_run_task):
            self.assertEqual(self.poll(), 0)
        return dict(snaps)

    def test_the_report_during_the_run_shows_the_done_ones_and_the_one_in_progress(self):
        self.put("010-a")
        self.put("020-b")
        self.warnings = ["harness/x.py が 501 行です"]   # 不合格にしない指摘は件数だけが済みの行に出る（C2）
        snaps = self.run_with_snapshots()
        first, second = snaps["実装 1-1 T-010-a"], snaps["実装 1-1 T-020-b"]
        self.assertIn("- 済み（0 件）: なし", first)
        self.assertIn("- 待ち（1 件）: 020-b", first)
        self.assertRegex(first, rf"- 処理中（1 件）: 010-a（段階: 実装 1-1／開始: {STAMP}）")
        self.assertIn("- 済み（1 件）: 010-a（警告 1 件）", second)
        self.assertRegex(second, rf"- 処理中（1 件）: 020-b（段階: 実装 1-1／開始: {STAMP}）")

    def test_the_stage_follows_the_implementation_and_gate_1_and_keeps_the_start_time(self):
        self.put("010-a")
        snaps = self.run_with_snapshots()
        pattern = r"- 処理中（1 件）: 010-a（段階: {}／開始: ({})）"
        started = {stage: re.search(pattern.format(stage, STAMP), snaps[stage])
                   for stage in ("実装 1-1", "Gate 1 1-1")}
        self.assertTrue(all(started.values()), started)
        self.assertEqual(started["実装 1-1"].group(1), started["Gate 1 1-1"].group(1))
        self.assertIn("- 処理中（0 件）: なし", self.report())
        item = self.state()["items"]["010-a"]
        self.assertEqual(item["status"], "done")
        self.assertNotIn("stage", item)
        self.assertNotIn("started_at", item)

    def test_an_unconverged_what_leaves_no_stage_behind(self):
        self.failing = {"T-010-a"}
        self.put("010-a")
        self.assertEqual(self.poll(), 1)
        item = self.state()["items"]["010-a"]
        self.assertEqual(item["status"], "unconverged")
        self.assertNotIn("stage", item)
        self.assertNotIn("started_at", item)

    def test_the_report_starts_with_the_progress_report_and_the_h_section_follows(self):
        self.put("010-a")
        snaps = self.run_with_snapshots()
        self.assertTrue(snaps["実装 1-1 T-010-a"].startswith("## 進捗ツリー\n- 人間作業: NONE\n"))
        report = self.report()   # 走行の終わりは統合 PR の待ち。人間作業の行だけが置き換わる
        self.assertTrue(report.startswith("## 進捗ツリー\n- 人間作業: REVIEW_REQUIRED "))
        self.assertLess(report.index("## 進捗ツリー"), report.index("## H ライン"))

    def test_an_environment_fault_stops_the_run_and_the_report_says_so(self):
        self.put("010-a")
        with mock.patch.object(hline, "run_task", side_effect=hline.Infra("gh が落ちた")):
            self.assertEqual(self.poll(), 2)
        report = self.report()
        self.assertIn("- 状態: インフラ例外で停止", report)
        self.assertRegex(report, r"- 人間作業: INFRA_HALTED 010-a: gh が落ちた（\d+ 回の試行）")
        self.assertNotIn("- 人間作業: NONE", report)
        item = self.state()["items"]["010-a"]
        self.assertEqual(item["status"], "waiting")
        self.assertNotIn("stage", item)

    def test_a_queue_without_stage_fields_is_read_and_reported(self):
        st = self.state()
        st["items"] = {"010-a": {"status": "processing", "title": "t", "milestone": "B7.1", "task": None, "deps": []}}
        hline_queue.save_state(self.cfg, st)
        hline_report.write_report(self.cfg, self.state())
        self.assertIn("- 処理中（1 件）: 010-a（段階: 不明／開始: 不明）", self.report())


class Marks(World):
    def st(self):
        item = {"status": "processing", "title": "t", "milestone": "B7.1", "task": None, "deps": ["x"]}
        return {"items": {"010-a": item, "020-b": dict(item, status="waiting")}, "awaiting_pr": None, "skipped": [],
                "infra_halt": None}

    def test_now_has_the_date_and_the_minute_and_today_stays_a_date(self):
        self.assertRegex(hline_report.now(), rf"^{STAMP}$")
        self.assertRegex(hline_report.today(), r"^\d{4}-\d{2}-\d{2}$")

    def test_a_mark_without_a_name_changes_no_item_and_rewrites_the_report_and_the_state(self):
        st = self.st()
        before = json.loads(json.dumps(st))
        hline_report.mark(self.cfg, st)
        self.assertEqual(st, before)
        self.assertEqual(self.state()["items"], before["items"])
        self.assertIn("- 処理中（1 件）: 010-a（段階: 不明／開始: 不明）", self.report())
        self.assertTrue((self.inbox / "TODO.md").exists())

    def test_a_mark_with_a_stage_sets_it_and_keeps_the_first_start_time(self):
        st = self.st()
        with mock.patch.object(hline_report, "now", side_effect=["2026-10-05 04:32", "2026-10-05 04:40"]):
            hline_report.mark(self.cfg, st, "010-a", "分解")
            hline_report.mark(self.cfg, st, "010-a", "実装 1-1")
        item = st["items"]["010-a"]
        self.assertEqual((item["stage"], item["started_at"]), ("実装 1-1", "2026-10-05 04:32"))
        self.assertEqual((item["status"], item["deps"]), ("processing", ["x"]))
        self.assertEqual(list(st["items"]), ["010-a", "020-b"])
        self.assertNotIn("stage", st["items"]["020-b"])
        self.assertIn("010-a（段階: 実装 1-1／開始: 2026-10-05 04:32）", self.report())

    def test_a_mark_with_a_name_only_removes_the_stage_and_the_start_time(self):
        st = self.st()
        hline_report.mark(self.cfg, st, "010-a", "分解")
        hline_report.mark(self.cfg, st, "010-a")
        self.assertNotIn("stage", st["items"]["010-a"])
        self.assertNotIn("started_at", st["items"]["010-a"])

    def test_the_stage_note_survives_missing_fields(self):
        self.assertEqual(hline_report.stage_note({}), "（段階: 不明／開始: 不明）")
        self.assertEqual(hline_report.stage_note({"stage": "分解"}), "（段階: 分解／開始: 不明）")
        self.assertEqual(hline_report.stage_note({"stage": "分解", "started_at": "2026-10-05 04:32"}),
                         "（段階: 分解／開始: 2026-10-05 04:32）")

    def test_writing_the_same_state_twice_gives_the_same_report(self):
        st = self.st()
        hline_report.mark(self.cfg, st, "010-a", "分解")
        once = self.report()
        hline_report.write_report(self.cfg, st)
        self.assertEqual(self.report(), once)


class StageCalls(unittest.TestCase):
    def run_task(self, verdicts, on_stage=True, run=0):
        events, verdicts = [], iter(verdicts)

        def implement(*a):
            events.append("implement")
            return 0, ["m"], {}

        def gate(*a, **kw):
            events.append("gate")
            return next(verdicts), "out"

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline, "new_worktree", side_effect=lambda c, t: (Path(d), "b")), \
                mock.patch.object(hline, "implement", side_effect=implement), \
                mock.patch.object(hline, "changed_paths", return_value=["harness/x.py"]), \
                mock.patch.object(hline, "gate", side_effect=gate):
            kw = {"on_stage": lambda s: events.append(s)} if on_stage else {}
            got = hline.run_task(CFG, "t", {}, Path(d), run=run, **kw)
        return got, events

    def test_each_attempt_announces_the_implementer_and_gate_1_before_calling_them(self):
        (wt, _, tries), events = self.run_task([False, True])
        self.assertEqual(events, ["実装 1-1", "implement", "Gate 1 1-1", "gate", "実装 1-2", "implement", "Gate 1 1-2", "gate"])
        self.assertEqual([(t["run"], t["attempt"], t["gate"]) for t in tries], [(0, 1, False), (0, 2, True)])

    def test_a_run_counts_the_run_in_the_stage(self):
        for run in (0, 1):
            (wt, _, tries), events = self.run_task([False] * CFG["max_attempts"], run=run)
            stages = [e for e in events if e.startswith("実装")]
            self.assertEqual(stages, [f"実装 {run + 1}-{a}" for a in range(1, CFG["max_attempts"] + 1)])
            self.assertEqual({t["run"] for t in tries}, {run})
            self.assertIsNone(wt)

    def test_without_on_stage_nothing_extra_happens(self):
        (wt, _, tries), events = self.run_task([True], on_stage=False)
        self.assertEqual(events, ["implement", "gate"])
        self.assertIsNotNone(wt)


class Lock(unittest.TestCase):
    def write_lock(self, d, body):
        lock = Path(d) / ".hline.lock"
        lock.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        return lock

    def test_the_heartbeat_keeps_the_mtime_fresh_while_it_runs_and_stops_after(self):
        with tempfile.TemporaryDirectory() as d:
            lock = hline.acquire_lock(d, 0.5)
            first = lock.stat().st_mtime
            with hline.heartbeat(lock, 0.1):
                time.sleep(0.5)
                self.assertGreater(lock.stat().st_mtime, first)
                self.assertIsNone(hline.acquire_lock(d, 0.5))
            stopped = lock.stat().st_mtime
            time.sleep(0.3)
            self.assertEqual(lock.stat().st_mtime, stopped)
            self.assertIsNone(hline.acquire_lock(d, 0.5, now=stopped + 1))   # 生きている持ち主は、古くなっても奪わない

    def test_the_lock_body_is_one_json_object_of_the_owner(self):
        with tempfile.TemporaryDirectory() as d:
            record = hline_base.read_lock(hline.acquire_lock(d, 100))
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["token"], hline_base.process_token(os.getpid()))
        self.assertIn("created", record)

    def test_a_dead_owner_is_replaced_at_once_even_with_a_fresh_mtime(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        for how in ("a real exited pid", "a token lookup that finds no process"):
            with self.subTest(how), tempfile.TemporaryDirectory() as d:
                lock = self.write_lock(d, {"pid": child.pid, "token": "x", "created": time.time()})
                if how == "a real exited pid":
                    got = hline.acquire_lock(d, 3600)
                else:
                    self.write_lock(d, {"pid": os.getpid(), "token": "x", "created": 0})
                    with mock.patch.object(hline_base, "process_token", return_value=None):
                        got = hline.acquire_lock(d, 3600)
                self.assertEqual(got, lock)
                self.assertEqual(hline_base.read_lock(lock)["pid"], os.getpid())

    def test_poll_runs_the_line_over_the_lock_of_a_dead_owner(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = json.loads(json.dumps(CFG))
            cfg["inbox"] = d
            self.write_lock(d, {"pid": os.getpid(), "token": "x", "created": time.time()})
            with mock.patch.object(hline_base, "process_token", return_value=None), \
                    mock.patch.object(hline, "run_line", return_value=0) as run:
                self.assertEqual(quiet(hline.poll, cfg), 0)
            run.assert_called_once()
            self.assertFalse((Path(d) / ".hline.lock").exists())

    def test_a_live_owner_keeps_the_lock_even_when_it_is_older_than_the_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            lock = hline.acquire_lock(d, 10)
            self.assertIsNone(hline.acquire_lock(d, 10, now=lock.stat().st_mtime + 11))

    def test_a_reused_pid_is_a_dead_owner(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_lock(d, {"pid": os.getpid(), "token": "someone-else", "created": time.time()})
            self.assertEqual(hline_base.owner_state(hline_base.read_lock(Path(d) / ".hline.lock")), "dead")
            self.assertIsNotNone(hline.acquire_lock(d, 3600))

    def test_a_lock_that_cannot_be_read_falls_back_to_the_time_rule(self):
        bodies = {"old format": "9999", "empty": "", "broken json": "{\"pid\": ", "array": "[1, 2]", "number": "42"}
        for name, body in bodies.items():
            with self.subTest(name, case="fresh"), tempfile.TemporaryDirectory() as d:
                lock = self.write_lock(d, body)
                self.assertIsNone(hline.acquire_lock(d, 100, now=lock.stat().st_mtime + 1))
            with self.subTest(name, case="expired"), tempfile.TemporaryDirectory() as d:
                lock = self.write_lock(d, body)
                self.assertIsNotNone(hline.acquire_lock(d, 100, now=lock.stat().st_mtime + 101))

    def test_an_unknown_token_is_not_a_reason_to_take_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock = self.write_lock(d, {"pid": os.getpid(), "token": "x", "created": 0})
            with mock.patch.object(hline_base, "process_token", return_value=hline_base.UNKNOWN_TOKEN):
                self.assertIsNone(hline.acquire_lock(d, 100, now=lock.stat().st_mtime + 1))
                self.assertIsNotNone(hline.acquire_lock(d, 100, now=lock.stat().st_mtime + 101))

    def test_owner_state_of_a_record_that_has_no_usable_pid_or_token_is_unknown(self):
        for record in (None, [], "1", {}, {"pid": "1", "token": "x"}, {"pid": True, "token": "x"}, {"pid": 1.5}):
            with self.subTest(record=record), mock.patch.object(hline_base, "process_token", return_value="x"):
                self.assertEqual(hline_base.owner_state(record), "unknown")
        with mock.patch.object(hline_base, "process_token", return_value="x"):
            self.assertEqual(hline_base.owner_state({"pid": 1}), "unknown")
            self.assertEqual(hline_base.owner_state({"pid": 1, "token": 5}), "unknown")
            self.assertEqual(hline_base.owner_state({"pid": 1, "token": "x"}), "alive")

    def test_process_token_of_this_process_is_stable_and_an_exited_one_is_none(self):
        if sys.platform != "win32" and not sys.platform.startswith("linux"):
            self.skipTest("識別子を取れないプラットフォーム")
        mine = hline_base.process_token(os.getpid())
        self.assertIsInstance(mine, str)
        self.assertNotEqual(mine, hline_base.UNKNOWN_TOKEN)
        self.assertEqual(mine, hline_base.process_token(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertIsNone(hline_base.process_token(child.pid))

    def test_process_token_reads_starttime_from_proc_stat_on_linux(self):
        stat = "5 (a) b) S " + "0 " * 18 + "424242 0 0"
        with mock.patch.object(hline_base.sys, "platform", "linux"), \
                mock.patch.object(hline_base.Path, "read_text", return_value=stat):
            self.assertEqual(hline_base.process_token(5), "424242")
        with mock.patch.object(hline_base.sys, "platform", "linux"):
            with mock.patch.object(hline_base.Path, "read_text", side_effect=FileNotFoundError):
                self.assertIsNone(hline_base.process_token(5))
            with mock.patch.object(hline_base.Path, "read_text", side_effect=PermissionError):
                self.assertEqual(hline_base.process_token(5), hline_base.UNKNOWN_TOKEN)
            with mock.patch.object(hline_base.Path, "read_text", return_value="garbage"):
                self.assertEqual(hline_base.process_token(5), hline_base.UNKNOWN_TOKEN)

    def test_process_token_is_unknown_on_other_platforms(self):
        with mock.patch.object(hline_base.sys, "platform", "darwin"):
            self.assertEqual(hline_base.process_token(os.getpid()), hline_base.UNKNOWN_TOKEN)

    def test_a_second_start_while_the_run_exceeds_lock_stale_does_not_run_twice(self):
        with tempfile.TemporaryDirectory() as d:
            inbox = Path(d)
            cfg = json.loads(json.dumps(CFG))
            cfg["inbox"] = str(inbox)
            cfg["ttl_seconds"]["lock_stale"] = 1
            started, release, runs = threading.Event(), threading.Event(), []

            def slow(c):
                runs.append(1)
                started.set()
                release.wait(10)
                return 0

            with mock.patch.object(hline, "run_line", side_effect=slow):
                first = threading.Thread(target=quiet, args=(hline.poll, cfg))
                first.start()
                self.assertTrue(started.wait(5))
                time.sleep(1.6)   # lock_stale（1 秒）を超えても走行は続いている
                self.assertEqual(quiet(hline.poll, cfg), 0)
                self.assertEqual(len(runs), 1)
                release.set()
                first.join(10)
            self.assertFalse((inbox / ".hline.lock").exists())


# ============================================================ 実装役の入力（段は 1 つ。分解役は呼ばない）

class AgentFlow(World):
    """実装役は本物の呼び出しの経路を通し、CLI の起動（proc.run）だけを差し替える。"""

    def patch_agents(self):
        self.impl_inputs, self.impl_model = [], "claude-sonnet-5-5"

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            if label == "実装役":
                self.impl_inputs.append(input)
                return 0, claude_json("できた", self.impl_model), ""
            raise AssertionError(f"想定外の呼び出し: {label}")

        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", side_effect=lambda c, w, p, spec=None, task=None, **kw: (True, "ok"))

    def test_the_implementer_gets_the_body_of_the_what(self):
        self.put("010-a", extra="背景：BACKGROUND-TEXT")
        self.assertEqual(self.poll(), 0)
        self.assertEqual(len(self.impl_inputs), 1)
        body = self.impl_inputs[0].split("---\n", 1)[1]
        self.assertIn("BACKGROUND-TEXT", body)
        self.assertIn("T-010-a", body)
        self.assertEqual(self.status("010-a"), "done")
        self.assertIn(self.state()["items"]["010-a"]["tid"], str(list(self.out.iterdir())))

    def test_feedback_goes_to_the_implementer_after_the_what(self):
        prompt = hline.build_prompt("# 題名\n\n## What\n本文", "Gate 1 の出力")
        self.assertIn("Gate 1 の出力", prompt)
        self.assertIn("## What", prompt.split("---\n")[1])
        self.assertLess(prompt.index("## What"), prompt.index("Gate 1 の出力"))

    def test_the_implementer_model_is_checked_and_recorded(self):
        self.put("010-a")
        self.poll()
        self.assertEqual(self.state()["items"]["010-a"]["tries"][0]["models"], ["claude-sonnet-5-5"])

    def test_a_run_on_another_model_stops_as_an_environment_fault_and_keeps_the_what(self):
        self.impl_model = "claude-opus-5"
        self.put("010-a")
        self.assertEqual(self.poll(), 2)
        self.assertTrue((self.out / "queue" / "010-a.md").exists())
        self.assertEqual(self.status("010-a"), "waiting")


# ============================================================ 設定・規模・書式

class Config(unittest.TestCase):
    def test_the_decomposer_is_pinned_to_an_exact_model_id(self):
        dec = CFG["decomposer"]
        self.assertEqual((dec["model_flag"], dec["model"]), ("--model", "claude-opus-5"))
        args = hline.implementer_args(dec, ["claude"])
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5")
        self.assertEqual(model_pin.require(dec, "decomposer"), "claude-opus-5")
        self.assertIn("config/hline.json の decomposer", [w for w, _, _ in model_pin.agent_configs()])
        self.assertEqual([p for p in model_pin.problems() if "hline" in p], [])

    def test_a_decomposer_without_a_model_does_not_load(self):
        with tempfile.TemporaryDirectory() as d:
            bad = json.loads(json.dumps(CFG))
            del bad["decomposer"]["model"]
            p = Path(d) / "hline.json"
            p.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(model_pin.ModelPinError):
                hline.load_config(p)

    def test_the_decomposer_pin_is_inspected_with_the_other_agents(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            bad = json.loads(json.dumps(CFG))
            del bad["decomposer"]["model_flag"]
            (Path(d) / "config" / "hline.json").write_text(json.dumps(bad), encoding="utf-8")
            found = model_pin.problems(d)
        self.assertEqual(len(found), 1)
        self.assertIn("model_flag", found[0])

    def test_the_line_size_limits_hold(self):
        self.assertLessEqual(len((REPO / "harness" / "hline.py").read_text(encoding="utf-8").splitlines()), 500)
        modules = sorted((REPO / "harness").glob("hline_*.py"))
        self.assertGreaterEqual(len(modules), 4)
        for m in modules:
            with self.subTest(module=m.name):
                self.assertLessEqual(len(m.read_text(encoding="utf-8").splitlines()), 500)

    def test_the_progress_verification_of_s1_1_is_this_file(self):
        state = progress.load(REPO / "docs" / "progress.yaml")
        self.assertEqual(progress.task(state, "S1-1")["verification"],
                         {"command": "python -m unittest tests.test_hline_queue", "expected_exit_code": 0})

    def test_the_director_room_template_documents_the_declarations(self):
        text = hline.director_settings.__module__ and (REPO / "harness" / "templates" / "director_room" / "CLAUDE.md").read_text(
            encoding="utf-8")
        for word in ("マイルストーン:", "タスク:", "依存:", "統合 PR"):
            self.assertIn(word, text)
        self.assertNotIn("BLOCKED", text)   # 連座は外した（C1）

    def test_the_tests_of_this_file_make_no_real_external_call(self):
        """World は proc.run・proc.resolve_cli を差し替え、想定外の呼び出しを落とす。この検査はその前提を確かめる。"""
        class Probe(World):
            def runTest(self):
                with self.assertRaises(AssertionError):
                    hline.proc.run(["git", "push"], ".", 1, "push")
                with self.assertRaises(AssertionError):
                    hline.proc.resolve_cli("gh")

        probe = Probe()
        probe.setUp()
        try:
            probe.runTest()
        finally:
            probe.doCleanups()


if __name__ == "__main__":
    unittest.main()
