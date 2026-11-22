"""SWE-Game Mode 4（Bug Repair）の実行口（実験と計画 §6。WSL の root で走らせる）。ベンチ固有の層。

    python -m harness.sweg_run check                                          境界と環境の事実の検査（利用枠 0）
    python -m harness.sweg_run run --game <id> --case-id <id> --out <置き場> [--fake]

1 つのケースを、公式の手順で作った包みの visible だけを作業場にして実装役（agy）に 1 回渡し（H0。§6-4）、公式の
collect_submission と eval-task で採点する。ベンチのコードは書き換えない。境界・コンテナ・agy・走りの後の検査は
共通の層（harness/sandbox.py・harness/agy_port.py）が受け持つ。

- 包み：data/task-data.json の固定の版のゲームの束を SHA-256 で照らして取り（evalsys.reference_data.ensure_game）、
  公式の `bench gen-task --mode bugfix` で作る（採点のコンテナ。ネットワークは束を取るためだけ）。hidden は実装役に入れない
- 走らせるのは公開の dev（config/phase1_dev.json）のゲームとケースの組だけ。外なら何も作らずに止まる（harness/phase1_dev.py）
- 文面：公式の PROMPT.md に、公式の時間の文と環境の文（evalsys の _deadline_prompt・_environment_prompt）を足す。
  環境の事実（Godot・HOME・表示・道具）は実装役のコンテナで測る
- 提出：公式の走らせ方（evalsys.taskgen.matrix の run_coding_agent）と同じく、実装役が終わりの符号 0 で終わったときだけ
  提出物を集めて採点する。時間切れ（agy の --print-timeout か、実行口の上限）は agent_timeout、0 でない符号は
  agent_execution_error で、どちらも提出物を残さない（公式の時間の文：「If that timeout fires, the harness records no
  submission even when files exist」）。集計では、採点しなかったケースも母数に残し、resolved: false・0 点に数える
  （記録の aggregate。measured は、採点できたケースだけの公式の形の割合のため）
- 採点：ネットワークの無い採点のコンテナで、公式の collect_submission と `bench eval-task --visual-judge none`。
  基本のイメージの ID を固定の値と照らし、違えば採点しない
- 走らせ方の値（時間の上限・playtest の道具など。config/sweg_run.json）は、操縦士の承認（2026-11-20。実験と計画 §6-4）
"""
import argparse
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
CONFIG = ROOT / "config" / "sweg_run.json"
REPO = "/repo"                                 # 採点のコンテナの中の SWE-Game のリポジトリ
EVALSYS = REPO + "/eval/evalsys"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")   # ゲームとケースの ID（シェルに渡すので形を確かめる）
AGENT_ENV_TOOLS = ("jq", "ffmpeg", "ffprobe", "xvfb-run", "node", "ruby", "python3")   # 公式の matrix.py と同じ並び

PROBE = r'''
printf 'godot_path=%s\n' "$GODOT_BIN"
printf 'godot_version=%s\n' "$("$GODOT_BIN" --version 2>/dev/null | head -1)"
printf 'home=%s\n' "$HOME"
if [ -w "$HOME" ]; then echo home_writable=1; else echo home_writable=0; fi
printf 'display=%s\n' "${DISPLAY:-}"
printf 'path=%s\n' "$PATH"
printf 'uname=%s\n' "$(uname -a)"
for t in TOOLS; do if command -v "$t" >/dev/null 2>&1; then echo "tool_$t=1"; else echo "tool_$t=0"; fi; done
'''

RENDER = r'''
import json, sys
sys.path.insert(0, "EVALSYS")
from evalsys.taskgen import matrix as m
f = json.load(open("/out/env_facts.json", encoding="utf-8"))
probe = m.EnvironmentProbe(
    location="container:agy",
    tool_version=lambda argv: (f["godot_version"] or None) if argv and argv[0] == f["godot_path"] else None,
    has_tool=lambda name, _path: bool(f["tools"].get(name)),
    dir_writable=lambda path: bool(f["home_writable"]) if path == f["home"] else False,
    uname=lambda: f["uname"],
    agent_cli=lambda: {"codex": None, "claude": None},
)
env = {"GODOT_BIN": f["godot_path"], "HOME": f["home"], "PATH": f["path"]}
if f["display"]:
    env["DISPLAY"] = f["display"]
budget = f["budget_s"]
snap = m._agent_environment(env, timeout_s=budget, child_timeout_cap_s=m._child_timeout_cap(budget, mode="bugfix"), probe=probe)
text = (open("/out/package/visible/PROMPT.md", encoding="utf-8").read()
        + m._deadline_prompt(budget, mode="bugfix") + m._environment_prompt(snap))
open("/out/prompt.md", "w", encoding="utf-8").write(text)
json.dump(snap, open("/out/env_snapshot.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
'''

COLLECT = r'''
import sys
from pathlib import Path
sys.path.insert(0, "EVALSYS")
from evalsys.taskgen.matrix import collect_submission
print(collect_submission(Path("/ws"), Path("/out/submission"), mode="bugfix"))
'''


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def load_config(path=CONFIG, common=agy_port.CONFIG):
    """共通の設定（agent・sandbox・network・quota_floor）に、ベンチ固有の設定を sweg として足す。"""
    cfg = agy_port.load_config(common)
    cfg["sweg"] = json.loads(Path(path).read_text(encoding="utf-8"))
    return cfg


def check_id(value, what):
    if not ID_RE.match(value or ""):
        raise ValueError(f"{what} の形が違う: {value!r}")
    return value


def probe_script():
    return PROBE.replace("TOOLS", " ".join(AGENT_ENV_TOOLS))


def parse_facts(text, budget_s):
    """PROBE の出力 → 環境の事実（公式の EnvironmentProbe に渡す形）。"""
    kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {"godot_path": kv.get("godot_path", ""), "godot_version": kv.get("godot_version", ""),
            "home": kv.get("home", ""), "home_writable": kv.get("home_writable") == "1", "display": kv.get("display", ""),
            "path": kv.get("path", ""), "uname": kv.get("uname", ""),
            "tools": {t: kv.get(f"tool_{t}") == "1" for t in AGENT_ENV_TOOLS}, "budget_s": budget_s}


def submission(turn):
    """提出物を集めて採点するか。(採点するか, 落ちの種類)。公式と同じく、終わりの符号 0 で終わったときだけ採点する。"""
    agent = (turn or {}).get("agent") or {}
    if agent.get("timeout"):
        return False, "agent_timeout"
    if agent.get("rc") != 0:
        return False, "agent_execution_error"
    return True, None


def aggregate(ev):
    """集計の値。採点しなかった（提出物が無い）ケースは resolved: false・0 点で母数に残す（measured: false）。集めか採点が
    落ちた（測定系の異常）ケースは resolved を None にする（落ちに数えず、止める線で止める）。"""
    ev = ev or {}
    if ev.get("summary"):
        return {"resolved": bool(ev["summary"].get("resolved")), "score": ev["summary"].get("score"), "measured": True}
    if ev.get("failure_kind"):
        return {"resolved": False, "score": 0.0, "measured": False, "reason": ev["failure_kind"]}
    return {"resolved": None, "score": None, "measured": False, "reason": "collect_or_verifier_error"}


def eval_summary(report):
    """公式の report.json → 要約（resolved・0〜100 の点・状態・落ちた必須の項目・判定の数）。"""
    head = report.get("headline") or {}
    res = report.get("resolution") or {}
    return {"resolved": report.get("resolved"), "score": head.get("score"), "status": head.get("status"),
            "score_scope": head.get("score_scope"),
            "failing_strict_items": [i.get("id") for i in res.get("failing_strict_items") or []],
            "by_verdict": (report.get("score") or {}).get("by_verdict")}


# ---------------------------------------------------------------- 採点のコンテナ（公式のコード）

def check_image(sw):
    image_id = sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", sw["image"]], "image inspect").strip()
    if image_id != sw["image_id"]:
        raise RuntimeError(f"基本のイメージの ID が固定の値と違う: {image_id}")
    return image_id


def bench(cfg, name, script, mounts, network="none", repo_rw=False, timeout=3600):
    """採点のコンテナ（SWE-Game のイメージ、root）で bash の 1 行を走らせる。(終わりの符号, 出力)。時間切れでも
    コンテナを残さない。"""
    sw = cfg["sweg"]
    check_image(sw)
    cmd = ["docker", "run", "--rm", "--name", name, "--network", network, "--user", "0:0",
           "-v", f"{sw['repo']}:{REPO}" + ("" if repo_rw else ":ro"), "-w", REPO]
    for host, inside, mode in mounts:
        cmd += ["-v", f"{host}:{inside}:{mode}"]
    try:
        return sandbox.sh(cmd + [sw["image"], "bash", "-c", script], timeout=timeout)
    finally:
        sandbox.remove(name)


def make_package(cfg, game, case_id, out_dir, tag):
    """公式の手順で包みを作る（束を照らして取り、gen-task）。ネットワークは束を取るためだけ。manifest の SHA-256 を返す。"""
    script = (f"set -e; python3 -c \"import sys; sys.path.insert(0, '{EVALSYS}'); "
              f"from evalsys.reference_data import ensure_game; print(ensure_game('{game}'))\"; "
              f"./eval/evalsys/bin/bench gen-task --mode bugfix --game games/{game} --case-id {case_id} --out /out/package")
    code, out = bench(cfg, f"{tag}-package", script, [(out_dir, "/out", "rw")], network="bridge", repo_rw=True,
                      timeout=3600)
    manifest = Path(out_dir) / "package" / "manifest.json"
    if code != 0 or not manifest.is_file():
        raise RuntimeError(f"包みを作れない (rc={code}): {out.strip()[-400:]}")
    return {"rc": code, "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}


def render_prompt(cfg, out_dir, facts, tag):
    """公式の PROMPT.md・時間の文・環境の文を、公式のコードで組み立てる。文面の置き場を返す。"""
    (Path(out_dir) / "env_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    script = "python3 - <<'PY'\n" + RENDER.replace("EVALSYS", EVALSYS) + "\nPY"
    code, out = bench(cfg, f"{tag}-prompt", script, [(out_dir, "/out", "rw")], timeout=300)
    prompt = Path(out_dir) / "prompt.md"
    if code != 0 or not prompt.is_file():
        raise RuntimeError(f"文面を組み立てられない (rc={code}): {out.strip()[-400:]}")
    return prompt


def collect_and_evaluate(cfg, workspace, out_dir, tag):
    """公式の collect_submission で提出物を作り、公式の eval-task で採点する（ネットワーク無し）。"""
    script = "python3 - <<'PY'\n" + COLLECT.replace("EVALSYS", EVALSYS) + "\nPY"
    code, out = bench(cfg, f"{tag}-collect", script, [(workspace, "/ws", "ro"), (out_dir, "/out", "rw")], timeout=600)
    if code != 0:
        return {"collect_rc": code, "error": out.strip()[-400:], "summary": None}
    script = ("./eval/evalsys/bin/bench eval-task --package /out/package --submission /out/submission "
              f"--out /out/evaluation --visual-judge {cfg['sweg']['visual_judge']}")
    code, out = bench(cfg, f"{tag}-eval", script, [(out_dir, "/out", "rw")], timeout=7200)
    report = Path(out_dir) / "evaluation" / "report.json"
    data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    return {"collect_rc": 0, "rc": code, "image_id": cfg["sweg"]["image_id"],
            "summary": eval_summary(data) if data else None, "error": None if data else out.strip()[-400:]}


# ---------------------------------------------------------------- 1 つのケースを回す

def write_record(out_dir, rec):
    (Path(out_dir) / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def measure_facts(box, sbx, budget_s):
    code, out = sandbox.sh(["docker", "exec", "--user", f"{sbx['uid']}:{sbx['uid']}", "-e", f"HOME={sbx['home']}", box,
                            "bash", "-c", probe_script()], timeout=120)
    return parse_facts(out, budget_s)


def run_case(cfg, game, case_id, out_dir, fake=False, token_file=None):
    phase1_dev.require_mode4(game, case_id)   # 公開の dev の外は、何も作らずに止まる
    check_id(game, "ゲーム")
    check_id(case_id, "ケース")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sw, sbx, net = cfg["sweg"], cfg["sandbox"], cfg["network"]
    uid, budget = sbx["uid"], sw["budget_s"]
    tag = f"sweg-{case_id}-{os.getpid()}"
    rec = {"game": game, "case_id": case_id, "cond": "H0", "fake": fake, "model": cfg["agent"]["model_name"],
           "budget_s": budget, "playtest_kit": sw["playtest_kit"]}
    box = None
    try:
        if (out_dir / "package").exists():
            raise RuntimeError(f"包みの置き場がすでにある: {out_dir / 'package'}")
        rec["package"] = make_package(cfg, game, case_id, out_dir, tag)
        workspace = out_dir / "workspace"
        shutil.copytree(out_dir / "package" / "visible", workspace, symlinks=True)
        for p in [workspace, *workspace.rglob("*")]:
            os.lchown(p, uid, uid)
        if not fake:
            rec["quota_before"], ok, why = agy_port.quota_check(cfg)
            if not ok:
                rec["stopped"] = why
                return rec
        rec["egress"] = sandbox.ensure_egress(net)
        box = sandbox.start(net, sbx, sw["image"], f"{tag}-agent", workspace)
        port = agy_port.AgyPort(cfg, box, lambda n: out_dir / f"stream_t{n}.jsonl", label=f"{game} {case_id}", fake=fake)
        rec["hooks_sha256"] = port.install(token_file)
        rec["network"] = sandbox.net_check(box, sbx)
        if not rec["network"]["ok"]:
            raise RuntimeError(f"境界の検査に落ちた: {rec['network']}")
        prompt = render_prompt(cfg, out_dir, measure_facts(box, sbx, budget), tag)
        rec["prompt_sha256"] = hashlib.sha256(prompt.read_bytes()).hexdigest()
        port.start(prompt.read_text(encoding="utf-8"), budget)
        t = port.records[-1]
        rec["turn"] = {k: v for k, v in t.items() if k != "turn"}
        rec["valid"] = t["valid"]
        sandbox.remove(box)
        box = None
        if t.get("model_error"):
            rec["stopped"] = "モデルの照合に落ちた（採点しない。作業場は workspace に残る）"
            raise model_pin.ModelPinError(t["model_error"])
        submit, failure = submission(t)
        if submit:
            rec["eval"] = collect_and_evaluate(cfg, workspace, out_dir, tag)
        else:
            rec["eval"] = {"skipped": "公式と同じく提出物を残さない（採点しない）", "failure_kind": failure}
        rec["aggregate"] = aggregate(rec["eval"])
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
    cfg = load_config()
    sw, sbx, net = cfg["sweg"], cfg["sandbox"], cfg["network"]
    check_image(sw)
    sandbox.ensure_egress(net)
    workspace = tempfile.mkdtemp(prefix="sweg-check-")
    box = sandbox.start(net, sbx, sw["image"], f"sweg-check-{os.getpid()}", workspace)
    try:
        sha = agy_port.install(cfg, box, fake=True)
        res = sandbox.net_check(box, sbx)
        facts = measure_facts(box, sbx, sw["budget_s"])
        print(json.dumps({"hooks_sha256": sha, "network": res, "facts": facts}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    finally:
        sandbox.remove(box)
        shutil.rmtree(workspace, ignore_errors=True)


def cmd_run(args):
    cfg = load_config()
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_case(cfg, args.game, args.case_id, args.out, fake=args.fake, token_file=token)
    s = ((rec.get("eval") or {}).get("summary")) or {}
    t = rec.get("turn") or {}
    print(f"{rec['case_id']} valid={rec.get('valid')} resolved={s.get('resolved')} score={s.get('score')} "
          f"status={s.get('status')} rc={(t.get('agent') or {}).get('rc')} timeout={(t.get('agent') or {}).get('timeout')} "
          f"failure_kind={(rec.get('eval') or {}).get('failure_kind')} aggregate={rec.get('aggregate')} "
          f"denied_ran={sum(1 for d in t.get('denied_tools') or [] if d['ran'])} stopped={rec.get('stopped')}")
    print(f"boxes_left={rec['boxes_left']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="SWE-Game Mode 4 の実行口（実験と計画 §6）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="境界と環境の事実の検査（利用枠 0）")
    r = sub.add_parser("run", help="1 つのケースを回す（H0）")
    r.add_argument("--game", required=True)
    r.add_argument("--case-id", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    return cmd_check(args) if args.cmd == "check" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
