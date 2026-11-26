"""Multi-step SWE（Harbor 形式）の実行口（Step B-1。WSL の root で走らせる）。

    python -m harness.ms_run run --task <名前> --out <置き場> [--cond <比較条件>] [--max-steps N] [--fake [--fake-oracle]]

Harbor の多段の流れを、共通の層（sandbox・agy_port）と制御核（core.Chain）でなぞる。ベンチのコードとデータは変えない。

- 走らせるのは中身を読んだ dev の扱いの課題だけ（config/ms_run.json の dev_tasks。イメージの ID を固定の値と照らす）
- 実装役のコンテナは 1 つで、段をまたいで残す（Harbor と同じ）。上限は task.toml の environment の cpus と memory_mb
  （mem_cap_mb を超える値は mem_cap_mb に切る）。境界は内部のネットワークと許可リストのプロキシ（モデルの API だけ）。
  実装役の uid は sandbox.uid（0 でなければ、段の workdir の重ねと setup.sh の後に /app をその uid に渡す）
- 段ごと（task.toml の steps の順）：steps/<段>/workdir/ を /app に重ね、setup.sh があれば root で走らせる（setup_net の
  課題だけ、その間だけ既定の bridge につなぎ、プロキシの環境を外す。課題自身の道具の取得で、実装役のターンではない。終わった
  後、内部のネットワークだけに戻ったことを確認する）→ 境界の検査 → 制御核の Chain で段（単位）を回す（比較条件 conditions：
  gate＝第 1 関門の押し返し・context＝段の間の文脈・stop_rules＝H_stop_1・rollback・halt_on_stop。段の時間の上限は task.toml の
  段の agent の timeout_sec で、押し返しと検査を含めて全比較条件で同じ）→ 実装役のプロセスを止める → /app を artifacts の
  exclude とハーネスのファイルを除いて tar にし、ホストで tarfile の data の filter で展開して（Harbor と同じ）、段の
  tests/Dockerfile から作った採点のイメージの新しいコンテナ（ネットワーク無し・verifier.environment の上限）の /app に写す →
  /tests/test.sh → /logs/verifier の reward.json と段の報告
- 第 1 関門（harness/ms_adapter.py の MsAdapter）は公式のテストを使わない（A にも見えるものだけ。設計 §5-1 の公平の決まり）。
  検査は config/ms_run.json の gate
- 止める線（実験単体）：limits の run_tokens（キャッシュでない入力＋出力のトークンの累計）と run_seconds（走りの壁時計）を
  段の前ごとに見る。利用枠は枯渇寸前の安全弁（config/agy_sandbox.json の quota_guard）だけを段の前ごとに見る
- 偽物の agy（--fake、利用枠 0）で通しを確認する。--fake-oracle は段の 1 回目のターンの後に段の公式の solve.sh を当て、多段の
  受け渡しと別の採点のコンテナで 1.0 になることを確認する
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_port  # noqa: E402
import agy_quota  # noqa: E402
import core  # noqa: E402
import exitcode  # noqa: E402
import model_pin  # noqa: E402
from ms_adapter import HARNESS_FILES, WORKDIR, MsAdapter, safe_extract  # noqa: E402
import phase1_dev  # noqa: E402
import sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "ms_run.json"
TASK_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,63}$")
NO_PROXY_ENV = ["env", "-u", "HTTPS_PROXY", "-u", "HTTP_PROXY", "-u", "https_proxy", "-u", "http_proxy",
                "-u", "NO_PROXY", "-u", "no_proxy"]
SOLUTION, VERIFIER = "/solution", "/logs/verifier"
COND_KEYS = {"gate", "context", "max_rounds", "stop_rules", "rollback", "halt_on_stop", "show_ledger", "protect"}
COND_DEFAULTS = {"gate": False, "context": "fresh", "max_rounds": 4, "stop_rules": {}, "rollback": False,
                 "halt_on_stop": False, "show_ledger": False, "protect": True}


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def load_config(path=CONFIG, common=agy_port.CONFIG):
    cfg = agy_port.load_config(common)
    cfg["ms"] = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg["sandbox"] = dict(cfg["sandbox"], **cfg["ms"]["sandbox"])
    return cfg


def require_dev(cfg, task):
    if task not in cfg["ms"]["dev_tasks"]:
        raise phase1_dev.NotDevError("中身を読んだ dev の扱いの課題（config/ms_run.json の dev_tasks）の外は走らせない"
                                     "（held-out の誤走の防止）")
    if not TASK_RE.match(task):
        raise ValueError(f"課題の名前の形が違う: {task!r}")
    return task


def condition(cfg, name):
    """比較条件の設定（config/ms_run.json の conditions）。知らないキーと context は止める。"""
    conds = cfg["ms"]["conditions"]
    if name.startswith("_") or name not in conds:
        raise ValueError(f"比較条件が無い: {name!r}（{sorted(k for k in conds if not k.startswith('_'))}）")
    c = {k: v for k, v in conds[name].items() if not k.startswith("_")}
    unknown = set(c) - COND_KEYS
    if unknown:
        raise ValueError(f"比較条件 {name} に知らないキー: {sorted(unknown)}")
    c = dict(COND_DEFAULTS, **c)
    if c["context"] not in core.CONTEXTS:
        raise ValueError(f"比較条件 {name} の context が違う: {c['context']!r}")
    return c


def gate_checks(cfg, task):
    """課題の第 1 関門の検査（config/ms_run.json の gate）→ core.Check の並び。知らない kind は止める。"""
    out = []
    for d in cfg["ms"]["gate"][task]:
        if d["kind"] not in MsAdapter.KINDS:
            raise ValueError(f"検査の kind が違う: {d['kind']!r}（{MsAdapter.KINDS}）")
        out.append(core.Check(d["id"], d["kind"], list(d.get("target") or []), bool(d.get("required", True))))
    return out


def step_unit(step, checks):
    """段 → 制御核の単位。台帳は段の指示書を 1 つの要件にする（上流の台帳づくり E-2 はここでは作らない）。"""
    req = core.Requirement("S", "instruction", f"{step['name']}: instruction.md",
                           [c.id for c in checks if c.required])
    return core.Unit(core.Ledger(step["name"], [req], [core.Check(**asdict(c)) for c in checks]), step["instruction"])


def image_tag(task, suffix):
    return "msscan-" + re.sub(r"[^a-z0-9.]", "-", task)[:48] + ":" + suffix


def capped(mb, cap):
    return min(int(mb), int(cap))


def limits(cpus, memory_mb, cap):
    m = capped(memory_mb, cap)
    return ["--cpus", str(cpus), "--memory", f"{m}m", "--memory-swap", f"{m}m"]


def read_task(cfg, task):
    """task.toml → 上限・artifacts の exclude・段の並び（名前・instruction・時間の上限・ファイルの SHA-256）。"""
    d = Path(cfg["ms"]["data"]) / task
    meta = tomllib.loads((d / "task.toml").read_text(encoding="utf-8"))
    env = meta["environment"]
    venv = (meta.get("verifier") or {}).get("environment") or env
    exclude = []
    for a in meta.get("artifacts", []):
        if isinstance(a, dict) and a.get("source") == WORKDIR:
            exclude += list(a.get("exclude") or [])
    steps = []
    for s in meta["steps"]:
        sd = d / "steps" / s["name"]
        files = ["instruction.md"] + sorted(str(p.relative_to(sd)).replace(os.sep, "/")
                                            for p in (sd / "tests").rglob("*") if p.is_file())
        steps.append({"name": s["name"], "dir": str(sd),
                      "agent_timeout_s": int((s.get("agent") or {}).get("timeout_sec")
                                             or (meta.get("agent") or {}).get("timeout_sec") or 3600),
                      "verifier_timeout_s": int((s.get("verifier") or {}).get("timeout_sec")
                                                or (meta.get("verifier") or {}).get("timeout_sec") or 1800),
                      "instruction": (sd / "instruction.md").read_text(encoding="utf-8"),
                      "setup": (sd / "workdir" / "setup.sh").is_file(),
                      "sha256": {f: hashlib.sha256((sd / f).read_bytes()).hexdigest() for f in files}})
    return {"dir": str(d), "cpus": env["cpus"], "memory_mb": env["memory_mb"], "v_cpus": venv["cpus"],
            "v_memory_mb": venv["memory_mb"], "exclude": exclude, "steps": steps,
            "strategy": meta.get("multi_step_reward_strategy")}


def tar_excludes(exclude):
    return [f"--exclude={p}" for p in list(exclude) + list(HARNESS_FILES)]


def weekly(q):
    try:
        return q["buckets"]["gemini-weekly"]["remaining_fraction"]
    except (TypeError, KeyError):
        return None


def used_pt(before, after):
    """週の残りの差 × 100（ポイント。記録だけで、止める線には使わない）。読めなければ None。"""
    w0, w1 = weekly(before), weekly(after)
    return None if w0 is None or w1 is None else round((w0 - w1) * 100, 3)


def run_tokens(records):
    """実行口のターンの記録の、キャッシュでない入力と出力（思考を含む）のトークンの累計。"""
    n = 0
    for r in records:
        u = r.get("usage") or {}
        n += sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens") if isinstance(u.get(k), int))
    return n


def run_limit(lim, tokens, seconds):
    """実験単体の上限（limits の run_tokens・run_seconds）に当たったか。(止めるか, 理由)。"""
    if lim.get("run_tokens") and tokens >= lim["run_tokens"]:
        return True, f"走りのトークン {tokens} ≥ 上限 {lim['run_tokens']}"
    if lim.get("run_seconds") and seconds >= lim["run_seconds"]:
        return True, f"走りの壁時計 {round(seconds)} 秒 ≥ 上限 {lim['run_seconds']} 秒"
    return False, None


def groups(report):
    """段の報告（stepNN-report.json）→ 群ごとの要約 {群: {部: {passed, total} か reward}}。形が違えば None。"""
    fam = (report or {}).get("families")
    if not isinstance(fam, dict):
        return None
    out = {}
    for g, parts in fam.items():
        out[g] = {}
        for k, v in (parts.items() if isinstance(parts, dict) else []):
            det = v.get("detail") if isinstance(v, dict) else None
            if isinstance(det, dict) and "passed" in det and "total" in det:
                out[g][k] = {"passed": det["passed"], "total": det["total"]}
            elif isinstance(v, dict) and "score" in v:
                out[g][k] = {"score": v["score"]}
    return out


def mib(n):
    return None if n is None else round(n / (1024 * 1024))


class OraclePort:
    """--fake-oracle の実行口の包み：単位の 1 回目のターンの後に、段の公式の solve.sh を 1 回だけ当てる（制御核から見ると、
    1 回目のターンで正解を書いたエージェント）。"""

    def __init__(self, inner, box, sbx, out_dir, as_agent=False):
        self.inner, self.box, self.sbx, self.out_dir, self.as_agent = inner, box, sbx, out_dir, as_agent
        self.step, self.k, self.done, self.applied = None, None, True, {}

    def arm(self, step, k):
        self.step, self.k, self.done = step, k, False

    def start(self, text, budget_s):
        return self._after(self.inner.start(text, budget_s))

    def send(self, text, budget_s):
        return self._after(self.inner.send(text, budget_s))

    def _after(self, turn):
        if not self.done:
            self.done = True
            self.applied[self.k] = apply_oracle(self.box, self.step, self.out_dir, self.k,
                                                self.sbx if self.as_agent else None)
            give_app(self.box, self.sbx)
        return turn


# ---------------------------------------------------------------- コンテナの中の操作

def check_image(ref, want):
    got = sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", ref], "image inspect").strip()
    if got != want:
        raise RuntimeError(f"課題のイメージの ID が固定の値と違う: {got}")
    return got


def give_app(box, sbx):
    """実装役が root でなければ /app をその uid に渡す（段の workdir の重ね・setup.sh・正解は root で書くため）。"""
    if int(sbx["uid"]) != 0:
        sandbox.must_root(box, f"chown -R {sbx['uid']}:{sbx['uid']} {WORKDIR}", "app chown", timeout=600)


def root_paths(env):
    """イメージの環境の変数（"K=V" の並び）のうち、値が /root の下の 1 つのパスのもの → {K: パス}（PATH のような並びは除く）。"""
    out = {}
    for kv in env or []:
        k, _, v = kv.partition("=")
        if v.startswith("/root/") and ":" not in v and " " not in v:
            out[k] = v.rstrip("/")
    return out


def give_root_paths(box, sbx, image):
    """実装役が root でなければ、課題のイメージの環境の変数が指す /root の下の置き場（例：Go の GOCACHE=/root/.cache/go-build）を
    作ってその uid に渡し、/root からそこまでのディレクトリを通れるようにする（o+x。中身の一覧は見せない）。課題のイメージは
    root を前提に作られているため（2026-11-26 の ooxml で実測）。渡したものを返す。"""
    if int(sbx["uid"]) == 0:
        return {}
    env = json.loads(sandbox.must(["docker", "image", "inspect", "-f", "{{json .Config.Env}}", image], "image env") or "[]")
    paths = root_paths(env)
    for p in sorted(set(paths.values())):
        parts = p.split("/")[1:]
        walk = ["/" + "/".join(parts[:i]) for i in range(1, len(parts))]   # /root, /root/.cache, ...（p の手前まで）
        sandbox.must_root(box, f"mkdir -p '{p}' && chown -R {sbx['uid']}:{sbx['uid']} '{p}' && "
                               + " && ".join(f"chmod o+x '{d}'" for d in walk), "root path handover")
    return paths


def overlay_and_setup(box, step, net, setup_net, out_dir, k, sbx):
    """段の workdir を /app に重ね、setup.sh を root で走らせる（setup_net なら、その間だけ bridge につなぐ）。"""
    wd = Path(step["dir"]) / "workdir"
    rec = {}
    if wd.is_dir():
        sandbox.must(["docker", "cp", f"{wd}/.", f"{box}:{WORKDIR}/"], "workdir copy")
    if step["setup"]:
        if setup_net:
            sandbox.must(["docker", "network", "connect", "bridge", box], "setup network connect")
        t0 = time.time()
        try:
            code, out = sandbox.sh(["docker", "exec", "--user", "0:0", "-w", WORKDIR, box] + NO_PROXY_ENV +
                                   ["bash", "setup.sh"], timeout=3600)
        finally:
            if setup_net:
                sandbox.sh(["docker", "network", "disconnect", "bridge", box])
        (Path(out_dir) / f"s{k}_setup.log").write_text(out, encoding="utf-8")
        rec = {"rc": code, "seconds": round(time.time() - t0, 1), "network": "bridge" if setup_net else "none"}
    give_app(box, sbx)
    nets = sandbox.must(["docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", box],
                        "inspect networks").split()
    if nets != [net["internal"]]:
        raise RuntimeError(f"setup の後のネットワークが内部のネットワークだけでない: {nets}")
    return rec


def apply_oracle(box, step, out_dir, k, sbx=None):
    """段の公式の solve.sh を当てる。sbx を渡すと実装役の uid と HOME で走らせる（実装役が非 root で段をこなせるかの測り）。"""
    sandbox.must_root(box, f"rm -rf {SOLUTION}", "solution clean")
    sandbox.copy_in(box, Path(step["dir"]) / "solution", SOLUTION, "solution copy")
    user = ["--user", "0:0"]
    if sbx is not None:
        sandbox.must_root(box, f"chmod -R a+rX {SOLUTION}", "solution chmod")
        user = ["--user", f"{sbx['uid']}:{sbx['uid']}", "-e", f"HOME={sbx['home']}"]
    code, out = sandbox.sh(["docker", "exec"] + user + ["-w", WORKDIR, box] + NO_PROXY_ENV +
                           ["bash", f"{SOLUTION}/solve.sh"], timeout=3600)
    (Path(out_dir) / f"s{k}_oracle_solve.log").write_text(out, encoding="utf-8")
    sandbox.as_root(box, f"rm -rf {SOLUTION}", timeout=60)
    return {"rc": code, "user": "agent" if sbx is not None else "root"}


def artifact(box, t, out_dir, k):
    """/app の tar（exclude とハーネスのファイルを除く）を取り出し、ホストで data の filter で展開する。"""
    ex = " ".join(f"'{e}'" for e in tar_excludes(t["exclude"]))
    code, out = sandbox.as_root(box, f"cd {WORKDIR} && tar -cf /tmp/ms_art.tar {ex} .", timeout=1800)
    tar_path = Path(out_dir) / f"s{k}_art.tar"
    sandbox.must(["docker", "cp", f"{box}:/tmp/ms_art.tar", str(tar_path)], "artifact copy")
    sandbox.as_root(box, "rm -f /tmp/ms_art.tar", timeout=60)
    dest = Path(tempfile.mkdtemp(prefix=f"ms_art{k}_", dir=out_dir))
    n = safe_extract(tar_path.read_bytes(), dest)
    return dest, {"tar_rc": code, "bytes": tar_path.stat().st_size, "members": n}


def verify(t, step, k, art_dir, tag, out_dir, cap):
    """段の tests/Dockerfile から採点のイメージを作り、新しいコンテナ（ネットワーク無し）で test.sh。"""
    img = image_tag(tag["task"], f"step{k}")
    t0 = time.time()
    sandbox.must(["docker", "build", "-q", "-t", img, str(Path(step["dir"]) / "tests")], "verifier build", timeout=3600)
    rec = {"image": img, "image_id": sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", img], "inspect").strip(),
           "build_s": round(time.time() - t0, 1)}
    v = f"{tag['prefix']}-v{k}"
    sandbox.remove(v)
    sandbox.must(["docker", "run", "-d", "--name", v, "--network", "none", "-w", WORKDIR] +
                 limits(t["v_cpus"], t["v_memory_mb"], cap) + [img, "sleep", "infinity"], "verifier run")
    try:
        sandbox.must_root(v, f"mkdir -p {WORKDIR} {VERIFIER}", "verifier dirs")
        sandbox.must(["docker", "cp", f"{art_dir}/.", f"{v}:{WORKDIR}/"], "artifact restore")
        t1, timed_out = time.time(), False
        try:
            code, out = sandbox.sh(["docker", "exec", "--user", "0:0", "-w", WORKDIR, v, "bash", "/tests/test.sh"],
                                   timeout=step["verifier_timeout_s"])
        except subprocess.TimeoutExpired as e:
            code, out, timed_out = agy_port.HARD_TIMEOUT_RC, agy_port.timeout_output(e), True
        (Path(out_dir) / f"s{k}_test.log").write_text(out, encoding="utf-8")
        vdir = Path(out_dir) / f"s{k}_verifier"
        sandbox.sh(["docker", "cp", f"{v}:{VERIFIER}", str(vdir)])
        reward, report = None, None
        try:
            reward = json.loads((vdir / "reward.json").read_text(encoding="utf-8")).get("reward")
        except (OSError, ValueError, AttributeError):
            pass
        for p in sorted(vdir.glob("step*-report.json")) if vdir.is_dir() else []:
            try:
                report = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
        rec.update(rc=code, seconds=round(time.time() - t1, 1), timeout=timed_out, reward=reward,
                   score=(report or {}).get("reward"), groups=groups(report),
                   peak_mib=mib(sandbox.memory_peak(v)), oom_kills=sandbox.oom_kills(v))
    finally:
        sandbox.remove(v)
    return rec


# ---------------------------------------------------------------- 1 つの課題を回す

def write_record(out_dir, rec):
    (Path(out_dir) / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def unit_digest(urec):
    """単位の記録の要約（record.json に入れる。全文は unit_s<k>.json）。"""
    return {"decision": urec["decision"], "stop": urec["stop"], "stop_rule": urec.get("stop_rule"),
            "rounds": len(urec["rounds"]), "verdicts": [r["verdict"] for r in urec["rounds"]],
            "seconds": urec.get("seconds"), "chain": urec.get("chain"), "resume": urec.get("resume"),
            "usage": core.total_usage(urec)}


def run_task(cfg, task, out_dir, *, cond="H0_continue", max_steps=None, fake=False, fake_oracle=False, token_file=None,
             oracle_as_agent=False):
    require_dev(cfg, task)
    c = condition(cfg, cond)
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise RuntimeError(f"置き場がすでにある: {out_dir}")
    out_dir.mkdir(parents=True)
    sbx, net, ms = cfg["sandbox"], cfg["network"], cfg["ms"]
    cap = int(ms["mem_cap_mb"])
    t = read_task(cfg, task)
    steps = t["steps"][:max_steps] if max_steps else t["steps"]
    setup_net = task in ms.get("setup_net", [])
    checks = gate_checks(cfg, task) if c["gate"] else []
    tag = {"task": task, "prefix": f"ms-{re.sub(r'[^a-z0-9]', '-', task)[:24]}-{os.getpid()}"}
    rec = {"task": task, "cond": cond, "condition": c, "fake": fake, "fake_oracle": fake_oracle,
           "oracle_as_agent": oracle_as_agent, "model": cfg["agent"]["model_name"],
           "gate_checks": [asdict(x) for x in checks],
           "limits": {"cpus": t["cpus"], "memory_mb": capped(t["memory_mb"], cap), "memory_mb_task": t["memory_mb"],
                      "v_cpus": t["v_cpus"], "v_memory_mb": capped(t["v_memory_mb"], cap), "mem_cap_mb": cap},
           "run_limits": {k: v for k, v in ms["limits"].items() if not k.startswith("_")},
           "user": sbx["uid"], "home": sbx["home"], "workdir": sbx["workdir"], "allow": net["allow"],
           "setup_net": setup_net, "exclude": t["exclude"], "steps_total": len(t["steps"]), "steps_run": [],
           "strategy": t["strategy"]}
    box, t_run = None, time.time()
    try:
        image = image_tag(task, "env")
        rec["image_id"] = check_image(image, ms["dev_tasks"][task])
        rec["egress"] = sandbox.ensure_egress(net)
        box = sandbox.start(net, sbx, image, f"{tag['prefix']}-agent", extra=limits(t["cpus"], t["memory_mb"], cap))
        rec["root_paths"] = give_root_paths(box, sbx, image)
        agy = agy_port.AgyPort(cfg, box, lambda n: out_dir / f"stream_t{n}.jsonl", label=task, fake=fake)
        rec["hooks_sha256"] = agy.install(token_file)
        port = OraclePort(agy, box, sbx, out_dir, as_agent=oracle_as_agent) if fake_oracle else agy
        adapter = MsAdapter(box, sbx, out_dir, pyflakes_cmd=ms.get("pyflakes"),
                            check_timeout_s=ms.get("check_timeout_s", 300), keep_checkpoints=c["rollback"])
        chain = core.Chain(port, adapter, expected_model=cfg["agent"]["model_name"], context=c["context"],
                           gate=c["gate"], show_ledger=c["show_ledger"], max_rounds=c["max_rounds"],
                           stop_rules=c["stop_rules"], rollback=c["rollback"], halt_on_stop=c["halt_on_stop"],
                           protect=c["protect"])
        for k, step in enumerate(steps, 1):
            s = {"k": k, "name": step["name"], "files_sha256": step["sha256"],
                 "prompt_sha256": hashlib.sha256(step["instruction"].encode("utf-8")).hexdigest()}
            rec["steps_run"].append(s)
            stop, why = run_limit(ms["limits"], run_tokens(agy.records), time.time() - t_run)
            if stop:
                rec["stopped"] = why
                break
            if not fake:
                s["quota_before"], ok, why = agy_port.quota_check(cfg)
                if not ok:
                    rec["stopped"] = why
                    break
            s["setup"] = overlay_and_setup(box, step, net, setup_net, out_dir, k, sbx)
            s["network"] = sandbox.net_check(box, sbx)
            if not s["network"]["ok"]:
                raise RuntimeError(f"境界の検査に落ちた: {s['network']}")
            unit = step_unit(step, checks)
            adapter.unit = step["name"]
            if c["gate"]:
                adapter.begin_unit(step["name"], unit.ledger.checks)
            if fake_oracle:
                port.arm(step, k)
            n0 = len(agy.records)
            s["agent_start"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            urec = chain.run(unit, step["agent_timeout_s"])
            (out_dir / f"unit_s{k}.json").write_text(core.dumps(urec), encoding="utf-8")
            turns = agy.records[n0:]
            s["unit"] = unit_digest(urec)
            s["turns"] = [{kk: v for kk, v in tr.items() if kk != "turn"} for tr in turns]
            s["valid"] = bool(turns) and all(tr["valid"] for tr in turns)
            s["tokens"] = run_tokens(turns)
            s["left_procs_after_agent"] = sandbox.stop_user(box, sbx)
            s["memory"] = {"after_agent_peak_mib": mib(sandbox.memory_peak(box)), "oom_kills": sandbox.oom_kills(box)}
            bad = [tr for tr in turns if tr.get("model_error")]
            if bad:
                rec["stopped"] = "モデルの照合に落ちた（採点しない）"
                raise model_pin.ModelPinError(bad[0]["model_error"])
            if fake_oracle:
                s["oracle"] = port.applied.get(k)
            if not fake:
                s["quota_after"] = agy_quota.read()
                s["used_pt"] = used_pt(s["quota_before"], s["quota_after"])
            art_dir, s["artifact"] = artifact(box, t, out_dir, k)
            try:
                s["eval"] = verify(t, step, k, art_dir, tag, out_dir, cap)
            finally:
                shutil.rmtree(art_dir, ignore_errors=True)
            write_record(out_dir, rec)
            if chain.halted is not None:
                rec["stopped"] = "合格せずに止まった単位で列を止めた（halt_on_stop。人の裁定を待つ）"
                break
        rec["adapter_notes"] = adapter.notes
        rec["run_tokens"] = run_tokens(agy.records)
        rec["run_seconds"] = round(time.time() - t_run, 1)
        rec["memory"] = {"agent_peak_mib": mib(sandbox.memory_peak(box)), "agent_oom_kills": sandbox.oom_kills(box)}
    finally:
        if box:
            sandbox.remove(box)
        rec["boxes_left"] = sandbox.boxes_left(tag["prefix"])
        write_record(out_dir, rec)
    return rec


# ---------------------------------------------------------------- 入口

def cmd_run(args):
    cfg = load_config()
    if args.fake_oracle and not args.fake:
        raise RuntimeError("--fake-oracle は偽物の走り（--fake）の確認だけで使う")
    if args.oracle_as_agent and not args.fake_oracle:
        raise RuntimeError("--oracle-as-agent は --fake-oracle と一緒に使う")
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_task(cfg, args.task, args.out, cond=args.cond, max_steps=args.max_steps, fake=args.fake,
                   fake_oracle=args.fake_oracle, token_file=token, oracle_as_agent=args.oracle_as_agent)
    for s in rec["steps_run"]:
        u, ev = s.get("unit") or {}, s.get("eval") or {}
        print(f"step {s['k']} {s['name']} reward={ev.get('reward')} score={ev.get('score')} decision={u.get('decision')} "
              f"stop={u.get('stop')}/{u.get('stop_rule')} rounds={u.get('rounds')} verdicts={u.get('verdicts')} "
              f"valid={s.get('valid')} tokens={s.get('tokens')} used_pt={s.get('used_pt')} v_peak={ev.get('peak_mib')} "
              f"oom={ev.get('oom_kills')}")
    print(f"cond={rec['cond']} user={rec['user']} stopped={rec.get('stopped')} boxes_left={rec['boxes_left']} "
          f"run_tokens={rec.get('run_tokens')} run_seconds={rec.get('run_seconds')} memory={rec.get('memory')}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multi-step SWE の実行口（Step B-1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="1 つの課題を段の順に回す")
    r.add_argument("--task", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--cond", default="H0_continue",
                   help="比較条件（config/ms_run.json の conditions。既定は S3a のベースライン）")
    r.add_argument("--max-steps", type=int, default=None)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--fake-oracle", action="store_true", help="段の 1 回目の偽物のターンの後に段の公式の solve.sh を当てる")
    r.add_argument("--oracle-as-agent", action="store_true",
                   help="--fake-oracle の solve.sh を実装役の uid で走らせる（非 root で段をこなせるかの測り）")
    r.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
