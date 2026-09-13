"""MS4 スケジューラ。ready の Issue を 1 本ずつ、分解 → 監査 → 実装 → PR → 承認 → マージまで運ぶ。

    python harness/scheduler.py --project unity-2d --dry-run     # 対象と予定だけ表示。何も変えない
    python harness/scheduler.py --project unity-2d               # 一覧を 1 周して終わる
    python harness/scheduler.py --project unity-2d --max-issues 1

終了コード: 0=不合格なし（マージ・承認待ち・対象なしを含む） / 1=不合格を含む / 2=ABORT（停止）

**1 周の流れ**

    1. 承認待ちの PR（ms4:awaiting-approval）を見る
         ms4:declined  → PR を閉じ、Issue を ms4:failed
         ms4:approved  → 必須チェック（required_checks）が今の head SHA で全部 success なら
                         gh pr merge --merge --match-head-commit <その SHA>
                         → main の CI を待つ → Issue をクローズ
                         承認後に push されていたら、マージせず待ちに戻す
         それ以外      → 待つ
    2. ready の Issue
         ready → ms4:running
         ブランチ ms4/issue-N を切る（既にあれば不合格。前回の残骸か人間の作業中）
         decompose.py   0 → 次 / 1 → 不合格 / それ以外 → ABORT
         生成物をコミット（想定外のパスがあれば ABORT）
         audit.py       キーが無ければスキップ。結果は合否に使わない（required=false のとき）
         pipeline.py    0 → PR / 1 → 不合格 / それ以外 → ABORT
         PR を作り、受入テストの一覧（C# から機械抽出）をコメントし、ms4:awaiting-approval

**なぜ承認が実装の後なのか**

承認は最終的な head SHA に対して 1 回だけ有効で、push されると必須チェック approval が
ラベルを外す。実装前にテストを承認させると、実装の push で承認が消える。
代償として、テストが誤っていても実装を 1 回走らせてしまう。

**スケジューラ自身は ms4:approved を付けない。** 付けるのは人間（dispatch --approve）だけ。
ただし同じ GitHub アカウントで動く限り、approval チェックは両者を区別できない（慣習）。

**なぜブランチを切るのか**

パイプラインのサンドボックスは HEAD から同期し、起動時に作業ツリーが clean で
あることを要求する。分解役が書いたテストはコミットしないと届かない。一方、
実装の無いテストを main にコミットすると CI が赤になる（main を壊す検証は禁止）。

**ABORT で何を残すか**

作業ツリーには一切触らない。証拠を保全する（無差別な checkout で自分の修正を
消した実例がある）。Issue は ms4:running のまま残し、次回の一覧から外す。
Issue に着手した後の ABORT ではロックも残す。人間が原因を見るまで次を回さない。

**不合格（1）で何をするか**

ms4:failed を付け、ログ末尾をコメントし、main に戻って次の Issue へ進む。
その時点で作業ツリーが汚れていたら、それはもう不合格ではなく ABORT に格上げする。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import exitcode
import project
import test_summary

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class Abort(Exception):
    """環境異常。スケジューラを止める。"""

    def __init__(self, msg, log=None):
        super().__init__(msg)
        self.log = log


class Reject(Exception):
    """その Issue だけの不合格。次の Issue へ進んでよい。"""

    def __init__(self, msg, log=None, delete_branch=False):
        super().__init__(msg)
        self.log = log
        self.delete_branch = delete_branch


# ============================================================ プロセス

def run_cmd(args, cwd, ttl):
    """短いコマンド（git / gh）。出力はメモリに取る。"""
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=ttl, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"TTL超過 ({ttl}s): {' '.join(args[:3])}"
    except FileNotFoundError as e:
        return 127, "", f"コマンドが見つかりません: {e}"


def kill_tree(proc):
    """子だけ殺すと孫（Unity・agy）が孤児になって走り続ける。木ごと止める。"""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW, timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()  # taskkill 自体が固まったら、せめて直下の子は止める
    else:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_logged(args, cwd, ttl, log_path, env):
    """長い子プロセス（decompose / audit / pipeline）。出力は逐次ファイルへ。

    pipeline は 1 時間を超えうる。メモリに溜めて最後に書くと、途中で何が
    起きているか誰にも見えず、殺されたときに何も残らない。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as f:
        f.write(("$ " + " ".join(args) + "\n\n").encode("utf-8"))
        f.flush()
        try:
            proc = subprocess.Popen(args, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env,
                                    creationflags=_NO_WINDOW)
        except FileNotFoundError as e:
            f.write(f"コマンドが見つかりません: {e}\n".encode("utf-8"))
            return 127
        try:
            return proc.wait(timeout=ttl)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            f.write(f"\n\nTTL超過 ({ttl}s)。プロセス木ごと停止しました\n".encode("utf-8"))
            return 124


def tail_of(log_path, chars):
    try:
        text = Path(log_path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return "（ログを読めません）"
    return text[-chars:]


# ============================================================ ネットワーク再試行

def with_retries(attempt, done, retries, interval, sleep, what):
    """attempt() -> (ok, detail)。失敗したら interval 待って再試行する。

    書き込みは「失敗と表示されたが実は反映されていた」がありうる（応答だけ落ちた）。
    そのまま重ねるとコメントが二重に付くので、再試行の前に done() で読み直し、
    反映済みなら何もしない。読み取りは done=None。
    """
    detail = ""
    for i in range(retries + 1):
        if i > 0:
            sleep(interval)
            if done is not None and done():
                return None
        ok, detail = attempt()
        if ok:
            return detail
    raise Abort(f"{what} が {retries + 1} 回とも失敗しました: {str(detail)[:300]}")


# ============================================================ git

class Git:
    def __init__(self, repo, cfg, sleep):
        self.repo = Path(repo)
        self.ttl = cfg["ttl_seconds"]["git"]
        self.retries = cfg["net_retries"]
        self.interval = cfg["net_retry_interval_seconds"]
        self.sleep = sleep

    def _git(self, *args, check=True):
        rc, out, err = run_cmd(["git", "-c", "core.quotePath=false"] + list(args),
                               self.repo, self.ttl)
        if check and rc != 0:
            raise Abort(f"git {' '.join(args)} が失敗 (rc={rc}): {(err or out)[:300]}")
        return rc, out, err

    def _net(self, *args):
        def attempt():
            rc, out, err = self._git(*args, check=False)
            return rc == 0, out if rc == 0 else (err or out)
        return with_retries(attempt, None, self.retries, self.interval, self.sleep,
                            "git " + " ".join(args))

    def current_branch(self):
        return self._git("branch", "--show-current")[1].strip()

    def changed_paths(self):
        """未追跡を含む変更パス。-z で空白や日本語のパスも崩さない。"""
        _, out, _ = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        paths = []
        for entry in out.split("\0"):
            if len(entry) > 3:
                paths.append(entry[3:])
        return sorted(set(paths))

    def local_branch_exists(self, branch):
        rc, _, _ = self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
                             check=False)
        return rc == 0

    def remote_branch_exists(self, branch):
        out = self._net("ls-remote", "--heads", "origin", branch) or ""
        return bool(out.strip())

    def switch_new(self, branch):
        self._git("switch", "-c", branch)

    def switch(self, branch):
        self._git("switch", branch)

    def delete_branch(self, branch, force):
        self._git("branch", "-D" if force else "-d", branch)

    def add_commit(self, paths, message):
        self._git("add", "--", *paths)
        self._git("commit", "-m", message)

    def pull_ff(self):
        self._net("pull", "--ff-only")

    def push_upstream(self, branch):
        self._net("push", "--set-upstream", "origin", branch)

    def push(self, branch):
        self._net("push", "origin", branch)

    def head_sha(self):
        return self._git("rev-parse", "HEAD")[1].strip()


# ============================================================ GitHub

class GitHub:
    """gh の呼び出しはすべてここを通す。テストでは run を偽物に差し替える。"""

    def __init__(self, cfg, run, sleep):
        self.slug = cfg["repo_slug"]
        self.ttl = cfg["ttl_seconds"]["gh"]
        self.retries = cfg["net_retries"]
        self.interval = cfg["net_retry_interval_seconds"]
        self.labels = cfg["labels"]
        self.run = run
        self.sleep = sleep
        self.cwd = cfg["repo_dir"]

    def _once(self, args):
        rc, out, err = self.run(["gh"] + args + ["--repo", self.slug], self.cwd, self.ttl)
        return rc == 0, out if rc == 0 else (err or out or f"rc={rc}")

    def read(self, args):
        return with_retries(lambda: self._once(args), None, self.retries, self.interval,
                            self.sleep, "gh " + " ".join(args[:2]))

    def read_json(self, args):
        out = self.read(args)
        try:
            return json.loads(out or "null")
        except ValueError:
            raise Abort(f"gh {' '.join(args[:2])} の出力が JSON ではありません: {out[:200]}")

    def write(self, args, done):
        with_retries(lambda: self._once(args), done, self.retries, self.interval,
                     self.sleep, "gh " + " ".join(args[:2]))

    def api_json(self, path):
        """gh api は --repo を取らないので、パスにリポジトリを入れて呼ぶ。"""
        def attempt():
            rc, out, err = self.run(["gh", "api", path], self.cwd, self.ttl)
            return rc == 0, out if rc == 0 else (err or out or f"rc={rc}")
        out = with_retries(attempt, None, self.retries, self.interval, self.sleep,
                           "gh api " + path.split("?")[0])
        try:
            return json.loads(out or "null")
        except ValueError:
            raise Abort(f"gh api {path} の出力が JSON ではありません: {out[:200]}")

    # ---- 読み取り
    def list_ready(self):
        items = self.read_json(["issue", "list", "--label", self.labels["ready"]["name"],
                                "--state", "open", "--limit", "100",
                                "--json", "number,title,labels"])
        skip = {self.labels[k]["name"] for k in ("running", "failed", "awaiting")}
        picked = []
        for it in items or []:
            names = {l["name"] for l in it.get("labels", [])}
            if not names & skip:
                picked.append({"number": it["number"], "title": it["title"]})
        return sorted(picked, key=lambda x: x["number"])

    def list_waiting_prs(self):
        items = self.read_json(["pr", "list", "--label", self.labels["awaiting"]["name"],
                                "--state", "open", "--limit", "100",
                                "--json", "number,headRefName"])
        return sorted(items or [], key=lambda x: x["number"])

    def find_pr(self, branch):
        items = self.read_json(["pr", "list", "--head", branch, "--state", "open",
                                "--json", "number"]) or []
        return items[0]["number"] if items else None

    def view(self, number, kind="issue"):
        """kind は issue か pr。PR では head の SHA とマージコミットも返す。"""
        fields = "state,labels,comments"
        if kind == "pr":
            fields += ",headRefName,headRefOid,mergeCommit"
        d = self.read_json([kind, "view", str(number), "--json", fields])
        v = {
            "state": d.get("state"),
            "labels": {l["name"] for l in d.get("labels", [])},
            "comments": [c.get("body", "") for c in d.get("comments", [])],
        }
        if kind == "pr":
            v.update(branch=d.get("headRefName"), sha=d.get("headRefOid"),
                     merge_sha=(d.get("mergeCommit") or {}).get("oid"))
        return v

    def check_states(self, sha, names):
        """必須チェックごとの状態: success / failure 等 / pending / missing。

        同じ名前の run が複数あれば（再実行・ラベルの付け外し）、id が最大のものを採る。
        時刻では比べない。
        """
        d = self.api_json(f"repos/{self.slug}/commits/{sha}/check-runs?per_page=100") or {}
        latest = {}
        for r in d.get("check_runs", []):
            name = r.get("name")
            if name in names and (name not in latest or r["id"] > latest[name]["id"]):
                latest[name] = r
        states = {}
        for name in names:
            r = latest.get(name)
            if r is None:
                states[name] = "missing"
            elif r.get("status") != "completed":
                states[name] = "pending"
            else:
                states[name] = r.get("conclusion") or "unknown"
        return states

    def label_names(self):
        return {l["name"] for l in
                self.read_json(["label", "list", "--limit", "200", "--json", "name"]) or []}

    def find_run(self, sha):
        runs = self.read_json(["run", "list", "--commit", sha,
                               "--json", "databaseId,status,conclusion"]) or []
        return str(runs[0]["databaseId"]) if runs else None

    def run_state(self, run_id):
        d = self.read_json(["run", "view", run_id, "--json", "status,conclusion"])
        return d.get("status"), d.get("conclusion")

    # ---- 書き込み（再試行の前に読み直す）
    def ensure_labels(self):
        have = self.label_names()
        for spec in self.labels.values():
            if spec["name"] in have:
                continue
            self.write(["label", "create", spec["name"], "--color", spec["color"],
                        "--description", spec["description"]],
                       done=lambda n=spec["name"]: n in self.label_names())

    def set_labels(self, number, add=(), remove=(), kind="issue"):
        add = [self.labels[k]["name"] for k in add]
        remove = [self.labels[k]["name"] for k in remove]

        def done():
            now = self.view(number, kind)["labels"]
            return set(add) <= now and not (set(remove) & now)

        now = self.view(number, kind)["labels"]
        if set(add) <= now and not (set(remove) & now):
            return
        args = [kind, "edit", str(number)]
        for n in add:
            if n not in now:
                args += ["--add-label", n]
        for n in remove:
            if n in now:  # 付いていないラベルを外す指定は渡さない
                args += ["--remove-label", n]
        self.write(args, done)

    def comment(self, number, marker, body, kind="issue"):
        """marker（HTML コメント）で同じ投稿を識別し、二重に付けない。"""
        text = f"<!-- {marker} -->\n{body}"
        if any(marker in c for c in self.view(number, kind)["comments"]):
            return
        self.write([kind, "comment", str(number), "--body", text],
                   done=lambda: any(marker in c for c in self.view(number, kind)["comments"]))

    def close(self, number, kind="issue"):
        if self.view(number, kind)["state"] in ("CLOSED", "MERGED"):
            return
        self.write([kind, "close", str(number)],
                   done=lambda: self.view(number, kind)["state"] in ("CLOSED", "MERGED"))

    def create_pr(self, branch, base, title, body):
        existing = self.find_pr(branch)
        if existing:
            return existing
        self.write(["pr", "create", "--head", branch, "--base", base,
                    "--title", title, "--body", body],
                   done=lambda: self.find_pr(branch) is not None)
        n = self.find_pr(branch)
        if not n:
            raise Abort(f"PR を作ったはずが見つかりません: {branch}")
        return n

    def merge_pr(self, number, sha):
        """--merge（マージコミット。squash しない）と --match-head-commit（承認した SHA 以外は入れない）。"""
        self.write(["pr", "merge", str(number), "--merge", "--match-head-commit", sha],
                   done=lambda: self.view(number, "pr")["state"] == "MERGED")


# ============================================================ スケジューラ

class Scheduler:
    def __init__(self, cfg, gh_run=run_cmd, sleep=time.sleep):
        self.cfg = cfg
        self.repo = Path(cfg["repo_dir"])
        self.out = Path(cfg["out_dir"])
        self.base = cfg["base_branch"]
        self.sleep = sleep
        self.git = Git(self.repo, cfg, sleep)
        self.gh = GitHub(cfg, gh_run, sleep)
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.lock = Path(cfg.get("lock_path") or self.out / "scheduler.lock")
        self.touched = False

        self.test_dir = cfg["test_dir"].strip("/")
        self.audit_dir = cfg["audit_dir"].strip("/")

        self.env = dict(os.environ)
        # 子の Python がパイプへ CP932 で書くと、ログとコメントが化ける（実測）。
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"

    # ---- ロック
    def acquire_lock(self):
        try:
            fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "run_id": self.run_id}) + "\n")
        return True

    def release_lock(self):
        try:
            self.lock.unlink()
        except FileNotFoundError:
            pass

    # ---- 記録
    def record(self, rec):
        rec["finished"] = datetime.now().isoformat(timespec="seconds")
        with (self.out / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def step(self, rec, name, tag=None, **fmt):
        args = [a.format(python=sys.executable, harness=project.HARNESS_DIR.as_posix(),
                         project=self.cfg.get("project_id", ""), **fmt)
                for a in self.cfg["commands"][name]]
        log = self.out / f"issue_{rec['issue']}" / (f"{name}_{tag}.log" if tag else f"{name}.log")
        print(f"  [{name}{' ' + tag if tag else ''}] 実行中… ログ: {log}")
        t0 = time.monotonic()
        rc = run_logged(args, self.repo, self.cfg["ttl_seconds"][name], log, self.env)
        secs = round(time.monotonic() - t0, 1)
        rec["steps"].append({"name": name, "tag": tag, "rc": rc, "seconds": secs, "log": str(log)})
        print(f"  [{name}{' ' + tag if tag else ''}] rc={rc} ({secs}s)")
        return rc, log

    # ---- 前提
    def preflight(self):
        missing = [c for c in self.cfg["required_clis"]
                   if not any(shutil.which(c + ext) for ext in (".cmd", ".exe", ""))]
        if missing:
            raise Abort("CLI が PATH にありません: " + ", ".join(missing))
        br = self.git.current_branch()
        if br != self.base:
            raise Abort(f"{self.base} ではなく {br or '(detached)'} に居ます")
        dirty = self.git.changed_paths()
        if dirty:
            raise Abort("作業ツリーが汚れています: " + ", ".join(dirty[:5]))
        self.git.pull_ff()

    def require_only(self, changed, allowed, phase):
        bad = [p for p in changed if not any(a(p) for a in allowed)]
        if bad:
            raise Abort(f"{phase} が想定外のパスを変更しました: " + ", ".join(bad[:5]))

    # ---- 1 Issue
    def process(self, n, title, rec):
        branch = f"{self.cfg['branch_prefix']}{n}"
        unit = self.cfg["unit_path_template"].format(number=n)
        rec["branch"] = branch

        self.gh.set_labels(n, add=["running"], remove=["ready"])

        if self.git.local_branch_exists(branch) or self.git.remote_branch_exists(branch):
            raise Reject(f"ブランチ `{branch}` が既にあります。前回の残骸か、人間の作業中です。"
                         "中身を確認してブランチを消し、`ready` を付け直してください。")
        self.git.switch_new(branch)

        # ---- 分解
        rc, log = self.step(rec, "decompose", number=n)
        if rc == 1:
            raise Reject("分解役の出力が要件を満たしませんでした（何も書き出していません）。",
                         log=log, delete_branch=True)
        if rc != 0:
            raise Abort(f"decompose.py が rc={rc} で終了しました（環境異常）", log=log)

        changed = self.git.changed_paths()
        if unit not in changed:
            raise Abort(f"decompose.py は rc=0 ですが単位定義 {unit} がありません", log=log)
        in_tests = lambda p: p.startswith(self.test_dir + "/")
        self.require_only(changed, [lambda p: p == unit, in_tests], "decompose.py")
        tests = [p for p in changed if in_tests(p)]
        if not tests:
            raise Abort("decompose.py は rc=0 ですがテストファイルがありません", log=log)
        rec["tests"] = tests
        self.git.add_commit(changed, f"test(ms4): acceptance tests for issue #{n}")

        # ---- 監査
        audit_failed = self.audit(rec, [unit] + tests)
        changed = self.git.changed_paths()
        if changed:
            self.require_only(changed, [lambda p: p.startswith(self.audit_dir + "/")], "audit.py")
            self.git.add_commit(changed, f"docs(audit): audit reports for issue #{n}")
        if audit_failed and self.cfg["audit"]["required"]:
            raise Reject("監査が必須の設定ですが、監査が完了しませんでした: " + rec["audit"])

        self.git.push_upstream(branch)

        # ---- 実装
        rc, log = self.step(rec, "pipeline", unit=unit)
        if rc == 1:
            raise Reject("実装パイプラインが不合格でした（全試行で門を通りませんでした）。"
                         f"ブランチ `{branch}` は残してあります。", log=log)
        if rc != 0:
            raise Abort(f"ms3_pipeline.py が rc={rc} で終了しました（環境異常）", log=log)

        self.open_pr(n, title, branch, unit, rec)

    def audit(self, rec, files):
        """戻り値: 監査が完了しなかったら True。合否に使うかは呼び出し側が設定で決める。"""
        key_env = self.cfg["audit"]["key_env"]
        if not os.environ.get(key_env):
            rec["audit"] = f"skipped ({key_env} 未設定)"
            print(f"  [audit] {key_env} が無いのでスキップ")
            return True
        failed = []
        for f in files:
            rc, _ = self.step(rec, "audit", tag=Path(f).stem, file=f)
            if rc != 0:
                failed.append(f"{f} rc={rc}")
        rec["audit"] = "failed: " + "; ".join(failed) if failed else "done"
        return bool(failed)

    def open_pr(self, n, title, branch, unit, rec):
        """門を通った実装を PR にし、人間の承認を待つ。ここではマージしない。

        承認は最終的な head SHA に対して 1 回だけ行う。承認後に push すると
        必須チェック approval が承認を外すので、承認の前に実装を済ませておく。
        """
        dirty = self.git.changed_paths()
        if dirty:
            raise Abort("パイプラインは合格ですが作業ツリーが汚れています: " + ", ".join(dirty[:5]))
        self.git.push(branch)  # パイプラインが push 済みのはず。同じなら何も起きない
        sha = self.git.head_sha()
        rec["head_sha"] = sha
        summary = self.approval_summary(n, unit, rec, sha)
        self.git.switch(self.base)

        pr = self.gh.create_pr(
            branch, self.base, f"#{n}: {title}",
            f"Closes #{n}\n\nms4 スケジューラが作成した PR です。"
            "受入テストの一覧はコメントにあります。承認は dispatch から行います。\n")
        rec["pr"] = pr
        self.gh.comment(pr, f"ms4:approval-request:{sha}", summary, kind="pr")
        self.gh.set_labels(pr, add=["awaiting"], kind="pr")
        self.gh.set_labels(n, add=["awaiting"], remove=["running", "ready"])

    def approval_summary(self, n, unit, rec, sha):
        """承認依頼の本文。テスト一覧は C# から機械的に抜き出す（LLM に要約させない）。"""
        tests = rec.get("tests") or []
        try:
            total, md = test_summary.summarize_files(tests, root=self.repo)
        except (OSError, ValueError) as e:
            raise Abort(f"受入テストの一覧を作れません: {e}")
        if total == 0:
            raise Abort("受入テストを 1 件も読み取れません。空の一覧で承認させません: "
                        + ", ".join(tests))
        try:
            hcp = json.loads((self.repo / unit).read_text(encoding="utf-8")).get("human_check_point")
        except (OSError, ValueError):
            hcp = None
        project_id = self.cfg.get("project_id") or self.cfg["repo_slug"]
        body = (
            f"**ms4: 承認依頼** — Issue #{n}\n\n"
            f"対象コミット: `{sha[:8]}`（この SHA に対してだけ有効。push されると承認は外れます）\n\n"
            f"### 受入テスト（分解役が書いたもの。C# から機械抽出。合計 {total} 件）\n\n{md}\n\n"
            f"### 人間が実機で見ること\n\n{hcp or '（単位定義に human_check_point がありません）'}\n\n"
            f"### 監査\n\n{rec.get('audit', '（記録なし）')}\n\n"
            f"### 承認\n\n"
            f"```\npython tools/dispatch.py --approve {project_id}#PR番号\n"
            f"python tools/dispatch.py --decline {project_id}#PR番号 --reason \"...\"\n```\n")
        limit = self.cfg.get("comment_max_chars", 60000)
        if len(body) > limit:
            body = body[:limit] + "\n\n…（長すぎるため省略。テストファイルを直接確認してください）\n"
        return body

    def handle_waiting(self, item):
        """承認待ちの PR を 1 本見る。承認済みならマージ、却下なら不合格、それ以外は待つ。"""
        prefix = self.cfg["branch_prefix"]
        branch = item.get("headRefName") or ""
        if not branch.startswith(prefix) or not branch[len(prefix):].isdigit():
            return "WAITING"  # スケジューラが作った PR ではない
        n, num = int(branch[len(prefix):]), item["number"]
        pr = self.gh.view(num, "pr")
        names = pr["labels"]
        L = {k: self.labels_name(k) for k in ("approved", "declined")}

        if L["declined"] not in names and L["approved"] not in names:
            return "WAITING"
        if L["declined"] not in names:
            states = self.gh.check_states(pr["sha"], self.cfg["required_checks"])
            not_ok = {k: v for k, v in states.items() if v != "success"}
            if not_ok:
                print(f"  PR #{num}: 承認ラベルはあるが必須チェックが揃っていません: {not_ok}")
                return "WAITING"

        rec = {"run_id": self.run_id, "issue": n, "pr": num, "branch": branch, "steps": [],
               "started": datetime.now().isoformat(timespec="seconds")}
        self.touched = True
        try:
            if L["declined"] in names:
                print(f"\n=== PR #{num}（Issue #{n}）: 却下")
                self.gh.close(num, "pr")
                self.gh.comment(n, f"ms4:{n}:declined:{pr['sha']}",
                                f"**ms4: 人間が却下しました**（PR #{num}、`{pr['sha'][:8]}`）\n\n"
                                "理由は PR のコメントを見てください。ブランチは残してあります。"
                                "直すには、ブランチを消して要求を直し、`ready` を付け直してください。\n")
                self.gh.set_labels(num, remove=["awaiting"], kind="pr")
                self.gh.set_labels(n, add=["failed"], remove=["awaiting", "running", "ready"])
                rec["result"] = "REJECT"
                rec["reason"] = "declined"
                return "REJECT"

            print(f"\n=== PR #{num}（Issue #{n}）: 承認済み。マージします")
            try:
                self.gh.merge_pr(num, pr["sha"])
            except Abort:
                now = self.gh.view(num, "pr")
                if now["state"] != "MERGED" and now["sha"] != pr["sha"]:
                    print(f"  承認後に push されました（{pr['sha'][:8]} → {now['sha'][:8]}）。承認待ちに戻します")
                    rec["result"] = "WAITING"
                    rec["reason"] = "head moved after approval"
                    return "WAITING"
                if now["state"] != "MERGED":
                    raise
            merged = self.gh.view(num, "pr")
            rec["merge_sha"] = merged["merge_sha"]
            self.git.pull_ff()
            run_id = self.wait_ci(merged["merge_sha"])
            rec["ci_run"] = run_id

            self.gh.comment(n, f"ms4:{n}:passed:{merged['merge_sha']}",
                            f"**ms4 スケジューラ: 合格**\n\n"
                            f"- PR #{num} をマージ: `{merged['merge_sha'][:8]}`（承認した `{pr['sha'][:8]}`）\n"
                            f"- main の CI: run {run_id}\n"
                            f"- 記録: `reports/TIMELINE.md` の末尾\n")
            self.gh.close(n)
            self.gh.set_labels(num, remove=["awaiting"], kind="pr")
            self.gh.set_labels(n, remove=["awaiting", "running", "ready"])
            if self.git.local_branch_exists(branch):
                self.git.delete_branch(branch, force=False)
            rec["result"] = "PASSED"
            print(f"=== #{n} 合格（マージ済み）")
            return "PASSED"
        except Abort as e:
            rec["result"], rec["reason"] = "ABORT", str(e)
            self.on_abort(n, e)
            raise
        finally:
            if rec.get("result") != "WAITING":
                self.record(rec)

    def labels_name(self, key):
        return self.cfg["labels"][key]["name"]

    def wait_ci(self, sha):
        """SHA で run を特定してから待つ（直近の run を拾うと別の結果を見る。欠陥 7）。

        run が現れるまでと、完了するまでを別々に待つ。キュー待ちの間は ABORT しない。
        完了は終了コードではなく conclusion で判定する。
        """
        interval = self.cfg["ci_poll_interval_seconds"]
        run_id = None
        for _ in range(max(1, -(-self.cfg["ci_find_seconds"] // interval))):
            run_id = self.gh.find_run(sha)
            if run_id:
                break
            self.sleep(interval)
        if not run_id:
            raise Abort(f"main の CI run が {self.cfg['ci_find_seconds']}s 以内に見つかりません "
                        f"(sha={sha[:8]})。マージは push 済みです")
        for _ in range(max(1, -(-self.cfg["ci_watch_seconds"] // interval))):
            status, conclusion = self.gh.run_state(run_id)
            if status == "completed":
                if conclusion != "success":
                    raise Abort(f"main の CI が {conclusion} です (run={run_id})。"
                                "マージは push 済みで、main が壊れている可能性があります")
                return run_id
            self.sleep(interval)
        raise Abort(f"main の CI が {self.cfg['ci_watch_seconds']}s 以内に終わりません (run={run_id})")

    # ---- 結末
    def on_reject(self, n, e, rec):
        dirty = self.git.changed_paths()
        if dirty:
            raise Abort("不合格の後始末の時点で作業ツリーが汚れています（掃除しません）: "
                        + ", ".join(dirty[:5]), log=e.log)
        body = f"**ms4 スケジューラ: 不合格**\n\n{e}\n"
        if e.log:
            tail = tail_of(e.log, self.cfg["comment_log_tail_chars"]).replace("```", "'''")
            body += (f"\nログ: `{e.log}`\n\n<details><summary>ログ末尾</summary>\n\n"
                     f"```\n{tail}\n```\n</details>\n")
        body += "\n再実行するには、原因を直し、ブランチがあれば消してから `ready` を付け直してください。\n"
        self.gh.comment(n, f"ms4:{n}:reject:{self.run_id}", body)
        self.gh.set_labels(n, add=["failed"], remove=["running", "ready"])
        if self.git.current_branch() != self.base:
            self.git.switch(self.base)
        branch = rec.get("branch")
        if e.delete_branch and branch and self.git.local_branch_exists(branch):
            self.git.delete_branch(branch, force=True)

    def on_abort(self, n, e):
        """GitHub への報告は試みるだけ。git と作業ツリーには触らない。"""
        body = (f"**ms4 スケジューラ: ABORT（停止）**\n\n{e}\n\n"
                f"作業ツリーとブランチはそのまま残しています。"
                f"ロック `{self.lock}` も残しています。原因を確認してから消してください。\n")
        if e.log:
            body += f"\nログ: `{e.log}`\n"
        try:
            self.gh.comment(n, f"ms4:{n}:abort:{self.run_id}", body)
        except Exception as ce:  # 報告の失敗で本来の ABORT 理由を覆い隠さない
            print(f"  Issue への ABORT 報告に失敗: {ce}")

    def handle(self, issue):
        n, title = issue["number"], issue["title"]
        rec = {"run_id": self.run_id, "issue": n, "title": title, "steps": [],
               "started": datetime.now().isoformat(timespec="seconds")}
        print(f"\n=== Issue #{n}: {title}")
        self.touched = True
        try:
            try:
                self.process(n, title, rec)
                rec["result"] = "AWAITING"
                print(f"=== #{n} 門を通過。PR #{rec.get('pr')} で人間の承認を待ちます")
                return "AWAITING"
            except Reject as e:
                rec["result"], rec["reason"] = "REJECT", str(e)
                print(f"=== #{n} 不合格: {e}")
                self.on_reject(n, e, rec)
                return "REJECT"
        except Abort as e:
            rec["result"], rec["reason"] = "ABORT", str(e)
            self.on_abort(n, e)
            raise
        finally:
            self.record(rec)

    # ---- 入口
    def dry_run(self):
        print(f"ブランチ: {self.git.current_branch()} / 変更: {len(self.git.changed_paths())} 件")
        waiting = self.gh.list_waiting_prs()
        print(f"承認待ちの PR: {len(waiting)} 本")
        for item in waiting:
            pr = self.gh.view(item["number"], "pr")
            mark = ("却下" if self.labels_name("declined") in pr["labels"] else
                    "承認済み" if self.labels_name("approved") in pr["labels"] else "未承認")
            print(f"  PR #{item['number']} {item.get('headRefName')} `{(pr['sha'] or '')[:8]}` {mark}")
        issues = self.gh.list_ready()
        print(f"対象: {len(issues)} 件（上限 {self.cfg['max_issues_per_run']}）")
        for it in issues[:self.cfg["max_issues_per_run"]]:
            n = it["number"]
            unit = self.cfg["unit_path_template"].format(number=n)
            print(f"\n#{n} {it['title']}")
            print(f"  ブランチ: {self.cfg['branch_prefix']}{n}")
            for name, fmt in (("decompose", {"number": n}), ("audit", {"file": unit}),
                              ("pipeline", {"unit": unit})):
                print("  " + " ".join(a.format(python="python", **fmt)
                                      for a in self.cfg["commands"][name]))
        print("\n（dry-run: 何も変更していません）")
        return 0

    def run(self, dry_run=False, max_issues=None):
        self.out.mkdir(parents=True, exist_ok=True)
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            try:
                return self.dry_run()
            except Abort as e:
                print(f"ABORT: {e}")
                return 2

        if not self.acquire_lock():
            print(f"ABORT: ロック {self.lock} があります。別のスケジューラが動いているか、"
                  "前回が ABORT で止まっています。確認してから消してください。")
            return 2

        results = []
        try:
            self.preflight()
            self.gh.ensure_labels()
            # 先に承認待ちを片付ける（承認済みはマージ、却下は不合格）
            waiting = self.gh.list_waiting_prs()
            if waiting:
                print(f"承認待ちの PR: {len(waiting)} 本")
            for item in waiting:
                results.append(self.handle_waiting(item))
            limit = max_issues or self.cfg["max_issues_per_run"]
            issues = self.gh.list_ready()[:limit]
            print(f"対象: {len(issues)} 件")
            for it in issues:
                results.append(self.handle(it))
        except Abort as e:
            print(f"\nABORT: {e}")
            if e.log:
                print(f"ログ: {e.log}")
            if self.touched:
                with self.lock.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"aborted": str(e)}, ensure_ascii=False) + "\n")
            else:
                self.release_lock()  # 何も変えていないので、次回を止める理由がない
            return 2

        self.release_lock()
        print(f"\n完了: マージ {results.count('PASSED')} / 承認待ちへ {results.count('AWAITING')} / "
              f"待機中 {results.count('WAITING')} / 不合格 {results.count('REJECT')}")
        return 1 if "REJECT" in results else 0


PROJECT_KEYS = ("project_id", "repo_slug", "repo_dir", "base_branch", "out_dir",
                "test_dir", "audit_dir", "unit_path_template", "required_checks")


def build_config(project_id):
    """共通設定にプロジェクト固有の値を差し込み、Scheduler が読む平らな dict にする。

    共通設定の側にプロジェクト固有のキーがあれば落とす。2 か所に書くと、
    片方だけ直して食い違う（再掲しない原則）。
    """
    p = project.load(project_id)
    cfg = project.config("scheduler")
    dup = [k for k in PROJECT_KEYS if k in cfg]
    if dup:
        raise project.ProjectError("config/scheduler.json にプロジェクト固有のキーがあります: "
                                   + ", ".join(dup))
    cfg.update(
        project_id=p["id"],
        repo_slug=p["repo_slug"],
        repo_dir=p["repo_dir"],
        base_branch=p["base_branch"],
        out_dir=p["out_dir"],
        test_dir=p["test_dir"],
        audit_dir=project.config("audit")["out_dir"],
        unit_path_template=p["units_dir"].rstrip("/") + "/issue_{number}.json",
        required_checks=p.get("required_checks") or [],
    )
    if not cfg["required_checks"]:
        raise project.ProjectError(f"{p['dir']}\\project.json に required_checks がありません。"
                                   "チェック無しでマージさせません")
    return cfg


def main(argv=None, gh_run=run_cmd, sleep=time.sleep):
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--project", help="projects/<id>（harness のプロジェクト ID）")
    src.add_argument("--config", help="平らな設定 JSON を直接渡す（テスト用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-issues", type=int)
    a = ap.parse_args(argv)
    if a.project:
        cfg = build_config(a.project)
    else:
        cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    return Scheduler(cfg, gh_run=gh_run, sleep=sleep).run(dry_run=a.dry_run,
                                                          max_issues=a.max_issues)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
