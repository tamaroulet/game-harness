"""tests/test_scheduler*.py が共有する足場（Base）・偽 GitHub（FakeGH）・stub の本文。

test_ で始まらない名前なので、探索（discover）には拾われない。使い方の説明は tests/test_scheduler.py の先頭にある。

一時ディレクトリは C:\\src\\.local\\out\\harness\\selftest\\ の下（リポジトリ直下に置かない）。
環境変数 HARNESS_SELFTEST_DIR で差し替えられる（CI のランナー用）。
"""
import hashlib
import io
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quiet  # noqa: E402
import exitcode  # noqa: E402
import project  # noqa: E402
import scheduler as ms4  # noqa: E402

SELFTEST_BASE = Path(os.environ.get("HARNESS_SELFTEST_DIR", r"C:\src\.local\out\harness\selftest"))
SLUG = "owner/repo"

STUB = r'''
import json, os, subprocess, sys, time
from pathlib import Path

role, arg = sys.argv[1], sys.argv[2]
tel_path = sys.argv[3] if len(sys.argv) > 3 else None   # {telemetry} を渡されたときだけ書く
plan = json.loads(Path(os.environ["STUB_PLAN"]).read_text(encoding="utf-8"))

def write_tel(obj):
    if tel_path:
        Path(tel_path).write_text(json.dumps(obj), encoding="utf-8")

def number():
    if role == "decompose":
        return arg
    return "".join(ch for ch in Path(arg).stem if ch.isdigit())

n = number()
mode = plan.get(role, {}).get(n, plan.get(role, {}).get("*", "ok"))

def write(rel, text):
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

def git(*a):
    subprocess.run(["git"] + list(a), check=True, capture_output=True)

print(f"stub {role} #{n} mode={mode}")

if role == "decompose":
    if mode in ("ok", "stray", "no_test_methods", "playtest"):
        core = Path("Game/Assets/Core")
        unit = {"id": f"issue_{n}", "human_check_point": "実機で見る点",
                "saw": sorted(p.name for p in core.glob("*.cs")) if core.is_dir() else []}
        if mode == "playtest":
            unit.update(playtest="required", human_check_point="ボス戦で溜めが見える\n回避が間に合う")
        write(f"tools/units/issue_{n}.json", json.dumps(unit, ensure_ascii=False))
        if mode == "no_test_methods":
            write(f"tests/Core.Tests/Issue{n}Tests.cs", "// [Test] はコメントの中だけ\n")
        else:
            write(f"tests/Core.Tests/Issue{n}Tests.cs",
                  '[Test] public void Works() { Assert.AreEqual(1, Answer(), "答えは 1"); }\n')
        if mode == "stray":
            write("Game/Assets/Core/Sneaky.cs", "// not allowed\n")
        write_tel({"schema": 1, "tool": "decompose", "usage": {"total_tokens": 100}})
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

if role == "pipeline":
    if mode in ("ok", "companion"):
        write(f"Game/Assets/Core/Issue{n}.cs", "// impl\n")
        git("add", "--", f"Game/Assets/Core/Issue{n}.cs")
        if mode == "companion":
            # エンジンが自動で作る付随ファイル（本体と一緒に持ち出される）
            write(f"Game/Assets/Core/Issue{n}.cs.meta", "fileFormatVersion: 2\nguid: 0123456789ab\n")
            git("add", "--", f"Game/Assets/Core/Issue{n}.cs.meta")
        git("commit", "-m", f"impl {n}")
        git("push")
        write_tel({"schema": 1, "tool": "pipeline", "attempts_judged": 2,
                   "p2p_violation_rate": 50.0, "retry_entropy": 0.25, "retry_same_failure_repeats": 0,
                   "attempts": [{"implementer": {"usage": {"total_tokens": 30}}},
                                {"implementer": {"usage": {"total_tokens": 20}}}]})
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
        # 門は通るが、統合ブランチ側が先に同じ行を変えている（マージが衝突する）
        write("README.md", "branch side\n")
        git("commit", "-am", "branch edits README")
        git("push")
        helper, integ = plan["helper"], plan["integration"]
        subprocess.run(["git", "-C", helper, "fetch", "-q", "origin"], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "checkout", "-q", "-B", integ, "origin/" + integ],
                       check=True, capture_output=True)
        Path(helper, "README.md").write_text("integration side\n", encoding="utf-8")
        subprocess.run(["git", "-C", helper, "commit", "-am", "integration edits README"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "push", "-q", "origin", integ], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "checkout", "-q", "main"], check=True, capture_output=True)
        sys.exit(0)
    if mode == "ahead":
        # 門は通るが、統合ブランチが先に進む（push が fast-forward にならない）
        write(f"Game/Assets/Core/Issue{n}.cs", "// impl\n")
        git("add", "--", f"Game/Assets/Core/Issue{n}.cs")
        git("commit", "-m", f"impl {n}")
        git("push")
        helper, integ = plan["helper"], plan["integration"]
        subprocess.run(["git", "-C", helper, "fetch", "-q", "origin"], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "checkout", "-q", "-B", integ, "origin/" + integ],
                       check=True, capture_output=True)
        Path(helper, "AHEAD.md").write_text("someone else\n", encoding="utf-8")
        subprocess.run(["git", "-C", helper, "add", "AHEAD.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "commit", "-m", "someone else moved integration"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "push", "-q", "origin", integ], check=True, capture_output=True)
        subprocess.run(["git", "-C", helper, "checkout", "-q", "main"], check=True, capture_output=True)
        sys.exit(0)

if role == "playtest":
    sys.exit({"ok": 0, "fail": 1, "abort": 2}[mode])

if role == "spec":
    out_dir, work_dir = Path(sys.argv[4]), Path(sys.argv[5])
    Path(plan["spec_calls"]).open("a", encoding="utf-8").write(json.dumps(sys.argv[2:]) + "\n")
    if mode in ("ok", "questions", "stray"):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "spec.md").write_text("# spec\n", encoding="utf-8")
        (out_dir / "questions.md").write_text("# questions\n", encoding="utf-8")
        q = [{"id": "Q-01", "question": "何倍か", "refs": "L7", "why": "無い", "headings": ["# 仕様"]}] if mode == "questions" else []
        summary = {"gdd": {"lines": 10, "target_lines": 8}, "counts": {"LP": 1, "Q": len(q)},
                   "provisional": [], "questions": q, "human_checks": [], "anchors_missing": [],
                   "term_candidates": [], "id_changes": None, "passed": True, "mergeable": mode != "questions"}
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "summary.json").write_text(json.dumps({"problems": [], "summary": summary}), encoding="utf-8")
        if mode == "stray":
            (out_dir.parent.parent / "stray.txt").write_text("x", encoding="utf-8")
        sys.exit(0)
    if mode == "fail":
        print("全試行で不合格")
        sys.exit(1)
    if mode == "abort":
        sys.exit(2)

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
        self.label_events = {}  # PR 番号 -> issue events（テレメトリ用）
        # gh api rate_limit の応答。資源名 -> (残量, リセットの epoch 秒)
        self.rate = {"core": (5000, int(time.time()) + 3600), "graphql": (5000, int(time.time()) + 3600)}

    def writes(self):
        verbs = {"edit", "comment", "close", "create", "merge"}
        return [c for c in self.calls if len(c) > 2 and c[2] in verbs]

    def approve(self, pr_number):
        self.prs[pr_number]["labels"].add("ms4:approved")

    def integration_pr(self):
        """統合 PR の番号（Issue ごとの PR はもう作らない）。"""
        return next(num for num, p in self.prs.items() if p["branch"].startswith("integration/"))

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
                return 1, "", rule.get("message", "HTTP 502: simulated")
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
        if key == "api rate_limit":
            return 0, json.dumps({"resources": {k: {"limit": 5000, "remaining": r, "reset": t}
                                                for k, (r, t) in self.rate.items()}}), ""
        if key.startswith("api repos/") and "/events" in key:
            # テレメトリ（承認待ち時間）用。既定は空で、テストが label_events に置いた分だけ返す
            num = int(key.split("/issues/")[1].split("/")[0])
            page = int(key.split("page=")[-1]) if "page=" in key else 1
            events = self.label_events.get(num, [])
            return 0, json.dumps(events[(page - 1) * 100:page * 100]), ""
        return 1, "", f"FakeGH: 未対応 {a}"


# ============================================================ 足場

class Base(quiet.Quiet, unittest.TestCase):
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
        # 契約。マージコミットに Contract-SHA を書くので、base に必ず要る
        (self.work / ".harness.toml").write_text(
            '[static]\nselftest_forbidden_probe = "using UnityEngine;"\n'
            '[[static.forbidden]]\npattern = "UnityEngine"\nlabel = "engine word in core"\n',
            encoding="utf-8")
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
                "integrated": {"name": "ms4:integrated", "color": "0052CC", "description": "i"},
            },
            "integration_prefix": "integration/",
            "integration_target": "mvp",
            "integration_pr_min_issues": 3,
            "integration_max_issues": 4,
            "runner_worktree_name": "issue-runner",
            "required_checks": ["test", "approval"],
            "adapters": {"engine": "unity", "fast": "dotnet"},
            "required_clis": ["git"],
            "commands": {
                "decompose": ["{python}", stub, "decompose", "{number}"],
                "pipeline": ["{python}", stub, "pipeline", "{unit}"],
            },
            "unit_path_template": "tools/units/issue_{number}.json",
            "test_dir": "tests/Core.Tests",
            "ttl_seconds": {"git": 60, "gh": 10, "decompose": 60, "pipeline": 60},
            "ci_find_seconds": 3, "ci_watch_seconds": 3, "ci_poll_interval_seconds": 1,
            "net_retries": 2, "net_retry_interval_seconds": 5,
            "comment_log_tail_chars": 800,
            "max_issues_per_run": 5,
        }
        self.plan["integration"] = "integration/mvp"
        self.sleeps = []
        self.env = mock.patch.dict(os.environ, {"STUB_PLAN": str(self.tmp / "plan.json")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))

    def run_scheduler(self, gh, *extra, sleep=None):
        gh.helper = self.helper
        (self.tmp / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        cfg_path = self.tmp / "ms4.config.json"
        cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")
        return ms4.main(["--config", str(cfg_path)] + list(extra),
                        gh_run=gh, sleep=sleep or self.sleeps.append)

    # ---- 観測
    def runs(self):
        p = self.out / "runs.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]

    def branch(self):
        return git(self.work, "branch", "--show-current")

    INTEG = "integration/mvp"

    def runner_wt(self):
        """Issue の作業に使い回す固定 worktree（テストの設定には worktree_root も project_id も無い）。"""
        return self.out / "worktrees" / "project-issue-runner"

    def wt_branch(self):
        return git(self.runner_wt(), "branch", "--show-current")

    def wt_dirty(self):
        return git(self.runner_wt(), "status", "--porcelain", "--untracked-files=all")

    def merge_messages(self):
        """統合ブランチに積まれたマージコミットの本文（古い順）。"""
        git(self.work, "fetch", "origin")
        out = git(self.work, "log", "--first-parent", "--format=%B%x1e",
                  f"origin/main..origin/{self.INTEG}")
        return list(reversed([x.strip() for x in out.split(chr(30))
                              if x.strip() and "Issue: #" in x]))

    def trailer(self, body, key):
        for line in body.splitlines():
            if line.startswith(key + ": "):
                return line[len(key) + 2:]
        return None

    def dirty(self):
        return git(self.work, "status", "--porcelain", "--untracked-files=all")

    def remote_has_branch(self, b):
        return bool(git(self.work, "ls-remote", "--heads", "origin", b))

    def local_has_branch(self, b):
        return bool(git(self.work, "branch", "--list", b))

    def remote_main_files(self):
        return self.remote_files("main")

    def remote_files(self, branch):
        git(self.work, "fetch", "origin")
        return set(git(self.work, "ls-tree", "-r", "--name-only", f"origin/{branch}").splitlines())

    @property
    def lock(self):
        return self.out / "scheduler.lock"
