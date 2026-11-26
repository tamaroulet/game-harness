"""RoadmapBench の薄い接続（Gate 1。操縦士の指示、2026-11-25。WSL の root で走らせる）。ベンチ固有の層。

    python -m harness.rb_run check --task <id>                                境界と環境の検査（利用枠 0）
    python -m harness.rb_run run --task <id> --out <置き場> [--fake [--fake-oracle]]

1 つの課題を、公式の組み上げ済みのイメージ（Docker Hub の znpt/roadmapbench-*。ID を固定の値と照らす）で起こし、
実装役（agy）に公式の instruction.md をそのまま 1 回渡し（H0）、公式の推奨の走らせ方（Harbor）と同じく、同じ
コンテナに /tests を入れて公式の test.sh で採点する。ベンチのコードとデータは変えない。境界・agy・走りの後の検査は
共通の層（harness/sandbox.py・harness/agy_port.py）が受け持つ。

- 走らせるのは中身を読んだ dev の扱いの課題だけ（config/rb_run.json の dev_tasks。外なら何も作らずに止まる）
- 実装役：Harbor の公式（task.toml に [agent] の user が無い）と同じく、イメージの既定のユーザー（root）と HOME（/root）で、
  作業場はイメージの WORKDIR（/app）。CPU とメモリの上限は task.toml の値（Harbor もこの値でコンテナを縛り、エージェントも
  同じコンテナで動く。スワップは足さない）。時間の上限は task.toml の agent.timeout_sec
- ネットワーク：内部のネットワークと許可リストのプロキシ（モデルの API だけ。パッケージのレジストリは塞ぐ）
- 採点：Harbor と同じく、時間切れでも 0 でない終わりの符号でも採点する（Harbor 0.25.0 の trial/single_step.py は
  AgentTimeoutError と NonZeroAgentExitCodeError を記録して採点に進む）。実装役のプロセスを止め、コンテナをネットワーク
  から外し（Stage 2 のオフラインの 0b と同じ条件）、/tests を写して test.sh を root で走らせ（上限は task.toml の
  verifier.timeout_sec）、/logs/verifier/reward.txt と reward.json を読む
- 記録：目標（Phase）ごとの合否・実装役の差分（始まりの HEAD からの差分と、追跡していない新しいファイル）・コンテナの
  メモリの最大（cgroup の memory.peak。実装役の後と採点の後）と OOM の数・stream-json の全文・前後の利用枠
- 偽物の agy（--fake、利用枠 0）で通しを確認する。--fake-oracle は、偽物のターンの後に公式の solve.sh をネットワークの
  無い状態で当て、正解が同じ経路で 1.0 になることを確認する
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_port  # noqa: E402
import agy_quota  # noqa: E402
import exitcode  # noqa: E402
import model_pin  # noqa: E402
import phase1_dev  # noqa: E402
import sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "rb_run.json"
TASK_RE = re.compile(r"^[a-z]{3}-[0-9][0-9.]*-roadmap$")   # 課題の ID（シェルとコンテナの名前に渡すので形を確認する）
PHASE_RE = re.compile(r"^=== Phase (\d+): (.*?) ===$")      # 公式の test.sh の目標の見出し
TESTS, SOLUTION, VERIFIER = "/tests", "/solution", "/logs/verifier"
NO_PROXY_ENV = ["env", "-u", "HTTPS_PROXY", "-u", "HTTP_PROXY", "-u", "https_proxy", "-u", "http_proxy",
                "-u", "NO_PROXY", "-u", "no_proxy"]
UNTRACKED_TAR_LIMIT = 50 * 1024 * 1024
PROBE = ("for t in python3 pip node yarn npx git; do printf '%s=%s\\n' $t \"$(command -v $t || echo -)\"; done; "
         "printf 'whoami=%s\\nhome=%s\\npwd=%s\\n' \"$(whoami)\" \"$HOME\" \"$(pwd)\"; "
         "printf 'python=%s\\n' \"$(python3 --version 2>&1)\"; "
         "if command -v yarn >/dev/null; then printf 'yarn_version=%s\\n' \"$(timeout 60 yarn --version 2>&1 | tail -1)\"; fi")


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def load_config(path=CONFIG, common=agy_port.CONFIG):
    """共通の設定に、ベンチ固有の設定を rb として足す。実装役の uid・HOME・作業場は rb の sandbox で置き換える。"""
    cfg = agy_port.load_config(common)
    cfg["rb"] = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg["sandbox"] = dict(cfg["sandbox"], **cfg["rb"]["sandbox"])
    return cfg


def require_dev(cfg, task):
    """中身を読んだ dev の扱いの課題か。外なら NotDevError（渡された名前を文に入れない）。"""
    if task not in cfg["rb"]["dev_tasks"]:
        raise phase1_dev.NotDevError("中身を読んだ dev の扱いの課題（config/rb_run.json の dev_tasks）の外は走らせない"
                                     "（held-out の誤走の防止）")
    if not TASK_RE.match(task):
        raise ValueError(f"課題の ID の形が違う: {task!r}")
    return task


def read_task(cfg, task):
    """課題の置き場の task.toml・instruction.md・tests を読む。上限・イメージ・文面と、ファイルの SHA-256。"""
    d = Path(cfg["rb"]["data"]) / task
    meta = tomllib.loads((d / "task.toml").read_text(encoding="utf-8"))
    env = meta["environment"]
    files = ["instruction.md", "task.toml"] + sorted("tests/" + p.name for p in (d / "tests").iterdir() if p.is_file())
    return {"dir": str(d), "image": env["docker_image"], "cpus": env["cpus"], "memory_mb": env["memory_mb"],
            "agent_timeout_s": int(meta["agent"]["timeout_sec"]), "verifier_timeout_s": int(meta["verifier"]["timeout_sec"]),
            "instruction": (d / "instruction.md").read_text(encoding="utf-8"),
            "sha256": {f: hashlib.sha256((d / f).read_bytes()).hexdigest() for f in files}}


def limits(t):
    """コンテナの上限（task.toml の cpus と memory_mb。スワップは足さない）。"""
    return ["--cpus", str(t["cpus"]), "--memory", f"{t['memory_mb']}m", "--memory-swap", f"{t['memory_mb']}m"]


def phases(log_text):
    """公式の test.sh の出力 → [{"n", "name", "passed"}]。見出し「=== Phase n: 名前 ===」の後で、最初に「PASSED」か
    「FAILED」だけの行（test.sh の echo）が目標の合否。テストの行（pytest の「… PASSED [ 1%]」など）は数えない。"""
    out, cur = [], None
    for line in (log_text or "").splitlines():
        s = line.strip()
        m = PHASE_RE.match(s)
        if m:
            cur = {"n": int(m.group(1)), "name": m.group(2), "passed": None}
            out.append(cur)
        elif cur is not None and cur["passed"] is None and s in ("PASSED", "FAILED"):
            cur["passed"] = s == "PASSED"
    return out


def reward_of(text):
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def mib(n):
    return None if n is None else round(n / (1024 * 1024))


# ---------------------------------------------------------------- コンテナの中の操作

def check_image(ref, want):
    got = sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", ref], "image inspect").strip()
    if got != want:
        raise RuntimeError(f"課題のイメージの ID が固定の値と違う: {got}")
    return got


def git(box, args, timeout=300):
    """作業場の git を root で（safe.directory はこの呼び出しにだけ渡す）。(終わりの符号, 出力)。"""
    return sandbox.sh(["docker", "exec", "--user", "0:0", "-w", "/app", box, "git", "-c", "safe.directory=*"] + args,
                      timeout=timeout)


def save_changes(box, base, out_dir):
    """実装役の変更を残す：始まりの HEAD（base）からの差分（agent.diff）と、追跡していない新しいファイルの一覧と写し
    （無視の規則に当たるものは除く。写しは 50 MiB まで）。要約を返す。"""
    out_dir = Path(out_dir)
    code, diff = git(box, ["diff", "--binary", base])
    (out_dir / "agent.diff").write_text(diff, encoding="utf-8")
    code2, names = git(box, ["ls-files", "--others", "--exclude-standard"])
    new = [n for n in names.splitlines() if n.strip()]
    (out_dir / "agent_untracked.txt").write_text("\n".join(new) + ("\n" if new else ""), encoding="utf-8")
    code3, files = git(box, ["diff", "--name-only", base])
    head = git(box, ["rev-parse", "HEAD"])[1].strip()
    tar = None
    if new:
        size = sandbox.as_root(box, "cd /app && git -c safe.directory='*' ls-files -z --others --exclude-standard | "
                                    "du -cb --files0-from=- | tail -1 | cut -f1", timeout=300)[1].strip()
        if size.isdigit() and int(size) <= UNTRACKED_TAR_LIMIT:
            sandbox.as_root(box, "cd /app && git -c safe.directory='*' ls-files -z --others --exclude-standard | "
                                 "tar --null -T - -cf /tmp/agent_untracked.tar", timeout=300)
            sandbox.must(["docker", "cp", f"{box}:/tmp/agent_untracked.tar", str(out_dir / "agent_untracked.tar")],
                         "untracked copy")
            sandbox.as_root(box, "rm -f /tmp/agent_untracked.tar", timeout=60)
            tar = int(size)
    return {"base": base, "head_after": head, "diff_rc": code, "diff_bytes": len(diff.encode("utf-8")),
            "changed_files": [f for f in files.splitlines() if f.strip()] if code3 == 0 else None,
            "untracked": len(new), "untracked_tar_bytes": tar}


def apply_oracle(box, t, out_dir):
    """偽物の走りの確認だけ：公式の solve.sh を当てる（ネットワークの無い状態。Stage 2 のオフラインの 0b と同じ）。"""
    sandbox.must_root(box, f"rm -rf {SOLUTION}", "solution clean")
    sandbox.copy_in(box, Path(t["dir"]) / "solution", SOLUTION, "solution copy")
    code, out = sandbox.sh(["docker", "exec", "--user", "0:0", box] + NO_PROXY_ENV + ["bash", f"{SOLUTION}/solve.sh"],
                           timeout=3600)
    (Path(out_dir) / "oracle_solve.log").write_text(out, encoding="utf-8")
    sandbox.as_root(box, f"rm -rf {SOLUTION}", timeout=60)
    return {"rc": code}


def verify(box, t, out_dir):
    """公式の test.sh（root。プロキシの環境は外す。ネットワークは外してある）。目標ごとの合否と報酬。"""
    out_dir = Path(out_dir)
    sandbox.must_root(box, f"rm -rf {TESTS} && mkdir -p {VERIFIER}", "verifier dirs")
    sandbox.copy_in(box, Path(t["dir"]) / "tests", TESTS, "tests copy")
    t0, timed_out = time.time(), False
    try:
        code, out = sandbox.sh(["docker", "exec", "--user", "0:0", "-w", "/app", box] + NO_PROXY_ENV +
                               ["bash", f"{TESTS}/test.sh"], timeout=t["verifier_timeout_s"])
    except subprocess.TimeoutExpired as e:
        code, out, timed_out = agy_port.HARD_TIMEOUT_RC, agy_port.timeout_output(e), True
        sandbox.as_root(box, "kill -9 -1", timeout=60)
    (out_dir / "test.log").write_text(out, encoding="utf-8")
    reward = sandbox.as_root(box, f"cat {VERIFIER}/reward.txt", timeout=60)
    detail = sandbox.as_root(box, f"cat {VERIFIER}/reward.json", timeout=60)
    try:
        detail_json = json.loads(detail[1]) if detail[0] == 0 else None
    except ValueError:
        detail_json = None
    return {"rc": code, "seconds": round(time.time() - t0, 1), "timeout": timed_out,
            "reward": reward_of(reward[1]) if reward[0] == 0 else None, "reward_json": detail_json,
            "phases": phases(out)}


# ---------------------------------------------------------------- 1 つの課題を回す

def write_record(out_dir, rec):
    (Path(out_dir) / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def run_task(cfg, task, out_dir, fake=False, fake_oracle=False, token_file=None):
    require_dev(cfg, task)   # dev の扱いの外は、何も作らずに止まる
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise RuntimeError(f"置き場がすでにある: {out_dir}")
    out_dir.mkdir(parents=True)
    sbx, net = cfg["sandbox"], cfg["network"]
    t = read_task(cfg, task)
    tag = f"rb-{task.replace('.', '-')}-{os.getpid()}"
    rec = {"task": task, "cond": "H0", "fake": fake, "fake_oracle": fake_oracle, "model": cfg["agent"]["model_name"],
           "budget_s": t["agent_timeout_s"], "limits": {"cpus": t["cpus"], "memory_mb": t["memory_mb"]},
           "user": sbx["uid"], "home": sbx["home"], "workdir": sbx["workdir"], "files_sha256": t["sha256"],
           "allow": net["allow"]}
    box = None
    try:
        rec["image_id"] = check_image(t["image"], cfg["rb"]["dev_tasks"][task])
        if not fake:
            rec["quota_before"], ok, why = agy_port.quota_check(cfg)
            if not ok:
                rec["stopped"] = why
                return rec
        rec["egress"] = sandbox.ensure_egress(net)
        box = sandbox.start(net, sbx, t["image"], f"{tag}-agent", extra=limits(t))
        port = agy_port.AgyPort(cfg, box, lambda n: out_dir / f"stream_t{n}.jsonl", label=task, fake=fake)
        rec["hooks_sha256"] = port.install(token_file)
        rec["network"] = sandbox.net_check(box, sbx)
        if not rec["network"]["ok"]:
            raise RuntimeError(f"境界の検査に落ちた: {rec['network']}")
        code, base = git(box, ["rev-parse", "HEAD"])
        if code != 0:
            raise RuntimeError(f"作業場の HEAD を読めない: {base.strip()[-200:]}")
        base = base.strip()
        rec["prompt_sha256"] = hashlib.sha256(t["instruction"].encode("utf-8")).hexdigest()
        rec["agent_start"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        port.start(t["instruction"], t["agent_timeout_s"])
        turn = port.records[-1]
        rec["turn"] = {k: v for k, v in turn.items() if k != "turn"}
        rec["valid"] = turn["valid"]
        rec["agent_end"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rec["left_procs_after_agent"] = sandbox.stop_user(box, sbx)
        rec["memory"] = {"after_agent_peak_mib": mib(sandbox.memory_peak(box)), "oom_kills_after_agent": sandbox.oom_kills(box)}
        if turn.get("model_error"):
            rec["stopped"] = "モデルの照合に落ちた（採点しない。コンテナは消す）"
            raise model_pin.ModelPinError(turn["model_error"])
        sandbox.must(["docker", "network", "disconnect", net["internal"], box], "network disconnect")
        rec["changes"] = save_changes(box, base, out_dir)
        if fake and fake_oracle:
            rec["oracle"] = apply_oracle(box, t, out_dir)
        rec["eval"] = verify(box, t, out_dir)
        rec["memory"].update(run_peak_mib=mib(sandbox.memory_peak(box)), oom_kills=sandbox.oom_kills(box))
        ev = rec["eval"]
        rec["aggregate"] = ({"reward": None, "resolved": None, "reason": "invalid_run"} if not rec["valid"] else
                            {"reward": ev["reward"], "resolved": ev["reward"] == 1.0 if ev["reward"] is not None else None,
                             "phases_passed": sum(1 for p in ev["phases"] if p["passed"]), "phases": len(ev["phases"])})
        if not fake:
            rec["quota_after"] = agy_quota.read()
    finally:
        if box:
            sandbox.remove(box)
        rec["boxes_left"] = sandbox.boxes_left(tag)
        write_record(out_dir, rec)
    return rec


# ---------------------------------------------------------------- 入口

def cmd_check(args):
    """境界と環境の検査（利用枠 0）：課題のイメージに本物の agy（SHA-512 を照らす）とフックとトークンを置き、境界の
    検査と、モデルを呼ばない /quota（root・プロキシ越しの認証）と、道具の有無（yarn の版はネットワーク無しで）を見る。"""
    cfg = load_config()
    task = require_dev(cfg, args.task)
    sbx, net = cfg["sandbox"], cfg["network"]
    t = read_task(cfg, task)
    check_image(t["image"], cfg["rb"]["dev_tasks"][task])
    sandbox.ensure_egress(net)
    box = sandbox.start(net, sbx, t["image"], f"rb-check-{os.getpid()}", extra=limits(t))
    try:
        token = Path(cfg["agent"]["token"])
        sha = agy_port.install(cfg, box, token_file=token)
        res = sandbox.net_check(box, sbx)
        code, quota = sandbox.sh(["docker", "exec", "--user", "0:0", "-e", f"HOME={sbx['home']}",
                                  "-e", "AGY_CLI_DISABLE_AUTO_UPDATE=1", box, agy_port.AGY_BIN, "-p", "/quota",
                                  "--output-format", "json"], timeout=180)
        try:
            q = agy_quota.parse(quota[quota.index("{"):])
            quota_line = {"tokens": q["tokens"], "turns": q["turns"],
                          "weekly": round(q["buckets"]["gemini-weekly"]["remaining_fraction"], 4)}
        except (ValueError, KeyError):
            quota_line = {"rc": code, "tail": quota.strip()[-300:]}
        probe = sandbox.as_root(box, PROBE, timeout=180)[1]
        print(json.dumps({"hooks_sha256": sha, "network": res, "quota_in_container": quota_line, "probe": probe.splitlines(),
                          "limits": limits(t)}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] and quota_line.get("tokens") == 0 else 1
    finally:
        sandbox.remove(box)


def cmd_run(args):
    cfg = load_config()
    if args.fake_oracle and not args.fake:
        raise RuntimeError("--fake-oracle は偽物の走り（--fake）の確認だけで使う")
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_task(cfg, args.task, args.out, fake=args.fake, fake_oracle=args.fake_oracle, token_file=token)
    t = rec.get("turn") or {}
    ag = t.get("agent") or {}
    ev = rec.get("eval") or {}
    print(f"{rec['task']} valid={rec.get('valid')} reward={ev.get('reward')} phases="
          f"{''.join('P' if p['passed'] else 'F' for p in ev.get('phases') or [])} rc={ag.get('rc')} "
          f"timeout={ag.get('timeout')} agent_s={ag.get('seconds')} memory={rec.get('memory')} "
          f"denied_ran={sum(1 for d in t.get('denied_tools') or [] if d['ran'])} stopped={rec.get('stopped')}")
    print(f"boxes_left={rec['boxes_left']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="RoadmapBench の薄い接続（Gate 1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="境界と環境の検査（利用枠 0）")
    c.add_argument("--task", required=True)
    r = sub.add_parser("run", help="1 つの課題を回す（H0）")
    r.add_argument("--task", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--fake-oracle", action="store_true", help="偽物の走りの後に公式の solve.sh を当てる（経路の確認）")
    r.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    return cmd_check(args) if args.cmd == "check" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
