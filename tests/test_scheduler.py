"""scheduler の状態遷移と異常系（移設前は unity-2d/tools/test_ms4_scheduler.py）。

    python -m unittest discover -s tests -v

本物の GitHub・claude・agy・Unity は使わない。

  git     本物。一時ディレクトリに bare リモートと clone を作る（ブランチ・マージ・
          汚れの扱いは git の実挙動で確かめないと意味がない）
  GitHub  FakeGH。gh の引数を解釈してメモリ上の Issue を書き換える。
          GitHub クラスの引数組み立て・JSON 解釈・再試行はそのまま通る
  子      stub.py。decompose / audit / pipeline の代わりに、Issue 番号ごとの
          指示どおりにファイルを書き、終了コードを返す

一時ディレクトリは C:\\src\\.local\\out\\harness\\selftest\\ の下（リポジトリ直下に置かない）。
環境変数 HARNESS_SELFTEST_DIR で差し替えられる（CI のランナー用）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HERE))

import exitcode  # noqa: E402
import project  # noqa: E402
import scheduler as ms4  # noqa: E402

SELFTEST_BASE = Path(os.environ.get("HARNESS_SELFTEST_DIR", r"C:\src\.local\out\harness\selftest"))
SLUG = "owner/repo"

STUB = r'''
import json, os, subprocess, sys, time
from pathlib import Path

role, arg = sys.argv[1], sys.argv[2]
plan = json.loads(Path(os.environ["STUB_PLAN"]).read_text(encoding="utf-8"))

def number():
    if role == "decompose":
        return arg
    return "".join(ch for ch in Path(arg).stem if ch.isdigit())

n = number()
mode = plan.get(role, {}).get(n, "ok")
if role == "audit":
    mode = plan.get("audit", {}).get(n, "ok")

def write(rel, text):
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

def git(*a):
    subprocess.run(["git"] + list(a), check=True, capture_output=True)

print(f"stub {role} #{n} mode={mode}")

if role == "decompose":
    if mode in ("ok", "stray", "no_test_methods"):
        write(f"tools/units/issue_{n}.json",
              json.dumps({"id": f"issue_{n}", "human_check_point": "実機で見る点"}, ensure_ascii=False))
        if mode == "no_test_methods":
            write(f"tests/Core.Tests/Issue{n}Tests.cs", "// [Test] はコメントの中だけ\n")
        else:
            write(f"tests/Core.Tests/Issue{n}Tests.cs",
                  '[Test] public void Works() { Assert.AreEqual(1, Answer(), "答えは 1"); }\n')
        if mode == "stray":
            write("Game/Assets/Core/Sneaky.cs", "// not allowed\n")
        sys.exit(0)
    if mode == "reject":
        print("不合格の理由: 自明アサーション")
        sys.exit(1)
    if mode == "reject_dirty":
        write(f"tests/Core.Tests/Issue{n}Tests.cs", "// half\n")
        sys.exit(1)
    if mode == "abort":
        sys.exit(2)
    if mode == "rc3":
        sys.exit(3)

if role == "audit":
    if mode == "ok":
        write(f"reports/audits/audit_{Path(arg).stem}.md", "# audit\n")
        sys.exit(0)
    if mode == "fail":
        sys.exit(1)

if role == "pipeline":
    if mode == "ok":
        write(f"Game/Assets/Core/Issue{n}.cs", "// impl\n")
        git("add", "--", f"Game/Assets/Core/Issue{n}.cs")
        git("commit", "-m", f"impl {n}")
        git("push")
        sys.exit(0)
    if mode == "reject":
        print("試行3: REJECT テストが赤")
        sys.exit(1)
    if mode == "abort_dirty":
        write("Game/Assets/Core/Half.cs", "// half\n")
        sys.exit(2)
    if mode == "abort":
        sys.exit(2)
    if mode == "rc3":
        sys.exit(3)
    if mode == "sleep":
        time.sleep(120)
        sys.exit(0)
    if mode == "conflict":
        write("README.md", "branch side\n")
        git("commit", "-am", "branch edits README")
        git("push")
        helper = plan["helper"]
        Path(helper, "README.md").write_text("main side\n", encoding="utf-8")
        subprocess.run(["git", "-C", helper, "commit", "-am", "main edits README"], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "push", "origin", "main"], check=True, capture_output=True)
        sys.exit(0)

print("stub: 未知のモード")
sys.exit(9)
'''


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


# ============================================================ 偽 GitHub

class FakeGH:
    """gh の引数を解釈する偽物。PR のマージだけは helper clone で本物の git merge を行い、
    bare リモートの main に実際に入れる（マージ方式・head の照合を git の実挙動で確かめる）。"""

    def __init__(self, issues):
        self.issues = {n: {"title": t, "state": "OPEN", "labels": {"ready"}, "comments": []}
                       for n, t in issues}
        self.prs = {}
        self.labels = {"ready"}
        self.ci_conclusion = "success"
        self.ci_missing = False
        self.runs = {}
        self.calls = []
        self.fail_rules = []   # {"match": "issue comment", "times": 1, "apply": bool}
        self.helper = None     # Base.run_scheduler が設定する
        self.check_override = {}   # name -> (status, conclusion)
        self.extra_check_runs = []
        self.before_merge = None
        self.merge_args = []

    def writes(self):
        verbs = {"edit", "comment", "close", "create", "merge"}
        return [c for c in self.calls if len(c) > 2 and c[2] in verbs]

    def approve(self, pr_number):
        self.prs[pr_number]["labels"].add("ms4:approved")

    def pr_for_issue(self, n):
        return next(num for num, p in self.prs.items() if p["branch"] == f"ms4/issue-{n}")

    def _git(self, *args, check=True):
        r = subprocess.run(["git"] + list(args), cwd=str(self.helper), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if check and r.returncode != 0:
            raise RuntimeError(f"helper git {args}: {r.stderr}")
        return r

    def head_of(self, branch):
        self._git("fetch", "-q", "origin")
        return self._git("rev-parse", f"origin/{branch}").stdout.strip()

    def _pr_json(self, num):
        p = self.prs[num]
        return {"number": num, "state": p["state"], "headRefName": p["branch"],
                "headRefOid": self.head_of(p["branch"]),
                "labels": [{"name": l} for l in sorted(p["labels"])],
                "comments": [{"body": b} for b in p["comments"]],
                "mergeCommit": {"oid": p["merge_sha"]} if p["merge_sha"] else None}

    def __call__(self, args, cwd, ttl):
        assert args[0] == "gh", args
        if args[1] == "api":
            a = args[1:]
        else:
            assert args[-2:] == ["--repo", SLUG], args
            a = args[1:-2]
        self.calls.append(args)
        key = " ".join(a[:2])
        for rule in self.fail_rules:
            if rule["times"] > 0 and rule["match"] == key:
                rule["times"] -= 1
                if rule.get("apply"):
                    self._do(a)
                return 1, "", "HTTP 502: simulated"
        return self._do(a)

    def _opt(self, a, name):
        vals = []
        for i, x in enumerate(a):
            if x == name:
                vals.append(a[i + 1])
        return vals

    def _do(self, a):
        key = " ".join(a[:2])
        if key == "issue list":
            label = self._opt(a, "--label")[0]
            out = [{"number": n, "title": d["title"], "labels": [{"name": l} for l in sorted(d["labels"])]}
                   for n, d in self.issues.items() if d["state"] == "OPEN" and label in d["labels"]]
            return 0, json.dumps(out), ""
        if key == "issue view":
            d = self.issues[int(a[2])]
            return 0, json.dumps({"state": d["state"],
                                  "labels": [{"name": l} for l in d["labels"]],
                                  "comments": [{"body": b} for b in d["comments"]]}), ""
        if key == "issue edit":
            d = self.issues[int(a[2])]
            d["labels"] |= set(self._opt(a, "--add-label"))
            d["labels"] -= set(self._opt(a, "--remove-label"))
            return 0, "", ""
        if key == "issue comment":
            self.issues[int(a[2])]["comments"].append(self._opt(a, "--body")[0])
            return 0, "", ""
        if key == "issue close":
            self.issues[int(a[2])]["state"] = "CLOSED"
            return 0, "", ""
        if key == "label list":
            return 0, json.dumps([{"name": l} for l in sorted(self.labels)]), ""
        if key == "label create":
            self.labels.add(a[2])
            return 0, "", ""
        if key == "run list":
            if self.ci_missing:
                return 0, "[]", ""
            sha = self._opt(a, "--commit")[0]
            rid = self.runs.setdefault(sha, 9000 + len(self.runs))
            return 0, json.dumps([{"databaseId": rid, "status": "completed",
                                   "conclusion": self.ci_conclusion}]), ""
        if key == "run view":
            return 0, json.dumps({"status": "completed", "conclusion": self.ci_conclusion}), ""

        # ---- PR
        if key == "pr list":
            open_prs = [(num, p) for num, p in sorted(self.prs.items()) if p["state"] == "OPEN"]
            if self._opt(a, "--head"):
                head = self._opt(a, "--head")[0]
                open_prs = [(num, p) for num, p in open_prs if p["branch"] == head]
            if self._opt(a, "--label"):
                label = self._opt(a, "--label")[0]
                open_prs = [(num, p) for num, p in open_prs if label in p["labels"]]
            return 0, json.dumps([{"number": num, "headRefName": p["branch"]} for num, p in open_prs]), ""
        if key == "pr create":
            num = 100 + len(self.prs)
            self.prs[num] = {"branch": self._opt(a, "--head")[0], "base": self._opt(a, "--base")[0],
                             "title": self._opt(a, "--title")[0], "body": self._opt(a, "--body")[0],
                             "state": "OPEN", "labels": set(), "comments": [], "merge_sha": None}
            return 0, f"https://github.com/{SLUG}/pull/{num}\n", ""
        if key == "pr view":
            return 0, json.dumps(self._pr_json(int(a[2]))), ""
        if key == "pr edit":
            p = self.prs[int(a[2])]
            p["labels"] |= set(self._opt(a, "--add-label"))
            p["labels"] -= set(self._opt(a, "--remove-label"))
            return 0, "", ""
        if key == "pr comment":
            self.prs[int(a[2])]["comments"].append(self._opt(a, "--body")[0])
            return 0, "", ""
        if key == "pr close":
            self.prs[int(a[2])]["state"] = "CLOSED"
            return 0, "", ""
        if key == "pr merge":
            num = int(a[2])
            p = self.prs[num]
            self.merge_args.append(list(a))
            if self.before_merge:
                hook, self.before_merge = self.before_merge, None
                hook()
            head = self.head_of(p["branch"])
            want = self._opt(a, "--match-head-commit")
            if want and want[0] != head:
                return 1, "", "GraphQL: Head branch was modified. Review and try the merge again."
            self._git("checkout", "-q", "main")
            self._git("reset", "-q", "--hard", "origin/main")
            mode = "--no-ff" if "--merge" in a else "--squash"
            r = self._git("merge", mode, "-m", f"Merge pull request #{num}", f"origin/{p['branch']}",
                          check=False)
            if r.returncode != 0:
                self._git("merge", "--abort", check=False)
                return 1, "", "Pull request is not mergeable: conflict"
            if mode == "--squash":
                self._git("commit", "-q", "-m", f"squash #{num}")
            self._git("push", "-q", "origin", "main")
            p["state"] = "MERGED"
            p["merge_sha"] = self._git("rev-parse", "HEAD").stdout.strip()
            for m in re.finditer(r"Closes #(\d+)", p["body"]):
                if int(m.group(1)) in self.issues:
                    self.issues[int(m.group(1))]["state"] = "CLOSED"
            return 0, "", ""
        if key.startswith("api repos/") and "/check-runs" in key:
            sha = key.split("/commits/")[1].split("/")[0]
            pr = next((p for p in self.prs.values() if p["state"] == "OPEN"
                       and self.head_of(p["branch"]) == sha), None)
            approved = bool(pr and "ms4:approved" in pr["labels"])
            runs = [{"id": 1000, "name": "test", "status": "completed", "conclusion": "success"},
                    {"id": 1001, "name": "approval", "status": "completed",
                     "conclusion": "success" if approved else "failure"}]
            for i, (name, (status, conclusion)) in enumerate(self.check_override.items()):
                runs.append({"id": 2000 + i, "name": name, "status": status, "conclusion": conclusion})
            runs += self.extra_check_runs
            return 0, json.dumps({"check_runs": runs}), ""
        return 1, "", f"FakeGH: 未対応 {a}"


# ============================================================ 足場

class Base(unittest.TestCase):
    def setUp(self):
        SELFTEST_BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="t-", dir=SELFTEST_BASE))
        self.remote = self.tmp / "remote.git"
        self.work = self.tmp / "work"
        self.helper = self.tmp / "helper"
        git(self.tmp, "init", "--bare", "-b", "main", str(self.remote))
        git(self.tmp, "clone", str(self.remote), str(self.work))
        for repo in (self.work,):
            git(repo, "config", "user.name", "t")
            git(repo, "config", "user.email", "t@example.invalid")
        (self.work / "README.md").write_text("base\n", encoding="utf-8")
        (self.work / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git(self.work, "add", ".")
        git(self.work, "commit", "-m", "init")
        git(self.work, "push", "-u", "origin", "main")
        git(self.tmp, "clone", str(self.remote), str(self.helper))
        git(self.helper, "config", "user.name", "h")
        git(self.helper, "config", "user.email", "h@example.invalid")

        (self.tmp / "stub.py").write_text(STUB, encoding="utf-8")
        self.plan = {"helper": str(self.helper)}

        # 存在しない深い出力先から始める（起動時に作れること）
        self.out = self.tmp / "deep" / "nested" / "out"
        stub = str(self.tmp / "stub.py")
        self.cfg = {
            "repo_slug": SLUG,
            "repo_dir": str(self.work),
            "base_branch": "main",
            "branch_prefix": "ms4/issue-",
            "out_dir": str(self.out),
            "labels": {
                "ready": {"name": "ready", "color": "FEF2C0", "description": "r"},
                "running": {"name": "ms4:running", "color": "1D76DB", "description": "x"},
                "failed": {"name": "ms4:failed", "color": "D93F0B", "description": "f"},
                "awaiting": {"name": "ms4:awaiting-approval", "color": "FBCA04", "description": "w"},
                "approved": {"name": "ms4:approved", "color": "0E8A16", "description": "a"},
                "declined": {"name": "ms4:declined", "color": "B60205", "description": "d"},
            },
            "required_checks": ["test", "approval"],
            "required_clis": ["git"],
            "commands": {
                "decompose": ["{python}", stub, "decompose", "{number}"],
                "audit": ["{python}", stub, "audit", "{file}"],
                "pipeline": ["{python}", stub, "pipeline", "{unit}"],
            },
            "unit_path_template": "tools/units/issue_{number}.json",
            "test_dir": "tests/Core.Tests",
            "audit_dir": "reports/audits",
            "audit": {"required": False, "key_env": "MS4_TEST_AUDIT_KEY"},
            "ttl_seconds": {"git": 60, "gh": 10, "decompose": 60, "audit": 60, "pipeline": 60},
            "ci_find_seconds": 3, "ci_watch_seconds": 3, "ci_poll_interval_seconds": 1,
            "net_retries": 2, "net_retry_interval_seconds": 5,
            "comment_log_tail_chars": 800,
            "max_issues_per_run": 5,
        }
        self.sleeps = []
        self.env = mock.patch.dict(os.environ, {"STUB_PLAN": str(self.tmp / "plan.json")})
        self.env.start()
        os.environ.pop("MS4_TEST_AUDIT_KEY", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))

    def run_scheduler(self, gh, *extra):
        gh.helper = self.helper
        (self.tmp / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        cfg_path = self.tmp / "ms4.config.json"
        cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")
        return ms4.main(["--config", str(cfg_path)] + list(extra),
                        gh_run=gh, sleep=self.sleeps.append)

    # ---- 観測
    def runs(self):
        p = self.out / "runs.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]

    def branch(self):
        return git(self.work, "branch", "--show-current")

    def dirty(self):
        return git(self.work, "status", "--porcelain", "--untracked-files=all")

    def remote_has_branch(self, b):
        return bool(git(self.work, "ls-remote", "--heads", "origin", b))

    def local_has_branch(self, b):
        return bool(git(self.work, "branch", "--list", b))

    def remote_main_files(self):
        git(self.work, "fetch", "origin")
        return set(git(self.work, "ls-tree", "-r", "--name-only", "origin/main").splitlines())

    @property
    def lock(self):
        return self.out / "scheduler.lock"


# ============================================================ 終了コード

class ExitCodeTests(unittest.TestCase):
    def test_normalization_does_not_swallow_normal_exits(self):
        def boom():
            raise ValueError("x")
        cases = {
            "return 0": (lambda: 0, 0),
            "return 1": (lambda: 1, 1),
            "return 2": (lambda: 2, 2),
            "return None": (lambda: None, 0),
            "sys.exit(0)": (lambda: sys.exit(0), 0),
            "sys.exit(1)": (lambda: sys.exit(1), 1),
            "sys.exit()": (lambda: sys.exit(), 0),
            "sys.exit(str)": (lambda: sys.exit("ABORT: x"), 2),
            "exception": (boom, 2),
        }
        with mock.patch("sys.stderr"), mock.patch("traceback.print_exc"):
            for name, (fn, want) in cases.items():
                with self.subTest(name):
                    self.assertEqual(exitcode.normalized(fn), want)

    def test_real_pipeline_abort_is_rc2(self):
        """修正前は rc=1（REJECT と区別できなかった）。require_unit_safe で止まり、何も変えない。"""
        with tempfile.TemporaryDirectory(dir=SELFTEST_BASE if SELFTEST_BASE.exists() else None) as d:
            unit = Path(d) / "bad.json"
            unit.write_text(json.dumps({
                "id": "neg", "title": "neg", "prompt": "x", "max_impl_lines": 1,
                "whitelist": ["tests/Core.Tests/XTests.cs"],
                "acceptance": {"required_tests": ["XTests"]}}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(HERE / "pipeline.py"), "--project", "unity-2d", "--unit", str(unit)],
                               capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2)


# ============================================================ プロジェクト設定

class ProjectConfigTests(unittest.TestCase):
    """道具は 1 本、ゲームごとの違いは設定だけ。値を 2 か所に書かせない。"""

    def test_unity_2d_builds_a_complete_scheduler_config(self):
        cfg = ms4.build_config("unity-2d")
        self.assertEqual(cfg["repo_slug"], "tamaroulet/unity-2d")
        self.assertEqual(cfg["unit_path_template"], "tools/units/issue_{number}.json")
        self.assertEqual(cfg["test_dir"], "tests/Core.Tests")
        self.assertEqual(cfg["audit_dir"], "reports/audits")
        for k in ms4.PROJECT_KEYS:
            self.assertIn(k, cfg)
        # Scheduler がそのまま受け取れること（GitHub・git には触れない）
        ms4.Scheduler(cfg, gh_run=lambda *a: (1, "", "not called"), sleep=lambda s: None)

    def test_commands_expand_to_harness_scripts(self):
        cfg = ms4.build_config("unity-2d")
        for name, script in (("decompose", "decompose.py"), ("audit", "audit.py"),
                             ("pipeline", "pipeline.py")):
            args = [a.format(python="py", harness=project.HARNESS_DIR.as_posix(),
                             project="unity-2d", number=5, file="f", unit="u")
                    for a in cfg["commands"][name]]
            self.assertTrue(Path(args[1]).name == script and Path(args[1]).exists(), args)
            self.assertEqual(args[2:4], ["--project", "unity-2d"])

    def test_duplicated_project_key_in_common_config_is_rejected(self):
        real = project.config
        def fake(name):
            d = real(name)
            if name == "scheduler":
                d["repo_dir"] = r"C:\somewhere"
            return d
        with mock.patch.object(project, "config", side_effect=fake):
            with self.assertRaises(project.ProjectError):
                ms4.build_config("unity-2d")

    def test_pipeline_config_injects_repo_and_rejects_restated_paths(self):
        p = project.load("unity-2d")
        cfg = project.pipeline_config(p)
        self.assertEqual(cfg["paths"]["repo"], p["repo_dir"])
        self.assertEqual(cfg["paths"]["unity_project_subdir"], "Game")

        real = project._read
        def restated(path):
            d = real(path)
            if Path(path).name == "pipeline.json":
                d["paths"]["repo"] = r"C:\elsewhere"
            return d
        with mock.patch.object(project, "_read", side_effect=restated):
            with self.assertRaises(project.ProjectError):
                project.pipeline_config(p)

    def test_unknown_project_names_the_known_ones(self):
        with self.assertRaises(project.ProjectError) as ctx:
            project.load("no-such-game")
        self.assertIn("unity-2d", str(ctx.exception))

    def test_decompose_takes_repo_values_from_project(self):
        import decompose
        decompose.configure("unity-2d")
        self.assertEqual(str(decompose.ROOT), r"C:\src\unity-2d")
        self.assertEqual(decompose.CFG["repo"], "tamaroulet/unity-2d")
        self.assertEqual(decompose.CFG["units_dir"], "tools/units")
        self.assertIn(r"\harness\unity-2d\decompose\prompt.md", decompose.CFG["prompt_file"])

    def test_no_subprocess_call_without_timeout(self):
        """TTL の無い子プロセス呼び出しは、常駐時に沈黙したまま固まる原因になる。

        subprocess.run / check_output / call は timeout= を必須にする。
        Popen は wait(timeout=) で待つ形になっていることを run_logged で担保する。
        """
        import ast
        offenders = []
        for py in sorted(HERE.glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                        and node.func.attr in ("run", "check_output", "call", "check_call")):
                    if not any(k.arg == "timeout" for k in node.keywords):
                        offenders.append(f"{py.name}:{node.lineno} subprocess.{node.func.attr}")
        self.assertEqual(offenders, [])


# ============================================================ 状態遷移

class SchedulerTests(Base):
    # 1
    def test_no_ready_issues(self):
        gh = FakeGH([])
        head = git(self.work, "rev-parse", "HEAD")
        self.assertFalse(self.out.exists())
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertTrue(self.out.exists())
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertFalse(self.lock.exists())

    # 2
    def test_happy_path_waits_for_approval_then_merges(self):
        gh = FakeGH([(5, "タメ")])

        # ---- 1 周目: 門を通って PR を作り、承認待ちで止まる（マージしない）
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertNotIn("Game/Assets/Core/Issue5.cs", self.remote_main_files(), "承認前にマージしない")
        pr_num = gh.pr_for_issue(5)
        pr = gh.prs[pr_num]
        self.assertEqual(pr["state"], "OPEN")
        self.assertEqual(pr["labels"], {"ms4:awaiting-approval"})
        self.assertIn("Closes #5", pr["body"])
        self.assertEqual(gh.issues[5]["labels"], {"ms4:awaiting-approval"})
        head = gh.head_of("ms4/issue-5")
        request = [c for c in pr["comments"] if f"ms4:approval-request:{head}" in c]
        self.assertEqual(len(request), 1, "今の head SHA に対する承認依頼が 1 件")
        self.assertIn("`Works`", request[0])
        self.assertIn("「答えは 1」", request[0])
        self.assertIn("実機で見る点", request[0])
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.lock.exists())
        self.assertEqual([r["result"] for r in self.runs()], ["AWAITING"])

        # ---- 2 周目: 人間が承認 → 必須チェック緑 → マージコミットで入る
        gh.approve(pr_num)
        self.assertEqual(self.run_scheduler(gh), 0)

        files = self.remote_main_files()
        self.assertIn("tests/Core.Tests/Issue5Tests.cs", files)
        self.assertIn("tools/units/issue_5.json", files)
        self.assertIn("Game/Assets/Core/Issue5.cs", files)
        parents = git(self.work, "rev-list", "--parents", "-n", "1", "origin/main").split()
        self.assertEqual(len(parents), 3, "squash ではなくマージコミットであること")
        self.assertEqual(len(gh.merge_args), 1)
        self.assertIn("--merge", gh.merge_args[0])
        self.assertNotIn("--squash", gh.merge_args[0])
        self.assertEqual(gh.merge_args[0][gh.merge_args[0].index("--match-head-commit") + 1], head)

        issue = gh.issues[5]
        self.assertEqual(issue["state"], "CLOSED")
        self.assertEqual(issue["labels"], set())
        self.assertEqual(sum("合格" in c for c in issue["comments"]), 1)
        self.assertEqual(gh.prs[pr_num]["state"], "MERGED")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), gh.prs[pr_num]["merge_sha"],
                         "ローカルの main もマージ後に追従する")
        self.assertFalse(self.local_has_branch("ms4/issue-5"))
        self.assertFalse(self.lock.exists())

        runs = self.runs()
        self.assertEqual([r["result"] for r in runs], ["AWAITING", "PASSED"])
        self.assertIn("skipped", runs[0]["audit"])
        self.assertEqual([s["name"] for s in runs[0]["steps"]], ["decompose", "pipeline"])
        self.assertTrue(runs[1]["ci_run"])

    def test_scheduler_never_applies_the_approval_label(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        self.run_scheduler(gh)
        for call in gh.calls:
            for i, x in enumerate(call[:-1]):
                if x == "--add-label":
                    self.assertNotEqual(call[i + 1], "ms4:approved", call)

    def test_unapproved_pr_just_waits(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[gh.pr_for_issue(5)]["state"], "OPEN")
        self.assertEqual([r["result"] for r in self.runs()], ["AWAITING"], "再取得も記録もしない")
        self.assertEqual(sum(1 for c in gh.calls if c[1:3] == ["pr", "create"]), 1)

    def test_approved_but_required_check_pending_does_not_merge(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        gh.check_override = {"test": ("in_progress", None)}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])
        self.assertEqual(gh.issues[5]["state"], "OPEN")

    def test_label_without_green_approval_check_does_not_merge(self):
        """ラベルは付いていても、approval チェックが赤（承認者以外が付けた等）ならマージしない。"""
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        gh.check_override = {"approval": ("completed", "failure")}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])

    def test_misconfigured_project_without_approval_check_still_needs_the_label(self):
        """required_checks から approval が抜けた誤設定でも、人間のラベル無しではマージしない。

        必須チェック approval が効いている間はスケジューラ側の確認と二重になるが、
        設定を誤るとスケジューラ側の確認が最後の防壁になる（変異 M28 で実測）。
        """
        self.cfg["required_checks"] = ["test"]
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])
        self.assertEqual(gh.prs[gh.pr_for_issue(5)]["state"], "OPEN")

    def test_latest_check_run_wins_by_id_not_by_order(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        gh.extra_check_runs = [{"id": 1, "name": "test", "status": "completed", "conclusion": "failure"}]
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[gh.pr_for_issue(5)]["state"], "MERGED")

        gh2 = FakeGH([(7, "b")])
        self.run_scheduler(gh2)
        gh2.approve(gh2.pr_for_issue(7))
        gh2.extra_check_runs = [{"id": 99999, "name": "test", "status": "completed", "conclusion": "failure"}]
        self.assertEqual(self.run_scheduler(gh2), 0)
        self.assertEqual(gh2.prs[gh2.pr_for_issue(7)]["state"], "OPEN", "新しい run が赤ならマージしない")

    def test_push_after_approval_is_not_merged(self):
        """承認した SHA 以外は入れない。承認後に push されたら待ちに戻す（ABORT しない）。"""
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        pr_num = gh.pr_for_issue(5)
        gh.approve(pr_num)

        def push_extra_commit():
            git(self.helper, "fetch", "-q", "origin")
            git(self.helper, "switch", "-q", "-c", "late", "origin/ms4/issue-5")
            (self.helper / "late.txt").write_text("late\n", encoding="utf-8")
            git(self.helper, "add", "late.txt")
            git(self.helper, "commit", "-q", "-m", "late push")
            git(self.helper, "push", "-q", "origin", "late:ms4/issue-5")
            git(self.helper, "switch", "-q", "main")
        gh.before_merge = push_extra_commit

        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[pr_num]["state"], "OPEN")
        self.assertNotIn("late.txt", self.remote_main_files())
        self.assertFalse(self.lock.exists(), "ABORT ではない")
        self.assertEqual(gh.issues[5]["state"], "OPEN")

    def test_declined_pr_is_closed_and_issue_fails(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        pr_num = gh.pr_for_issue(5)
        gh.prs[pr_num]["labels"].add("ms4:declined")
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertEqual(gh.prs[pr_num]["state"], "CLOSED")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:failed"})
        self.assertEqual(gh.merge_args, [])
        self.assertTrue(self.remote_has_branch("ms4/issue-5"), "ブランチは残す")
        self.assertEqual([r["result"] for r in self.runs()], ["AWAITING", "REJECT"])

    def test_empty_test_list_is_never_sent_for_approval(self):
        gh = FakeGH([(5, "a")])
        self.plan["decompose"] = {"5": "no_test_methods"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.prs, {})
        self.assertIn("1 件も読み取れません", self.runs()[0]["reason"])

    # 3
    def test_decompose_reject_then_next_issue_passes(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["decompose"] = {"5": "reject"}
        self.assertEqual(self.run_scheduler(gh), 1)

        i5 = gh.issues[5]
        self.assertEqual(i5["labels"], {"ms4:failed"})
        self.assertEqual(i5["state"], "OPEN")
        self.assertTrue(any("不合格の理由: 自明アサーション" in c for c in i5["comments"]),
                        "ログ末尾が UTF-8 のままコメントされること")
        self.assertFalse(self.local_has_branch("ms4/issue-5"))
        self.assertEqual(gh.issues[6]["labels"], {"ms4:awaiting-approval"}, "次の Issue へ進む")
        self.assertEqual([r["result"] for r in self.runs()], ["REJECT", "AWAITING"])

    # 4
    def test_decompose_abort_stops_everything(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["decompose"] = {"5": "abort"}
        self.assertEqual(self.run_scheduler(gh), 2)

        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})
        self.assertTrue(any("ABORT" in c for c in gh.issues[5]["comments"]))
        self.assertEqual(gh.issues[6]["labels"], {"ready"})
        self.assertEqual(gh.issues[6]["comments"], [])
        self.assertTrue(self.lock.exists(), "着手後の ABORT ではロックを残す")
        self.assertEqual(self.branch(), "ms4/issue-5", "ブランチを戻さない（触らない）")
        self.assertEqual([r["result"] for r in self.runs()], ["ABORT"])

    def test_decompose_reject_leaving_dirty_tree_escalates_to_abort(self):
        gh = FakeGH([(5, "a")])
        self.plan["decompose"] = {"5": "reject_dirty"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertIn("Issue5Tests.cs", self.dirty())
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})

    # 5
    def test_pipeline_reject_keeps_branch_and_continues(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["pipeline"] = {"5": "reject"}
        self.assertEqual(self.run_scheduler(gh), 1)

        self.assertEqual(gh.issues[5]["labels"], {"ms4:failed"})
        self.assertTrue(self.local_has_branch("ms4/issue-5"))
        self.assertTrue(self.remote_has_branch("ms4/issue-5"))
        self.assertNotIn("tests/Core.Tests/Issue5Tests.cs", self.remote_main_files())
        self.assertEqual(gh.issues[6]["labels"], {"ms4:awaiting-approval"})
        self.assertEqual(self.branch(), "main")

    # 6
    def test_pipeline_abort_leaves_evidence_untouched(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["pipeline"] = {"5": "abort_dirty"}
        self.assertEqual(self.run_scheduler(gh), 2)

        self.assertTrue((self.work / "Game/Assets/Core/Half.cs").exists(), "汚れを掃除しない")
        self.assertEqual(self.branch(), "ms4/issue-5")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})
        self.assertEqual(gh.issues[6]["labels"], {"ready"})
        self.assertTrue(self.lock.exists())

    def test_pipeline_abort_with_clean_tree_still_stops(self):
        """汚れが無い rc=2（例: パイプライン内で CI の run が見つからない）。

        汚れを残す ABORT だけを試すと、rc=2 を不合格と取り違えても後始末の
        汚れ検査が ABORT に格上げしてしまい、取り違えを検出できない（変異 M1 で実測）。
        """
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["pipeline"] = {"5": "abort"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})
        self.assertEqual(gh.issues[6]["labels"], {"ready"})
        self.assertEqual(self.branch(), "ms4/issue-5")
        self.assertEqual([r["result"] for r in self.runs()], ["ABORT"])

    # 7
    def test_unexpected_child_rc_is_abort(self):
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "rc3"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(self.runs()[0]["result"], "ABORT")

    def test_child_ttl_kills_and_aborts(self):
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "sleep"}
        self.cfg["ttl_seconds"]["pipeline"] = 3
        t0 = time.monotonic()
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertLess(time.monotonic() - t0, 60)
        step = self.runs()[0]["steps"][-1]
        self.assertEqual(step["rc"], 124)
        self.assertIn("TTL超過", Path(step["log"]).read_text(encoding="utf-8"))

    # 8
    def test_existing_branch_is_rejected_without_running_children(self):
        git(self.helper, "switch", "-c", "ms4/issue-5")
        git(self.helper, "push", "origin", "ms4/issue-5")
        gh = FakeGH([(5, "a"), (6, "b")])
        self.assertEqual(self.run_scheduler(gh), 1)

        runs = self.runs()
        self.assertEqual(runs[0]["result"], "REJECT")
        self.assertEqual(runs[0]["steps"], [])
        self.assertEqual(gh.issues[5]["labels"], {"ms4:failed"})
        self.assertTrue(self.remote_has_branch("ms4/issue-5"), "既存ブランチを消さない")
        self.assertEqual(gh.issues[6]["labels"], {"ms4:awaiting-approval"})

    # 9
    def test_existing_lock_blocks_without_touching_anything(self):
        gh = FakeGH([(5, "a")])
        self.out.mkdir(parents=True)
        self.lock.write_text("held\n", encoding="utf-8")
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.calls, [])
        self.assertEqual(self.lock.read_text(encoding="utf-8"), "held\n")

    def test_dirty_start_blocks_and_releases_lock(self):
        gh = FakeGH([(5, "a")])
        (self.work / "stray.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.calls, [])
        self.assertFalse(self.lock.exists(), "何も変えていないのでロックは残さない")
        self.assertTrue((self.work / "stray.txt").exists())

    def test_not_on_base_branch_blocks(self):
        git(self.work, "switch", "-c", "elsewhere")
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.calls, [])

    # 10
    def test_audit_runs_and_reports_are_merged_when_key_present(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.approve(gh.pr_for_issue(5))
        self.assertEqual(self.run_scheduler(gh), 0)
        files = self.remote_main_files()
        self.assertIn("reports/audits/audit_issue_5.md", files)
        self.assertIn("reports/audits/audit_Issue5Tests.md", files)
        self.assertEqual(self.runs()[0]["audit"], "done")

    def test_audit_failure_is_recorded_but_not_blocking(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        gh = FakeGH([(5, "a")])
        self.plan["audit"] = {"5": "fail"}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertTrue(self.runs()[0]["audit"].startswith("failed"))
        self.assertEqual(gh.issues[5]["labels"], {"ms4:awaiting-approval"})

    def test_required_audit_without_key_rejects(self):
        self.cfg["audit"]["required"] = True
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertEqual(gh.issues[5]["labels"], {"ms4:failed"})
        self.assertEqual([s["name"] for s in self.runs()[0]["steps"]], ["decompose"],
                         "実装へ進まない")
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.dirty(), "")

    # 11
    def test_decompose_writing_outside_allowed_paths_aborts(self):
        gh = FakeGH([(5, "a")])
        self.plan["decompose"] = {"5": "stray"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertIn("Sneaky.cs", self.runs()[0]["reason"])
        self.assertTrue((self.work / "Game/Assets/Core/Sneaky.cs").exists())

    # 12
    def test_merge_conflict_aborts_cleanly(self):
        """承認後、main が先に進んで PR が衝突した。マージできないので ABORT（人間が見る）。"""
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "conflict"}
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.approve(gh.pr_for_issue(5))
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertFalse((self.helper / ".git" / "MERGE_HEAD").exists(), "merge --abort 済み")
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.dirty(), "")
        self.assertEqual(gh.issues[5]["state"], "OPEN")
        self.assertEqual(gh.prs[gh.pr_for_issue(5)]["state"], "OPEN")
        self.assertTrue(self.lock.exists())

    # 13
    def test_red_ci_on_main_aborts_without_closing(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        gh.ci_conclusion = "failure"
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.issues[5]["labels"], {"ms4:awaiting-approval"})
        self.assertFalse(any("合格" in c for c in gh.issues[5]["comments"]))
        self.assertIn("failure", self.runs()[-1]["reason"])

    def test_ci_run_never_appears_aborts_after_waiting(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.pr_for_issue(5))
        gh.ci_missing = True
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertIn("見つかりません", self.runs()[-1]["reason"])
        self.assertEqual(self.sleeps.count(1), 3, "ci_find_seconds / 間隔 の回数だけ待つ")

    # 再試行
    def test_gh_transient_failure_is_retried(self):
        gh = FakeGH([(5, "a")])
        gh.fail_rules.append({"match": "issue list", "times": 2})
        gh.fail_rules.append({"match": "pr comment", "times": 1})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs[gh.pr_for_issue(5)]["comments"]), 1)
        self.assertGreaterEqual(self.sleeps.count(5), 3)

    def test_lost_response_does_not_double_post(self):
        """書き込みは反映されたのに失敗が返る。読み直して重ねないこと。"""
        gh = FakeGH([(5, "a")])
        gh.fail_rules.append({"match": "pr create", "times": 1, "apply": True})
        gh.fail_rules.append({"match": "pr comment", "times": 1, "apply": True})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs), 1, "PR を二重に作らない")
        self.assertEqual(len(gh.prs[gh.pr_for_issue(5)]["comments"]), 1)

        gh.approve(gh.pr_for_issue(5))
        gh.fail_rules.append({"match": "pr merge", "times": 1, "apply": True})
        gh.fail_rules.append({"match": "issue comment", "times": 1, "apply": True})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.merge_args), 1, "マージを重ねない")
        self.assertEqual(len(gh.issues[5]["comments"]), 1)

    def test_gh_failing_three_times_aborts(self):
        gh = FakeGH([(5, "a")])
        gh.fail_rules.append({"match": "issue list", "times": 3})
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(sum(1 for c in gh.calls if c[1:3] == ["issue", "list"]), 3)
        self.assertFalse(self.lock.exists(), "着手前なのでロックを残さない")

    # dry-run
    def test_dry_run_changes_nothing(self):
        gh = FakeGH([(5, "a")])
        head = git(self.work, "rev-parse", "HEAD")
        self.assertEqual(self.run_scheduler(gh, "--dry-run"), 0)
        self.assertEqual(gh.writes(), [])
        self.assertEqual(gh.issues[5]["labels"], {"ready"})
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.runs(), [])


if __name__ == "__main__":
    unittest.main()
