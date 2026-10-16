"""工廠の走らせ役（W-46）：監修つきの計画 → 開始のコミット → H ラインで積み切り → 採点つきの記録。

    python -m harness.bench_run factory --task <id> --run-id <r> --repo <clone> --base <rev> [--out <dir>] [--sessions <dir>]
    python -m harness.bench_run finish  --task <id> --run-id <r> --repo <clone> [--out <dir>] [--oracles <dir>]

factory（制限のアカウント）は、走行のディレクトリ <out>/runs/<id>/factory-<r>/（env.json・inputs/・calls/・factory/・git/）を書く。
終了コード：書けば 0（無効でも 0）、<clone> の HEAD が <rev> でない・書けないときは 2。
finish（操縦士のアカウント）は、そのディレクトリに漏れの走査（REQ-11）と枝の先端の採点（REQ-18）を掛け、
manifest.json と <clone>/bench/records/<id>/factory-<r>.json を書く。factory/run.json が無ければ 1、書けば 0、採点できなければ 2。
<out> は無ければ <clone>/.bench。poll は 1 回ずつ TTL つきの子プロセスで起こし、同じ終わり方が 3 回続けば止める（REQ-22）。
"""
import argparse
import datetime
import hashlib
import getpass
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_score  # noqa: E402
import bench_start  # noqa: E402
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import hline_queue  # noqa: E402
import hline_quota  # noqa: E402
import hline_usage  # noqa: E402
import proc  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIMIT_SECONDS = 3 * 3600
POLL_TTL = 3600
TOKEN_LIMIT = 8_000_000
SAME_ENDING = 3
PAUSE = 30   # 進みの無かった poll の後に眠る秒
QUOTA_NAP = 600
GROUP = "bench"
REVISED = "監修役が差し戻しました"
CLI_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
LOG_SPLIT = "\n--- stderr ---\n"
PY = sys.executable

clock = time.time   # 試験が差し替える
sleep = time.sleep


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child_env():
    """Python の子プロセスに UTF-8 で書かせる環境（Windows の既定の文字コードで書かれると、読む側で文字化けする）。"""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run_dir(out, task, run_id):
    return Path(out) / "runs" / task / f"factory-{run_id}"


def work_dir(out, task, run_id):
    return Path(out) / "work" / task / f"factory-{run_id}"


def read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def line_config(base_cfg, work, inbox, base, branch):
    """clone の config/hline.json の 8 つの欄を走行用に上書きしたもの。"""
    return {**base_cfg, "inbox": str(inbox), "out": str(work / "out"), "worktrees": str(work / "wt"), "base": base,
            "integration_branch": branch, "milestones": [GROUP], "open_pr": False, "gate_always_modules": []}


# ============================================================ 呼び出しの記録

def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def usage_of(doc):
    u = doc.get("usage") if isinstance(doc, dict) else None
    return tuple(_int(u.get(k)) for k in CLI_KEYS) if isinstance(u, dict) else (0, 0, 0, 0)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if isinstance(p, str)) or None
    return None


def read_session(path):
    """セッションの記録（jsonl）から、入力の全文・作業ディレクトリ・最初の時刻・CLI の出力に似せた結果を読む。"""
    info = {"prompt": None, "cwd": None, "ts": "", "sid": None}
    steps, last = {}, ""
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        info["cwd"] = info["cwd"] or e.get("cwd")
        info["ts"] = info["ts"] or str(e.get("timestamp") or "")
        info["sid"] = info["sid"] or e.get("sessionId")
        m = e["message"] if isinstance(e.get("message"), dict) else {}
        if e.get("type") == "user" and info["prompt"] is None:
            info["prompt"] = text_of(m.get("content"))
        elif e.get("type") == "assistant":
            if isinstance(m.get("usage"), dict):
                steps[m.get("id") or len(steps)] = (m.get("model") or "unknown", m["usage"])   # 同じ id は後のものが最終の値
            last = text_of(m.get("content")) or last
    per_model = {}
    for model, u in steps.values():
        row = per_model.setdefault(model, {k: 0 for k in CLI_KEYS})
        for k in CLI_KEYS:
            row[k] += _int(u.get(k))
    total = [sum(r[k] for r in per_model.values()) for k in CLI_KEYS]
    info["sid"] = info["sid"] or Path(path).stem
    info["result"] = {"session_id": info["sid"], "result": last, "usage": dict(zip(CLI_KEYS, total)),
                      "modelUsage": {m: {"inputTokens": r[CLI_KEYS[0]], "cacheCreationInputTokens": r[CLI_KEYS[1]],
                                         "cacheReadInputTokens": r[CLI_KEYS[2]], "outputTokens": r[CLI_KEYS[3]]}
                                     for m, r in per_model.items()}}
    return info


def cli_outputs(out):
    """生ログ（<out> の下の *.log）の CLI の出力を session_id で引く。"""
    found = {}
    for p in sorted(Path(out).rglob("*.log")) if Path(out).is_dir() else []:
        head = p.read_text(encoding="utf-8", errors="replace").split(LOG_SPLIT, 1)[0]
        try:
            doc = json.loads(head)
        except ValueError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("session_id"), str):
            found[doc["session_id"]] = doc
    return found


def _under(path, root):
    norm = lambda p: str(p).replace("\\", "/").rstrip("/").lower()   # noqa: E731
    return norm(path) == norm(root) or norm(path).startswith(norm(root) + "/")


def sessions_now(sessions):
    return {p for p in Path(sessions).rglob("*.jsonl")} if Path(sessions).is_dir() else set()


def log_role(path):
    """生ログの置き場から役を決める。呼び出しの生ログでないもの（gate-*.log など）は None。"""
    if path.parent.name == "planner":
        return "planner"
    if path.parent.name == "review":
        return "reviewer"
    return "implementer" if path.name.startswith("implementer-") else None


def log_calls(out, skip_sids):
    """生ログ（CLI の出力が JSON のもの）を呼び出し 1 回ごとに。セッションの記録で拾えた分（skip_sids）は除く。"""
    calls = []
    for p in sorted(Path(out).rglob("*.log")) if Path(out).is_dir() else []:
        role = log_role(p)
        if role is None or (role == "planner" and p.with_name(p.stem + "-1.log").exists()):   # <名前>.log は最後の回の写し
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8", errors="replace").split(LOG_SPLIT, 1)[0])
        except ValueError:
            continue
        if not isinstance(doc, dict) or not ({"usage", "modelUsage", "session_id"} & doc.keys()) or doc.get("session_id") in skip_sids:
            continue
        prompt = hline_base.prompt_path(p)
        calls.append({"role": role, "path": None, "ts": f"{p.stat().st_mtime:020.6f}", "result": doc,
                      "prompt": prompt.read_text(encoding="utf-8", errors="replace") if prompt.is_file() else ""})
    return sorted(calls, key=lambda c: c["ts"])


def collect_calls(sessions, before, work):
    """この走行の呼び出しを、LLM の 1 回ごとに。セッションの記録（時刻の順。役は作業ディレクトリで決める）に、
    記録の見つからなかった呼び出しを生ログから足す。"""
    outputs, calls = cli_outputs(work / "out"), []
    for p in sorted(sessions_now(sessions) - before):
        info = read_session(p)
        cwd = info["cwd"] or ""
        role = ("planner" if _under(cwd, work / "plan") else "implementer" if _under(cwd, work / "wt") else "reviewer")
        calls.append({"role": role, "path": p, "prompt": info["prompt"] or "", "ts": info["ts"],
                      "result": outputs.get(info["sid"], info["result"])})
    calls.sort(key=lambda c: (c["ts"], str(c["path"])))
    return calls + log_calls(work / "out", {c["result"].get("session_id") for c in calls})


def tokens_of(calls):
    sums = [sum(usage_of(c["result"])[i] for c in calls) for i in range(4)]
    return {**dict(zip(hline_usage.KEYS, sums)), "total": sum(sums)}


def write_calls(rundir, calls):
    for i, c in enumerate(calls, 1):
        d = rundir / "calls" / f"{i:03d}-{c['role']}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt.txt").write_text(c["prompt"], encoding="utf-8")
        hline_base.write_json(d / "result.json", c["result"])
        if c["path"] is not None:
            shutil.copy2(c["path"], d / "session.jsonl")


# ============================================================ 時間

class Timer:
    """実行の時間。起点は factory の開始で、キューの quota_wait が立っていた間は除く（REQ-17）。"""

    def __init__(self):
        self.start = clock()
        self.waits = {}   # 立った時刻 → 再開の時刻（epoch 秒）

    def note(self, st):
        qw = st.get("quota_wait")
        if isinstance(qw, dict):
            try:
                self.waits[qw["at"]] = datetime.datetime.fromisoformat(qw["until"]).timestamp()
            except (KeyError, TypeError, ValueError):
                pass

    def waited(self, now):
        total = 0.0
        for at, until in self.waits.items():
            try:
                a = max(datetime.datetime.fromisoformat(at).timestamp(), self.start)
            except ValueError:
                continue
            total += max(0.0, min(until, now) - a)
        return total

    def elapsed(self):
        now = clock()
        return now - self.start - self.waited(now)


def ending(code, err):
    """0 以外で終わった poll の終わり方（終了コード、標準エラーの最後の空でない行）。TTL 切れは (124, "TTL")。"""
    if code == 124:
        return 124, "TTL"
    lines = [x.strip() for x in err.splitlines() if x.strip()]
    return code, lines[-1] if lines else ""


# ============================================================ factory

class Factory:
    def __init__(self, a):
        self.task, self.run_id, self.repo, self.rev = a.task, a.run_id, Path(a.repo), a.base
        self.out = Path(a.out) if a.out else self.repo / ".bench"
        self.sessions = Path(a.sessions) if a.sessions else Path.home() / ".claude" / "projects"
        self.rundir, self.work = run_dir(self.out, self.task, self.run_id), work_dir(self.out, self.task, self.run_id)
        self.branch = f"bench/{self.task}/factory-{self.run_id}"
        self.config_path = self.repo / "config" / "hline.json"
        self.timer = Timer()
        self.started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        self.run = {"task": self.task, "run_id": self.run_id, "base": None, "start": None, "head": None,
                    "started_at": self.started_at, "invalid": None, "reason": None, "stop": None, "plan_rounds": 0,
                    "plan_confirmed": False, "planner_sha256": {}, "queue_sha256": {}, "replaced": 0, "tries": 0,
                    "intake_revised": 0, "tokens": {}, "quota_wait_seconds": 0}
        self.revised, self.polls, self.before = set(), [], set()

    # ---- 準備
    def check_head(self):
        self.run["base"] = git(self.repo, ["rev-parse", "--verify", "-q", f"{self.rev}^{{commit}}"]).decode().strip()
        if git(self.repo, ["rev-parse", "HEAD"]).decode().strip() != self.run["base"]:
            raise BenchError(f"{self.repo} の HEAD が {self.rev} ではありません")
        if (self.rundir / "factory" / "run.json").exists():
            raise BenchError(f"走行のディレクトリがすでにあります: {self.rundir}")

    def cfg(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def invalid(self, code, reason):
        self.run["invalid"], self.run["reason"] = code, reason

    def guard(self, cfg):
        code, out, err = proc.run([PY, "-m", "harness.bench_guard", "check", "--config", str(self.config_path)],
                                  self.repo, 600, "bench_guard check", env=child_env())
        if code != 0:
            self.invalid("REQ-10", f"bench_guard check が終了コード {code}: {(out + err).strip()[-400:]}")
            return False
        want = self.freeze_version()
        if want is not None:
            _, out, err = proc.run(proc.resolve_cli(cfg["implementer"]["cli"]) + ["--version"], self.repo, 60, "claude --version")
            have = out.strip()
            if want not in (have, have.split()[0] if have else ""):
                self.invalid("REQ-03", f"CLI の版が凍結の記録と違います: 記録 {want} ／ 実物 {have}")
                return False
        return True

    def freeze_version(self):
        try:
            doc = json.loads(git(self.repo, ["show", f"{self.run['base']}:bench/records/freeze.json"]).decode("utf-8"))
        except (BenchError, ValueError):
            return None
        return str(doc.get("cli_version")) if isinstance(doc, dict) and doc.get("cli_version") is not None else None

    # ---- 計画
    def plan(self, cfg):
        package = bench_start.package_of(self.repo, self.run["base"], self.task)
        spec = self.repo / "docs" / "bench" / self.task / "spec.md"
        plan_inbox = self.work / "plan"
        for d in (plan_inbox, self.work / "out", self.work / "wt", self.work / "inbox"):
            d.mkdir(parents=True, exist_ok=True)
        plan_cfg = self.work / "plan-config.json"
        hline_base.write_json(plan_cfg, line_config(cfg, self.work, plan_inbox, self.run["base"], self.branch))
        code, out, err = proc.run([PY, "-m", "harness.plan_loop", "--spec", str(spec), "--package", package, "--inbox",
                                   str(plan_inbox), "--config", str(plan_cfg)], self.repo,
                                  max(1, LIMIT_SECONDS - self.timer.elapsed()), "plan_loop", env=child_env())
        (self.rundir / "factory").mkdir(parents=True, exist_ok=True)
        (self.rundir / "factory" / "plan.txt").write_text(out + LOG_SPLIT + err, encoding="utf-8")
        doc = read_json(plan_inbox / "planner.json", {})
        loop = doc.get("loop") if isinstance(doc.get("loop"), dict) else {}
        self.run["plan_rounds"] = _int(loop.get("rounds"))
        self.run["planner_sha256"] = doc.get("files") if isinstance(doc.get("files"), dict) else {}
        inputs = self.rundir / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(spec, inputs / "spec.md")
        for p in plan_inbox.iterdir():
            if p.is_file() and (p.name.startswith("P-") or p.name in ("ledger.yaml", "planner.json")):
                shutil.copy2(p, inputs / p.name)
        if code == 1:
            self.run["reason"] = loop.get("failed", [])
            return None
        if code != 0:
            self.invalid("plan", f"plan_loop が終了コード {code}: {(out + err).strip()[-400:]}")
            return None
        self.run["plan_confirmed"] = True
        return plan_inbox

    def start(self, plan_inbox):
        code, out, err = proc.run([PY, "-m", "harness.bench_start", "--repo", str(self.repo), "--base", self.run["base"],
                                   "--task", self.task, "--ledger", str(plan_inbox / "ledger.yaml")], ROOT, 300, "bench_start", env=child_env())
        lines = out.strip().splitlines()
        if code != 0 or not lines:
            self.invalid("plan", f"bench_start が終了コード {code}: {(out + err).strip()[-400:]}")
            return False
        self.run["start"] = self.run["head"] = lines[-1].strip()
        return True

    def setup_clone(self, plan_inbox, cfg):
        start, origin, clone = self.run["start"], self.work / "origin.git", self.work / "clone"
        git(self.work, ["init", "-q", "--bare", str(origin)])
        git(self.repo, ["push", "-q", str(origin), f"{start}:refs/heads/start"])
        git(self.work, ["init", "-q", str(clone)])
        git(clone, ["remote", "add", "origin", str(origin)])
        git(clone, ["fetch", "-q", "origin"])
        git(clone, ["checkout", "-q", "--detach", start])
        for p in plan_inbox.glob("P-*.md"):
            shutil.copy2(p, self.work / "inbox" / p.name)
        self.line_cfg = line_config(cfg, self.work, self.work / "inbox", start, self.branch)
        self.line_path = self.work / "hline.json"
        hline_base.write_json(self.line_path, self.line_cfg)
        self.clone = clone

    # ---- H ライン
    def tip(self):
        git(self.clone, ["fetch", "-q", "--prune", "origin"])
        try:
            return git(self.clone, ["rev-parse", "--verify", "-q", f"refs/remotes/origin/{self.branch}^{{commit}}"]).decode().strip()
        except BenchError:
            return self.run["start"]

    def tokens(self):
        return tokens_of(collect_calls(self.sessions, self.before, self.work))

    def stop_reason(self):
        if self.tokens()["total"] >= TOKEN_LIMIT:
            return "tokens"
        return "time" if self.timer.elapsed() >= LIMIT_SECONDS else None

    def one_poll(self):
        tip = self.tip()
        git(self.clone, ["checkout", "-q", "-f", "--detach", tip])
        ttl = min(LIMIT_SECONDS - self.timer.elapsed(), POLL_TTL)
        code, out, err = proc.run([PY, "-m", "harness.hline", "poll", "--config", str(self.line_path)], self.clone, ttl,
                                  "hline poll", env=child_env())
        self.polls.append({"code": code, "ending": list(ending(code, err)) if code else None, "ttl": ttl})
        return code, err

    def loop(self):
        last, streak = None, 0
        while True:
            if (why := self.stop_reason()):
                self.run["stop"] = why
                return
            code, err = self.one_poll()
            st = hline_queue.load_state(self.line_cfg)
            self.timer.note(st)
            self.revised |= {Path(s["file"]).stem for s in st["skipped"] if str(s.get("reason", "")).startswith(REVISED)}
            if code == 0:
                last, streak = None, 0
            else:
                end = ending(code, err)
                last, streak = end, (streak + 1 if end == last else 1)
                if streak >= SAME_ENDING:
                    self.invalid("REQ-22", f"poll が同じ終わり方（終了コード {end[0]}、標準エラーの最後の行 {end[1]!r}）で {SAME_ENDING} 回続きました")
                    return
            if hline_queue.only_orphans_wait(st) and not st.get("quota_wait"):
                self.run["stop"] = "done"
                return
            if (why := self.stop_reason()):
                self.run["stop"] = why
                return
            until = hline_quota.until(st)
            if st.get("quota_wait") and until and until.timestamp() > clock():
                sleep(min(until.timestamp() - clock(), QUOTA_NAP))
            elif not self.progressed(st):
                sleep(PAUSE)

    def progressed(self, st):
        key = json.dumps(st, sort_keys=True, default=str)
        changed = key != getattr(self, "_seen", None)
        self._seen = key
        return changed

    # ---- 書き出し
    def cli_version(self):
        try:
            code, out, _ = proc.run(proc.resolve_cli(self.cfg()["implementer"]["cli"]) + ["--version"], self.repo, 60, "claude --version")
        except (OSError, KeyError, ValueError, SystemExit):
            return None
        return out.strip() if code == 0 and out.strip() else None

    def env(self):
        """走行の環境の記録（REQ-31）。"""
        try:
            mujoco = importlib.metadata.version("mujoco")
        except importlib.metadata.PackageNotFoundError:
            mujoco = None
        return {"task": self.task, "run_id": self.run_id, "os": platform.platform(), "platform": platform.platform(),
                "cli_version": self.cli_version(), "python": platform.python_version(), "mujoco": mujoco,
                "base": self.run["base"], "start": self.run["start"], "account": getpass.getuser(),
                "started_at": self.started_at, "finished_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "quota_wait_seconds": int(round(self.timer.waited(clock())))}

    def finalize(self):
        run, rundir = self.run, self.rundir
        st = hline_queue.load_state(self.line_cfg) if getattr(self, "line_cfg", None) else {"items": {}}
        items = st["items"]
        run["replaced"] = sum(1 for i in items.values() if i.get("replaced"))
        run["tries"] = sum(len(i.get("tries") or []) for i in items.values())
        run["intake_revised"] = len(self.revised)
        run["queue_sha256"] = {n: sha256_file(hline_queue.what_path(self.line_cfg, n)) for n in sorted(items)
                               if hline_queue.what_path(self.line_cfg, n).is_file()}
        calls = collect_calls(self.sessions, self.before, self.work)
        write_calls(rundir, calls)
        run["tokens"] = tokens_of(calls)
        run["quota_wait_seconds"] = int(round(self.timer.waited(clock())))
        if run["start"]:
            run["head"] = self.tip()
            (rundir / "git").mkdir(parents=True, exist_ok=True)
            git(self.clone, ["update-ref", "refs/heads/tip", run["head"]])
            git(self.clone, ["bundle", "create", "-q", str(rundir / "git" / "run.bundle"), "refs/heads/tip"])
            if (queue := Path(self.line_cfg["out"]) / "queue.json").is_file():
                shutil.copy2(queue, rundir / "factory" / "queue.json")
        for sub in ("inputs", "calls", "factory", "git"):
            (rundir / sub).mkdir(parents=True, exist_ok=True)
        hline_base.write_json(rundir / "factory" / "polls.json", self.polls)
        hline_base.write_json(rundir / "env.json", self.env())
        hline_base.write_json(rundir / "factory" / "run.json", run)


def factory(a):
    f = Factory(a)
    try:
        f.check_head()
        cfg = f.cfg()
        f.before = sessions_now(f.sessions)
        f.rundir.mkdir(parents=True, exist_ok=True)
        if f.guard(cfg):
            plan_inbox = f.plan(cfg)
            if plan_inbox is not None and f.start(plan_inbox):
                f.setup_clone(plan_inbox, cfg)
                f.loop()
        f.finalize()
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


# ============================================================ finish

def manifest(rundir):
    files = sorted(p for p in Path(rundir).rglob("*") if p.is_file() and p != Path(rundir) / "manifest.json")
    return {p.relative_to(rundir).as_posix(): sha256_file(p) for p in files}


def score_tree(task, tree, out, oracles=None):
    return bench_score.score(task, tree, out, oracles)


def allowed_path(path, package):
    return (path.startswith(package + "/") or re.fullmatch(rf"tests/test_{re.escape(package)}[^/]*", path) is not None
            or path == "docs/progress.yaml")   # 進捗の記録（harness.progress complete）が書く台帳は、積む操作の一部


def grade(run, rundir, repo, task, oracles, package):
    """(violations, passed, total, timed_out)。bundle の先端の木を採点する。"""
    with tempfile.TemporaryDirectory(prefix="bench-run-") as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        git(tree, ["init", "-q"])
        git(tree, ["fetch", "-q", str(rundir / "git" / "run.bundle"), "refs/heads/tip:refs/heads/tip"])
        git(tree, ["checkout", "-q", "tip"])
        names = git(tree, ["diff", "--name-only", "-z", run["start"], "tip"]).decode("utf-8", "replace").split("\0")
        violations = [n for n in names if n and not allowed_path(n, package)]
        out = Path(tmp) / "score.json"
        if score_tree(task, tree, out, oracles) != 0:
            raise BenchError("採点できません（非公開オラクルの指紋・置き場を確かめてください）")
        shutil.copy2(out, rundir / "score.json")
        score = json.loads(out.read_text(encoding="utf-8"))
    return violations, score["passed"], score["total"], bool(score.get("timed_out"))


def model_problem(rundir, cfg):
    allowed = cfg["implementer"]["model"]
    bad = set()
    for p in sorted((rundir / "calls").glob("*/result.json")):
        usage = read_json(p, {}).get("modelUsage")
        bad |= {m for m in usage if m != allowed and not m.startswith("claude-haiku-")} if isinstance(usage, dict) else set()
    return sorted(bad)


def finish(a):
    repo = Path(a.repo)
    out = Path(a.out) if a.out else repo / ".bench"
    rundir = run_dir(out, a.task, a.run_id)
    run = read_json(rundir / "factory" / "run.json", {})
    if not run:
        print(f"factory/run.json がありません: {rundir}", file=sys.stderr)
        return 1
    try:
        cfg = json.loads((repo / "config" / "hline.json").read_text(encoding="utf-8"))
        invalid, reason = run.get("invalid"), run.get("reason")
        if not invalid:
            code, text, err = proc.run([PY, "-m", "harness.bench_guard", "scan", str(rundir), "--config",
                                        str(repo / "config" / "hline.json")], ROOT, 600, "bench_guard scan", env=child_env())
            (rundir / "guard.txt").write_text(text + err, encoding="utf-8")
            if code == 1:
                invalid, reason = "REQ-11", "漏れの跡があります（guard.txt）"
            elif code != 0:
                raise BenchError(f"bench_guard scan が終了コード {code}: {(text + err).strip()[-400:]}")
        if not invalid and not list((rundir / "calls").glob("*/result.json")):
            invalid, reason = "REQ-22", "呼び出しの記録がありません"
        if not invalid and (bad := model_problem(rundir, cfg)):
            invalid, reason = "REQ-02", f"許されないモデルが使われました: {', '.join(bad)}"
        violations, passed, total, timed_out = [], 0, 0, False
        if not invalid and run.get("plan_confirmed"):
            package = bench_start.package_of(repo, run["base"], a.task)
            violations, passed, total, timed_out = grade(run, rundir, repo, a.task, a.oracles, package)
            passed = 0 if violations else passed
        mf = manifest(rundir)
        hline_base.write_json(rundir / "manifest.json", mf)
        keys = ("task", "run_id", "base", "start", "head", "started_at")
        tail = ("stop", "plan_rounds", "plan_confirmed", "planner_sha256", "queue_sha256", "replaced", "tries",
                "intake_revised", "tokens", "quota_wait_seconds")
        record = {**{k: run.get(k) for k in keys}, "invalid": invalid or False, "reason": reason, "violations": violations,
                  "passed": passed, "total": total, "rate": passed / total if total else 0.0, "timed_out": timed_out,
                  **{k: run.get(k) for k in tail}, "manifest_sha256": sha256_file(rundir / "manifest.json")}
        dest = repo / "bench" / "records" / a.task / f"factory-{a.run_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(dest, record)
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


# ============================================================ stage

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def set_execution_state(flags):   # 試験が差し替える。OS の眠りの止めを掛ける所
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))


class KeepAwake:
    def __enter__(self):
        if sys.platform == "win32":
            set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return self

    def __exit__(self, *exc):
        if sys.platform == "win32":
            set_execution_state(ES_CONTINUOUS)
        return False


def stage_tasks(repo, base):
    names = git(repo, ["ls-tree", "-d", "--name-only", base, "docs/bench/"]).decode("utf-8").split("\n")
    return sorted(n.rsplit("/", 1)[-1] for n in names if n.rsplit("/", 1)[-1].startswith("B"))


def stage_state(out, repo, task, tag):
    """(済みか, 仕上げ待ちの回の一覧, 使った最大の回)。"""
    pat = re.compile(rf"factory-{re.escape(tag)}-(\d+)")
    done, recorded, rounds = False, set(), {}
    for p in (Path(repo) / "bench" / "records" / task).glob(f"factory-{tag}-*.json"):
        if m := pat.fullmatch(p.stem):
            recorded.add(int(m.group(1)))
            rounds[int(m.group(1))] = True
            done = done or not read_json(p, {}).get("invalid", True)
    waiting = []
    for p in (Path(out) / "runs" / task).glob(f"factory-{tag}-*"):
        if (m := pat.fullmatch(p.name)) and p.is_dir():
            n = int(m.group(1))
            rounds[n] = True
            run = read_json(p / "factory" / "run.json", {})
            if run and not run.get("invalid") and n not in recorded:
                waiting.append(n)
    return done, sorted(waiting), max(rounds, default=0)


def stage_finish(a, out):
    worst = 0
    for task in stage_tasks(a.repo, a.base) if a.base else sorted(p.name for p in (Path(out) / "runs").glob("*") if p.is_dir()):
        _, waiting, _ = stage_state(out, a.repo, task, a.tag)
        for n in waiting:
            code = finish(argparse.Namespace(task=task, run_id=f"{a.tag}-{n}", repo=a.repo, out=a.out, oracles=a.oracles))
            worst = max(worst, code)
    return worst


def stage(a):
    out = Path(a.out) if a.out else Path(a.repo) / ".bench"
    if a.finish:
        return stage_finish(a, out)
    if not a.base:
        print("--base が要ります", file=sys.stderr)
        return 2
    try:
        tasks = stage_tasks(a.repo, a.base)
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    with KeepAwake():
        for task in tasks:
            done, waiting, last = stage_state(out, a.repo, task, a.tag)
            if done or waiting:
                continue
            streak = 0
            while True:
                last += 1
                run_id = f"{a.tag}-{last}"
                code = factory(argparse.Namespace(task=task, run_id=run_id, repo=a.repo, base=a.base, out=a.out, sessions=None))
                if code != 0:
                    print(f"{task}: factory が終了コード {code}（{run_id}）", file=sys.stderr)
                    return code
                run = read_json(run_dir(out, task, run_id) / "factory" / "run.json", {})
                if not run:
                    print(f"{task}: factory/run.json がありません（{run_id}）", file=sys.stderr)
                    return 2
                if not run.get("invalid"):
                    break
                streak += 1
                if streak >= SAME_ENDING:
                    print(f"{task}: 無効の走行が {SAME_ENDING} 回続きました: {run.get('reason') or run.get('invalid')}", file=sys.stderr)
                    return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_run", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("stage")
    sp.add_argument("--tag", required=True)
    sp.add_argument("--repo", required=True)
    sp.add_argument("--base")
    sp.add_argument("--out")
    sp.add_argument("--oracles")
    sp.add_argument("--finish", action="store_true")
    for name in ("factory", "finish"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--repo", required=True)
        p.add_argument("--out")
    sub.choices["factory"].add_argument("--base", required=True)
    sub.choices["factory"].add_argument("--sessions")
    sub.choices["finish"].add_argument("--oracles")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"factory": factory, "finish": finish, "stage": stage}[a.cmd](a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
