"""SlopCodeBench の実行口（実験と計画 §6-3〜6-5。WSL の root で走らせる）。

    python -m harness.scb_run check                                        境界の検査（利用枠 0）
    python -m harness.scb_run run --problem <名> --cond H0|H0_fresh --out <置き場> [--fake] [--ttl 秒] [--upto N]

1 つの問題を、チェックポイント 1 から順に実装役（agy）に渡し、チェックポイントごとに作業場の写しを公式の
`slop-code eval-snapshot` で採点する。ベンチのコードは書き換えない。

- 比較条件（§6-4）：H0 は 1 つのコンテナと 1 つの会話を続ける（2 つ目からは --conversation）。H0_fresh は
  チェックポイントごとに新しいコンテナと新しい会話で、引き継ぐのは作業場だけ。隠しテストの出力は返さない。
- 文面：公式の `slop-code utils render-prompts`（just-solve のひな形。canary を除き、入口の置き換えをしたもの）。
- 境界（§6-3）：実装役のコンテナは内部のネットワークだけにつなぎ、外への道は許可リストのプロキシだけ。
  agy の Web の道具と下位エージェントの口（invoke_subagent）はフック（PreToolUse で deny）で止め、フックは root の
  持ち物で書き換え不可にする。非 root（uid 1000）。
- 走りの後の検査：止めた道具の手番（DONE で終わった＝道具が走った走りは無効）・作業場の .evaluation_tests/
  （壊れたシンボリックリンクも含め、あれば採点せず無効）・フックの SHA-256。報告されたモデルが無いか違えば、
  その時点までを記録して止める。
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
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_quota  # noqa: E402
import agy_stream  # noqa: E402
import exitcode  # noqa: E402
import model_pin  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "scb_run.json"
ASSETS = ROOT / "config" / "scb_run"
CONDITIONS = ("H0", "H0_fresh")
# 止める道具（§6-3。agy 1.5.3 の実行ファイルの中の道具の実装の名前と文言で確かめた）。下位エージェントは親と違う
# モデルを選べ、その手番は親の stream-json に出ないので、モデルの照合と走りの後の検査が及ばない
WEB_TOOLS = ("search_web", "read_url_content", "read_url", "open_browser_url", "browser_subagent", "webview_browser")
SUBAGENT_TOOLS = ("invoke_subagent",)
DENIED_TOOLS = WEB_TOOLS + SUBAGENT_TOOLS
HOOK_DENIED = "denied by pre-tool hook"   # agy 1.5.3 が deny を返す文言（"Tool call denied by pre-tool hook: %s"）
RESERVED = ".evaluation_tests"
SNAPSHOT_IGNORE = ("*.pyc", "venv", ".venv", ".DS_Store")   # 公式の Snapshot の既定の除外（execution/snapshot.py）
HOME = "/home/agent"
WORK = "/workspace"
HOOKS_DIR = "/opt/scb-hooks"
HOOK_SCRIPTS = ("deny_web.sh", "deny_subagent.sh")
CONFIG_DIR = HOME + "/.gemini/config"
TOKEN_PATH = HOME + "/.gemini/antigravity-cli/antigravity-oauth-token"
AGY_CACHE = Path("/root/scb_run/agy")
DEFAULT_TTL = 3600

FAKE_AGY = r'''#!/usr/bin/env python3
"""偽物の agy（利用枠 0）。stream-json の形で init・道具の手番・result を出し、作業場に小さな変更を 1 つ書く。"""
import json, os, pathlib, sys, urllib.request, uuid
lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
prompt = json.loads(lines[0])["message"]["content"] if lines else ""
args = sys.argv[1:]
model = args[args.index("--model") + 1] if "--model" in args else "?"
conv = args[args.index("--conversation") + 1] if "--conversation" in args else "fake-conv-" + uuid.uuid4().hex[:8]
def reach(url):
    try:
        urllib.request.urlopen(url, timeout=10); return "open"
    except Exception as e:
        return "open" if getattr(e, "code", None) and e.code != 403 else "blocked"
net = {h: reach("https://%s/" % h) for h in ("pypi.org", "github.com")}
p = pathlib.Path("FAKE_AGY_NOTES.txt")
p.write_text((p.read_text() if p.exists() else "") + "turn: %d chars\n" % len(prompt))
pathlib.Path("requirements.txt").touch()
def ev(name, body):
    print(json.dumps({"event": name, name: body}), flush=True)
ev("init", {"model": model, "conversation_id": conv})
ev("step_update", {"conversation_id": conv, "step_index": 0, "step_type": "tool", "tool_name": "run_command", "state": "DONE",
                   "duration_seconds": 0.1, "usage": {"input_tokens": 10, "output_tokens": 2, "thinking_tokens": 0, "cache_read_tokens": 0}})
if os.environ.get("FAKE_WEB") == "1":
    ev("step_update", {"conversation_id": conv, "step_index": 1, "step_type": "tool", "tool_name": "search_web", "state": "DONE"})
ev("result", {"status": "SUCCESS", "conversation_id": conv, "response": "fake done; net %s" % json.dumps(net),
              "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})
'''


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    model_pin.require_implementer(cfg, str(path))
    return cfg


def filter_lines(allow):
    """tinyproxy の filter（拡張の正規表現。完全一致）。"""
    return "".join("^" + re.escape(h) + "$\n" for h in allow)


def env_config_with_os_env(text):
    """公式の環境の設定（YAML の文字列）の environment に include_os_env: true を足す。environment.env は変えない
    （基本のイメージのラベルのハッシュは environment.env から作られ、変えると作り直しになる）。形が違えば止める。"""
    if text.count("\nenvironment:\n") != 1 or "include_os_env" in text:
        raise ValueError("環境の設定の形が想定と違う（environment が 1 つでない、か、すでに include_os_env がある）")
    return text.replace("\nenvironment:\n", "\nenvironment:\n  include_os_env: true\n", 1)


def eval_env(scb, cutoff):
    """採点を起動するホストの最小の環境。include_os_env でコンテナのコマンドにも写るので、HOME は公式のイメージの既定に合わせる。"""
    return {"PATH": scb["uv_bin"] + ":/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp/agent_home",
            "SCBENCH_PROBLEMS_PATH": scb["problems"], "HUID": str(scb["uid"]), "HGID": str(scb["uid"]),
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


def step_errors(stream_text):
    """stream-json の手番ごとのエラーの文の先頭 1 行（agy は道具のエラーを state: ERROR と tool_info.error で返す）。"""
    out = {}
    for line in (stream_text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        su = e.get("step_update") if e.get("event") == "step_update" else None
        err = ((su or {}).get("tool_info") or {}).get("error") if isinstance(su, dict) else None
        msg = err.get("message") if isinstance(err, dict) else None
        if isinstance(msg, str) and msg.strip():
            out[su.get("step_index")] = msg.strip().splitlines()[0][:200]
    return out


def denied_tool_steps(steps, errors=None):
    """止めた道具の手番。ran は、手番が DONE で終わり（道具が走った）、フックの deny の印が無いもの。
    hook_denied は、エラーの文に agy の deny の文言があるもの。走りの有効の判定は ran だけで読む（§6-3）。"""
    errors = errors or {}
    out = []
    for s in steps:
        if s.get("tool") not in DENIED_TOOLS:
            continue
        err = errors.get(s.get("index"))
        denied = HOOK_DENIED in (err or "").lower()
        out.append({"index": s.get("index"), "tool": s.get("tool"), "state": s.get("state"), "error": err,
                    "hook_denied": denied, "ran": s.get("state") == "DONE" and not denied})
    return out


def is_valid(c, hooks_expected):
    """走りが有効か：止めた道具が走っていない・.evaluation_tests が無い・フックが変わっていない・モデルの照合を通った。"""
    return (not any(s["ran"] for s in c.get("denied_tools") or []) and not c.get("reserved_path")
            and c.get("hooks_sha256_after") == hooks_expected and not c.get("model_error"))


def reserved_present(workspace):
    """作業場の根の .evaluation_tests。壊れたシンボリックリンクは exists() が False を返すので、is_symlink() も見る。"""
    p = Path(workspace) / RESERVED
    return p.exists() or p.is_symlink()


def timeout_output(e):
    """subprocess.TimeoutExpired の出力を文字列に。POSIX では text=True でも bytes で返る（Python の文書のとおり）。"""
    parts = []
    for raw in (e.stdout, e.stderr):
        if isinstance(raw, bytes):
            parts.append(raw.decode("utf-8", "replace"))
        elif isinstance(raw, str):
            parts.append(raw)
    return "".join(parts)


def prompt_files(directory):
    """render-prompts の出力（part_<n>.md）をチェックポイントの順に。"""
    files = sorted(Path(directory).glob("part_*.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not files:
        raise RuntimeError(f"文面が無い: {directory}")
    return files


def quota_ok(rec, floor):
    """週と 5 時間の残りが、どちらも下限を切っていないか。(ok, 理由)。"""
    if rec is None:
        return False, "利用枠を読めない"
    for k, low in floor.items():
        frac = ((rec.get("buckets") or {}).get(k) or {}).get("remaining_fraction")
        if not isinstance(frac, (int, float)) or frac < low:
            return False, f"{k} の残り {frac} が下限 {low} を切っている"
    return True, None


def copy_snapshot(src, dst):
    """作業場の写し（公式のスナップショットの既定の除外に合わせる）。"""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SNAPSHOT_IGNORE), symlinks=True)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- docker と WSL

def sh3(cmd, input=None, timeout=600, env=None, cwd=None, replace_env=False):
    """(終わりの符号, stdout, stderr)。replace_env なら、ホストの環境を引き継がずに env だけで起動する。"""
    p = subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                       env=dict(env or {}) if replace_env else dict(os.environ, **(env or {})))
    return p.returncode, p.stdout or "", p.stderr or ""


def sh(cmd, input=None, timeout=600, env=None, cwd=None):
    code, out, err = sh3(cmd, input=input, timeout=timeout, env=env, cwd=cwd)
    return code, out + err


def must(cmd, what, **kw):
    code, out = sh(cmd, **kw)
    if code != 0:
        raise RuntimeError(f"{what} failed (rc={code}): {out.strip()[-400:]}")
    return out


def ensure_agy(cfg):
    """配布物を取り、SHA-512 を照らしてから展開する（config の install）。展開した実行ファイルのパス。"""
    inst = cfg["install"]
    binary = AGY_CACHE / inst["version"] / "antigravity"
    if binary.is_file():
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    tgz = binary.parent / "cli.tar.gz"
    urllib.request.urlretrieve(inst["url"], tgz)   # noqa: S310 - 固定の配布の URL
    if hashlib.sha512(tgz.read_bytes()).hexdigest() != inst["sha512"]:
        tgz.unlink()
        raise RuntimeError("agy の配布物の SHA-512 が設定と違う")
    with tarfile.open(tgz) as t:
        t.extractall(binary.parent, filter="data")
    tgz.unlink()
    if not binary.is_file():
        raise RuntimeError(f"展開した中に antigravity が無い: {binary.parent}")
    return binary


def ensure_egress(cfg):
    """内部のネットワークと許可リストのプロキシを用意する（無ければ作る。許可の行を設定から作り直す）。"""
    net = cfg["network"]
    if sh(["docker", "network", "inspect", net["internal"]])[0] != 0:
        must(["docker", "network", "create", "--internal", net["internal"]], "network create")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shutil.copy(ASSETS / "egress.Dockerfile", d / "Dockerfile")
        shutil.copy(ASSETS / "tinyproxy.conf", d / "tinyproxy.conf")
        (d / "filter").write_text(filter_lines(net["allow"]), encoding="utf-8")
        must(["docker", "build", "-q", "-t", net["proxy_image"], str(d)], "egress build")
    sh(["docker", "rm", "-f", net["proxy_name"]])
    must(["docker", "run", "-d", "--name", net["proxy_name"], "--network", "bridge", net["proxy_image"]], "egress run")
    must(["docker", "network", "connect", net["internal"], net["proxy_name"]], "egress connect")
    return {"internal": net["internal"], "proxy": net["proxy_name"], "allow": net["allow"]}


def proxy_env(cfg):
    url = f"http://{cfg['network']['proxy_name']}:{cfg['network']['proxy_port']}"
    env = {k: url for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
    env.update(NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
    return env


def start_box(cfg, name, workspace):
    uid = cfg["scb"]["uid"]
    cmd = ["docker", "run", "-d", "--name", name, "--network", cfg["network"]["internal"], "-e", f"HOME={HOME}",
           "-v", f"{workspace}:{WORK}", "-w", WORK]
    for k, v in proxy_env(cfg).items():
        cmd += ["-e", f"{k}={v}"]
    must(cmd + [cfg["scb"]["image"], "sleep", "infinity"], "box run")
    must(["docker", "exec", name, "bash", "-c",
          f"mkdir -p {HOOKS_DIR} {CONFIG_DIR} {HOME}/.gemini/antigravity-cli && chown -R {uid}:{uid} {HOME}"], "box home")
    return name


def install(cfg, box, token_file=None, fake=False):
    """agy（本物か偽物）・フック・トークンを置く。フックの SHA-256 を返す。"""
    uid = cfg["scb"]["uid"]
    if fake:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as f:
            f.write(FAKE_AGY)
        must(["docker", "cp", f.name, f"{box}:/usr/local/bin/agy"], "fake agy copy")
        os.unlink(f.name)
    else:
        must(["docker", "cp", str(ensure_agy(cfg)), f"{box}:/usr/local/bin/agy"], "agy copy")
    must(["docker", "exec", box, "chmod", "755", "/usr/local/bin/agy"], "agy chmod")
    scripts = " ".join(f"{HOOKS_DIR}/{s}" for s in HOOK_SCRIPTS)
    for s in HOOK_SCRIPTS:
        must(["docker", "cp", str(ASSETS / s), f"{box}:{HOOKS_DIR}/{s}"], "hook copy")
    must(["docker", "cp", str(ASSETS / "hooks.json"), f"{box}:{CONFIG_DIR}/hooks.json"], "hooks copy")
    must(["docker", "exec", box, "bash", "-c",
          f"chown root:root {HOOKS_DIR} {scripts} {CONFIG_DIR} {CONFIG_DIR}/hooks.json && "
          f"chmod 755 {HOOKS_DIR} {scripts} && chmod 1777 {CONFIG_DIR} && chmod 444 {CONFIG_DIR}/hooks.json"],
         "hooks lock")
    if token_file:
        must(["docker", "cp", str(token_file), f"{box}:{TOKEN_PATH}"], "token copy")
        must(["docker", "exec", box, "bash", "-c", f"chown {uid}:{uid} {TOKEN_PATH} && chmod 600 {TOKEN_PATH}"], "token chmod")
    return sha256_file(ASSETS / "hooks.json")


def box_hooks_sha(box):
    out = must(["docker", "exec", box, "sha256sum", f"{CONFIG_DIR}/hooks.json"], "hooks sha")
    return out.split()[0]


def box_scratch(box):
    """コンテナの /tmp と実装役の ~/.cache の中の名前（境界の検査の残りを数える）。"""
    code, out = sh(["docker", "exec", box, "bash", "-c",
                    f"ls -A1 /tmp | sed 's,^,/tmp/,'; ls -A1 {HOME}/.cache 2>/dev/null | sed 's,^,{HOME}/.cache/,'"],
                   timeout=60)
    return set(out.split())


def net_check(cfg, box):
    """境界の検査。netcheck.py は標準入力で渡してコンテナに写さず、検査の一時のフォルダは消す。
    実装役が始まる前に、検査が /tmp と ~/.cache に何も残していないこと（residue が空）も合格の条件にする。"""
    uid = cfg["scb"]["uid"]
    before = box_scratch(box)
    code, out = sh(["docker", "exec", "-i", "--user", f"{uid}:{uid}", box, "python3", "-"],
                   input=(ASSETS / "netcheck.py").read_text(encoding="utf-8"), timeout=300)
    sh(["docker", "exec", box, "bash", "-c", "rm -rf /tmp/scb-netcheck-*"], timeout=60)
    residue = sorted(box_scratch(box) - before)
    return {"ok": "BOUNDARY_OK" in out and not residue, "tail": out.strip().splitlines()[-6:], "residue": residue}


def render_prompts(cfg, problem, out_dir):
    scb = cfg["scb"]
    env = {"PATH": scb["uv_bin"] + ":" + os.environ.get("PATH", ""), "SCBENCH_PROBLEMS_PATH": scb["problems"]}
    must([f"{scb['bench']}/.venv/bin/slop-code", "--quiet", "utils", "render-prompts", "-p", scb["prompt"], "-e",
          scb["env_config"].rsplit("/", 1)[-1].removesuffix(".yaml"), "-o", str(out_dir), "--problem", problem],
         "render-prompts", env=env, timeout=300, cwd=scb["bench"])
    return prompt_files(out_dir)


def run_agent(cfg, box, prompt, conversation_id, ttl, stream_path):
    uid = cfg["scb"]["uid"]
    argv = agy_stream.args(cfg, [cfg["cli"]], "", ttl, conversation_id=conversation_id)
    cmd = ["docker", "exec", "-i", "--user", f"{uid}:{uid}", "-e", f"HOME={HOME}", "-e", "AGY_CLI_DISABLE_AUTO_UPDATE=1",
           "-w", WORK, box] + argv
    t0 = time.time()
    extra = {}
    try:
        code, out = sh(cmd, input=agy_stream.stdin_for(cfg, prompt), timeout=ttl)
    except subprocess.TimeoutExpired as e:
        code, out = 124, timeout_output(e)
        extra["left_procs"] = stop_agent(cfg, box)
    Path(stream_path).write_text(out, encoding="utf-8")
    return {"rc": code, "seconds": round(time.time() - t0, 1), **extra}


def stop_agent(cfg, box, tries=10):
    """時間切れのとき、コンテナの中の実装役（uid 1000）のプロセスを止め、生きている残りの数を返す。docker exec の側が
    止まってもコンテナの中の agy は走り続け、H0 の次のチェックポイントや、作業場の写しと採点に混ざるため。
    止めたプロセスは回収されるまでゾンビ（State: Z）で /proc に残るので数えず、0 になるまで少し待って止め直す。"""
    uid = cfg["scb"]["uid"]
    live = ("for p in /proc/[0-9]*; do [ \"$(stat -c %u $p 2>/dev/null)\" = " + str(uid) + " ] || continue; "
            "s=$(awk '/^State:/{print $2}' $p/status 2>/dev/null); [ -n \"$s\" ] && [ \"$s\" != Z ] && echo $p; done")
    left = None
    for _ in range(tries):
        sh(["docker", "exec", "--user", f"{uid}:{uid}", box, "bash", "-c", "kill -9 -1"], timeout=60)
        left = len(sh(["docker", "exec", box, "bash", "-c", live], timeout=60)[1].split())
        if left == 0:
            break
        time.sleep(0.5)
    return left


def evaluate(cfg, problem, cp, snap, out_dir):
    """公式の eval-snapshot。UV_EXCLUDE_NEWER（採点の時刻）をホストの環境で渡し、時刻とイメージの ID を返す。"""
    scb = cfg["scb"]
    cutoff = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = Path(scb["bench"]) / scb["env_config"]
    env_cfg = Path(out_dir) / "env_os.yaml"
    env_cfg.write_text(env_config_with_os_env(base.read_text(encoding="utf-8")), encoding="utf-8")
    image_id = must(["docker", "image", "inspect", "-f", "{{.Id}}", scb["image"]], "image inspect").strip()
    if image_id != scb["image_id"]:
        raise RuntimeError(f"基本のイメージの ID が固定の値と違う: {image_id}")
    out_json = Path(out_dir) / f"eval_c{cp}.json"
    code, out, err = sh3([f"{scb['bench']}/.venv/bin/slop-code", "--quiet", "eval-snapshot", str(snap), "-o",
                          str(Path(out_dir) / f"eval_c{cp}"), "-p", problem, "-c", str(cp), "-e", str(env_cfg), "--json"],
                         env=eval_env(scb, cutoff), timeout=7200, cwd=scb["bench"], replace_env=True)
    try:
        data = json.loads(out) if code == 0 else None
    except ValueError:
        data = None
    if data is not None:
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rc": code, "cutoff": cutoff, "image_id": image_id, "summary": eval_summary(data) if data else None,
            "error": None if data else (err or out).strip()[-400:]}


def remove_box(box):
    sh(["docker", "rm", "-f", box])


# ---------------------------------------------------------------- 1 つの問題を回す

def run_problem(cfg, problem, cond, out_dir, fake=False, ttl=DEFAULT_TTL, upto=None, token_file=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    uid = cfg["scb"]["uid"]
    workspace = out_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    os.chown(workspace, uid, uid)
    prompts = render_prompts(cfg, problem, out_dir / "prompts")[:upto]
    egress = ensure_egress(cfg)
    rec = {"problem": problem, "cond": cond, "fake": fake, "model": cfg["model_name"], "egress": egress, "checkpoints": []}
    tag = f"scb-{problem.replace('_', '-')}-{cond.lower().replace('_', '-')}-{os.getpid()}"
    box, conv, hooks_expected = None, None, None
    try:
        for cp, prompt_file in enumerate(prompts, 1):
            c = {"checkpoint": cp, "prompt_sha256": sha256_file(prompt_file)}
            rec["checkpoints"].append(c)
            if not fake:
                c["quota_before"] = agy_quota.read()
                ok, why = quota_ok(c["quota_before"], cfg["quota_floor"])
                if not ok:
                    c["stopped"] = why
                    break
            if box is None or cond == "H0_fresh":
                if box:
                    remove_box(box)
                box = start_box(cfg, f"{tag}-c{cp}", workspace)
                c["hooks_sha256"] = hooks_expected = install(cfg, box, token_file=None if fake else token_file, fake=fake)
                c["network"] = net_check(cfg, box)
                if not c["network"]["ok"]:
                    raise RuntimeError(f"境界の検査に落ちた: {c['network']['tail']}")
                if cond == "H0_fresh":
                    conv = None
            stream = out_dir / f"stream_c{cp}.jsonl"
            c["agent"] = run_agent(cfg, box, prompt_file.read_text(encoding="utf-8"), conv, ttl, stream)
            text = stream.read_text(encoding="utf-8")
            parsed = agy_stream.parse(text, workdir=WORK)
            try:
                c["model_reported"] = model_pin.check_reported(cfg["model_name"], parsed["model"], f"{problem} c{cp}")
            except model_pin.ModelPinError as e:   # init の前に落ちた走りも、ここまでを記録してから止める
                c["model_reported"], c["model_error"] = parsed["model"], str(e)
            if cond == "H0":
                conv = parsed["conversation_id"] or conv
            c["conversation_id"] = parsed["conversation_id"]
            c["usage"] = parsed["usage"]
            c["outcome"] = parsed["outcome"]
            c["tools"] = sorted({s.get("tool") for s in parsed["steps"] if s.get("tool")})
            c["denied_tools"] = denied_tool_steps(parsed["steps"], step_errors(text))
            c["hooks_sha256_after"] = box_hooks_sha(box)
            c["reserved_path"] = reserved_present(workspace)
            c["valid"] = is_valid(c, hooks_expected)
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
            (out_dir / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        if box:
            remove_box(box)
        left = must(["docker", "ps", "-a", "--filter", f"name={tag}", "-q"], "ps").split()
        rec["boxes_left"] = len(left)
        (out_dir / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- 入口

def cmd_check(args):
    cfg = load_config()
    ensure_egress(cfg)
    box = start_box(cfg, f"scb-check-{os.getpid()}", tempfile.mkdtemp(prefix="scb-check-"))
    try:
        sha = install(cfg, box, fake=True)
        res = net_check(cfg, box)
        print(json.dumps({"hooks_sha256": sha, "network": res}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    finally:
        remove_box(box)


def cmd_run(args):
    cfg = load_config()
    token = None if args.fake else Path(args.token or cfg["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_problem(cfg, args.problem, args.cond, args.out, fake=args.fake, ttl=args.ttl, upto=args.upto, token_file=token)
    for c in rec["checkpoints"]:
        s = ((c.get("eval") or {}).get("summary")) or {}
        print(f"c{c['checkpoint']} valid={c.get('valid')} strict={s.get('strict')} iso={s.get('iso')} core={s.get('core')} "
              f"passed={s.get('passed')}/{s.get('total')} denied_tools={len(c.get('denied_tools') or [])} "
              f"denied_ran={sum(1 for d in c.get('denied_tools') or [] if d['ran'])} stopped={c.get('stopped')}")
    print(f"boxes_left={rec['boxes_left']}")
    return 0


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
    args = ap.parse_args(argv)
    return cmd_check(args) if args.cmd == "check" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
