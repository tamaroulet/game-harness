"""工廠の走らせ役（W-46）：監修つきの計画 → 開始のコミット → H ラインで積み切り → 採点つきの記録。

    python -m harness.bench_run factory --task <id> --run-id <r> --repo <clone> --base <rev> [--out <dir>] [--sessions <dir>]
    python -m harness.bench_run finish  --task <id> --run-id <r> --repo <clone> [--out <dir>] [--oracles <dir>]
    python -m harness.bench_run plan --task <id> --repo <clone> --base <rev> [--out <dir>]
    python -m harness.bench_run plan --check --all --repo <clone> --base <rev>

plan は plan_loop を 1 回走らせ、確定すれば <clone>/bench/plans/<id>/ に P-*.md・ledger.yaml・planner.json を写して 0、
確定しなければ書かずに 1、すでにあれば上書きせず・環境の異常は 2。--check --all は、固定のすべての計画が
plan_check・要件の網羅・planner.json の sha256 に通れば 0、どれかが欠ければ 1。
factory --plan <dir> は plan_loop を呼ばず、固定の計画から走る。stage --plans <dir> は課題ごとに <dir>/<課題> を渡す。
固定の計画の走行は、H ラインを始める前に、受信箱の P-*.md のそれぞれに取り込みの監修役の控え（pass）を同じ鍵で置き、監修役を呼ばない（W-102）。
factory と stage は、走らせ役の harness/ の木と <rev> の harness/ の木が違えば（求められないときも）、何も走らせず終了コード 2 で止まる。

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

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_score  # noqa: E402
import bench_start  # noqa: E402
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import hline_queue  # noqa: E402
import hline_quota  # noqa: E402
import hline_review  # noqa: E402
import hline_usage  # noqa: E402
import machine  # noqa: E402
import plan_check  # noqa: E402
import plan_loop  # noqa: E402
import proc  # noqa: E402
import reqcov  # noqa: E402
import toolchain  # noqa: E402
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


def line_config(base_cfg, work, inbox, base, branch, implementer=None):
    """clone の config/hline.json の 8 つの欄を走行用に上書きしたもの。implementer があれば実装役の設定もそれに替える。"""
    cfg = {**base_cfg, "inbox": str(inbox), "out": str(work / "out"), "worktrees": str(work / "wt"), "base": base,
           "integration_branch": branch, "milestones": [GROUP], "open_pr": False, "gate_always_modules": []}
    return {**cfg, "implementer": implementer} if implementer is not None else cfg


def role_cli(cfg, role):
    """役の CLI の名前。planner の欄が無い設定では実装役のものを使う。"""
    return ((cfg.get(role) or {}).get("cli")) or cfg["implementer"]["cli"]


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


def pass_rates(items):
    """(first_pass, recovered)：試行のある What のうち最初の試行で Gate 1 を通った割合と、最初に落ちた What のうち後で通った割合。"""
    tried = [[isinstance(t, dict) and bool(t.get("gate")) for t in i.get("tries") or []] for i in items.values()]
    tried = [g for g in tried if g]
    if not tried:
        return None, None
    failed = [g for g in tried if not g[0]]
    return (len(tried) - len(failed)) / len(tried), (sum(any(g[1:]) for g in failed) / len(failed) if failed else None)


def ending(code, err):
    """0 以外で終わった poll の終わり方（終了コード、標準エラーの最後の空でない行）。TTL 切れは (124, "TTL")。"""
    if code == 124:
        return 124, "TTL"
    lines = [x.strip() for x in err.splitlines() if x.strip()]
    return code, lines[-1] if lines else ""


# ============================================================ 計画

def plan_files(d):
    """計画のディレクトリの P-*.md・ledger.yaml・planner.json（あるものだけ、名前の順）。"""
    return sorted(p for p in Path(d).iterdir() if p.is_file() and (
        (p.name.startswith("P-") and p.suffix == ".md") or p.name in ("ledger.yaml", "planner.json")))


def frozen_missing(d):
    """固定の計画に欠けているものの名前。"""
    if not Path(d).is_dir():
        return [str(d)]
    names = [p.name for p in plan_files(d)]
    return [w for w, ok in (("P-*.md", any(n.startswith("P-") for n in names)), ("ledger.yaml", "ledger.yaml" in names),
                            ("planner.json", "planner.json" in names)) if not ok]


def run_plan_loop(repo, base, task, cfg, work, branch, seconds):
    """作業の場所 <work>/plan を受信箱にして plan_loop を 1 回走らせる。(終了コード, 標準出力, 標準エラー, 受信箱, 仕様)。"""
    package = bench_start.package_of(repo, base, task)
    spec = repo / "docs" / "bench" / task / "spec.md"
    plan_inbox = work / "plan"
    for d in (plan_inbox, work / "out", work / "wt", work / "inbox"):
        d.mkdir(parents=True, exist_ok=True)
    plan_cfg = work / "plan-config.json"
    hline_base.write_json(plan_cfg, line_config(cfg, work, plan_inbox, base, branch))
    code, out, err = proc.run([PY, "-m", "harness.plan_loop", "--spec", str(spec), "--package", package, "--inbox",
                               str(plan_inbox), "--config", str(plan_cfg)], repo, max(1, seconds), "plan_loop", env=child_env())
    return code, out, err, plan_inbox, spec


def plan_problems(d, spec):
    """固定の計画 d の欠け（sha256・plan_check・要件の網羅）の理由の列。通れば空。"""
    d = Path(d)
    if not d.is_dir():
        return ["計画がありません"]
    files = read_json(d / "planner.json", {}).get("files")
    if not isinstance(files, dict) or not files:
        return ["planner.json に files がありません"]
    found = []
    for name in sorted(set(files) | {p.name for p in plan_files(d) if p.name != "planner.json"}):
        if name not in files:
            found.append(f"{name} が planner.json の files にありません")
        elif not (d / name).is_file():
            found.append(f"{name} がありません")
        elif sha256_file(d / name) != files[name]:
            found.append(f"{name} の sha256 が planner.json と違います")
    found += [f"plan_check: {rule}（{task or '全体'}）" for rule, task in plan_check.check(d)]
    try:
        defined = reqcov.definitions(spec)
    except (reqcov.InputError, OSError) as e:
        return found + [f"仕様を読めません: {e}"]
    return found + [f"reqcov: {i['reason']}" for i in plan_loop.stage_reqcov(d, defined)]


def plan_one(a):
    repo = Path(a.repo)
    out = Path(a.out) if a.out else repo / ".bench"
    dest = repo / "bench" / "plans" / a.task
    if dest.exists():
        print(f"ABORT: 固定の計画がすでにあります: {dest}", file=sys.stderr)
        return 2
    try:
        base = git(repo, ["rev-parse", "--verify", "-q", f"{a.base}^{{commit}}"]).decode().strip()
        if git(repo, ["rev-parse", "HEAD"]).decode().strip() != base:
            raise BenchError(f"{repo} の HEAD が {a.base} ではありません")
        cfg = machine.read_json(repo / "config" / "hline.json")
        work = work_dir(out, a.task, "plan")
        shutil.rmtree(work, ignore_errors=True)
        code, text, err, inbox, _ = run_plan_loop(repo, base, a.task, cfg, work, f"bench/{a.task}/plan", LIMIT_SECONDS)
        if code != 0:
            print((text + err).strip()[-800:], file=sys.stderr)
            return 1 if code == 1 else 2
        dest.mkdir(parents=True)
        for p in plan_files(inbox):
            shutil.copy2(p, dest / p.name)
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


def plan_check_all(a):
    repo = Path(a.repo)
    try:
        tasks = stage_tasks(repo, a.base)
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    bad = 0
    for task in tasks:
        for why in plan_problems(repo / "bench" / "plans" / task, repo / "docs" / "bench" / task / "spec.md"):
            print(f"{task}: {why}", file=sys.stderr)
            bad += 1
    if not tasks:
        print("課題がありません", file=sys.stderr)
    return 1 if bad or not tasks else 0


def plan(a):
    if a.check:
        if not a.all:
            print("--check には --all が要ります", file=sys.stderr)
            return 2
        return plan_check_all(a)
    if not a.task:
        print("--task が要ります", file=sys.stderr)
        return 2
    return plan_one(a)


# ============================================================ factory

class Factory:
    def __init__(self, a):
        self.task, self.run_id, self.repo, self.rev = a.task, a.run_id, Path(a.repo), a.base
        self.frozen = Path(a.plan) if getattr(a, "plan", None) else None
        self.out = Path(a.out) if a.out else self.repo / ".bench"
        self.sessions = Path(a.sessions) if a.sessions else Path.home() / ".claude" / "projects"
        self.rundir, self.work = run_dir(self.out, self.task, self.run_id), work_dir(self.out, self.task, self.run_id)
        self.branch = f"bench/{self.task}/factory-{self.run_id}"
        self.config_path = self.repo / "config" / "hline.json"
        self.timer = Timer()
        self.started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        self.run = {"task": self.task, "run_id": self.run_id, "base": None, "start": None, "head": None,
                    "started_at": self.started_at, "invalid": None, "reason": None, "stop": None, "plan_rounds": 0,
                    "plan_confirmed": False, "plan_frozen": bool(self.frozen), "planner_sha256": {}, "queue_sha256": {},
                    "replaced": 0, "tries": 0, "first_pass": None, "recovered": None,
                    "intake_revised": 0, "tokens": {}, "quota_wait_seconds": 0}
        self.revised, self.polls, self.before = set(), [], set()
        self.implementer_path = getattr(a, "implementer", None)
        self.impl = None

    # ---- 準備
    def load_implementer(self):
        if self.implementer_path:
            doc = json.loads(Path(self.implementer_path).read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise BenchError(f"--implementer の中身がオブジェクトではありません: {self.implementer_path}")
            self.impl = doc

    def effective(self, cfg):
        return {**cfg, "implementer": self.impl} if self.impl is not None else cfg

    def version_of(self, cli):
        _, out, err = proc.run(proc.resolve_cli(cli) + ["--version"], self.repo, 60, f"{cli} --version")
        return out.strip()

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
        cfg = self.effective(cfg)
        planner_cli, impl_cli = role_cli(cfg, "planner"), cfg["implementer"]["cli"]
        want = self.freeze_version()
        if want is not None:
            have = self.version_of(planner_cli)
            if want not in (have, have.split()[0] if have else ""):
                self.invalid("REQ-03", f"CLI の版が凍結の記録と違います: 記録 {want} ／ 実物 {have}")
                return False
        if impl_cli != planner_cli and (want := self.implementer_version(cfg, impl_cli)) is not None:
            have = self.version_of(impl_cli)
            if want not in (have, have.split()[0] if have else ""):
                self.invalid("REQ-03", f"実装役の CLI の版が設定と違います: 設定 {want} ／ 実物 {have}")
                return False
        return True

    def implementer_version(self, cfg, cli):
        """実装役の CLI の固定の版。設定の version、無ければ clone の台帳（config/toolchain.json の cli。
        台帳の無い古い base では、この版の台帳）。台帳にその CLI が無ければ None。"""
        if cfg["implementer"].get("version") is not None:
            return str(cfg["implementer"]["version"])
        path = self.repo / "config" / "toolchain.json"
        return (toolchain.load(path if path.exists() else toolchain.TOOLCHAIN).get("cli") or {}).get(cli)

    def freeze_version(self):
        try:
            doc = json.loads(git(self.repo, ["show", f"{self.run['base']}:bench/records/freeze.json"]).decode("utf-8"))
        except (BenchError, ValueError):
            return None
        return str(doc.get("cli_version")) if isinstance(doc, dict) and doc.get("cli_version") is not None else None

    # ---- 計画
    def check_plan(self):
        if self.frozen is not None and (missing := frozen_missing(self.frozen)):
            raise BenchError(f"固定の計画 {self.frozen} に {', '.join(missing)} がありません")

    def keep_plan(self, plan_inbox, spec):
        """計画のファイルと仕様を、走行のディレクトリの inputs/ に写す。"""
        inputs = self.rundir / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(spec, inputs / "spec.md")
        for p in plan_files(plan_inbox):
            shutil.copy2(p, inputs / p.name)

    def frozen_plan(self):
        spec = self.repo / "docs" / "bench" / self.task / "spec.md"
        plan_inbox = self.work / "plan"
        for d in (plan_inbox, self.work / "out", self.work / "wt", self.work / "inbox"):
            d.mkdir(parents=True, exist_ok=True)
        (self.rundir / "factory").mkdir(parents=True, exist_ok=True)
        for p in plan_files(self.frozen):
            shutil.copy2(p, plan_inbox / p.name)
        loop = read_json(self.frozen / "planner.json", {}).get("loop")
        self.run["plan_rounds"] = _int(loop.get("rounds")) if isinstance(loop, dict) else 0
        self.run["planner_sha256"] = {p.name: sha256_file(p) for p in plan_files(self.frozen) if p.name != "planner.json"}
        self.keep_plan(plan_inbox, spec)
        self.run["plan_confirmed"] = True
        return plan_inbox

    def plan(self, cfg):
        if self.frozen is not None:
            return self.frozen_plan()
        code, out, err, plan_inbox, spec = run_plan_loop(self.repo, self.run["base"], self.task, cfg, self.work, self.branch,
                                                         LIMIT_SECONDS - self.timer.elapsed())
        (self.rundir / "factory").mkdir(parents=True, exist_ok=True)
        (self.rundir / "factory" / "plan.txt").write_text(out + LOG_SPLIT + err, encoding="utf-8")
        doc = read_json(plan_inbox / "planner.json", {})
        loop = doc.get("loop") if isinstance(doc.get("loop"), dict) else {}
        self.run["plan_rounds"] = _int(loop.get("rounds"))
        self.run["planner_sha256"] = doc.get("files") if isinstance(doc.get("files"), dict) else {}
        self.keep_plan(plan_inbox, spec)
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

    def seed_reviews(self):
        """固定の計画の走行：受信箱の P-*.md のそれぞれに、取り込みの監修役の控え（pass）を置く。H ラインは同じ鍵でそれを読み、監修役を呼ばない。
        すでに控えがあれば上書きしない。指示・台帳を読めない・What が台帳のタスクを宣言しないときは置かず、H ラインに任せる（今どおり監修役が呼ばれる）。"""
        cfg, start = self.line_cfg, self.run["start"]
        rel = (cfg.get("reviewer") or {}).get("instruction")
        if not rel:
            return
        ttl = cfg["ttl_seconds"]["git"]
        code, instr, _ = proc.run(["git", "show", f"{start}:{rel}"], self.clone, ttl, "git show (reviewer instruction)")
        if code != 0:
            return

        def show_fn(rel_path):
            c, out, _ = proc.run(["git", "show", f"{start}:{rel_path}"], self.clone, ttl, f"git show ({rel_path})")
            return out if c == 0 else None

        try:
            ledger = hline_queue.progress.read_ref(show_fn)
            if not ledger or "tasks" not in ledger:
                return
            rows = {t["id"]: t for t in ledger["tasks"]}
        except Exception:   # noqa: BLE001
            return
        for p in sorted((self.work / "inbox").glob("P-*.md")):
            what = p.read_text(encoding="utf-8")
            row = rows.get(hline_queue.parse_header(what)["task"])
            if row is None:
                continue
            path = hline_review.store_dir(cfg) / f"{hline_review.key_of(instr, what, row)}.json"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                hline_base.write_json(path, {"verdict": "pass", "findings": [], "frozen_plan": self.run["planner_sha256"].get(p.name)})

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
        self.line_cfg = line_config(cfg, self.work, self.work / "inbox", start, self.branch, self.impl)
        self.line_path = self.work / "hline.json"
        hline_base.write_json(self.line_path, self.line_cfg)
        self.clone = clone
        if self.frozen is not None:
            self.seed_reviews()
        if self.impl is not None:
            (self.rundir / "factory").mkdir(parents=True, exist_ok=True)
            hline_base.write_json(self.rundir / "factory" / "config.json", hide_oracles(self.line_cfg))
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
    def cli_version(self, role="planner"):
        try:
            cfg = self.effective(self.cfg())
            cli = cfg["implementer"]["cli"] if role == "implementer" else role_cli(cfg, role)
            code, out, _ = proc.run(proc.resolve_cli(cli) + ["--version"], self.repo, 60, f"{cli} --version")
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
                "cli_version": self.cli_version(), "implementer_cli_version": self.cli_version("implementer"), "python": platform.python_version(), "mujoco": mujoco,
                "base": self.run["base"], "start": self.run["start"], "account": getpass.getuser(),
                "started_at": self.started_at, "finished_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "quota_wait_seconds": int(round(self.timer.waited(clock())))}

    def finalize(self):
        run, rundir = self.run, self.rundir
        st = hline_queue.load_state(self.line_cfg) if getattr(self, "line_cfg", None) else {"items": {}}
        items = st["items"]
        run["replaced"] = sum(1 for i in items.values() if i.get("replaced"))
        run["tries"] = sum(len(i.get("tries") or []) for i in items.values())
        run["first_pass"], run["recovered"] = pass_rates(items)
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


def version_problem(repo, rev):
    """走らせ役の harness/ の木と、clone の <rev> の harness/ の木が違う（または求められない）ときの理由。同じなら None。"""
    try:
        mine = git(ROOT, ["rev-parse", "HEAD:harness"]).decode().strip()
        theirs = git(repo, ["rev-parse", f"{rev}:harness"]).decode().strip()
    except BenchError as e:
        return f"harness/ の木の hash を求められません: {e}"
    if mine != theirs:
        return f"走らせ役の harness/ の木 {mine} と {rev} の harness/ の木 {theirs} が違います"
    return None


def factory(a):
    f = Factory(a)
    if (why := version_problem(f.repo, f.rev)):
        print(f"ABORT: {why}", file=sys.stderr)
        return 2
    try:
        f.check_head()
        f.check_plan()
        f.load_implementer()
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


def hide_oracles(value, oracles=None):
    """控え用の写し。非公開オラクルの場所を含む文字列は伏せる（走行に使う設定は変えない）。"""
    if oracles is None:
        oracles = value.get("oracles") if isinstance(value, dict) else None
        needles = {str(oracles), str(oracles).replace("\\", "/"), str(oracles).replace("/", "\\")} if oracles else set()
        needles = {n.rstrip("/\\") for n in needles if n.strip("/\\")}
        if not any(c in str(oracles) for c in "/\\"):
            return value   # 場所を表さない名前（区切りの無い値）は伏せない。部分一致で無関係な欄まで潰すため
        value = {k: v for k, v in value.items() if k != "oracles"} if isinstance(value, dict) else value
        return hide_oracles(value, needles)
    if isinstance(value, dict):
        return {k: hide_oracles(v, oracles) for k, v in value.items()}
    if isinstance(value, list):
        return [hide_oracles(v, oracles) for v in value]
    if isinstance(value, str):
        low = value.replace("\\", "/").lower()
        if any(n.replace("\\", "/").lower() in low for n in oracles):
            return "<hidden>"
    return value


def model_allowed(model, role_cfg):
    if role_cfg.get("cli") == "agy":
        return model == role_cfg.get("model_name")
    return model == role_cfg.get("model") or any(model.startswith(p) for p in role_cfg.get("auxiliary_models") or [])


def model_problem(rundir, cfg):
    used = read_json(rundir / "factory" / "config.json", {})
    allowed = cfg["implementer"]["model"]
    bad = set()
    for p in sorted((rundir / "calls").glob("*/result.json")):
        usage = read_json(p, {}).get("modelUsage")
        if not isinstance(usage, dict):
            continue
        if used:
            role = p.parent.name.split("-", 1)[-1]
            role_cfg = used.get(role) if role in ("planner", "reviewer") and isinstance(used.get(role), dict) else used.get("implementer", {})
            bad |= {m for m in usage if not model_allowed(m, role_cfg)}
        else:
            bad |= {m for m in usage if m != allowed and not m.startswith("claude-haiku-")}
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
        cfg = machine.read_json(repo / "config" / "hline.json")
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
        tail = ("stop", "plan_rounds", "plan_confirmed", "plan_frozen", "planner_sha256", "queue_sha256", "replaced", "tries",
                "first_pass", "recovered", "intake_revised", "tokens", "quota_wait_seconds")
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
    if (why := version_problem(Path(a.repo), a.base)):
        print(f"ABORT: {why}", file=sys.stderr)
        return 2
    try:
        tasks = stage_tasks(a.repo, a.base)
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    plans = Path(a.plans) if getattr(a, "plans", None) else None
    if plans is not None and (missing := [t for t in tasks if not (plans / t).is_dir()]):
        print(f"固定の計画がありません: {', '.join(missing)}（{plans}）", file=sys.stderr)
        return 1
    with KeepAwake():
        for task in tasks:
            done, waiting, last = stage_state(out, a.repo, task, a.tag)
            if done or waiting:
                continue
            streak = 0
            while True:
                last += 1
                run_id = f"{a.tag}-{last}"
                code = factory(argparse.Namespace(task=task, run_id=run_id, repo=a.repo, base=a.base, out=a.out, sessions=None,
                                                  plan=str(plans / task) if plans is not None else None,
                                                  implementer=getattr(a, "implementer", None)))
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
    sp.add_argument("--plans")
    sp.add_argument("--implementer")
    pp =sub.add_parser("plan")
    pp.add_argument("--task")
    pp.add_argument("--repo", required=True)
    pp.add_argument("--base", required=True)
    pp.add_argument("--out")
    pp.add_argument("--check", action="store_true")
    pp.add_argument("--all", action="store_true")
    for name in ("factory", "finish"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--repo", required=True)
        p.add_argument("--out")
    sub.choices["factory"].add_argument("--base", required=True)
    sub.choices["factory"].add_argument("--sessions")
    sub.choices["factory"].add_argument("--plan")
    sub.choices["factory"].add_argument("--implementer")
    sub.choices["finish"].add_argument("--oracles")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"factory": factory, "finish": finish, "stage": stage, "plan": plan}[a.cmd](a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
