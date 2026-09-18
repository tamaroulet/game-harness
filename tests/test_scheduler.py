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

if role == "audit":
    if mode in ("ok", "concern", "reject", "no_verdict"):
        write(f"reports/audits/audit_{Path(arg).stem}.md", "# audit\n")
        if mode != "no_verdict":   # no_verdict = 監査役が決められた形で返さなかった
            write_tel({"schema": 1, "tool": "audit", "file": arg, "verdict": mode,
                       "findings": [] if mode == "ok" else ["境界値の確認が要る"]})
        sys.exit(0)
    if mode == "fail":
        sys.exit(1)

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
                "audit": ["{python}", stub, "audit", "{file}", "{verdict}"],
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
        self.plan["integration"] = "integration/mvp"
        self.sleeps = []
        self.env = mock.patch.dict(os.environ, {"STUB_PLAN": str(self.tmp / "plan.json")})
        self.env.start()
        os.environ.pop("MS4_TEST_AUDIT_KEY", None)

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
                             project="unity-2d", number=5, file="f", unit="u", telemetry="t.json",
                             verdict="v.json", repo="r")
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
        self.assertEqual(cfg["adapters"], {"engine": "unity", "fast": "dotnet"})
        self.assertEqual(cfg["project"]["unity_project_subdir"], "Game",
                         "エンジン固有のキーは project 経由でアダプタに渡る")

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
        for py in sorted(HERE.rglob("*.py")):
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
        self.assertTrue(self.remote_has_branch(self.INTEG), "統合ブランチが無ければ作る")
        self.assertEqual(git(self.work, "rev-parse", f"origin/{self.INTEG}"), head,
                         "起点は origin/main")
        self.assertEqual(gh.prs, {}, "積まれた Issue が無ければ統合 PR は作らない")
        self.assertFalse(self.lock.exists())

    # 2
    def test_gate_pass_pushes_to_integration_then_one_pr_to_main(self):
        gh = FakeGH([(5, "タメ")])

        # ---- 1 周目: 門を通ったら Issue の PR を作らず、統合ブランチへ直接 push する
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertNotIn("Game/Assets/Core/Issue5.cs", self.remote_main_files(),
                         "承認前に main へは入れない")
        files = self.remote_files(self.INTEG)
        for want in ("Game/Assets/Core/Issue5.cs", "tests/Core.Tests/Issue5Tests.cs",
                     "tools/units/issue_5.json"):
            self.assertIn(want, files)
        self.assertFalse(self.remote_has_branch("ms4/issue-5"), "Issue のブランチは片付ける")
        self.assertFalse(self.local_has_branch("ms4/issue-5"))

        body = self.merge_messages()[0]
        self.assertTrue(body.startswith(f"feat(core): implement Issue #5 into {self.INTEG}"), body)
        self.assertEqual(self.trailer(body, "Issue"), "#5")
        self.assertEqual(self.trailer(body, "Gate-Result"), "PASSED")
        self.assertEqual(self.trailer(body, "Audit-Verdict"), "skipped", "鍵が無ければ判定は skipped")
        self.assertEqual(self.trailer(body, "Run-Id"), self.runs()[0]["run_id"])
        self.assertEqual(self.trailer(body, "Contract-SHA"),
                         git(self.work, "rev-parse", "origin/main:.harness.toml"))
        parents = git(self.work, "rev-list", "--parents", "-n", "1", f"origin/{self.INTEG}").split()
        self.assertEqual(len(parents), 3, "fast-forward ではなくマージコミット")

        self.assertEqual(gh.issues[5]["labels"], {"ms4:integrated"})
        self.assertEqual(gh.issues[5]["state"], "OPEN", "統合 PR のマージまで閉じない")

        # ---- 対象の Issue が尽きたので、周の末尾に統合 PR が 1 本できる
        num = gh.integration_pr()
        pr = gh.prs[num]
        self.assertEqual((pr["branch"], pr["base"]), (self.INTEG, "main"))
        self.assertEqual(pr["labels"], {"ms4:awaiting-approval"})
        self.assertIn("Closes #5", pr["body"])
        self.assertIn("すべてのマージコミットが", pr["body"], "Run-Id と runs.jsonl の照合が載る")
        head = gh.head_of(self.INTEG)
        request = [c for c in pr["comments"] if f"ms4:approval-request:{head}" in c]
        self.assertEqual(len(request), 1, "今の head SHA に対する承認依頼が 1 件")
        self.assertIn("`Works`", request[0])
        self.assertIn("「答えは 1」", request[0])
        self.assertIn("実機で見る点", request[0])
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.lock.exists())
        self.assertEqual([r["result"] for r in self.runs()], ["PASSED", "AWAITING"])

        # ---- 2 周目: 人間が承認 → 必須チェック緑 → main へマージコミットで入る
        gh.approve(num)
        self.assertEqual(self.run_scheduler(gh), 0)

        files = self.remote_main_files()
        self.assertIn("tests/Core.Tests/Issue5Tests.cs", files)
        self.assertIn("tools/units/issue_5.json", files)
        self.assertIn("Game/Assets/Core/Issue5.cs", files)
        self.assertEqual(len(gh.merge_args), 1)
        self.assertIn("--merge", gh.merge_args[0])
        self.assertNotIn("--squash", gh.merge_args[0])
        self.assertEqual(gh.merge_args[0][gh.merge_args[0].index("--match-head-commit") + 1], head)

        issue = gh.issues[5]
        self.assertEqual(issue["state"], "CLOSED")
        self.assertEqual(sum("ms4 スケジューラ: 合格" in c for c in issue["comments"]), 1)
        self.assertEqual(gh.prs[num]["state"], "MERGED")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), gh.prs[num]["merge_sha"],
                         "ローカルの main もマージ後に追従する")
        self.assertFalse(self.remote_has_branch(self.INTEG), "統合ブランチは役目を終えたら消す")
        self.assertFalse(self.lock.exists())

        runs = self.runs()
        self.assertEqual([r["result"] for r in runs], ["PASSED", "AWAITING", "PASSED"])
        self.assertIn("skipped", runs[0]["audit"])
        self.assertEqual([s["name"] for s in runs[0]["steps"]], ["decompose", "pipeline"])
        self.assertTrue(runs[2]["ci_run"])

    def test_three_issues_reach_the_pr_threshold_in_one_pr(self):
        """統合 PR は 1 本。3 件の Issue が 1 つの承認にまとまる。"""
        gh = FakeGH([(5, "a"), (6, "b"), (7, "c")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs), 1)
        pr = gh.prs[gh.integration_pr()]
        for n in (5, 6, 7):
            self.assertIn(f"Closes #{n}", pr["body"])
            self.assertEqual(gh.issues[n]["labels"], {"ms4:integrated"})
        self.assertEqual([self.trailer(b, "Issue") for b in self.merge_messages()],
                         ["#5", "#6", "#7"], "積まれた順に 1 Issue = 1 マージコミット")
        self.assertEqual([r["result"] for r in self.runs()],
                         ["PASSED", "PASSED", "PASSED", "AWAITING"])

    def test_no_pr_until_the_threshold_while_issues_remain(self):
        """対象が残っているうちは、下限に達するまで統合 PR を作らない。"""
        self.cfg["max_issues_per_run"] = 2
        gh = FakeGH([(5, "a"), (6, "b"), (7, "c")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs, {}, "2 本では下限 3 本に足りず、対象も残っている")
        self.assertEqual(len(self.merge_messages()), 2)
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs), 1, "3 本目で下限に達する")
        self.assertEqual(len(self.merge_messages()), 3)

    def test_an_integrated_issue_is_not_processed_again(self):
        """人間が ready を付け直しても、統合ブランチに入った Issue は作り直さない。

        マージのときに ready を外してはいるが、外し忘れや付け直しが起きうる。二重に積むと、
        同じ実装が 2 つのマージコミットとして履歴に残り、Run-Id との対応が崩れる。
        """
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.issues[5]["labels"].add("ready")
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(self.merge_messages()), 1, "二重に積まない")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:integrated", "ready"}, "ラベルにも触らない")
        self.assertEqual([r["result"] for r in self.runs()], ["PASSED", "AWAITING"])

    def test_a_later_issue_starts_from_the_earlier_ones(self):
        """後続の Issue は、先行 Issue の実装を含む統合ブランチの先頭から切る（直列の依存で止まらない）。"""
        gh = FakeGH([(5, "a"), (6, "b")])
        self.assertEqual(self.run_scheduler(gh), 0)
        unit5 = json.loads(git(self.work, "show", f"origin/{self.INTEG}:tools/units/issue_5.json"))
        unit6 = json.loads(git(self.work, "show", f"origin/{self.INTEG}:tools/units/issue_6.json"))
        self.assertEqual(unit5["saw"], [])
        self.assertEqual(unit6["saw"], ["Issue5.cs"], "#6 の分解役は #5 の実装を見ている")

    def test_a_record_that_does_not_match_the_commit_is_reported(self):
        """コミット履歴と runs.jsonl が食い違ったら、人間が承認する前に統合 PR に出す。"""
        self.cfg["max_issues_per_run"] = 1
        gh = FakeGH([(5, "a"), (6, "b")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs, {}, "1 本では下限に足りず、対象も残っている")
        path = self.out / "runs.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["harness_sha"] = "0" * 40
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in rows),
                        encoding="utf-8")

        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[gh.integration_pr()]
        self.assertIn("**不一致**", pr["body"])
        self.assertIn("ハーネスの版が違います", pr["body"])
        self.assertEqual(len(self.runs()[-1]["run_id_problems"]), 1)

    def test_missing_contract_aborts_before_merging(self):
        """契約（.harness.toml）が base に無ければ、その版を記録できないので統合ブランチへ入れない。"""
        git(self.helper, "fetch", "-q", "origin")
        git(self.helper, "reset", "-q", "--hard", "origin/main")
        git(self.helper, "rm", "-q", ".harness.toml")
        git(self.helper, "commit", "-q", "-m", "drop contract")
        git(self.helper, "push", "-q", "origin", "main")
        git(self.work, "pull", "-q", "--ff-only")
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(self.merge_messages(), [])
        self.assertIn(".harness.toml", self.runs()[0]["reason"])
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})

    def test_stacking_stops_at_the_cap(self):
        """1 つの統合ブランチに積むのは上限まで。承認待ちの間は新しい Issue に着手しない。"""
        self.cfg["integration_pr_min_issues"] = 9      # 周の末尾に PR を作らせない
        self.cfg["integration_max_issues"] = 2
        gh = FakeGH([(5, "a"), (6, "b"), (7, "c")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(self.merge_messages()), 2, "上限まで積んだら止まる")
        self.assertEqual(gh.issues[7]["labels"], {"ready"}, "3 本目には着手しない")
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(self.merge_messages()), 2, "承認されるまで増えない")

    def test_telemetry_reaches_runs_jsonl_with_research_metrics(self):
        """Step 4: 道具のテレメトリが runs.jsonl に載り、査読用の 4 指標が算出されること。"""
        for name in ("decompose", "pipeline"):
            self.cfg["commands"][name].append("{telemetry}")
        self.cfg["experiment_condition"] = "harness"
        gh = FakeGH([(5, "タメ")])
        self.assertEqual(self.run_scheduler(gh), 0)
        pr_num = gh.integration_pr()
        gh.approve(pr_num)
        gh.label_events[pr_num] = [
            {"event": "labeled", "label": {"name": "ms4:awaiting-approval"},
             "created_at": "2026-09-17T06:00:00Z", "actor": {"login": "scheduler"}},
            {"event": "labeled", "label": {"name": "ms4:approved"},
             "created_at": "2026-09-17T06:02:30Z", "actor": {"login": "human"}},
        ]
        self.assertEqual(self.run_scheduler(gh), 0)

        issue, opened, merged = self.runs()
        self.assertEqual(issue["condition"], "harness")
        self.assertEqual(issue["steps"][0]["telemetry"]["usage"]["total_tokens"], 100)
        self.assertIsNone(issue["steps"][0].get("telemetry_null_reason"))
        self.assertEqual(issue["p2p_violation_rate"], 50.0)
        self.assertEqual(issue["attempts_judged"], 2)
        self.assertEqual(issue["retry_entropy"], 0.25)
        self.assertEqual(issue["tokens_total"], 150)
        self.assertEqual(issue["accepted_loc"], 1, "Issue5.cs の 1 行だけ（テスト・単位定義は数えない）")
        self.assertEqual(issue["token_to_accepted_loc"], 150.0)
        self.assertEqual(issue["gate_result"], "PASSED")

        self.assertEqual(opened["result"], "AWAITING")
        self.assertEqual(opened["issues"], [5])
        self.assertEqual(opened["run_id_problems"], [], "Run-Id と runs.jsonl が一致する")

        self.assertEqual(merged["human"]["wait_seconds"], 150.0)
        self.assertEqual(merged["human"]["decided_by"], "human")
        self.assertIsNone(merged["human"]["active_seconds"])
        self.assertIsNone(merged["human_active_intervention_time"])
        self.assertIn("human_active_intervention_time_null_reason", merged)
        self.assertIn("total_seconds", merged)
        self.assertNotIn("_t0", merged)

    def test_dry_run_expands_every_placeholder(self):
        """本番の commands（{harness} {project} {telemetry} を含む）で、対象の Issue があっても落ちない。"""
        self.cfg["commands"] = ms4.build_config("unity-2d")["commands"]
        gh = FakeGH([(5, "a")])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(self.run_scheduler(gh, "--dry-run"), 0)
        self.assertIn("--telemetry <pipeline.telemetry.json>", out.getvalue())
        self.assertEqual(gh.writes(), [])

    def test_missing_telemetry_is_null_with_reason_not_zero(self):
        """テレメトリを書かない道具（{telemetry} を渡していない）でも、0 や {} にしない。"""
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.approve(gh.integration_pr())
        self.assertEqual(self.run_scheduler(gh), 0)
        issue, _, merged = self.runs()
        for s in issue["steps"]:
            self.assertIsNone(s["telemetry"], s)
            self.assertTrue(s["telemetry_null_reason"])
        self.assertIsNone(issue["p2p_violation_rate"])
        self.assertIsNone(issue["tokens_total"])
        self.assertIsNone(issue["token_to_accepted_loc"])
        self.assertIsNone(merged["human"]["wait_seconds"], "イベントが無ければ不明")
        self.assertEqual(gh.prs[gh.integration_pr()]["state"], "MERGED", "テレメトリでマージを止めない")

    # ---- プレイ確認（Step 6）
    def enable_playtest(self, build_mode):
        stub = str(self.tmp / "stub.py")
        self.cfg["commands"]["playtest"] = ["{python}", stub, "playtest", "{pr}"]
        self.cfg["ttl_seconds"]["playtest"] = 60
        self.cfg["labels"]["playtest"] = {"name": "ms4:playtest-required", "color": "5319E7", "description": "p"}
        self.plan["decompose"] = {"5": "playtest"}
        self.plan["playtest"] = {"*": build_mode}

    def test_playtest_required_unit_gets_label_and_build(self):
        self.enable_playtest("ok")
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[gh.integration_pr()]
        head = gh.head_of(self.INTEG)
        self.assertEqual(pr["labels"], {"ms4:awaiting-approval", "ms4:playtest-required"})
        request = [c for c in pr["comments"] if f"ms4:approval-request:{head}" in c][0]
        self.assertIn("### プレイ確認", request)
        self.assertIn("回避が間に合う", request, "確認項目（human_check_point の各行）が承認依頼に載る")
        built = [c for c in pr["comments"] if f"ms4:playtest-build:{head}" in c]
        self.assertEqual(len(built), 1)
        self.assertIn("--playtest", built[0])
        issue, opened = self.runs()
        self.assertEqual([s["name"] for s in issue["steps"]], ["decompose", "pipeline"])
        self.assertEqual([s["name"] for s in opened["steps"]], ["playtest"],
                         "ビルドは統合 PR の head から 1 回だけ")

    def test_playtest_build_failure_stays_awaiting_with_a_comment(self):
        self.enable_playtest("fail")
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[gh.integration_pr()]
        self.assertEqual(pr["state"], "OPEN")
        self.assertIn("ms4:awaiting-approval", pr["labels"])
        self.assertTrue(any("ビルドに失敗" in c for c in pr["comments"]))
        self.assertEqual(self.runs()[-1]["result"], "AWAITING")

    def test_playtest_build_environment_error_aborts(self):
        self.enable_playtest("abort")
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(self.runs()[-1]["result"], "ABORT")
        self.assertEqual(self.runs()[0]["result"], "PASSED", "統合ブランチへは入っている")

    def test_playtest_none_does_not_build(self):
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[gh.integration_pr()]
        self.assertNotIn("ms4:playtest-required", pr["labels"])
        self.assertFalse(any("ms4:playtest-build" in c for c in pr["comments"]))
        self.assertNotIn("### プレイ確認", pr["comments"][0])

    def test_scheduler_never_applies_the_approval_label(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
        self.run_scheduler(gh)
        for call in gh.calls:
            for i, x in enumerate(call[:-1]):
                if x == "--add-label":
                    self.assertNotEqual(call[i + 1], "ms4:approved", call)

    def test_unapproved_pr_just_waits(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[gh.integration_pr()]["state"], "OPEN")
        self.assertEqual([r["result"] for r in self.runs()], ["PASSED", "AWAITING"],
                         "再取得も記録もしない")
        self.assertEqual(sum(1 for c in gh.calls if c[1:3] == ["pr", "create"]), 1)

    def test_approved_but_required_check_pending_does_not_merge(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
        gh.check_override = {"test": ("in_progress", None)}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])
        self.assertEqual(gh.issues[5]["state"], "OPEN")

    def test_label_without_green_approval_check_does_not_merge(self):
        """ラベルは付いていても、approval チェックが赤（承認者以外が付けた等）ならマージしない。"""
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
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
        self.assertEqual(gh.prs[gh.integration_pr()]["state"], "OPEN")

    def test_latest_check_run_wins_by_id_not_by_order(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
        gh.extra_check_runs = [{"id": 1, "name": "test", "status": "completed", "conclusion": "failure"}]
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[gh.integration_pr()]["state"], "MERGED")

        gh2 = FakeGH([(7, "b")])
        self.run_scheduler(gh2)
        gh2.approve(gh2.integration_pr())
        gh2.extra_check_runs = [{"id": 99999, "name": "test", "status": "completed", "conclusion": "failure"}]
        self.assertEqual(self.run_scheduler(gh2), 0)
        self.assertEqual(gh2.prs[gh2.integration_pr()]["state"], "OPEN", "新しい run が赤ならマージしない")

    def test_push_after_approval_is_not_merged(self):
        """承認した SHA 以外は入れない。承認後に push されたら待ちに戻す（ABORT しない）。"""
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        pr_num = gh.integration_pr()
        gh.approve(pr_num)

        def push_extra_commit():
            git(self.helper, "fetch", "-q", "origin")
            git(self.helper, "switch", "-q", "-c", "late", f"origin/{self.INTEG}")
            (self.helper / "late.txt").write_text("late\n", encoding="utf-8")
            git(self.helper, "add", "late.txt")
            git(self.helper, "commit", "-q", "-m", "late push")
            git(self.helper, "push", "-q", "origin", f"late:{self.INTEG}")
            git(self.helper, "switch", "-q", "main")
        gh.before_merge = push_extra_commit

        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[pr_num]["state"], "OPEN")
        self.assertNotIn("late.txt", self.remote_main_files())
        self.assertFalse(self.lock.exists(), "ABORT ではない")
        self.assertEqual(gh.issues[5]["state"], "OPEN")

    def test_declined_integration_pr_is_closed_and_issues_fail(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.run_scheduler(gh)
        pr_num = gh.integration_pr()
        gh.prs[pr_num]["labels"].add("ms4:declined")
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertEqual(gh.prs[pr_num]["state"], "CLOSED")
        for n in (5, 6):
            self.assertEqual(gh.issues[n]["labels"], {"ms4:integrated", "ms4:failed"},
                             "統合ブランチには入ったまま。ms4:failed で対象から外す")
            self.assertTrue(any("却下" in c for c in gh.issues[n]["comments"]))
        self.assertEqual(gh.merge_args, [])
        self.assertTrue(self.remote_has_branch(self.INTEG), "統合ブランチは残す（人間が中身を見る）")
        self.assertEqual([r["result"] for r in self.runs()],
                         ["PASSED", "PASSED", "AWAITING", "REJECT"])

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
        self.assertEqual(gh.issues[6]["labels"], {"ms4:integrated"}, "次の Issue へ進む")
        self.assertEqual([r["result"] for r in self.runs()], ["REJECT", "PASSED", "AWAITING"])

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
        self.assertEqual(self.branch(), "main", "本体の clone は main のまま（作業は worktree）")
        self.assertEqual(self.dirty(), "")
        self.assertEqual(self.wt_branch(), "ms4/issue-5", "証拠の worktree を掃除しない（触らない）")
        self.assertEqual([r["result"] for r in self.runs()], ["ABORT"])

    def test_decompose_reject_leaving_dirty_tree_escalates_to_abort(self):
        gh = FakeGH([(5, "a")])
        self.plan["decompose"] = {"5": "reject_dirty"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertIn("Issue5Tests.cs", self.wt_dirty(), "汚れた worktree を証拠として残す")
        self.assertEqual(self.dirty(), "", "本体の clone は汚れない")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})

    # 5
    def test_pipeline_reject_keeps_branch_and_continues(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["pipeline"] = {"5": "reject"}
        self.assertEqual(self.run_scheduler(gh), 1)

        self.assertEqual(gh.issues[5]["labels"], {"ms4:failed"})
        self.assertTrue(self.local_has_branch("ms4/issue-5"))
        self.assertTrue(self.remote_has_branch("ms4/issue-5"))
        self.assertNotIn("tests/Core.Tests/Issue5Tests.cs", self.remote_files(self.INTEG),
                         "不合格の Issue は統合ブランチに入らない")
        self.assertEqual([self.trailer(b, "Issue") for b in self.merge_messages()], ["#6"])
        self.assertEqual(gh.issues[6]["labels"], {"ms4:integrated"})
        self.assertEqual(self.branch(), "main")

    # 6
    def test_pipeline_abort_leaves_evidence_untouched(self):
        gh = FakeGH([(5, "a"), (6, "b")])
        self.plan["pipeline"] = {"5": "abort_dirty"}
        self.assertEqual(self.run_scheduler(gh), 2)

        self.assertTrue((self.runner_wt() / "Game/Assets/Core/Half.cs").exists(), "汚れを掃除しない")
        self.assertEqual(self.wt_branch(), "ms4/issue-5")
        self.assertEqual((self.branch(), self.dirty()), ("main", ""))
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
        self.assertEqual(self.wt_branch(), "ms4/issue-5")
        self.assertEqual((self.branch(), self.dirty()), ("main", ""))
        self.assertEqual([r["result"] for r in self.runs()], ["ABORT"])

    def test_issue_work_happens_in_one_reused_worktree(self):
        """本体の clone（repo_dir）は main のまま汚れない。固定 worktree は消さずに使い回す。

        以前は Issue ごとに使い捨ての worktree を作って削除していた。Windows では削除が
        WinError 32 で失敗しうる。削除しなければ、その衝突は原理的に起きない。
        """
        gh = FakeGH([(5, "a"), (6, "b")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual((self.branch(), self.dirty()), ("main", ""))
        self.assertEqual(git(self.work, "log", "--oneline", "main").count("\n"), 0,
                         "本体の main にコミットしない")
        self.assertTrue(self.runner_wt().exists(), "worktree は残す（消さない）")
        self.assertEqual(git(self.work, "worktree", "list").count("\n"), 1,
                         "登録されている worktree は固定の 1 つだけ")
        self.assertEqual([r["worktree"] for r in self.runs() if r.get("worktree")],
                         [str(self.runner_wt())] * 2, "2 件の Issue が同じ worktree を使う")
        self.assertEqual(self.wt_dirty(), "", "次に使う前に空にできる状態で終わる")

    def test_the_runner_worktree_is_cleaned_before_each_issue(self):
        """前の Issue の残骸（未追跡・無視されるファイル）を持ち越さない。"""
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        junk = self.runner_wt() / "leftover.txt"
        junk.write_text("x", encoding="utf-8")
        (self.runner_wt() / "__pycache__").mkdir(exist_ok=True)      # .gitignore 済み
        (self.runner_wt() / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
        gh.approve(gh.integration_pr())
        self.assertEqual(self.run_scheduler(gh), 0)

        gh2 = FakeGH([(6, "b")])
        self.assertEqual(self.run_scheduler(gh2), 0)
        self.assertFalse(junk.exists(), "clean -fdx で消える")
        self.assertFalse((self.runner_wt() / "__pycache__").exists(), "無視されるファイルも消す")
        self.assertEqual(self.wt_dirty(), "")

    def test_clean_reject_keeps_the_branch_and_the_worktree(self):
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "reject"}
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertTrue(self.runner_wt().exists(), "worktree は消さない")
        self.assertTrue(self.local_has_branch("ms4/issue-5"))
        self.assertEqual((self.branch(), self.dirty()), ("main", ""))

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
        self.assertEqual(gh.issues[6]["labels"], {"ms4:integrated"})

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
    def test_audit_runs_before_the_implementation_and_again_before_the_merge(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        files = self.remote_files(self.INTEG)
        self.assertIn("reports/audits/audit_issue_5.md", files, "実装の前: 単位定義")
        self.assertIn("reports/audits/audit_Issue5Tests.md", files, "実装の前: テスト")
        self.assertIn("reports/audits/audit_Issue5.md", files, "マージの直前: 実装そのもの")
        issue = self.runs()[0]
        self.assertEqual(issue["audit"], "done")
        self.assertEqual(issue["merge_audit"], "done")
        self.assertEqual(issue["impl_files"], ["Game/Assets/Core/Issue5.cs"],
                         "マージ直前の監査は実装だけを見る（テスト・単位定義・レポートは外す）")
        self.assertEqual(self.trailer(self.merge_messages()[0], "Audit-Verdict"), "ok")
        self.assertEqual(issue["merge_audit_verdict"], "ok")

    def test_the_pre_merge_audit_skips_engine_companion_files(self):
        """エンジンが自動で作る付随ファイルは、監査の対象にしない。

        人が書いた実装ではないし、中身は機械が振った識別子だけで、読ませても指摘は出るが
        意味が無い。実測では 59 文字の付随ファイル 6 件に監査役が約 6 分かけ、そのそれぞれに
        「指摘 7 件」を返していた（2026-09-19 の Issue #12。マージ直前の監査は当時の
        最大の時間項で、その半分が付随ファイルに使われていた）。

        外すのは監査の対象からだけで、付随ファイル自体はマージには載る。
        """
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        self.plan["pipeline"] = {"5": "companion"}
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)

        issue = self.runs()[0]
        self.assertEqual(issue["impl_files"], ["Game/Assets/Core/Issue5.cs"],
                         "付随ファイルは実装として数えない")

        files = self.remote_files(self.INTEG)
        self.assertIn("Game/Assets/Core/Issue5.cs.meta", files,
                      "外すのは監査からだけ。付随ファイルはマージに載る")
        self.assertIn("reports/audits/audit_Issue5.md", files, "実装そのものは監査する")
        self.assertNotIn("reports/audits/audit_Issue5.cs.md", files,
                         "付随ファイルの監査レポートは作られない")

    def test_an_unreadable_verdict_is_unknown_not_ok(self):
        """監査役が決められた形で返さなかった。判定を ok に畳まない。"""
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        self.plan["audit"] = {"5": "no_verdict"}
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(self.trailer(self.merge_messages()[0], "Audit-Verdict"), "unknown")
        self.assertEqual(self.runs()[0]["merge_audit"], "done", "監査自体は動いている")

    def test_audit_verdict_reaches_the_commit_and_the_approval_request(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        self.plan["audit"] = {"5": "concern"}
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(self.trailer(self.merge_messages()[0], "Audit-Verdict"), "concern")
        issue = self.runs()[0]
        self.assertEqual(issue["merge_audit_verdict"], "concern")
        self.assertEqual(issue["merge_audit_findings"], ["`Issue5.cs`: 境界値の確認が要る"])
        pr = gh.prs[gh.integration_pr()]
        request = [c for c in pr["comments"] if "ms4:approval-request" in c][0]
        self.assertIn("### 監査の判定", request)
        self.assertIn("#5 concern", request)
        self.assertIn("境界値の確認が要る", request, "指摘が承認依頼に出る")

    def test_a_single_reject_verdict_decides_the_whole_issue(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        self.plan["audit"] = {"5": "reject"}
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(self.trailer(self.merge_messages()[0], "Audit-Verdict"), "reject")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:integrated"},
                         "判定は合否に使わない（門を通っていれば統合ブランチへ入る）")

    def test_audit_failure_is_recorded_but_not_blocking(self):
        os.environ["MS4_TEST_AUDIT_KEY"] = "dummy"
        gh = FakeGH([(5, "a")])
        self.plan["audit"] = {"5": "fail"}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertTrue(self.runs()[0]["audit"].startswith("failed"))
        self.assertEqual(gh.issues[5]["labels"], {"ms4:integrated"})

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
        self.assertTrue((self.runner_wt() / "Game/Assets/Core/Sneaky.cs").exists())
        self.assertFalse((self.work / "Game/Assets/Core/Sneaky.cs").exists())

    # 12
    def test_integration_merge_conflict_rolls_back_and_aborts(self):
        """統合ブランチ側が先に同じ行を変えていた。自動で解決せず、取り消して ABORT する。"""
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "conflict"}
        self.assertEqual(self.run_scheduler(gh), 2)
        git(self.work, "fetch", "origin")
        tip = git(self.work, "log", "-1", "--format=%s", f"origin/{self.INTEG}")
        self.assertEqual(tip, "integration edits README", "衝突の相手（helper の push）だけが入っている")
        self.assertEqual(self.merge_messages(), [], "Issue のマージコミットは 1 つも入らない")
        self.assertFalse((Path(git(self.runner_wt(), "rev-parse", "--absolute-git-dir"))
                          / "MERGE_HEAD").exists(), "merge --abort 済み")
        self.assertEqual(self.wt_dirty(), "", "衝突の跡を残さない")
        self.assertEqual(gh.issues[5]["labels"], {"ms4:running"})
        self.assertEqual(gh.prs, {}, "統合 PR は作らない")
        self.assertEqual((self.branch(), self.dirty()), ("main", ""))
        self.assertTrue(self.lock.exists())
        self.assertIn("衝突", self.runs()[0]["reason"])

    def test_non_fast_forward_push_rolls_back_and_aborts(self):
        """統合ブランチが先に進んでいた。リベースせず、マージを取り消して ABORT する。"""
        gh = FakeGH([(5, "a")])
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.approve(gh.integration_pr())
        self.assertEqual(self.run_scheduler(gh), 0)

        gh2 = FakeGH([(6, "b")])
        self.assertEqual(self.run_scheduler(gh2), 0)   # 統合ブランチを作り直す
        stale = git(self.work, "rev-parse", f"origin/{self.INTEG}")
        gh3 = FakeGH([(7, "c")])
        self.plan["pipeline"] = {"7": "ahead"}
        # 誰かが先に統合ブランチを進めたのに、こちらの追跡参照は古いまま（fetch が届かない）
        with mock.patch.object(ms4.Git, "fetch_branch", lambda self, branch: None):
            self.assertEqual(self.run_scheduler(gh3), 2)
        self.assertEqual(git(self.runner_wt(), "rev-parse", "HEAD"), stale,
                         "マージを取り消して、統合ブランチに立つ前の状態に戻す")
        git(self.work, "fetch", "origin")
        self.assertNotEqual(stale, git(self.work, "rev-parse", f"origin/{self.INTEG}"),
                            "先に進めた側だけが残る")
        self.assertEqual(self.wt_dirty(), "")
        self.assertEqual(gh3.issues[7]["labels"], {"ms4:running"})
        self.assertIn("fast-forward", self.runs()[-1]["reason"])
        self.assertTrue(self.lock.exists())

    # 13
    def test_red_ci_on_main_aborts_without_closing(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
        gh.ci_conclusion = "failure"
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.issues[5]["labels"], {"ms4:integrated"})
        self.assertFalse(any("ms4 スケジューラ: 合格" in c for c in gh.issues[5]["comments"]))
        self.assertIn("failure", self.runs()[-1]["reason"])

    def test_ci_run_never_appears_aborts_after_waiting(self):
        gh = FakeGH([(5, "a")])
        self.run_scheduler(gh)
        gh.approve(gh.integration_pr())
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
        self.assertEqual(len(gh.prs[gh.integration_pr()]["comments"]), 1)
        self.assertGreaterEqual(self.sleeps.count(5), 3)

    def test_lost_response_does_not_double_post(self):
        """書き込みは反映されたのに失敗が返る。読み直して重ねないこと。"""
        gh = FakeGH([(5, "a")])
        gh.fail_rules.append({"match": "pr create", "times": 1, "apply": True})
        gh.fail_rules.append({"match": "pr comment", "times": 1, "apply": True})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs), 1, "PR を二重に作らない")
        self.assertEqual(len(gh.prs[gh.integration_pr()]["comments"]), 1)
        self.assertEqual(len(gh.issues[5]["comments"]), 2, "統合ブランチへのマージと、統合 PR の案内")

        gh.approve(gh.integration_pr())
        gh.fail_rules.append({"match": "pr merge", "times": 1, "apply": True})
        gh.fail_rules.append({"match": "issue comment", "times": 1, "apply": True})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.merge_args), 1, "マージを重ねない")
        self.assertEqual(len(gh.issues[5]["comments"]), 3)

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


# ============================================================ 常駐（Step 5）

RATE_LIMITED = "HTTP 403: API rate limit exceeded for user ID 1. (https://docs.github.com/rest/overview/rate-limits)"


GDD_WITH_ANCHORS = ("<!-- project: fb -->\n<!-- version: 1 -->\n# 仕様\n"
                    "- 1 ティックは 1/60 秒。乱数は XorShift32、シードを注入する。\n"
                    "- 不変条件: 消去ライン数 × 10 ＋ 盤面のブロック数 ＝ 4 × ロック数\n"
                    + "".join(f"- 形状 {s} 向き {r}: (0, 0) (1, 0) (2, 0) (3, 0)\n"
                              for s in "IOTSZJL" for r in ("0", "R", "2", "L")))
GDD_WITHOUT_ANCHORS = "<!-- project: fb -->\n<!-- version: 1 -->\n# 仕様\n- 幅 10 × 高さ 20。\n"


class GddPrTests(Base):
    """GDD の PR（B-2d）。構造化役はスタブ。GDD はヘルパーの clone から本物の git で push する。"""

    def setUp(self):
        super().setUp()
        stub = str(self.tmp / "stub.py")
        self.cfg["labels"].update({
            "gdd": {"name": "ms4:gdd", "color": "C5DEF5", "description": "g"},
            "questions": {"name": "ms4:questions", "color": "D876E3", "description": "q"}})
        self.cfg["commands"]["spec"] = ["{python}", stub, "spec", "{gdd}", "{telemetry}", "{out_dir}",
                                        "{work_dir}", "{previous_spec}"]
        self.cfg["ttl_seconds"]["spec"] = 60
        self.plan["spec_calls"] = str(self.tmp / "spec_calls.jsonl")

    def push_gdd(self, text, branch="spec/gdd-v1"):
        h = self.helper
        git(h, "fetch", "-q", "origin")
        git(h, "checkout", "-q", "-B", branch, "origin/main")
        (h / "docs" / "gdd").mkdir(parents=True, exist_ok=True)
        (h / "docs" / "gdd" / "source.md").write_bytes(text.encode("utf-8"))
        git(h, "add", "docs/gdd/source.md")
        git(h, "commit", "-q", "-m", "docs(gdd): v1")
        git(h, "push", "-q", "-f", "origin", branch)
        git(h, "checkout", "-q", "main")

    def gdd_pr(self, gh, text, branch="spec/gdd-v1", num=200):
        self.push_gdd(text, branch)
        gh.helper = self.helper   # run_scheduler の前に head を読むため
        gh.prs[num] = {"branch": branch, "base": "main", "title": "GDD v1", "body": "", "state": "OPEN",
                       "labels": {"ms4:gdd"}, "comments": [], "merge_sha": None}
        gh.labels |= {"ms4:gdd"}
        return num

    def spec_calls(self):
        p = Path(self.plan["spec_calls"])
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def sha(self, text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def assert_clean_after(self):
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.local_has_branch("spec/gdd-v1"), "作業用のローカルブランチを残さない")
        wt = self.out / "worktrees"
        self.assertTrue(not wt.exists() or not any(wt.iterdir()), "使い捨ての worktree を残さない")
        self.assertEqual(git(self.work, "worktree", "list").count("\n"), 0, "worktree の登録を残さない")

    def test_missing_anchors_returns_questions_without_calling_the_structurer(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITHOUT_ANCHORS)
        self.plan["spec"] = {"*": "abort"}      # 呼ばれたら ABORT になる
        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[num]
        self.assertEqual(pr["labels"], {"ms4:gdd", "ms4:questions"})
        marker = f"ms4:gdd-processed:{self.sha(GDD_WITHOUT_ANCHORS)}"
        self.assertEqual(sum(marker in c for c in pr["comments"]), 1)
        self.assertIn("「ティック」の記述が GDD にありません", pr["comments"][0])
        self.assertIn("0 組しかありません", pr["comments"][0])
        self.assertEqual(self.spec_calls(), [], "錨が足りない GDD では構造化役（LLM）を呼ばない")
        self.assertEqual(self.runs()[-1]["result"], "QUESTIONS")
        self.assert_clean_after()

        writes = len(gh.writes())
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.writes()), writes, "処理済みの GDD は再処理しない")

    def test_mergeable_spec_is_committed_to_the_pr_and_approval_is_requested(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        before = gh.head_of("spec/gdd-v1")
        self.assertEqual(self.run_scheduler(gh), 0)
        head = gh.head_of("spec/gdd-v1")
        self.assertNotEqual(head, before)
        git(self.helper, "fetch", "-q", "origin")
        changed = git(self.helper, "diff", "--name-only", before, head).splitlines()
        self.assertEqual(sorted(changed), ["docs/spec/questions.md", "docs/spec/spec.md"])
        pr = gh.prs[num]
        self.assertEqual(pr["labels"], {"ms4:gdd", "ms4:awaiting-approval"})
        self.assertEqual(sum(f"ms4:approval-request:{head}" in c for c in pr["comments"]), 1)
        self.assertTrue(any(f"ms4:gdd-processed:{self.sha(GDD_WITH_ANCHORS)}" in c and "マージ可" in c
                            for c in pr["comments"]))
        call = json.loads(self.spec_calls()[0])
        self.assertTrue(call[0].endswith("docs\\gdd\\source.md") or call[0].endswith("docs/gdd/source.md"))
        self.assertEqual(call[-1], "", "main に前の版の spec が無ければ --previous-spec は空")
        rec = self.runs()[-1]
        self.assertEqual((rec["kind"], rec["result"], rec["head_sha"]), ("gdd", "AWAITING", head))
        self.assert_clean_after()

        self.plan["spec"] = {"*": "abort"}
        writes = len(gh.writes())
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual((len(self.spec_calls()), len(gh.writes())), (1, writes),
                         "自分の push で head が変わっても、同じ GDD は再処理しない")

    def test_questions_label_without_approval_request(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.plan["spec"] = {"*": "questions"}
        self.assertEqual(self.run_scheduler(gh), 0)
        pr = gh.prs[num]
        self.assertEqual(pr["labels"], {"ms4:gdd", "ms4:questions"})
        self.assertFalse(any("ms4:approval-request" in c for c in pr["comments"]))
        self.assertTrue(any("Q-01 何倍か" in c for c in pr["comments"]))
        self.assert_clean_after()

    def test_structurer_failure_is_marked_and_not_retried(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        before = gh.head_of("spec/gdd-v1")
        self.plan["spec"] = {"*": "fail"}
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertEqual(gh.prs[num]["labels"], {"ms4:gdd", "ms4:failed"})
        self.assertEqual(gh.head_of("spec/gdd-v1"), before, "何もコミットしない")
        self.assert_clean_after()
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(self.spec_calls()), 1)

    def test_structurer_abort_leaves_no_marker_and_stops(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.plan["spec"] = {"*": "abort"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertFalse(any("ms4:gdd-processed" in c for c in gh.prs[num]["comments"]),
                         "環境異常では処理済みにしない（直してから再処理できるように）")
        self.assertEqual(self.dirty(), "")

    def test_changes_outside_docs_spec_abort_without_pushing(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        before = gh.head_of("spec/gdd-v1")
        self.plan["spec"] = {"*": "stray"}
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.head_of("spec/gdd-v1"), before)
        self.assertEqual(self.dirty(), "")
        self.assertFalse((self.out / "worktrees").exists() and any((self.out / "worktrees").iterdir()))

    def test_other_branch_names_are_ignored(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS, branch="feature/gdd")
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[num]["comments"], [])
        self.assertEqual(self.spec_calls(), [])

    def test_approved_gdd_pr_is_merged_with_a_merge_commit(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.assertEqual(self.run_scheduler(gh), 0)
        head = gh.head_of("spec/gdd-v1")
        gh.approve(num)
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.prs[num]["state"], "MERGED")
        self.assertIn("--merge", gh.merge_args[0])
        self.assertEqual(gh.merge_args[0][gh.merge_args[0].index("--match-head-commit") + 1], head)
        self.assertIn("docs/spec/spec.md", self.remote_main_files())
        self.assertEqual(self.runs()[-1]["result"], "PASSED")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), gh.prs[num]["merge_sha"])

    def test_approved_gdd_pr_waits_for_green_checks(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.approve(num)
        gh.check_override = {"test": ("completed", "failure")}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])
        self.assertEqual(gh.prs[num]["state"], "OPEN")

    def test_declined_gdd_pr_is_closed(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.prs[num]["labels"].add("ms4:declined")
        self.assertEqual(self.run_scheduler(gh), 1)
        self.assertEqual(gh.prs[num]["state"], "CLOSED")
        self.assertEqual(gh.merge_args, [])

    def test_questions_pr_is_never_merged_even_if_approved(self):
        gh = FakeGH([])
        num = self.gdd_pr(gh, GDD_WITH_ANCHORS)
        self.plan["spec"] = {"*": "questions"}
        self.assertEqual(self.run_scheduler(gh), 0)
        gh.prs[num]["labels"] |= {"ms4:approved", "ms4:awaiting-approval"}
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(gh.merge_args, [])
        self.assertEqual(gh.prs[num]["state"], "OPEN")


class ResidentTests(Base):
    """常駐・レート制限・死んだロック・ハートビート・自己監視。git は本物、GitHub は偽物。"""

    def heartbeat(self):
        return json.loads((self.out / "heartbeat.json").read_text(encoding="utf-8"))

    def stop_after(self, n):
        """n 回目の待機で停止ファイルを置く sleep。"""
        calls = []

        def sleep(secs):
            calls.append(secs)
            if len(calls) == n:
                (self.lock.parent / "scheduler.stop").write_text("x", encoding="utf-8")
        return sleep, calls

    # ---- レート制限
    def test_rate_limit_waits_until_reset_instead_of_aborting(self):
        gh = FakeGH([(5, "a")])
        gh.rate["core"] = (0, int(time.time()) + 600)
        gh.fail_rules.append({"match": "pr list", "times": 1, "message": RATE_LIMITED})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertGreaterEqual(sum(self.sleeps), 600, "リセットまで待つ（5 秒の再試行では抜けられない）")
        self.assertLess(sum(self.sleeps), 700)
        self.assertEqual(self.runs()[0]["result"], "PASSED", "待ったあと、そのまま処理を続ける")

    def test_secondary_rate_limit_backs_off_exponentially(self):
        gh = FakeGH([])
        gh.fail_rules.append({"match": "pr list", "times": 2, "message": "HTTP 403: You have exceeded a secondary rate limit"})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(sum(self.sleeps), 60 + 120)

    def test_rate_limit_beyond_the_cap_aborts(self):
        gh = FakeGH([(5, "a")])
        gh.rate["graphql"] = (0, int(time.time()) + 7200)
        gh.fail_rules.append({"match": "pr list", "times": 1, "message": RATE_LIMITED})
        self.assertEqual(self.run_scheduler(gh), 2)
        self.assertEqual(gh.prs, {})
        self.assertEqual(gh.issues[5]["labels"], {"ready"}, "Issue には着手していない")
        self.assertFalse(self.lock.exists(), "着手前の ABORT なのでロックは残さない")

    # ---- 常駐
    def test_watch_runs_cycles_until_the_stop_file(self):
        gh = FakeGH([(5, "a")])
        sleep, calls = self.stop_after(1)
        self.assertEqual(self.run_scheduler(gh, "--watch", "--interval", "10", sleep=sleep), 0)
        self.assertEqual(len(gh.prs), 1, "1 周目で Issue を処理した")
        self.assertFalse(self.lock.exists())
        self.assertFalse((self.lock.parent / "scheduler.stop").exists(), "停止ファイルは消す")
        self.assertEqual(self.heartbeat()["state"], "stopped")
        logs = list(self.out.glob("scheduler-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("常駐を終了", logs[0].read_text(encoding="utf-8"))

    def test_watch_exits_on_abort_and_keeps_the_lock(self):
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "abort"}
        sleep, _ = self.stop_after(1)
        self.assertEqual(self.run_scheduler(gh, "--watch", "--interval", "10", sleep=sleep), 2)
        self.assertIn("aborted", self.lock.read_text(encoding="utf-8"))
        self.assertEqual(self.heartbeat()["state"], "stopped")

    def test_watch_does_not_start_an_issue_when_the_budget_is_low(self):
        gh = FakeGH([(5, "a")])
        gh.rate["core"] = (10, int(time.time()) + 100)
        sleep, calls = self.stop_after(1)
        self.assertEqual(self.run_scheduler(gh, "--watch", sleep=sleep), 0)
        self.assertEqual(gh.writes(), [], "残量不足のあいだは Issue に触らない")
        self.assertEqual(gh.prs, {})

    def test_stop_and_status_commands(self):
        gh = FakeGH([])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(self.run_scheduler(gh, "--status"), 0)
        self.assertIn("ロック", out.getvalue())
        self.assertEqual(gh.calls, [], "--status は何も変えない")
        self.assertEqual(self.run_scheduler(gh, "--stop"), 0)
        self.assertTrue((self.lock.parent / "scheduler.stop").exists())

    def test_pythonw_without_stdout_writes_to_a_log_file(self):
        gh = FakeGH([])
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            self.assertEqual(self.run_scheduler(gh), 0)
        logs = list(self.out.glob("scheduler-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("対象: 0 件", logs[0].read_text(encoding="utf-8"))

    # ---- 死んだロック
    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=60)
        return p.pid

    def write_lock(self, *records):
        self.out.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(r) + "\n" for r in records)
        self.lock.write_text(text, encoding="utf-8")
        return text

    def test_dead_lock_without_abort_record_is_released(self):
        gh = FakeGH([(5, "a")])
        self.write_lock({"pid": self.dead_pid(), "run_id": "old"})
        self.assertEqual(self.run_scheduler(gh), 0)
        self.assertEqual(len(gh.prs), 1)
        self.assertFalse(self.lock.exists())

    def test_live_or_aborted_or_malformed_locks_are_kept(self):
        cases = {
            "生きている pid": [{"pid": os.getpid(), "run_id": "now"}],
            "ABORT の記録": [{"pid": self.dead_pid(), "run_id": "old"}, {"aborted": "理由"}],
            "pid が無い": [{"run_id": "old"}],
        }
        for label, records in cases.items():
            with self.subTest(label):
                gh = FakeGH([(5, "a")])
                text = self.write_lock(*records)
                self.assertEqual(self.run_scheduler(gh), 2)
                self.assertEqual(gh.calls, [])
                self.assertEqual(self.lock.read_text(encoding="utf-8"), text)
                self.lock.unlink()

    # ---- ハートビートと自己監視
    def test_heartbeat_is_updated_while_waiting_for_a_child(self):
        gh = FakeGH([(5, "a")])
        self.plan["pipeline"] = {"5": "sleep"}
        self.cfg["ttl_seconds"]["pipeline"] = 4
        self.cfg["heartbeat_seconds"] = 1
        beats, real = [], ms4.Heartbeat.beat

        def spy(hb, **fields):
            beats.append(fields)
            return real(hb, **fields)
        with mock.patch.object(ms4.Heartbeat, "beat", spy):
            self.assertEqual(self.run_scheduler(gh), 2)
        with_child = [b for b in beats if b.get("child_pid")]
        self.assertGreaterEqual(len(with_child), 3, "起動直後と、待つ間の刻みごとに更新する")

    def test_watchdog_kills_the_child_tree_and_exits_when_heartbeat_stalls(self):
        s = ms4.Scheduler(self.cfg, gh_run=FakeGH([]), sleep=lambda s: None)
        exits = []
        s._exit = exits.append
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            s.hb.beat(state="running", child_pid=child.pid)
            self.assertFalse(s.watchdog_check(900), "更新されたばかりなら何もしない")
            s.hb.last -= 1000
            self.assertTrue(s.watchdog_check(900))
            child.wait(timeout=60)
        finally:
            if child.poll() is None:
                child.kill()
        self.assertEqual(exits, [2])
        hb = self.heartbeat()
        self.assertEqual(hb["state"], "stopped")
        self.assertIn("自己監視", hb["reason"])


if __name__ == "__main__":
    unittest.main()
