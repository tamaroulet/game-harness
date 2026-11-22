"""SlopCodeBench の実行口（実験と計画 §6-3〜6-5。WSL の root で走らせる）。ベンチ固有の層。

    python -m harness.scb_run check                                        境界の検査（利用枠 0）
    python -m harness.scb_run run --problem <名> --cond H0|H0_fresh --out <置き場> [--fake] [--ttl 秒] [--upto N]
    python -m harness.scb_run deny-check --out <置き場> [--tools search_web,invoke_subagent] [--fake]
                                                                           deny の確認（§6-5。比べの記録に入れない）

1 つの問題を、チェックポイント 1 から順に実装役（agy）に渡し、チェックポイントごとに作業場の写しを公式の
`slop-code eval-snapshot` で採点する。ベンチのコードは書き換えない。境界・コンテナ・agy・走りの後の検査は共通の層
（harness/sandbox.py・harness/agy_port.py）が受け持つ。

- 比較条件（§6-4）：H0 は 1 つのコンテナと 1 つの会話を続ける（2 つ目からは同じ会話に送る）。H0_fresh は
  チェックポイントごとに新しいコンテナと新しい会話で、引き継ぐのは作業場だけ。隠しテストの出力は返さない。
- 文面：公式の `slop-code utils render-prompts`（just-solve のひな形。canary を除き、入口の置き換えをしたもの）。
- 走らせるのは公開の dev（config/phase1_dev.json）の問題だけ。外の名前なら何も作らずに止まる（harness/phase1_dev.py）。
- 走りの後の検査（ベンチ固有の分）：作業場の .evaluation_tests/（壊れたシンボリックリンクも含め、あれば採点せず無効）。
- 時間切れ（agy の --print-timeout か、実行口の上限。種類は記録の agent.timeout）：公式の走らせ方と同じく、その
  チェックポイントの作業場の写しを採点する。公式は時間切れでその問題を止めるが、ここでは次のチェックポイントへ進む
  （H0 は同じ会話に送る。続けられなければ Stage 1 の止める線で止める。実験と計画 §6-5）。同じ問題で
  TIMEOUT_STREAK_STOP 回続けて時間切れなら、そのチェックポイントを採点した後に問題を打ち切る（止める線 9）。
  集計では、時間切れのチェックポイントを母数から外さない。
- 採点：作業場の写し（公式のスナップショットの既定の除外に合わせる）を、非 root（HUID・HGID）で採点する。
  環境の設定は公式の値のまま environment.include_os_env だけを足し（environment.env を変えると、公式の採点は
  基本のイメージを作り直して同じ名前に付け替える）、UV_EXCLUDE_NEWER（採点の時刻）はホストの最小の環境で渡す。
  時刻と基本のイメージの ID を記録し、ID が固定の値と違えば採点しない。
- --fake は偽物の agy（利用枠 0）で、境界・文面・採点・記録の繋ぎを通す。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_port  # noqa: E402
import agy_quota  # noqa: E402
import exitcode  # noqa: E402
import model_pin  # noqa: E402
import phase1_dev  # noqa: E402
import sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "scb_run.json"
CONDITIONS = ("H0", "H0_fresh")
RESERVED = ".evaluation_tests"
SNAPSHOT_IGNORE = ("*.pyc", "venv", ".venv", ".DS_Store")   # 公式の Snapshot の既定の除外（execution/snapshot.py）
DEFAULT_TTL = 3600
TIMEOUT_STREAK_STOP = 2   # 同じ問題で続けて時間切れになったら打ち切る回数（実験と計画 §6-5 の止める線 9）


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def timeout_streak(checkpoints):
    """末尾から数えて、続けて時間切れ（記録の agent.timeout が有る）になったチェックポイントの数。"""
    n = 0
    for c in reversed(checkpoints):
        if not (c.get("agent") or {}).get("timeout"):
            break
        n += 1
    return n


def load_config(path=CONFIG, common=agy_port.CONFIG):
    """共通の設定（agent・sandbox・network・quota_floor）に、ベンチ固有の設定を scb として足す。"""
    cfg = agy_port.load_config(common)
    cfg["scb"] = json.loads(Path(path).read_text(encoding="utf-8"))
    return cfg


def env_config_with_os_env(text):
    """公式の環境の設定（YAML の文字列）の environment に include_os_env: true を足す。environment.env は変えない
    （基本のイメージのラベルのハッシュは environment.env から作られ、変えると作り直しになる）。形が違えば止める。"""
    if text.count("\nenvironment:\n") != 1 or "include_os_env" in text:
        raise ValueError("環境の設定の形が想定と違う（environment が 1 つでない、か、すでに include_os_env がある）")
    return text.replace("\nenvironment:\n", "\nenvironment:\n  include_os_env: true\n", 1)


def eval_env(scb, cutoff, uid):
    """採点を起動するホストの最小の環境。include_os_env でコンテナのコマンドにも写るので、HOME は公式のイメージの既定に合わせる。"""
    return {"PATH": scb["uv_bin"] + ":/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp/agent_home",
            "SCBENCH_PROBLEMS_PATH": scb["problems"], "HUID": str(uid), "HGID": str(uid),
            "UV_EXCLUDE_NEWER": cutoff}


def eval_summary(d):
    """eval-snapshot の JSON → 要約。strict＝全テスト、iso＝Regression のほかの全テスト、core＝Core の全テスト。"""
    passed = d.get("pass_counts") or {}
    total = d.get("total_counts") or {}
    infra = bool(d.get("infrastructure_failure"))
    tot, pas = sum(total.values()), sum(passed.values())
    non_reg = [k for k in total if k != "Regression"]
    regression_failed = sorted(x for k, v in (d.get("tests") or {}).items() if "Regression" in k for x in v.get("failed", []))
    return {"passed": pas, "total": tot, "pass_counts": passed, "total_counts": total, "infrastructure_failure": infra,
            "strict": (not infra) and tot > 0 and pas == tot,
            "iso": (not infra) and tot > 0 and all(passed.get(k, 0) == total.get(k, 0) for k in non_reg),
            "core": (not infra) and total.get("Core", 0) > 0 and passed.get("Core", 0) == total.get("Core", 0),
            "regression_failed": regression_failed, "collection_hash": d.get("test_collection_hash"),
            "duration": d.get("duration")}


def reserved_present(workspace):
    """作業場の根の .evaluation_tests。壊れたシンボリックリンクは exists() が False を返すので、is_symlink() も見る。"""
    p = Path(workspace) / RESERVED
    return p.exists() or p.is_symlink()


def prompt_files(directory):
    """render-prompts の出力（part_<n>.md）をチェックポイントの順に。"""
    files = sorted(Path(directory).glob("part_*.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not files:
        raise RuntimeError(f"文面が無い: {directory}")
    return files


def copy_snapshot(src, dst):
    """作業場の写し（公式のスナップショットの既定の除外に合わせる）。"""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SNAPSHOT_IGNORE), symlinks=True)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- 公式の文面と採点

def render_prompts(cfg, problem, out_dir):
    scb = cfg["scb"]
    env = {"PATH": scb["uv_bin"] + ":" + os.environ.get("PATH", ""), "SCBENCH_PROBLEMS_PATH": scb["problems"]}
    sandbox.must([f"{scb['bench']}/.venv/bin/slop-code", "--quiet", "utils", "render-prompts", "-p", scb["prompt"], "-e",
                  scb["env_config"].rsplit("/", 1)[-1].removesuffix(".yaml"), "-o", str(out_dir), "--problem", problem],
                 "render-prompts", env=env, timeout=300, cwd=scb["bench"])
    return prompt_files(out_dir)


def evaluate(cfg, problem, cp, snap, out_dir):
    """公式の eval-snapshot。UV_EXCLUDE_NEWER（採点の時刻）をホストの環境で渡し、時刻とイメージの ID を返す。"""
    scb = cfg["scb"]
    cutoff = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = Path(scb["bench"]) / scb["env_config"]
    env_cfg = Path(out_dir) / "env_os.yaml"
    env_cfg.write_text(env_config_with_os_env(base.read_text(encoding="utf-8")), encoding="utf-8")
    image_id = sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", scb["image"]], "image inspect").strip()
    if image_id != scb["image_id"]:
        raise RuntimeError(f"基本のイメージの ID が固定の値と違う: {image_id}")
    out_json = Path(out_dir) / f"eval_c{cp}.json"
    code, out, err = sandbox.sh3([f"{scb['bench']}/.venv/bin/slop-code", "--quiet", "eval-snapshot", str(snap), "-o",
                                  str(Path(out_dir) / f"eval_c{cp}"), "-p", problem, "-c", str(cp), "-e", str(env_cfg),
                                  "--json"],
                                 env=eval_env(scb, cutoff, cfg["sandbox"]["uid"]), timeout=7200, cwd=scb["bench"],
                                 replace_env=True)
    try:
        data = json.loads(out) if code == 0 else None
    except ValueError:
        data = None
    if data is not None:
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rc": code, "cutoff": cutoff, "image_id": image_id, "summary": eval_summary(data) if data else None,
            "error": None if data else (err or out).strip()[-400:]}


# ---------------------------------------------------------------- 1 つの問題を回す

def write_record(out_dir, rec):
    (Path(out_dir) / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def run_problem(cfg, problem, cond, out_dir, fake=False, ttl=DEFAULT_TTL, upto=None, token_file=None):
    phase1_dev.require_scb(problem)   # 公開の dev の外は、何も作らずに止まる
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scb, sbx, net = cfg["scb"], cfg["sandbox"], cfg["network"]
    uid = sbx["uid"]
    workspace = out_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    os.chown(workspace, uid, uid)
    prompts = render_prompts(cfg, problem, out_dir / "prompts")[:upto]
    egress = sandbox.ensure_egress(net)
    rec = {"problem": problem, "cond": cond, "fake": fake, "model": cfg["agent"]["model_name"], "egress": egress,
           "checkpoints": []}
    tag = f"scb-{problem.replace('_', '-')}-{cond.lower().replace('_', '-')}-{os.getpid()}"
    box, port = None, None
    try:
        for cp, prompt_file in enumerate(prompts, 1):
            c = {"checkpoint": cp, "prompt_sha256": sha256_file(prompt_file)}
            rec["checkpoints"].append(c)
            if not fake:
                c["quota_before"], ok, why = agy_port.quota_check(cfg)
                if not ok:
                    c["stopped"] = why
                    break
            if box is None or cond == "H0_fresh":
                if box:
                    sandbox.remove(box)
                box = sandbox.start(net, sbx, scb["image"], f"{tag}-c{cp}", workspace)
                port = agy_port.AgyPort(cfg, box, lambda n, first=cp: out_dir / f"stream_c{first + n - 1}.jsonl",
                                        label=f"{problem} c{cp}", fake=fake)
                c["hooks_sha256"] = port.install(token_file)
                c["network"] = sandbox.net_check(box, sbx)
                if not c["network"]["ok"]:
                    raise RuntimeError(f"境界の検査に落ちた: {c['network']}")
            text = prompt_file.read_text(encoding="utf-8")
            (port.send if port.records else port.start)(text, ttl)
            t = port.records[-1]
            c.update({k: v for k, v in t.items() if k not in ("turn", "valid")})
            c["reserved_path"] = reserved_present(workspace)
            c["valid"] = t["valid"] and not c["reserved_path"]
            if c.get("model_error"):
                c["stopped"] = "モデルの照合に落ちた（採点しない。作業場は workspace に残る）"
                raise model_pin.ModelPinError(c["model_error"])
            snap = out_dir / f"snapshot_c{cp}"
            copy_snapshot(workspace, snap)
            for p in [snap, *snap.rglob("*")]:
                os.lchown(p, uid, uid)
            if c["reserved_path"]:
                c["eval"] = {"skipped": f"{RESERVED} が作業場にある（採点しない。無効）"}
            else:
                c["eval"] = evaluate(cfg, problem, cp, snap, out_dir)
            if not fake:
                c["quota_after"] = agy_quota.read()
            if timeout_streak(rec["checkpoints"]) >= TIMEOUT_STREAK_STOP:
                c["stopped"] = f"{TIMEOUT_STREAK_STOP} 回続けて時間切れ（止める線 9。この問題を打ち切る）"
            write_record(out_dir, rec)
            if c.get("stopped"):
                break
    finally:
        if box:
            sandbox.remove(box)
        rec["boxes_left"] = sandbox.boxes_left(tag)
        write_record(out_dir, rec)
    return rec


# ---------------------------------------------------------------- 入口

def cmd_check(args):
    cfg = load_config()
    sbx, net = cfg["sandbox"], cfg["network"]
    sandbox.ensure_egress(net)
    workspace = tempfile.mkdtemp(prefix="scb-check-")
    box = sandbox.start(net, sbx, cfg["scb"]["image"], f"scb-check-{os.getpid()}", workspace)
    try:
        sha = agy_port.install(cfg, box, fake=True)
        res = sandbox.net_check(box, sbx)
        print(json.dumps({"hooks_sha256": sha, "network": res}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    finally:
        sandbox.remove(box)
        shutil.rmtree(workspace, ignore_errors=True)   # ホストの一時のフォルダも残さない


def cmd_run(args):
    cfg = load_config()
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_problem(cfg, args.problem, args.cond, args.out, fake=args.fake, ttl=args.ttl, upto=args.upto, token_file=token)
    for c in rec["checkpoints"]:
        s = ((c.get("eval") or {}).get("summary")) or {}
        print(f"c{c['checkpoint']} valid={c.get('valid')} strict={s.get('strict')} iso={s.get('iso')} core={s.get('core')} "
              f"passed={s.get('passed')}/{s.get('total')} denied_tools={len(c.get('denied_tools') or [])} "
              f"denied_ran={sum(1 for d in c.get('denied_tools') or [] if d['ran'])} "
              f"timeout={(c.get('agent') or {}).get('timeout')} stopped={c.get('stopped')}")
    print(f"boxes_left={rec['boxes_left']}")
    return 0


def cmd_deny_check(args):
    """deny の確認（実験と計画 §6-5）：問題の文を使わない新しい会話の 1 ターンで、止めた道具を 1 度ずつ呼ぶよう頼み、
    フックが止めるかを確かめる。境界・フック・モデル・利用枠の下限は本の走りと同じ。比べの記録には入れない。"""
    cfg = load_config()
    sbx, net = cfg["sandbox"], cfg["network"]
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    wanted = tuple(t for t in args.tools.split(",") if t)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True)   # すでにあれば止める（前の確認の記録を上書きしない）
    rec = {"kind": "deny_check", "fake": args.fake, "model": cfg["agent"]["model_name"], "tools": list(wanted)}
    tag, box = f"scb-deny-check-{os.getpid()}", None
    try:
        if not args.fake:
            rec["quota_before"], ok, why = agy_port.quota_check(cfg)
            if not ok:
                rec["stopped"] = why
                return 1
        workspace = out_dir / "workspace"
        workspace.mkdir()
        os.chown(workspace, sbx["uid"], sbx["uid"])
        rec["egress"] = sandbox.ensure_egress(net)
        box = sandbox.start(net, sbx, cfg["scb"]["image"], tag, workspace)
        port = agy_port.AgyPort(cfg, box, lambda n: out_dir / f"stream_t{n}.jsonl", label="deny-check", fake=args.fake)
        rec["hooks_sha256"] = port.install(token)
        rec["network"] = sandbox.net_check(box, sbx)
        if not rec["network"]["ok"]:
            raise RuntimeError(f"境界の検査に落ちた: {rec['network']}")
        port.start(agy_port.DENY_CHECK_PROMPT.format(tools=", ".join(wanted)), args.ttl)
        t = port.records[-1]
        rec["turn"] = {k: v for k, v in t.items() if k != "turn"}
        rec["attempts"] = agy_port.tool_attempts((out_dir / "stream_t1.jsonl").read_text(encoding="utf-8"), wanted)
        ok, why = agy_port.deny_verdict(rec["attempts"])
        if ok and not t["valid"]:
            ok, why = False, "走りが無効（モデルの照合・止めた道具の走り・フックの変化）: " + str(t.get("turn_note"))
        rec["verdict"] = {"pass": ok, "reason": why}
        if not args.fake:
            rec["quota_after"] = agy_quota.read()
        return 0 if ok else 1
    finally:
        if box:
            sandbox.remove(box)
        rec["boxes_left"] = sandbox.boxes_left(tag)
        write_record(out_dir, rec)
        print(f"deny_check verdict={rec.get('verdict')} stopped={rec.get('stopped')} boxes_left={rec['boxes_left']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="SlopCodeBench の実行口（実験と計画 §6-3〜6-5）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="境界の検査（利用枠 0）")
    r = sub.add_parser("run", help="1 つの問題を回す")
    r.add_argument("--problem", required=True)
    r.add_argument("--cond", required=True, choices=CONDITIONS)
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="1 チェックポイントの実装役の時間の上限（秒）")
    r.add_argument("--upto", type=int, default=None, help="先頭から何チェックポイントまで")
    r.add_argument("--token", default=None)
    d = sub.add_parser("deny-check", help="deny の確認（§6-5。止めた道具を 1 度ずつ呼ぶよう頼む。比べの記録に入れない）")
    d.add_argument("--out", required=True)
    d.add_argument("--tools", default=",".join(agy_port.DENY_CHECK_TOOLS))
    d.add_argument("--ttl", type=int, default=600, help="実装役の時間の上限（秒）")
    d.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    d.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    return {"check": cmd_check, "run": cmd_run, "deny-check": cmd_deny_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
