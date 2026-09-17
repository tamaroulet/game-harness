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
    2. GDD の PR（ms4:gdd、ブランチ spec/gdd-v<版>。docs/design/spec_pipeline.md §2）
         その head の GDD（docs/gdd/source.md）の sha256 に処理済みのマーカーがあれば触らない
         錨の事前検査（LLM を呼ばない）で足りなければ → ms4:questions
         使い捨ての worktree で spec.py → docs/spec の 2 ファイルだけをコミットして push
         マージ可 → 承認依頼と ms4:awaiting-approval / マージ不可 → ms4:questions
         spec.py 1 → ms4:failed / それ以外 → ABORT
    3. ready の Issue
         ready → ms4:running
         ブランチ ms4/issue-N を切る（既にあれば不合格。前回の残骸か人間の作業中）
         作業は使い捨ての worktree（<worktree_root>/<project>-issue-N）で行う。本体の clone は main のまま触らない
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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import adapters
import exitcode
import fileops
import gdd_check
import project
import telemetry

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GDD_BRANCH_RE = re.compile(r"spec/gdd-v([1-9][0-9]*)")
GDD_PATH = "docs/gdd/source.md"
SPEC_FILES = ("docs/spec/spec.md", "docs/spec/questions.md")

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


def kill_pid_tree(pid):
    """pid の木ごと止める（Windows）。taskkill 自体が固まったら False。"""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, stdin=subprocess.DEVNULL,
                       creationflags=_NO_WINDOW, timeout=60)
        return True
    except subprocess.TimeoutExpired:
        return False


def kill_tree(proc):
    """子だけ殺すと孫（エンジンのエディタ・agy）が孤児になって走り続ける。木ごと止める。"""
    if sys.platform == "win32":
        if not kill_pid_tree(proc.pid):
            proc.kill()  # taskkill 自体が固まったら、せめて直下の子は止める
    else:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def pid_alive(pid):
    """プロセスが生きているか。分からなければ生きているとみなす（ロックを消さない側に倒す）。

    Windows で os.kill(pid, 0) を使ってはいけない。シグナル 0 は TerminateProcess になり、
    確かめたい相手を終了させてしまう。OpenProcess と GetExitCodeProcess で見る。
    pid が別のプロセスに再利用されていれば生きていると判定される（安全側）。
    """
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE, ERROR_INVALID_PARAMETER = 0x1000, 259, 87
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER  # 87 = その pid は無い
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def run_logged(args, cwd, ttl, log_path, env, on_wait=None, tick=30):
    """長い子プロセス（decompose / audit / pipeline）。出力は逐次ファイルへ。

    pipeline は 1 時間を超えうる。メモリに溜めて最後に書くと、途中で何が
    起きているか誰にも見えず、殺されたときに何も残らない。

    TTL まで一度に待たず、tick 秒ごとに on_wait(pid) を呼ぶ（ハートビート）。
    待ちが長くても、外から「生きて待っている」ことが分かるようにするため。
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
        if on_wait is not None:
            on_wait(proc.pid)   # 自己監視が固まった子を止められるよう、pid をすぐ知らせる
        deadline = time.monotonic() + ttl
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_tree(proc)
                f.write(f"\n\nTTL超過 ({ttl}s)。プロセス木ごと停止しました\n".encode("utf-8"))
                return 124
            try:
                return proc.wait(timeout=min(tick, remaining))
            except subprocess.TimeoutExpired:
                if on_wait is not None:
                    on_wait(proc.pid)


def tail_of(log_path, chars):
    try:
        text = Path(log_path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return "（ログを読めません）"
    return text[-chars:]


# ============================================================ ネットワーク再試行

RATE_LIMIT_WORDS = ("rate limit", "http 429", "abuse detection")


def is_rate_limited(detail):
    text = str(detail).lower()
    return any(w in text for w in RATE_LIMIT_WORDS)


def with_retries(attempt, done, retries, interval, sleep, what, on_limited=None):
    """attempt() -> (ok, detail)。失敗したら interval 待って再試行する。

    書き込みは「失敗と表示されたが実は反映されていた」がありうる（応答だけ落ちた）。
    そのまま重ねるとコメントが二重に付くので、再試行の前に done() で読み直し、
    反映済みなら何もしない。読み取りは done=None。

    レート制限（on_limited があるとき）は数十分単位で続くので、5 秒の再試行では抜けられない。
    on_limited(待った合計, 回数) が返す秒数だけ待つ。上限を超えるなら on_limited が Abort を上げる。
    この待ちは retries の回数に数えない。
    """
    detail = ""
    failures, waited, limited = 0, 0, 0
    while True:
        ok, detail = attempt()
        if ok:
            return detail
        if on_limited is not None and is_rate_limited(detail):
            secs = on_limited(waited, limited)
            waited, limited = waited + secs, limited + 1
            sleep(secs)
        else:
            if failures >= retries:
                raise Abort(f"{what} が {retries + 1} 回とも失敗しました: {str(detail)[:300]}")
            failures += 1
            sleep(interval)
        if done is not None and done():
            return None


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

    def fetch_branch(self, branch):
        self._net("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")

    def show(self, ref, path):
        """ref にある path の本文。無ければ None（作業ツリーは見ない）。"""
        rc, out, _ = self._git("show", f"{ref}:{path}", check=False)
        return out if rc == 0 else None

    def worktree_add(self, path, branch, start, new_branch=False):
        """new_branch=True は -b（既にあれば失敗）、False は -B（あれば start に合わせ直す）。"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "-b" if new_branch else "-B", branch, str(path), start)

    def worktree_remove(self, path):
        """使い捨ての worktree を消す。消せたら True。

        Windows ではファイルが掴まれていて消せないことがある（WinError 32）。間を空けて数回試し、
        それでも残れば諦めて False を返す（ここで止まらない。残骸は次に同じ場所を使う前に消し直す）。
        """
        for delay in (0,) + fileops.DELAYS:
            if delay:
                self.sleep(delay)
            self._git("worktree", "remove", "--force", str(path), check=False)
            if not Path(path).exists():
                break
        self._git("worktree", "prune", check=False)
        return not Path(path).exists()

    def added_lines(self, merge_sha, exclude_prefixes):
        """マージコミットで第 1 親から増えた行数（除外する接頭辞のパスを除く）。(行数, 理由)。"""
        rc, out, err = self._git("diff", "--numstat", f"{merge_sha}^1", merge_sha, check=False)
        if rc != 0:
            return None, f"git diff --numstat が失敗: {(err or out)[:160]}"
        total = 0
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3 or any(parts[2].startswith(p) for p in exclude_prefixes):
                continue
            if parts[0] == "-":
                return None, f"バイナリの差分があり行数を数えられない: {parts[2]}"
            total += int(parts[0])
        return total, None


# ============================================================ GitHub

class GitHub:
    """gh の呼び出しはすべてここを通す。テストでは run を偽物に差し替える。"""

    def __init__(self, cfg, run, sleep, clock=time.time, on_limited=None):
        self.slug = cfg["repo_slug"]
        self.ttl = cfg["ttl_seconds"]["gh"]
        self.retries = cfg["net_retries"]
        self.interval = cfg["net_retry_interval_seconds"]
        self.labels = cfg["labels"]
        self.run = run
        self.sleep = sleep
        self.cwd = cfg["repo_dir"]
        self.clock = clock
        self.rl_margin = cfg.get("rate_limit_margin_seconds", 30)
        self.rl_max_wait = cfg.get("rate_limit_max_wait_seconds", 3600)
        self.on_limited = on_limited   # (秒, リセット時刻) を受け取る（ハートビート）

    def _once(self, args):
        rc, out, err = self.run(["gh"] + args + ["--repo", self.slug], self.cwd, self.ttl)
        return rc == 0, out if rc == 0 else (err or out or f"rc={rc}")

    # ---- レート制限
    def rate_limit(self):
        """{"core": {remaining, reset}, "graphql": {...}}。読めなければ None。この呼び出しは残量を使わない。"""
        rc, out, _ = self.run(["gh", "api", "rate_limit"], self.cwd, self.ttl)
        if rc != 0:
            return None
        try:
            res = json.loads(out)["resources"]
            return {k: {"remaining": int(res[k]["remaining"]), "reset": int(res[k]["reset"])}
                    for k in ("core", "graphql")}
        except (ValueError, KeyError, TypeError):
            return None

    def rate_limit_wait(self, waited, count):
        """レート制限に当たったときに待つ秒数。待ちの合計が上限を超えるなら Abort。"""
        info = self.rate_limit()
        exhausted = [v for v in (info or {}).values() if v["remaining"] == 0]
        reset = None
        if exhausted:
            reset = max(v["reset"] for v in exhausted)
            secs = max(reset - self.clock(), 0) + self.rl_margin
            kind = "一次制限（残量 0）"
        else:
            # 残量があるのに断られた = セカンダリ制限（短時間の呼びすぎ）。指数的に待つ
            secs = min(60 * 2 ** count, 240)
            kind = "セカンダリ制限"
        secs = int(secs)
        if waited + secs > self.rl_max_wait:
            raise Abort(f"GitHub API のレート制限（{kind}）: 待ちの合計が上限 {self.rl_max_wait}s を超えます"
                        f"（待機済み {waited}s、さらに {secs}s）")
        print(f"  GitHub API のレート制限（{kind}）。{secs}s 待ちます")
        if self.on_limited is not None:
            self.on_limited(secs, reset)
        return secs

    def read(self, args):
        return with_retries(lambda: self._once(args), None, self.retries, self.interval,
                            self.sleep, "gh " + " ".join(args[:2]), self.rate_limit_wait)

    def read_json(self, args):
        out = self.read(args)
        try:
            return json.loads(out or "null")
        except ValueError:
            raise Abort(f"gh {' '.join(args[:2])} の出力が JSON ではありません: {out[:200]}")

    def write(self, args, done):
        with_retries(lambda: self._once(args), done, self.retries, self.interval,
                     self.sleep, "gh " + " ".join(args[:2]), self.rate_limit_wait)

    def api_json(self, path):
        """gh api は --repo を取らないので、パスにリポジトリを入れて呼ぶ。"""
        def attempt():
            rc, out, err = self.run(["gh", "api", path], self.cwd, self.ttl)
            return rc == 0, out if rc == 0 else (err or out or f"rc={rc}")
        out = with_retries(attempt, None, self.retries, self.interval, self.sleep,
                           "gh api " + path.split("?")[0], self.rate_limit_wait)
        try:
            return json.loads(out or "null")
        except ValueError:
            raise Abort(f"gh api {path} の出力が JSON ではありません: {out[:200]}")

    # ---- 読み取り
    def decision_wait(self, number, awaiting, decided):
        """最後に awaiting が付いてから、最初に decided のどれかが付くまでの秒数。(秒, 付けた人, 理由)。"""
        events, page = [], 1
        while True:
            batch = self.api_json(f"repos/{self.slug}/issues/{number}/events"
                                  f"?per_page=100&page={page}") or []
            events += batch
            if len(batch) < 100:
                break
            page += 1
        start = end = None
        for ev in events:
            if ev.get("event") != "labeled":
                continue
            name = (ev.get("label") or {}).get("name")
            if name == awaiting:
                start, end = ev, None
            elif name in decided and start is not None and end is None:
                end = ev
        if start is None or end is None:
            return None, None, "承認待ちのラベルと、その後の承認・却下のラベルの組が見つからない"

        def at(ev):
            return datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00"))
        try:
            secs = (at(end) - at(start)).total_seconds()
        except (KeyError, ValueError, AttributeError) as e:
            return None, None, f"イベントの時刻を読めない: {e}"
        return round(secs, 1), (end.get("actor") or {}).get("login"), None

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

    def list_gdd_prs(self):
        if "gdd" not in self.labels:
            return []
        items = self.read_json(["pr", "list", "--label", self.labels["gdd"]["name"],
                                "--state", "open", "--limit", "100",
                                "--json", "number,headRefName"])
        return sorted(items or [], key=lambda x: x["number"])

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

class Heartbeat:
    """<out_dir>/heartbeat.json。主スレッドが生きていることを、外（--status・自己監視）へ示す。

    書き込みに失敗しても止めない（記録のためのもの）。置き換えは fileops 経由で、
    読み手が掴んでいる間は待って再試行する。
    """

    def __init__(self, path, clock=time.monotonic):
        self.path = Path(path)
        self.clock = clock
        self.state = {"pid": os.getpid(), "state": "starting", "step": None, "issue": None,
                      "child_pid": None, "reason": None, "rate_limit_reset": None}
        self.last = clock()
        self._lock = threading.Lock()

    def beat(self, **fields):
        with self._lock:
            self.state.update(fields)
            self.state["updated"] = datetime.now().isoformat(timespec="seconds")
            self.last = self.clock()
            data = dict(self.state)
        try:
            telemetry.write(self.path, data)
        except OSError as e:
            print(f"  ハートビートを書けません: {e}")

    def age(self):
        return self.clock() - self.last


class DailyLog:
    """stdout / stderr を <out_dir>/scheduler-YYYYMMDD.log へ付け替える。

    pythonw では sys.stdout が None で、print が黙って消える。常駐では誰も画面を見ないので、
    日付ごとのファイルに残す。日付の切り替えは周の区切りで行う（ensure）。
    """

    def __init__(self, out_dir):
        self.dir = Path(out_dir)
        self.date = None
        self.f = None

    def ensure(self):
        today = datetime.now().strftime("%Y%m%d")
        if today == self.date:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        old = self.f
        self.f = open(self.dir / f"scheduler-{today}.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = self.f
        self.date = today
        if old is not None:
            old.close()

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None


class Scheduler:
    def __init__(self, cfg, gh_run=run_cmd, sleep=time.sleep):
        self.cfg = cfg
        self.repo = Path(cfg["repo_dir"])
        self.out = Path(cfg["out_dir"])
        self.base = cfg["base_branch"]
        self.hb = Heartbeat(self.out / "heartbeat.json")
        self.tick = cfg.get("heartbeat_seconds", 30)
        # 待つときは tick ごとにハートビートを更新する（レート制限の待ちは数十分になりうる）
        self.raw_sleep = sleep
        self.sleep = self.pause
        self.git = Git(self.repo, cfg, self.pause)
        self.gh = GitHub(cfg, gh_run, self.pause,
                         on_limited=lambda secs, reset: self.hb.beat(
                             state="rate_limited", rate_limit_reset=reset,
                             reason=f"GitHub API のレート制限で {secs}s 待機"))
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.lock = Path(cfg.get("lock_path") or self.out / "scheduler.lock")
        self.stop_path = self.lock.parent / "scheduler.stop"
        self.touched = False
        self.wt, self.igit = None, None   # 処理中の Issue の worktree と、その Git
        self._wd_stop = threading.Event()
        self._exit = os._exit
        # 記録に残すハーネスの版。取れなければ null（記録のためだけなので止めない）。
        rc, out, _ = run_cmd(["git", "rev-parse", "HEAD"], project.ROOT, cfg["ttl_seconds"]["git"])
        self.harness_sha = out.strip() if rc == 0 and out.strip() else None

        self.test_dir = cfg["test_dir"].strip("/")
        self.audit_dir = cfg["audit_dir"].strip("/")
        # 承認依頼に載せる受入テスト一覧は、言語ごとの抽出器で作る（fast アダプタ）。
        # 設定に無ければ例外で止まる（既定の言語を仮定しない）。
        self.fast = adapters.load("fast", cfg["adapters"]["fast"])

        self.env = dict(os.environ)
        # 子の Python がパイプへ CP932 で書くと、ログとコメントが化ける（実測）。
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"

    # ---- 待機
    def pause(self, seconds):
        """tick ごとに区切って待ち、そのたびにハートビートを更新する。"""
        left = seconds
        while left > 0:
            chunk = min(left, self.tick)
            self.raw_sleep(chunk)
            left -= chunk
            self.hb.beat()

    # ---- ロック
    def _create_lock(self):
        try:
            fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "run_id": self.run_id}) + "\n")
        return True

    def acquire_lock(self):
        if self._create_lock():
            return True
        why = self.stale_lock_reason()
        if why is None:
            return False
        print(f"死んだロックを解放します（{why}）: {self.lock}")
        fileops.unlink(self.lock)
        return self._create_lock()

    def stale_lock_reason(self):
        """自動で消してよい死んだロックなら理由を返す。少しでも疑わしければ None（消さない）。

        消すのは「形が {pid, run_id} で、ABORT の記録が無く、pid が生きていない」ときだけ。
        原因が記録された停止（aborted）は、人間が理由を見てから消す。
        """
        try:
            lines = [l for l in self.lock.read_text(encoding="utf-8").splitlines() if l.strip()]
            records = [json.loads(l) for l in lines]
        except (OSError, ValueError):
            return None
        if not records or not all(isinstance(r, dict) for r in records):
            return None
        pid = records[0].get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            return None
        if any("aborted" in r for r in records):
            return None
        if pid_alive(pid):
            return None
        return f"pid {pid} は終了していて、ABORT の記録も無い"

    def release_lock(self):
        fileops.unlink(self.lock)

    # ---- 常駐
    def stop_requested(self):
        return self.stop_path.exists()

    def request_stop(self):
        self.stop_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
        print(f"停止を依頼しました: {self.stop_path}（常駐は周の区切りか待機中に止まります。"
              "実行中の子プロセスは止めません）")
        return 0

    def wait_or_stop(self, seconds):
        """5 秒刻みで待つ。停止ファイルがあれば True。"""
        left = seconds
        while left > 0:
            if self.stop_requested():
                return True
            chunk = min(5, left)
            self.raw_sleep(chunk)
            left -= chunk
            self.hb.beat()
        return self.stop_requested()

    def stop_watch(self):
        fileops.unlink(self.stop_path)
        self.release_lock()
        self.hb.beat(state="stopped", step=None, issue=None, reason="停止ファイルで停止")
        print("停止ファイルを見つけたので、常駐を終了します")
        return 0

    def start_watchdog(self):
        limit = self.cfg.get("watchdog_seconds", 900)

        def loop():
            while not self._wd_stop.wait(min(30, limit / 4)):
                if self.watchdog_check(limit):
                    return
        threading.Thread(target=loop, name="watchdog", daemon=True).start()

    def watchdog_check(self, limit):
        """ハートビートが limit 秒更新されていなければ、子の木を止めて終了する。

        TTL の外で主処理が固まった場合の最後の手段。aborted を書かないので、
        次の起動で死んだロックとして自動解放される（作業ツリーが汚れていれば起動時検査が止める）。
        """
        age = self.hb.age()
        if age <= limit:
            return False
        pid = self.hb.state.get("child_pid")
        if pid:
            if sys.platform == "win32":
                kill_pid_tree(pid)
            else:
                os.kill(pid, 9)
        reason = f"自己監視: {int(age)}s ハートビートが更新されなかった（主処理が固まった）"
        self.hb.beat(state="stopped", child_pid=None, reason=reason)
        print(reason)
        self._exit(2)
        return True

    def watch(self, interval, max_issues=None, log=None):
        """1 周 → 待機 → 1 周。ロックは常駐の間ずっと持つ。ABORT で抜ける。停止ファイルで止まる。"""
        self.out.mkdir(parents=True, exist_ok=True)
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        if not self.acquire_lock():
            print(f"ABORT: ロック {self.lock} があります。別のスケジューラが動いているか、"
                  "前回が ABORT で止まっています。確認してから消してください。")
            return 2
        self.start_watchdog()
        min_remaining = self.cfg.get("rate_limit_min_remaining", 300)
        try:
            while True:
                if log is not None:
                    log.ensure()
                if self.stop_requested():
                    return self.stop_watch()
                # 周の前に API の残量を確かめる。着手の途中で使い切ると ABORT になるので、
                # 足りなければ Issue に触る前にリセットまで待つ。
                low = [v for v in (self.gh.rate_limit() or {}).values() if v["remaining"] < min_remaining]
                if low:
                    reset = max(v["reset"] for v in low)
                    secs = max(int(reset - self.gh.clock()), 0) + self.gh.rl_margin
                    print(f"GitHub API の残量が {min_remaining} 未満です。着手せず {secs}s 待ちます")
                    self.hb.beat(state="rate_limited", step=None, issue=None, rate_limit_reset=reset,
                                 reason="API の残量不足で着手を見送り")
                    if self.wait_or_stop(secs):
                        return self.stop_watch()
                    continue
                self.hb.beat(state="running", step="cycle", reason=None, rate_limit_reset=None)
                rc = self.cycle(max_issues)
                if rc == 2:
                    self.hb.beat(state="stopped", step=None, child_pid=None,
                                 reason="ABORT（理由はログと Issue のコメント）")
                    return 2
                self.hb.beat(state="waiting", step=None, issue=None)
                if self.wait_or_stop(interval):
                    return self.stop_watch()
        finally:
            self._wd_stop.set()

    def status(self):
        """ロック・ハートビート・停止ファイルを表示する。何も変えない。"""
        print(f"ロック: {self.lock}")
        if self.lock.exists():
            text = self.lock.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                print(f"  {line}")
            try:
                pid = json.loads(text.splitlines()[0])["pid"]
                print(f"  pid {pid}: {'生きている' if pid_alive(pid) else '終了している'}")
            except (ValueError, KeyError, IndexError, TypeError):
                print("  （形が違うので pid を確かめられません）")
            why = self.stale_lock_reason()
            print(f"  次の起動で自動解放: {'する（' + why + '）' if why else 'しない'}")
        else:
            print("  なし")
        hb, why = telemetry.read(self.out / "heartbeat.json")
        print(f"ハートビート: {self.out / 'heartbeat.json'}")
        if hb is None:
            print(f"  {why}")
        else:
            try:
                age = (datetime.now() - datetime.fromisoformat(hb["updated"])).total_seconds()
                print(f"  最終更新: {age / 60:.1f} 分前（{hb['updated']}）")
            except (KeyError, ValueError, TypeError):
                print("  最終更新: 不明")
            for key in ("state", "step", "issue", "child_pid", "reason"):
                print(f"  {key}: {hb.get(key)}")
            if hb.get("rate_limit_reset"):
                print(f"  rate_limit_reset: {datetime.fromtimestamp(hb['rate_limit_reset']).isoformat()}")
        print(f"停止ファイル: {'あり' if self.stop_requested() else 'なし'}（{self.stop_path}）")
        return 0

    # ---- 記録
    # 査読用の指標。算出する行の種類が決まっているので、ほかの行では null ＋理由で枠だけ置く
    # （キーが無いのと null を区別できるように、どの行にも必ずある形にする）。
    METRICS = {
        "p2p_violation_rate": "実装を試した行（AWAITING / REJECT）でだけ算出する",
        "retry_entropy": "実装を試した行（AWAITING / REJECT）でだけ算出する",
        "token_to_accepted_loc": "マージした行（PASSED）でだけ算出する",
        "human_active_intervention_time": "承認・却下を処理した行でだけ記録する",
    }

    def new_record(self, **fields):
        rec = {"run_id": self.run_id, "condition": self.cfg.get("experiment_condition"),
               "harness_sha": self.harness_sha, **fields, "steps": [],
               "started": datetime.now().isoformat(timespec="seconds")}
        rec["_t0"] = time.monotonic()
        return rec

    def record(self, rec):
        rec["finished"] = datetime.now().isoformat(timespec="seconds")
        t0 = rec.pop("_t0", None)
        if t0 is not None:
            rec["total_seconds"] = round(time.monotonic() - t0, 1)
        for key, why in self.METRICS.items():
            if key not in rec:
                telemetry.put(rec, key, None, why)
        with (self.out / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def step(self, rec, name, tag=None, cwd=None, **fmt):
        log = self.out / f"issue_{rec['issue']}" / (f"{name}_{tag}.log" if tag else f"{name}.log")
        # 道具が書くテレメトリ。前回の残りを読まないよう、実行前に消す。
        tel_path = log.with_name(log.stem + ".telemetry.json")
        fileops.unlink(tel_path)
        cwd = Path(cwd or self.repo)
        args = [a.format(python=sys.executable, harness=project.HARNESS_DIR.as_posix(),
                         project=self.cfg.get("project_id", ""), telemetry=str(tel_path),
                         repo=str(cwd), **fmt)
                for a in self.cfg["commands"][name]]
        print(f"  [{name}{' ' + tag if tag else ''}] 実行中… ログ: {log}")
        t0 = time.monotonic()
        self.hb.beat(state="running", step=name, issue=rec["issue"], child_pid=None, reason=None)
        rc = run_logged(args, cwd, self.cfg["ttl_seconds"][name], log, self.env,
                        on_wait=lambda pid: self.hb.beat(child_pid=pid), tick=self.tick)
        self.hb.beat(child_pid=None)
        secs = round(time.monotonic() - t0, 1)
        entry = {"name": name, "tag": tag, "rc": rc, "seconds": secs, "log": str(log)}
        data, why = telemetry.read(tel_path)
        telemetry.put(entry, "telemetry", data, why)
        rec["steps"].append(entry)
        print(f"  [{name}{' ' + tag if tag else ''}] rc={rc} ({secs}s)")
        return rc, log

    def lift_attempt_metrics(self, rec):
        """pipeline のテレメトリにある試行の指標を、runs.jsonl の行の最上位へ写す。"""
        tel = rec["steps"][-1].get("telemetry")
        for key in ("p2p_violation_rate", "retry_entropy"):
            if tel is None:
                telemetry.put(rec, key, None, "pipeline のテレメトリが無い: "
                              + str(rec["steps"][-1].get("telemetry_null_reason")))
            else:
                telemetry.put(rec, key, tel.get(key),
                              tel.get(key + "_null_reason", "pipeline のテレメトリにキーが無い"))
        if tel is not None:
            rec["attempts_judged"] = tel.get("attempts_judged")
            rec["retry_same_failure_repeats"] = tel.get("retry_same_failure_repeats")

    def accepted_metrics(self, rec, n, pr_number, merge_sha):
        """マージした Issue の token_to_accepted_loc と、人間の待ち時間。失敗しても処理は止めない。"""
        records = []
        try:
            with (self.out / "runs.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("issue") == n:
                        records.append(r)
        except OSError:
            pass
        tokens, t_why = telemetry.tokens_total(records)
        units_dir = self.cfg["unit_path_template"].rsplit("/", 1)[0] + "/"
        exclude = [self.test_dir + "/", units_dir, self.audit_dir + "/", "reports/"]
        loc, l_why = self.git.added_lines(merge_sha, exclude)
        telemetry.put(rec, "tokens_total", tokens, t_why)
        telemetry.put(rec, "accepted_loc", loc, l_why)
        value, why = telemetry.token_to_accepted_loc(tokens, t_why, loc, l_why)
        telemetry.put(rec, "token_to_accepted_loc", value, why)
        self.record_human(rec, pr_number)

    def record_human(self, rec, pr_number):
        human = {}
        try:
            wait, who, why = self.gh.decision_wait(pr_number, self.labels_name("awaiting"),
                                                   {self.labels_name("approved"),
                                                    self.labels_name("declined")})
        except Abort as e:   # テレメトリで承認・マージを止めない
            wait, who, why = None, None, f"issue events を読めない: {e}"[:200]
        telemetry.put(human, "wait_seconds", wait, why)
        human["decided_by"] = who
        telemetry.put(human, "active_seconds", None, "dispatch 未計測（Step 4 は harness 側だけ）")
        rec["human"] = human
        active = human["active_seconds"]
        telemetry.put(rec, "human_active_intervention_time",
                      None if active is None else round(active / 60, 2),
                      "human.active_seconds が不明: " + str(human.get("active_seconds_null_reason")))

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
        self.git._git("worktree", "prune", check=False)
        self.git.pull_ff()

    def worktree_root(self):
        return Path(self.cfg.get("worktree_root") or self.out / "worktrees")

    def issue_worktree(self, n):
        return self.worktree_root() / f"{self.cfg.get('project_id') or 'project'}-issue-{n}"

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
        wt = self.issue_worktree(n)
        if wt.exists() and not self.git.worktree_remove(wt):
            raise Abort(f"前回の worktree の残骸を消せません（ファイルが掴まれている可能性）: {wt}")
        # 本体の clone（repo_dir）は main のまま触らない。分解・監査・実装・コミット・push は worktree で行う
        self.git.worktree_add(wt, branch, f"origin/{self.base}", new_branch=True)
        self.wt, self.igit = wt, Git(wt, self.cfg, self.pause)
        rec["worktree"] = str(wt)

        # ---- 分解
        rc, log = self.step(rec, "decompose", cwd=wt, number=n)
        if rc == 1:
            raise Reject("分解役の出力が要件を満たしませんでした（何も書き出していません）。",
                         log=log, delete_branch=True)
        if rc != 0:
            raise Abort(f"decompose.py が rc={rc} で終了しました（環境異常）", log=log)

        changed = self.igit.changed_paths()
        if unit not in changed:
            raise Abort(f"decompose.py は rc=0 ですが単位定義 {unit} がありません", log=log)
        in_tests = lambda p: p.startswith(self.test_dir + "/")
        self.require_only(changed, [lambda p: p == unit, in_tests], "decompose.py")
        tests = [p for p in changed if in_tests(p)]
        if not tests:
            raise Abort("decompose.py は rc=0 ですがテストファイルがありません", log=log)
        rec["tests"] = tests
        self.igit.add_commit(changed, f"test(ms4): acceptance tests for issue #{n}")

        # ---- 監査
        audit_failed = self.audit(rec, [unit] + tests)
        changed = self.igit.changed_paths()
        if changed:
            self.require_only(changed, [lambda p: p.startswith(self.audit_dir + "/")], "audit.py")
            self.igit.add_commit(changed, f"docs(audit): audit reports for issue #{n}")
        if audit_failed and self.cfg["audit"]["required"]:
            raise Reject("監査が必須の設定ですが、監査が完了しませんでした: " + rec["audit"])

        self.igit.push_upstream(branch)

        # ---- 実装
        rc, log = self.step(rec, "pipeline", cwd=wt, unit=unit)
        self.lift_attempt_metrics(rec)
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
            rc, _ = self.step(rec, "audit", tag=Path(f).stem, cwd=self.wt, file=f)
            if rc != 0:
                failed.append(f"{f} rc={rc}")
        rec["audit"] = "failed: " + "; ".join(failed) if failed else "done"
        return bool(failed)

    def open_pr(self, n, title, branch, unit, rec):
        """門を通った実装を PR にし、人間の承認を待つ。ここではマージしない。

        承認は最終的な head SHA に対して 1 回だけ行う。承認後に push すると
        必須チェック approval が承認を外すので、承認の前に実装を済ませておく。
        """
        dirty = self.igit.changed_paths()
        if dirty:
            raise Abort("パイプラインは合格ですが作業ツリーが汚れています: " + ", ".join(dirty[:5]))
        self.igit.push(branch)  # パイプラインが push 済みのはず。同じなら何も起きない
        sha = self.igit.head_sha()
        rec["head_sha"] = sha
        playtest = self.unit_field(unit, "playtest") == "required"
        summary = self.approval_summary(n, unit, rec, sha, playtest)
        self.close_issue_worktree(rec)

        pr = self.gh.create_pr(
            branch, self.base, f"#{n}: {title}",
            f"Closes #{n}\n\nms4 スケジューラが作成した PR です。"
            "受入テストの一覧はコメントにあります。承認は dispatch から行います。\n")
        rec["pr"] = pr
        self.gh.comment(pr, f"ms4:approval-request:{sha}", summary, kind="pr")
        self.gh.set_labels(pr, add=["awaiting"], kind="pr")
        self.gh.set_labels(n, add=["awaiting"], remove=["running", "ready"])
        if playtest:
            self.build_playtest(n, pr, sha, rec)

    def close_issue_worktree(self, rec):
        """Issue の worktree を消す（ブランチは残す）。消せなければ記録して続ける（WinError 32 で止まらない）。"""
        if self.wt is not None and not self.git.worktree_remove(self.wt):
            rec["worktree_left"] = str(self.wt)
            print(f"  worktree を消せませんでした（次に使う前に消し直します）: {self.wt}")
        self.wt, self.igit = None, None

    def unit_field(self, unit, key):
        """単位定義の値。Issue の worktree がある間（消す前）に読むこと。"""
        try:
            return json.loads((Path(self.wt or self.repo) / unit).read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError):
            return None

    def build_playtest(self, n, pr, sha, rec):
        """プレイ確認（H2）が必要な PR に印を付け、head SHA から実行ファイルを作る。

        ビルドの失敗は、承認待ちのまま人間に見せる（遊べないものは人間が却下する）。
        環境の異常（rc=2 など）は ABORT。
        """
        rec["playtest"] = "required"
        self.gh.set_labels(pr, add=["playtest"], kind="pr")
        rc, log = self.step(rec, "playtest", pr=pr, sha=sha)
        project_id = self.cfg.get("project_id") or self.cfg["repo_slug"]
        if rc == 0:
            self.gh.comment(pr, f"ms4:playtest-build:{sha}",
                            f"**ms4: プレイ確認用のビルドができました**（`{sha[:8]}`）\n\n"
                            f"```\npython tools/dispatch.py --playtest {project_id}#{pr}\n```\n\n"
                            "全項目 OK の結果が無いと、この PR は承認できません。\n", kind="pr")
            return
        if rc == 1:
            self.gh.comment(pr, f"ms4:playtest-build:{sha}",
                            f"**ms4: プレイ確認用のビルドに失敗しました**（`{sha[:8]}`）\n\n"
                            f"遊べないので、確認できません。内容を見て却下してください。\n\n"
                            f"ログ: `{log}`\n\n```\n{tail_of(log, self.cfg['comment_log_tail_chars'])}\n```\n",
                            kind="pr")
            rec["playtest_build"] = "failed"
            return
        raise Abort(f"playtest.py が rc={rc} で終了しました（環境異常）", log=log)

    def approval_summary(self, n, unit, rec, sha, playtest=False):
        """承認依頼の本文。テスト一覧は C# から機械的に抜き出す（LLM に要約させない）。"""
        tests = rec.get("tests") or []
        try:
            total, md = self.fast.summarize_files(tests, root=Path(self.wt or self.repo))
        except (OSError, ValueError) as e:
            raise Abort(f"受入テストの一覧を作れません: {e}")
        if total == 0:
            raise Abort("受入テストを 1 件も読み取れません。空の一覧で承認させません: "
                        + ", ".join(tests))
        hcp = self.unit_field(unit, "human_check_point")
        project_id = self.cfg.get("project_id") or self.cfg["repo_slug"]
        playtest_note = ("### プレイ確認\n\n**必要**（`dispatch --playtest` で全項目 OK の結果が無いと、"
                         "承認できません。ビルドができたらコメントで知らせます）\n\n" if playtest else "")
        body = (
            f"**ms4: 承認依頼** — Issue #{n}\n\n"
            f"対象コミット: `{sha[:8]}`（この SHA に対してだけ有効。push されると承認は外れます）\n\n"
            f"### 受入テスト（分解役が書いたもの。C# から機械抽出。合計 {total} 件）\n\n{md}\n\n"
            f"### 人間が実機で見ること\n\n{hcp or '（単位定義に human_check_point がありません）'}\n\n"
            f"{playtest_note}"
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
        if GDD_BRANCH_RE.fullmatch(branch):
            return self.handle_gdd_decision(item)
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

        rec = self.new_record(issue=n, pr=num, branch=branch)
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
                self.record_human(rec, num)
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
            self.accepted_metrics(rec, n, num, merged["merge_sha"])
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
        dirty = (self.igit or self.git).changed_paths()
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
        self.close_issue_worktree(rec)
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

    # ---- GDD の PR（フェーズ B-2d。docs/design/spec_pipeline.md §2・§7）
    def handle_gdd(self, item):
        """ms4:gdd の PR を 1 本見る。処理済み（同じ GDD の sha256 のマーカーがある）なら触らない。"""
        num, branch = item["number"], item.get("headRefName") or ""
        m = GDD_BRANCH_RE.fullmatch(branch)
        if not m:
            print(f"  GDD の PR #{num}: ブランチ {branch} は spec/gdd-v<版> ではないので扱いません")
            return "WAITING"
        version = int(m.group(1))
        self.git.fetch_branch(branch)
        text = self.git.show(f"origin/{branch}", GDD_PATH)
        if text is None:
            print(f"  GDD の PR #{num}: {GDD_PATH} がありません。扱いません")
            return "WAITING"
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        marker = f"ms4:gdd-processed:{sha}"
        if any(marker in c for c in self.gh.view(num, "pr")["comments"]):
            return "WAITING"   # この GDD は処理済み。再処理しない（周のたびに LLM を呼ばない）

        rec = self.new_record(issue=f"gdd-v{version}", kind="gdd", pr=num, branch=branch, gdd_sha256=sha)
        self.touched = True
        print(f"\n=== GDD の PR #{num}（v{version}、`{sha[:8]}`）")
        try:
            if gdd_check.CFG is None:
                try:
                    gdd_check.CFG = gdd_check.load_config()
                except gdd_check.ConfigError as e:
                    raise Abort(str(e))
            missing = gdd_check.precheck(text)
            if missing:
                rec["precheck_missing"] = missing
                self.gh.comment(num, marker, self.gdd_precheck_report(version, sha, missing), kind="pr")
                self.gh.set_labels(num, add=["questions"], kind="pr")
                rec["result"], rec["reason"] = "QUESTIONS", "錨の事前検査（LLM は呼んでいない）"
                print(f"=== GDD v{version}: 錨が足りません（構造化役は呼ばずに ms4:questions）")
                return "QUESTIONS"
            return self.structure_gdd(num, branch, version, sha, marker, rec)
        except Abort as e:
            rec["result"], rec["reason"] = "ABORT", str(e)
            raise
        finally:
            self.record(rec)

    def structure_gdd(self, num, branch, version, sha, marker, rec):
        """使い捨ての worktree で構造化役を動かし、docs/spec の 2 ファイルだけを PR にコミットする。"""
        tag = f"gdd-v{version}-{sha[:8]}"
        wt = self.worktree_root() / f"{self.cfg.get('project_id') or 'project'}-{tag}"
        work = self.out / "spec" / tag
        work.mkdir(parents=True, exist_ok=True)
        prev_path = ""
        prev = self.git.show(f"origin/{self.base}", SPEC_FILES[0])
        if prev is not None:
            (work / "previous_spec.md").write_bytes(prev.encode("utf-8"))
            prev_path = str(work / "previous_spec.md")
        if wt.exists() and not self.git.worktree_remove(wt):
            raise Abort(f"前回の worktree の残骸を消せません（ファイルが掴まれている可能性）: {wt}")
        self.git.worktree_add(wt, branch, f"origin/{branch}")
        wgit = Git(wt, self.cfg, self.pause)
        try:
            rc, log = self.step(rec, "spec", gdd=str(wt / GDD_PATH), out_dir=str(wt / "docs" / "spec"),
                                work_dir=str(work), previous_spec=prev_path)
            if rc == 1:
                tail = tail_of(log, self.cfg["comment_log_tail_chars"]).replace("```", "\'\'\'")
                self.gh.comment(num, marker,
                                f"**ms4: 構造化に失敗しました**（GDD v{version}、`{sha[:8]}`）\n\n"
                                "構造化役の出力が、全試行で網羅検査に通りませんでした（何もコミットしていません）。"
                                "この GDD は再処理しません。GDD を直して版を上げ、`dispatch --submit-gdd` で送り直してください。\n\n"
                                f"記録: `{work}`\n\n<details><summary>ログ末尾</summary>\n\n```\n{tail}\n```\n</details>\n",
                                kind="pr")
                self.gh.set_labels(num, add=["failed"], kind="pr")
                rec["result"], rec["reason"] = "REJECT", "構造化役の出力が全試行で不合格"
                return "REJECT"
            if rc != 0:
                raise Abort(f"spec.py が rc={rc} で終了しました（環境異常）", log=log)
            changed = wgit.changed_paths()
            bad = [c for c in changed if c not in SPEC_FILES]
            if bad:
                raise Abort("構造化で docs/spec の 2 ファイル以外が変わりました: " + ", ".join(bad[:5]), log=log)
            try:
                summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))["summary"]
            except (OSError, ValueError, KeyError) as e:
                raise Abort(f"spec.py は rc=0 ですが要約（{work / 'summary.json'}）を読めません: {e}", log=log)
            if changed:
                wgit.add_commit(changed, f"docs(spec): structured spec for GDD v{version}")
                wgit.push(branch)
            head = wgit.head_sha()
        finally:
            if not self.git.worktree_remove(wt):
                rec["worktree_left"] = str(wt)
                print(f"  worktree を消せませんでした（次に使う前に消し直します）: {wt}")
            self.git._git("branch", "-D", branch, check=False)

        rec.update(head_sha=head, mergeable=summary["mergeable"], provisional=len(summary["provisional"]),
                   questions=len(summary["questions"]), anchors_missing=summary["anchors_missing"])
        self.gh.comment(num, marker, self.gdd_report(version, sha, head, summary, work), kind="pr")
        if summary["mergeable"]:
            self.gh.comment(num, f"ms4:approval-request:{head}",
                            self.gdd_approval_request(num, version, sha, head, summary), kind="pr")
            self.gh.set_labels(num, add=["awaiting"], kind="pr")
            rec["result"] = "AWAITING"
            print(f"=== GDD v{version}: 構造化仕様はマージ可。PR #{num} で人間の承認を待ちます")
            return "AWAITING"
        self.gh.set_labels(num, add=["questions"], kind="pr")
        rec["result"], rec["reason"] = "QUESTIONS", "仮・質問・錨の欠落あり"
        print(f"=== GDD v{version}: 仮・質問・錨の欠落があります（ms4:questions）")
        return "QUESTIONS"

    def gdd_precheck_report(self, version, sha, missing):
        return (f"**ms4: GDD の錨が足りません**（GDD v{version}、`{sha[:8]}`）\n\n"
                "構造化役（LLM）は呼んでいません。錨が GDD に無いと、構造化しても必ずマージできないためです"
                "（`docs/design/spec_pipeline.md` §5・§7）。\n\n### 足りないもの\n\n"
                + "\n".join(f"- {x}" for x in missing)
                + "\n\n### 次にすること\n\nGDD に書き足して `<!-- version: -->` を上げ、"
                "`python tools/dispatch.py --submit-gdd drafts/gdd/<project>.md` で送り直してください"
                "（この PR は自動で閉じられます）。この GDD は再処理しません。\n")

    def gdd_report(self, version, sha, head, s, work):
        def rows(items, fmt):
            return "\n".join(fmt(x) for x in items) or "（なし）"
        heads = lambda x: " › ".join(x.get("headings") or [])
        body = (f"**ms4: 構造化仕様の検査結果**（GDD v{version}、`{sha[:8]}` → コミット `{head[:8]}`）\n\n"
                f"- 判定: **{'マージ可' if s['mergeable'] else 'マージ不可（GDD の不足）'}**\n"
                f"- GDD: {s['gdd']['lines']} 行（網羅の対象 {s['gdd']['target_lines']} 行）\n"
                "- 件数: " + ", ".join(f"{k} {v}" for k, v in s["counts"].items()) + "\n"
                f"- 記録: `{work}`\n\n"
                "### 仮の値\n\n" + rows(s["provisional"], lambda x: f"- {x['id']} {x['name']} = {x['value']}（{x['refs']}、{heads(x)}）")
                + "\n\n### 質問\n\n" + rows(s["questions"], lambda x: f"- {x['id']} {x['question']}（{x['refs']}、{heads(x)}）")
                + "\n\n### 錨の欠落\n\n" + rows(s["anchors_missing"], lambda x: f"- {x}")
                + "\n\n### 人間確認・演出（HC）\n\n" + rows(s["human_checks"], lambda x: f"- {x['id']} {x['content']}（{x['refs']}）")
                + "\n\n### GDD に無い、機能名らしい語（候補。合否には使っていない）\n\n"
                + (", ".join(s["term_candidates"]) or "（なし）"))
        if s.get("id_changes"):
            c = s["id_changes"]
            body += ("\n\n### 前の版からの ID の変化\n\n"
                     f"- 変わった: {', '.join(c['changed']) or 'なし'}\n- 消えた: {', '.join(c['removed']) or 'なし'}\n"
                     f"- 新しい: {', '.join(c['added']) or 'なし'}")
        if not s["mergeable"]:
            body += ("\n\n### 次にすること\n\n仮・質問・錨の欠落は、GDD に書かれていないことを表します。"
                     "値を決めて GDD に書き足し、`<!-- version: -->` を上げて `dispatch --submit-gdd` で送り直してください。"
                     "仮のまま承認する道はありません（§7）。この GDD は再処理しません。")
        return body + "\n"

    def gdd_approval_request(self, num, version, sha, head, s):
        project_id = self.cfg.get("project_id") or self.cfg["repo_slug"]
        return (f"**ms4: 承認依頼** — GDD v{version} の構造化仕様（PR #{num}）\n\n"
                f"対象コミット: `{head[:8]}`（この SHA に対してだけ有効。push されると承認は外れます）\n\n"
                f"GDD `{sha[:8]}` から構造化役が作り、網羅検査に合格しました。仮 0・質問 0・錨の欠落 0。"
                "内容は直前の「構造化仕様の検査結果」と、PR の `docs/spec/spec.md` を見てください。\n\n"
                "### 人間が実機で見ること\n\n（GDD の構造化仕様のため、実機での確認はありません）\n\n"
                "### 承認\n\n```\n"
                f"python tools/dispatch.py --approve {project_id}#{num}\n"
                f"python tools/dispatch.py --decline {project_id}#{num} --reason \"...\"\n```\n")

    def handle_gdd_decision(self, item):
        """承認待ちの GDD の PR。承認済み・必須チェック緑ならマージ、却下なら閉じる。Issue は無い。"""
        num, branch = item["number"], item["headRefName"]
        pr = self.gh.view(num, "pr")
        names = pr["labels"]
        L = {k: self.labels_name(k) for k in ("approved", "declined")}
        if L["declined"] not in names and L["approved"] not in names:
            return "WAITING"
        if "questions" in self.cfg["labels"] and self.labels_name("questions") in names:
            print(f"  GDD の PR #{num}: ms4:questions が付いているのでマージしません")
            return "WAITING"
        if L["declined"] not in names:
            states = self.gh.check_states(pr["sha"], self.cfg["required_checks"])
            not_ok = {k: v for k, v in states.items() if v != "success"}
            if not_ok:
                print(f"  GDD の PR #{num}: 承認ラベルはあるが必須チェックが揃っていません: {not_ok}")
                return "WAITING"
        rec = self.new_record(issue=branch.replace("spec/", ""), kind="gdd", pr=num, branch=branch)
        self.touched = True
        try:
            if L["declined"] in names:
                print(f"\n=== GDD の PR #{num}: 却下")
                self.gh.close(num, "pr")
                self.gh.set_labels(num, remove=["awaiting"], kind="pr")
                rec["result"], rec["reason"] = "REJECT", "declined"
                self.record_human(rec, num)
                return "REJECT"
            print(f"\n=== GDD の PR #{num}: 承認済み。マージします")
            try:
                self.gh.merge_pr(num, pr["sha"])
            except Abort:
                now = self.gh.view(num, "pr")
                if now["state"] != "MERGED" and now["sha"] != pr["sha"]:
                    print(f"  承認後に push されました（{pr['sha'][:8]} → {now['sha'][:8]}）。承認待ちに戻します")
                    rec["result"], rec["reason"] = "WAITING", "head moved after approval"
                    return "WAITING"
                if now["state"] != "MERGED":
                    raise
            merged = self.gh.view(num, "pr")
            rec["merge_sha"] = merged["merge_sha"]
            self.git.pull_ff()
            self.gh.set_labels(num, remove=["awaiting"], kind="pr")
            self.record_human(rec, num)
            rec["result"] = "PASSED"
            print(f"=== GDD の PR #{num} をマージしました")
            return "PASSED"
        except Abort as e:
            rec["result"], rec["reason"] = "ABORT", str(e)
            raise
        finally:
            if rec.get("result") != "WAITING":
                self.record(rec)

    def handle(self, issue):
        n, title = issue["number"], issue["title"]
        self.wt, self.igit = None, None
        rec = self.new_record(issue=n, title=title)
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
        gdds = self.gh.list_gdd_prs()
        print(f"GDD の PR: {len(gdds)} 本")
        for item in gdds:
            print(f"  PR #{item['number']} {item.get('headRefName')}")
        issues = self.gh.list_ready()
        print(f"対象: {len(issues)} 件（上限 {self.cfg['max_issues_per_run']}）")
        for it in issues[:self.cfg["max_issues_per_run"]]:
            n = it["number"]
            unit = self.cfg["unit_path_template"].format(number=n)
            print(f"\n#{n} {it['title']}")
            print(f"  ブランチ: {self.cfg['branch_prefix']}{n}")
            for name, fmt in (("decompose", {"number": n}), ("audit", {"file": unit}),
                              ("pipeline", {"unit": unit})):
                print("  " + " ".join(a.format(python="python", harness=project.HARNESS_DIR.as_posix(),
                                               project=self.cfg.get("project_id", ""),
                                               telemetry=f"<{name}.telemetry.json>",
                                               repo=str(self.issue_worktree(n)), **fmt)
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
        rc = self.cycle(max_issues)
        if rc != 2:
            self.release_lock()
        return rc

    def cycle(self, max_issues=None):
        """1 周。ロックは呼び出し側が持っている。ABORT のときのロックの扱いはここで決める。"""
        self.touched = False
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
            # GDD の PR（処理済みの GDD は handle_gdd の中で飛ばす）
            for item in self.gh.list_gdd_prs():
                results.append(self.handle_gdd(item))
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

        print(f"\n完了: マージ {results.count('PASSED')} / 承認待ちへ {results.count('AWAITING')} / "
              f"待機中 {results.count('WAITING')} / 質問待ち {results.count('QUESTIONS')} / 不合格 {results.count('REJECT')}")
        return 1 if "REJECT" in results else 0


PROJECT_KEYS = ("project_id", "repo_slug", "repo_dir", "base_branch", "out_dir",
                "test_dir", "audit_dir", "unit_path_template", "required_checks", "adapters")


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
        adapters=p["adapters"],
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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="常駐する（1 周 → 待機 → 1 周）")
    mode.add_argument("--stop", action="store_true", help="常駐に停止を依頼する（停止ファイルを置く）")
    mode.add_argument("--status", action="store_true", help="ロック・ハートビートを表示する（何も変えない）")
    ap.add_argument("--interval", type=int, help="常駐の待機秒数（既定は watch_interval_seconds）")
    a = ap.parse_args(argv)
    if a.project:
        cfg = build_config(a.project)
    else:
        cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    s = Scheduler(cfg, gh_run=gh_run, sleep=sleep)
    if a.stop:
        return s.request_stop()
    if a.status:
        return s.status()

    # pythonw（sys.stdout が None）と常駐では、出力をファイルへ付け替える
    log = DailyLog(cfg["out_dir"]) if (a.watch or sys.stdout is None) else None
    saved = (sys.stdout, sys.stderr)
    try:
        if log is not None:
            log.ensure()
        if a.watch:
            return s.watch(a.interval or cfg.get("watch_interval_seconds", 120), a.max_issues, log)
        return s.run(dry_run=a.dry_run, max_issues=a.max_issues)
    finally:
        if log is not None:
            sys.stdout, sys.stderr = saved
            log.close()


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
