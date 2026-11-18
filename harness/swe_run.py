"""§6-3 の最小の実行口：SWE-EVO の課題のコンテナで agy を 1 回走らせ、公式の採点まで通す（H0・H_cots。実験と計画 §6-3）。

    /opt/swe-evo/venv/bin/python -m harness.swe_run run --task <ID> --cond H0|H_cots --out <置き場> [--fake]

- コンテナの準備は S0 と同じ（swe_s0.prepare_box：`git reset --hard <base_commit>`・評価のスクリプトのテストの前の行・
  準備の終わりの環境）。版の割り当ては公式の評価（evaluate_instance.py）と同じ。準備が変えた追跡のファイル（pydantic の
  pdm.lock など）は、エージェントの前に元に戻す（採点の準備がもう一度変えるので、エージェントのパッチに入れない）。
- イメージの git には、先のリリースのコミットとタグが入っている。エージェントの前に、base_commit より先に届く参照を
  全部消して掃除し、end_version_commit が引けないことを確認する（採点は別の新しいコンテナで行う）。
- 準備と agy の入れる段はネットワーク越し（ブリッジ）。agy を走らせる前に、コンテナのネットワークを全部外し、
  コンテナの中の中継（FeatureBench の proxy_bridge.py）から、ホストの Unix ソケットの許可リストの CONNECT プロキシ
  （FeatureBench の network.py。agy の 3 つの宛先の 443 だけ）にだけ届くようにする。github.com・pypi.org などは届かない。
- プロンプト：H0 は FRAME の 1 文と課題の文（リリースノート）。H_cots はその末尾に COTS（課題によらない 1 段落）を足す。
- 走りの後に残すもの：stream-json の全文・パッチ・漏れの検査・軌跡の要約（コマンド・pytest の走り・ファイルの編集・
  最後のパッチのテストのファイルの足し・変え・消し）・t=0 の落ち（エージェントの pytest の走りの引数の .py のファイルと、
  落ちた項目のファイルを、S0 と同じ probe で t=0 のコンテナで走らせる。利用枠 0）・公式の採点（SWE-EVO の
  run_instances。予測のパッチに test_patch をつなぐ）。
- agy は、約 10 KB を超えるコマンドの出力の中ほどを切って返し、全文をコンテナの brain の下に置く（X-01・X-02 の
  記録では pytest の走りの約半分）。コンテナを閉じる前に brain を写し、切れた出力は全文から読む。
- 本物の agy では、走りの前後に Gemini の利用枠を /quota で読み（harness/agy_quota。トークン 0）、週の残り 0.35・
  5 時間の残り 0.15 を切っていたら走らせない。
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agy_stream  # noqa: E402
import exitcode  # noqa: E402
import fb_agy  # noqa: E402
import fb_gate_probe as gate  # noqa: E402
import swe_s0  # noqa: E402
from fb_replay import TOOLS_DIR, probe, sh, sh_split  # noqa: E402

FB_SRC = os.environ.get("FB_SRC", "/opt/fb/FeatureBench")   # FeatureBench（コミット 08b25d9）。プロキシと中継をそのまま使う
SOCKET_DIR = "/run/featurebench-network"
BRIDGE = "/installed-agent/fb_proxy_bridge.py"
NET_ENV = "/installed-agent/net_env.sh"
FAKE_TOKEN = "/opt/fb/fake_token"
BRAIN = fb_agy.TOKEN_DIR + "/brain"   # agy が切った出力の全文を置く所（<会話>/.system_generated/steps/<段>/output.txt）
TTL = 3600                       # 1 課題 60 分（設計 §5-1）
WEEKLY_FLOOR, FIVE_HOUR_FLOOR = 0.35, 0.15
T0_BUDGET = 600                  # 走らせたテストのファイルの t=0 の落ちを読む時間
CONDITIONS = ("H0", "H_cots")

FRAME = ("The repository in the current directory (/testbed) is at the previous release. "
         "Implement the changes described in the following release notes.")
COTS = ("Before you finish, run the existing tests that are related to the code you changed, and check the syntax of "
        "every Python file you changed (for example with `python -m py_compile <file>`). Fix any problems that these "
        "checks reveal.")

NET_CHECK = r'''
import socket, urllib.error, urllib.request
def via(url):
    try:
        urllib.request.urlopen(url, timeout=10)
        return "open"
    except urllib.error.HTTPError as e:
        return "open(%d)" % e.code
    except Exception as e:
        return "blocked(%s)" % type(e).__name__
def direct(host):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        return "open"
    except Exception as e:
        return "blocked(%s)" % type(e).__name__
for h in ("oauth2.googleapis.com", "pypi.org", "github.com"):
    print(h, via("https://%s/" % h), direct(h))
'''

FAKE_AGY_SWE = r'''#!/usr/bin/env python3
"""偽物の agy（§6-3 の実行口の確認用。利用枠を使わない）。本物と同じ形の stream-json を出し、道具の呼び出しを 3 つ真似る：
テストを走らせる（FAKE_PYTEST）・コードでないファイルを 1 つ書く・テストのファイルを 1 つ足す。届き方も報告する。
テストの出力が 2,000 字を超えたら、本物と同じ形で中ほどを切って返し、全文を brain の下に置く（本物は約 10 KB で切る）。"""
import json, os, pathlib, subprocess, sys, time, urllib.error, urllib.request
data = sys.stdin.read()
lines = [line for line in data.splitlines() if line.strip()]
prompt = json.loads(lines[0])["message"]["content"] if lines else ""
args = sys.argv[1:]
model = args[args.index("--model") + 1] if "--model" in args else "?"
token = pathlib.Path(os.environ.get("HOME", "/root")) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
def ev(name, body):
    print(json.dumps({"event": name, name: body}), flush=True)
def tool(i, name, params, output, secs):
    ev("step_update", {"step_index": i, "step_type": "tool", "tool_name": name, "state": "ACTIVE",
                       "tool_info": {"name": name, "parameters": params}})
    ev("step_update", {"step_index": i, "step_type": "tool", "tool_name": name, "state": "DONE", "duration_seconds": secs,
                       "tool_info": {"name": name, "parameters": params, "output": output}})
def reach(url):
    try:
        urllib.request.urlopen(url, timeout=8)
        return "open"
    except urllib.error.HTTPError as e:
        return "open(%d)" % e.code
    except Exception as e:
        return "blocked(%s)" % type(e).__name__
ev("init", {"model": model, "conversation_id": "fake-conv"})
cmd = "python -m pytest -p no:cacheprovider " + os.environ.get("FAKE_PYTEST", "")
t = time.time()
p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
full = "Command exited with code %d.\n\nCommand output:\n%s" % (p.returncode, p.stdout + p.stderr)
output = full
if len(full) > 2000:
    saved = pathlib.Path(os.environ.get("HOME", "/root")) / ".gemini/antigravity-cli/brain/fake-conv/.system_generated/steps/1"
    saved.mkdir(parents=True, exist_ok=True)
    (saved / "output.txt").write_text(full)
    output = ("The tool's output was truncated due to being too long. You can find the full output in a file at file://%s.\n\n"
              "Here is a preview with the middle of the output truncated:\n%s\n<truncated %d of %d bytes from the middle of the "
              "output>\n%s" % (saved / "output.txt", full[:1000], len(full) - 2000, len(full), full[-1000:]))
tool(1, "run_command", {"CommandLine": cmd}, output, round(time.time() - t, 2))
pathlib.Path("FAKE_AGY_CHANGE.txt").write_text("written by the fake agy\n")
tool(2, "write_file", {"Path": "/testbed/FAKE_AGY_CHANGE.txt"}, "ok", 0.01)
pathlib.Path("tests").mkdir(exist_ok=True)
pathlib.Path("tests/test_fake_agy.py").write_text("def test_fake():\n    assert True\n")
tool(3, "write_file", {"Path": "/testbed/tests/test_fake_agy.py"}, "ok", 0.01)
net = " ".join("%s=%s" % (h, reach("https://%s/" % h)) for h in ("oauth2.googleapis.com", "pypi.org", "github.com"))
ev("step_update", {"step_index": 4, "step_type": "agent_response", "state": "DONE",
                   "usage": {"input_tokens": 10, "output_tokens": 2, "thinking_tokens": 0, "cache_read_tokens": 0}})
ev("result", {"status": "SUCCESS", "conversation_id": "fake-conv",
              "response": "fake done: prompt %d chars, token %s, net %s" % (len(prompt), "present" if token.is_file() else "missing", net),
              "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})
'''


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_for(cond, problem_statement):
    """H0：FRAME と課題の文。H_cots：その末尾に COTS の 1 段落。"""
    if cond not in CONDITIONS:
        raise ValueError(f"unknown condition: {cond}")
    text = f"{FRAME}\n\n{(problem_statement or '').strip()}\n"
    return f"{text}\n{COTS}\n" if cond == "H_cots" else text


def official_row(row):
    """公式の評価（SWE-EVO の evaluate_instance.py の load_instances）と同じ版・テストのコマンド・ログの読み方を当てた行。"""
    from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
    from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
    d = dict(row)
    current, found = d.get("end_version") or d.get("version"), None
    for v in MAP_REPO_VERSION_TO_SPECS.get(d["repo"], {}):   # 公式と同じく、後に当たったものを使う
        if v in current:
            found = v
    if found is None:
        raise ValueError(f"no harness version for {d['instance_id']}")
    d["version"] = found
    d["test_cmds"] = MAP_REPO_VERSION_TO_SPECS[d["repo"]][found]["test_cmd"]
    d["log_parser"] = MAP_REPO_TO_PARSER[d["repo"]].__name__
    return d


def merge_patches(code_patch, test_patch):
    """SWE-EVO の evaluate_instance.py の merge_patches（order="test_then_code"）と同じ。"""
    def clean(x):
        if not x:
            return ""
        x = x.replace("\r\n", "\n").replace("\r", "\n")
        return x if x.endswith("\n") else x + "\n"
    parts = [p for p in (clean(test_patch), clean(code_patch)) if p]
    return ("\n".join(p.rstrip("\n") for p in parts) + "\n") if parts else ""


# ---------------------------------------------------------------- 軌跡の要約

_EXIT_RE = re.compile(r"Command exited with code (-?\d+)")
# コマンドの頭（行の頭か ; & | ( の後。環境変数の代入と timeout と python -m を挟んでよい）の pytest だけ。
# python -c の中の `import pytest` や `which pytest` は走りではない
_PYTEST_RE = re.compile(r"(?:^|[;&|(])\s*(?:\w+=\S*\s+)*(?:timeout\s+\S+\s+)?(?:\S*python[\d.]*\s+-m\s+)?pytest(?=\s|$|[;&|)])",
                        re.M)
_VALUE_OPTS = {"-k", "-m", "-p", "-o", "-c", "-n", "-W", "--deselect", "--ignore", "--rootdir", "--tb", "--timeout",
               "--junitxml", "--maxfail", "--durations"}
# 短い要約の行の項目（.py のファイルの名で始まるもの）。mlflow のプラグインは、行の頭と項目の間に「| MEM … GB |
# DISK … GB」を挟むので、| で始まる間だけは飛ばす。取り込んだログの行（ERROR    name:file.py:12 …）は項目にしない
_NODE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(?:\|[^\n]*?\s)?([\w./-]+\.py(?:::\S*)?)(?=\s|$)", re.M)
_SUMMARY_RE = re.compile(r"^=*\s*((?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, )?)+"
                         r" in [\d.]+s|no tests ran in [\d.]+s)", re.M)
_TRUNC_RE = re.compile(r"output was truncated.*?full output in a file at file://(/\S+?\.txt)", re.S)
_CUT_RE = re.compile(r"[^\n]*\n<truncated \d+ of \d+ bytes from the middle of the output>\n[^\n]*")   # 切れ目の前後の欠けた行


def _rel(path):
    p = str(path or "").strip()
    return p[len("/testbed/"):] if p.startswith("/testbed/") else p


def test_targets(cmd):
    """pytest のコマンドの、テストのファイルかディレクトリの引数（:: より前。/testbed/ を外す）。"""
    try:
        tokens = shlex.split(cmd.replace(";", " ; "), posix=True)   # -k の式のような引用符の中の空白を切らない
    except ValueError:
        tokens = cmd.replace(";", " ").split()
    out, skip = [], False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok in _VALUE_OPTS:   # 値を取る旗の次の語（-k の式など）は対象ではない
            skip = True
            continue
        t = _rel(tok.strip("'\"").split("::")[0])
        if t.startswith("-") or not t:
            continue
        if t.endswith(".py") or "tests" in t.split("/") or t.startswith("test"):
            if t not in out:
                out.append(t)
    return out


def saved_output(brain_dir):
    """agy が切った出力の全文（file:// の名）を、コンテナから写した brain の下から読む関数。brain の外と無いものは None。"""
    root = Path(brain_dir).resolve()

    def read(path):
        if not path.startswith(BRAIN + "/"):
            return None
        p = (root / path[len(BRAIN) + 1:]).resolve()
        if root not in p.parents or not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    return read


def trajectory(stream_text, full_output=None):
    """stream-json → {"commands": [...], "edits": [...], "summary": {...}}。コマンドの全文（500 字まで）・秒・終わりの符号、
    pytest の走りは対象と落ちた項目と要約の行、ファイルの編集はテストのファイルか。agy が中ほどを切った出力は、
    full_output（file:// の名 → 全文か None）で全文を読む。読めなければ、切れ目の前後の欠けた行を外した残りから読む。"""
    commands, edits, reads = [], [], 0
    for line in (stream_text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        su = e.get("step_update") or {}
        if e.get("event") != "step_update" or su.get("step_type") != "tool" or su.get("state") not in ("DONE", "ERROR"):
            continue
        ti = su.get("tool_info") or {}
        params = ti.get("parameters") if isinstance(ti.get("parameters"), dict) else {}
        name = su.get("tool_name") or ti.get("name")
        secs = su.get("duration_seconds")
        secs = round(float(secs), 1) if secs is not None else None
        if name == "run_command":
            cmd = str(params.get("CommandLine") or "")
            out = str(ti.get("output") or ti.get("error") or "")
            m = _EXIT_RE.search(out)
            rec = {"step": su.get("step_index"), "command": cmd[:500], "seconds": secs, "exit": int(m.group(1)) if m else None}
            cut = _TRUNC_RE.search(out[:1000])
            if cut:
                full = full_output(cut.group(1)) if full_output else None
                rec["truncated"] = "full" if full is not None else "preview"
                out = full if full is not None else _CUT_RE.sub("\n", out)
            if _PYTEST_RE.search(cmd):
                summ = _SUMMARY_RE.findall(out)
                targets, failed = test_targets(cmd), list(dict.fromkeys(_NODE_RE.findall(out)))
                if not failed and rec["exit"] not in (None, 0, 1, 5, 124):
                    # 項目の行の無い終わり（符号 4：conftest の import の誤りなど）。probe と同じく、ファイルの単位の落ちにする
                    failed = [t for t in targets if t.endswith(".py")]
                    rec["file_level"] = True
                rec.update(pytest=True, targets=targets, failed=failed, summary=summ[-1].strip() if summ else "")
            commands.append(rec)
        elif name in ("edit_file", "write_file"):
            path = _rel(params.get("Path") or params.get("TargetFile") or params.get("AbsolutePath"))
            edits.append({"step": su.get("step_index"), "tool": name, "path": path, "test": gate.is_test_file(path),
                          "state": su.get("state")})
        elif name == "read_file":
            reads += 1
    runs = [c for c in commands if c.get("pytest")]
    return {"commands": commands, "edits": edits, "summary": {
        "commands": len(commands), "command_seconds": round(sum(c["seconds"] or 0 for c in commands), 1),
        "pytest_runs": len(runs), "pytest_seconds": round(sum(c["seconds"] or 0 for c in runs), 1),
        "pytest_runs_with_failures": sum(1 for c in runs if c["failed"]),
        "truncated_outputs": sum(1 for c in commands if c.get("truncated")),
        "truncated_without_full": sum(1 for c in commands if c.get("truncated") == "preview"),
        "reads": reads, "edited_files": len({x["path"] for x in edits if x["state"] == "DONE"}),
        "edited_test_files": sorted({x["path"] for x in edits if x["test"] and x["state"] == "DONE"}),
        "failed_edits": sum(1 for x in edits if x["state"] != "DONE")}}   # 本物の記録では edit_file の誤りが返ることがある


def patch_summary(name_status):
    """`git diff --name-status` → 変えたファイルの数と、テストのファイルの足し・変え・消し、コードのファイル。"""
    out = {"files": 0, "added_tests": [], "modified_tests": [], "deleted_tests": [], "code_files": []}
    for line in (name_status or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0][:1], parts[-1]
        out["files"] += 1
        if gate.is_test_file(path):
            out[{"A": "added_tests", "D": "deleted_tests"}.get(status, "modified_tests")].append(path)
        else:
            out["code_files"].append(path)
    return out


def t0_files(commands):
    """t=0 で走らせるテストのファイル：pytest の走りの引数の .py のファイルと、落ちた項目のファイル。ディレクトリを指した
    走りや引数の無い走りでも、落ちた項目からファイルを拾う（t0_overlap が見るのは落ちた項目だけ）。"""
    files = set()
    for c in commands:
        if c.get("pytest"):
            files.update(t for t in c.get("targets") or [] if t.endswith(".py"))
            files.update(f for f in (_rel(n).split("::")[0] for n in c.get("failed") or []) if f.endswith(".py"))
    return sorted(files)


def t0_overlap(commands, t0_failed):
    """pytest の走りの落ちのうち、t=0 でも落ちる項目（環境や古いテストの落ち）だけだった走りの数と秒。"""
    pre = set(t0_failed or [])
    runs = [c for c in commands if c.get("pytest") and c.get("failed")]
    only = [c for c in runs if all(_rel(n) in pre for n in c["failed"])]
    return {"runs_with_failures": len(runs), "runs_only_t0_failures": len(only),
            "seconds_only_t0_failures": round(sum(c["seconds"] or 0 for c in only), 1)}


def quota_ok(rec):
    b = rec.get("buckets") or {}
    w = (b.get("gemini-weekly") or {}).get("remaining_fraction")
    h = (b.get("gemini-5h") or {}).get("remaining_fraction")
    if w is None or h is None:
        return False, "quota not readable"
    if w < WEEKLY_FLOOR:
        return False, f"weekly remaining {w:.3f} < {WEEKLY_FLOOR}"
    if h < FIVE_HOUR_FLOOR:
        return False, f"5h remaining {h:.3f} < {FIVE_HOUR_FLOOR}"
    return True, ""


# ---------------------------------------------------------------- コンテナの中の手順

def strip_future(box, base, end_commit):
    """base_commit より先に届く参照（枝・タグ・リモート・stash）を全部消し、掃除する。end_commit が引けたら止める。"""
    cmd = (f"cd /testbed && git checkout -q --detach {base} && "
           "git for-each-ref --format='%(refname)' | while read -r r; do git update-ref -d \"$r\"; done && "
           "rm -f .git/FETCH_HEAD .git/ORIG_HEAD .git/MERGE_HEAD && "
           "git reflog expire --expire=now --all && git gc -q --prune=now")
    code, out = box.exec(cmd, timeout=1800)
    if code != 0:
        raise RuntimeError(f"history strip failed: {out[-300:]}")
    _, stdout, _ = box.exec_split(f"cd /testbed && echo $(git rev-parse HEAD) $(git rev-list --all | wc -l) "
                                  f"$(git tag | wc -l) $(git cat-file -e {end_commit} 2>/dev/null && echo present || echo absent)")
    head, commits, tags, end = (stdout.split() + ["", "", "", ""])[:4]
    if head != base or end != "absent":
        raise RuntimeError(f"history strip check failed: head={head[:12]} end={end}")
    return {"commits": int(commits or 0), "tags": int(tags or 0), "end_commit": end}


def place_token(box, token_file):
    """認証のトークンを、コンテナの HOME の下に置く（中身は記録に残さない。/testbed の外）。"""
    box.exec(f"mkdir -p {fb_agy.TOKEN_DIR} && chmod 700 {fb_agy.TOKEN_DIR}")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / fb_agy.TOKEN_NAME
        tmp.write_bytes(Path(token_file).read_bytes())
        os.chmod(tmp, 0o600)
        code, out = sh(["docker", "cp", str(tmp), f"{box.name}:{fb_agy.TOKEN_DIR}/{fb_agy.TOKEN_NAME}"])
    if code != 0:
        raise RuntimeError(f"token copy failed: {out[-200:]}")
    box.exec(f"chmod 600 {fb_agy.TOKEN_DIR}/{fb_agy.TOKEN_NAME}")


def isolate(box):
    """中継を起こしてプロキシの環境を NET_ENV に書き、コンテナのネットワークを全部外す（FeatureBench の isolate と同じ手順）。"""
    box.exec(f"mkdir -p /installed-agent {fb_agy.LOG_DIR}")
    code, out = sh(["docker", "cp", f"{FB_SRC}/featurebench/infer/proxy_bridge.py", f"{box.name}:{BRIDGE}"])
    if code != 0:
        raise RuntimeError(f"bridge copy failed: {out[-200:]}")
    cmd = ("rm -f /installed-agent/fb-proxy-port; "
           "py=\"$(command -v python3 || command -v python || echo /opt/miniconda3/bin/python3)\"; "
           f"nohup \"$py\" {BRIDGE} --socket {SOCKET_DIR}/proxy.sock --port-file /installed-agent/fb-proxy-port "
           f"< /dev/null > {fb_agy.LOG_DIR}/fb_proxy_bridge.log 2>&1 & "
           "for i in $(seq 1 100); do [ -s /installed-agent/fb-proxy-port ] && break; sleep 0.05; done; "
           "[ -s /installed-agent/fb-proxy-port ] || exit 1; port=$(cat /installed-agent/fb-proxy-port); "
           "{ for v in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do echo \"export $v=http://127.0.0.1:$port\"; done; "
           "echo 'export NO_PROXY=localhost,127.0.0.1,::1'; echo 'export no_proxy=localhost,127.0.0.1,::1'; "
           f"echo 'unset ALL_PROXY all_proxy'; }} > {NET_ENV}")
    code, out = box.exec(cmd)
    if code != 0:
        raise RuntimeError(f"proxy bridge failed: {out[-300:]}")
    _, nets, _ = sh_split(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", box.name])
    for net in json.loads(nets or "{}") or {}:
        sh(["docker", "network", "disconnect", "-f", net, box.name])
    _, nets, _ = sh_split(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", box.name])
    left = sorted(json.loads(nets or "{}") or {})
    if left:
        raise RuntimeError(f"networks left after isolation: {left}")


def agent_env():
    return f"{swe_s0.CONDA} && {swe_s0.PRELUDE} && source {NET_ENV}"


def net_check(box):
    """エージェントと同じ環境から、プロキシ越しと直の届き方を読む。{宛先: {"proxy", "direct"}}。"""
    box.put("/installed-agent/net_check.py", NET_CHECK)
    _, out = box.exec(f"{agent_env()} && python /installed-agent/net_check.py")
    res = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            res[parts[0]] = {"proxy": parts[1], "direct": parts[2]}
    return res


def net_ok(check):
    """oauth2 はプロキシ越しで届き、pypi と github はプロキシ越しでも届かず、どの宛先も直には届かない。"""
    try:
        return (check["oauth2.googleapis.com"]["proxy"].startswith("open")
                and all(check[h]["proxy"].startswith("blocked") for h in ("pypi.org", "github.com"))
                and all(v["direct"].startswith("blocked") for v in check.values()))
    except KeyError:
        return False


def run_command(imp, fake_target=None):
    argv = agy_stream.args(imp, [fb_agy.NAME], "", TTL)
    extra = f"FAKE_PYTEST={shlex.quote(fake_target)} " if fake_target else ""
    return (f"set -o pipefail; {agent_env()} && cd /testbed && {extra}HOME={fb_agy.HOME} AGY_CLI_DISABLE_AUTO_UPDATE=1 "
            f"{shlex.join(argv)} < {fb_agy.PROMPT_PATH} | tee {fb_agy.STREAM_PATH} > /dev/null")


# ---------------------------------------------------------------- 採点と t=0 の落ち

def score(inst, model_patch, run_id):
    """公式の採点（SWE-EVO の run_instances と get_eval_report）。予測のパッチに test_patch をつなぐ。"""
    from swebench.harness.constants import KEY_INSTANCE_ID, KEY_MODEL, KEY_PREDICTION, RUN_EVALUATION_LOG_DIR
    from swebench.harness.grading import get_eval_report
    from swebench.harness.run_evaluation import run_instances
    from swebench.harness.test_spec.test_spec import make_test_spec
    iid = inst["instance_id"]
    pred = {KEY_MODEL: run_id, KEY_PREDICTION: merge_patches(model_patch, inst["test_patch"]), KEY_INSTANCE_ID: iid}
    t = time.time()
    run_instances({iid: pred}, [inst], "instance", False, False, 1, run_id, 1800, namespace=None,
                  instance_image_tag="latest", rewrite_reports=False)
    log_fp = Path(RUN_EVALUATION_LOG_DIR) / run_id / run_id / iid / "test_output.txt"
    res = {"secs": round(time.time() - t, 1), "log": str(log_fp.resolve())}
    try:
        r = get_eval_report(make_test_spec(inst, namespace=None), pred, str(log_fp), include_tests_status=True)[iid]
        st = r.get("tests_status") or {}
        f2p, p2p = st.get("FAIL_TO_PASS") or {}, st.get("PASS_TO_PASS") or {}
        res.update({"applied": r.get("patch_successfully_applied"), "resolved": r.get("resolved"),
                    "f2p_success": f2p.get("success") or [], "f2p_failure": f2p.get("failure") or [],
                    "p2p_success": len(p2p.get("success") or []), "p2p_failure": (p2p.get("failure") or [])[:200]})
    except Exception as e:   # 記録して返す
        res["error"] = repr(e)[:300]
    return res


def t0_failures(inst, commands):
    """t0_files のファイルのうち t=0 にあるものを、t=0 のコンテナで走らせ、落ちる項目を読む。"""
    files = t0_files(commands)
    if not files:
        return {"files": [], "failed": []}
    box, info = swe_s0.start_box(inst, "t0chk")
    try:
        existing = [f for f in files if box.exec(f"test -f /testbed/{shlex.quote(f)}")[0] == 0]
        res = probe(box, {"root": "/testbed", "packages": [inst["repo"].split("/")[-1]], "checks": ["tests"],
                          "test_files": existing, "pyflakes_dir": TOOLS_DIR, "test_cmd": swe_s0.test_cmd_of(inst),
                          "timeout_one": swe_s0.PER_TEST_TIMEOUT if info["pytest_timeout"] else None,
                          "file_timeout": 300, "test_budget": T0_BUDGET, "strict_budget": True}, prelude=swe_s0.PRELUDE)
    finally:
        box.close()
    tests = res.get("tests") or {}
    return {"files": existing, "missing_at_t0": sorted(set(files) - set(existing)),
            "failed": [x["node"] for x in tests.get("failed") or []], "not_run": tests.get("not_run") or [],
            "secs": (res.get("secs") or {}).get("tests"), "error": res.get("error")}


def quota_read():
    import agy_quota
    rec = agy_quota.read()
    rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return rec


# ---------------------------------------------------------------- 1 回の走り

def run_one(row, cond, out_dir, fake=False, token_file=None, t0_check=True, fake_target=None):
    from featurebench.infer.network import ApiEndpoint, _AllowlistUnixProxyServer   # PYTHONPATH に FB_SRC を足して走らせる
    agent_cfg = fb_agy.load_json(fb_agy.AGENT_CONFIG)
    imp = fb_agy.agent_settings(agent_cfg, agent_cfg["model_name"])
    inst = official_row(row)
    iid = inst["instance_id"]
    out = Path(out_dir) / f"{iid}__{cond}{'__fake' if fake else ''}"
    out.mkdir(parents=True, exist_ok=True)
    prompt = prompt_for(cond, inst["problem_statement"])
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    rec = {"id": iid, "cond": cond, "fake": fake, "model_requested": imp["model_name"], "ttl": TTL,
           "prompt_sha256": sha256(prompt), "frame_sha256": sha256(FRAME), "cots_sha256": sha256(COTS)}
    token_file = Path(token_file or (FAKE_TOKEN if fake else agent_cfg["token"]))

    def save():
        (out / "run_record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    if not fake:
        rec["quota_before"] = quota_read()
        ok, why = quota_ok(rec["quota_before"])
        if not ok:
            rec["stopped"] = why
            save()
            return rec
    sock_dir = tempfile.TemporaryDirectory(prefix="swe-api-only-")
    sock = Path(sock_dir.name) / "proxy.sock"
    proxy = _AllowlistUnixProxyServer(str(sock), [ApiEndpoint(h, 443) for h in fb_agy.AGY_HOSTS], None,
                                      logging.getLogger("swe_run.proxy"))
    sock.chmod(0o777)
    threading.Thread(target=proxy.serve_forever, name="swe-run-proxy", daemon=True).start()
    box, patch, stream, traj = None, "", "", {"commands": [], "edits": [], "summary": {}}
    try:
        box, info = swe_s0.prepare_box(inst, cond.lower(), volumes=[(sock_dir.name, SOCKET_DIR, "ro")], prefix="h63")
        rec["setup"] = {k: info.get(k) for k in ("setup_rc", "setup_secs", "reset_discarded", "setup_created")}
        _, st = box.exec("cd /testbed && git status --porcelain --untracked-files=no")
        rec["setup_tracked_restored"] = [ln for ln in st.splitlines() if ln.strip()]
        box.exec("cd /testbed && git checkout -q -- .")
        rec["history"] = strip_future(box, inst["base_commit"], inst["end_version_commit"])
        script = ("set -eu\ncat > /usr/local/bin/agy << 'FAKEEOF'\n" + FAKE_AGY_SWE + "FAKEEOF\nchmod 755 /usr/local/bin/agy\n"
                  if fake else fb_agy.install_script(agent_cfg))
        code, o = box.exec(script, timeout=900)
        if code != 0:
            raise RuntimeError(f"agy install failed: {o[-300:]}")
        place_token(box, token_file)
        box.exec(f"mkdir -p {fb_agy.LOG_DIR}")
        box.put(fb_agy.PROMPT_PATH, agy_stream.stdin_for(imp, prompt))
        isolate(box)
        rec["network_check"] = net_check(box)
        rec["network_ok"] = net_ok(rec["network_check"])
        if not rec["network_ok"]:
            raise RuntimeError(f"network check failed: {rec['network_check']}")
        t = time.time()
        code, _ = box.exec(run_command(imp, fake_target if fake else None), timeout=TTL + 300)
        rec.update(agy_exit=code, agent_wall=round(time.time() - t, 1))
        if code == 124:
            box.exec("pkill -9 -f /usr/local/bin/agy; true")
        stream = box.get(fb_agy.STREAM_PATH) or ""
        (out / "agy_stream.jsonl").write_text(stream, encoding="utf-8")
        parsed = agy_stream.parse(stream)
        rec.update(model_reported=parsed["model"], model_ok=parsed["model"] == imp["model_name"], usage=parsed["usage"],
                   outcome=parsed["outcome"], complete=parsed["complete"], steps=len(parsed["steps"]),
                   response=(parsed["response"] or "")[:2000])
        brain = out / "agy_brain"   # 切れた出力の全文（トークンは brain の外にあるので写らない）
        shutil.rmtree(brain, ignore_errors=True)
        rec["brain_copied"] = sh(["docker", "cp", f"{box.name}:{BRAIN}", str(brain)])[0] == 0
        traj = trajectory(stream, saved_output(brain))
        (out / "trajectory.json").write_text(json.dumps(traj, ensure_ascii=False, indent=1), encoding="utf-8")
        rec["trajectory"] = traj["summary"]
        code, patch, err = box.exec_split(f"cd /testbed && git add -A && git diff --cached --binary {inst['base_commit']}")
        if code != 0:
            raise RuntimeError(f"patch collect failed: {err[-200:]}")
        _, ns, _ = box.exec_split(f"cd /testbed && git diff --cached --name-status {inst['base_commit']}")
        (out / "model.patch").write_text(patch, encoding="utf-8")
        rec["patch"] = dict(patch_summary(ns), bytes=len(patch))
        saved = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (brain.rglob("*") if brain.is_dir() else [])
                        if p.is_file())
        rec["leaks"] = fb_agy.leak_problems(patch, stream + "\n" + saved, token_file.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:   # 記録して、採点の前に止める
        rec["error"] = repr(e)[:500]
        save()
        raise
    finally:
        if box is not None:
            box.close()
        proxy.shutdown()
        proxy.server_close()
        sock_dir.cleanup()
    save()
    if t0_check:
        rec["t0_check"] = t0_failures(inst, traj["commands"])
        rec["t0_overlap"] = t0_overlap(traj["commands"], rec["t0_check"].get("failed"))
        save()
    rec["eval"] = score(inst, patch, f"h63_{cond}_{'fake_' if fake else ''}{int(time.time())}")
    if not fake:
        rec["quota_after"] = quota_read()
    save()
    return rec


def cmd_run(args):
    rows = swe_s0.load_rows([args.task])
    args.out = str(Path(args.out).resolve())
    Path(args.out).mkdir(parents=True, exist_ok=True)
    os.chdir(args.out)   # 公式の採点の記録（logs/run_evaluation）を置き場の下に書く
    r = run_one(rows[args.task], args.cond, args.out, fake=args.fake, token_file=args.token_file,
                t0_check=not args.no_t0_check, fake_target=args.fake_pytest)
    e = r.get("eval") or {}
    print(f"{r['id']} {r['cond']}{' fake' if r['fake'] else ''} exit={r.get('agy_exit')} wall={r.get('agent_wall')}s "
          f"model_ok={r.get('model_ok')} network_ok={r.get('network_ok')} history={r.get('history')} "
          f"patch={r.get('patch', {}).get('files')} files (added tests {len(r.get('patch', {}).get('added_tests') or [])}) "
          f"leaks={len(r.get('leaks') or [])} trajectory={r.get('trajectory')} t0_overlap={r.get('t0_overlap')} "
          f"eval: applied={e.get('applied')} resolved={e.get('resolved')} f2p={len(e.get('f2p_success') or [])}/"
          f"{len(e.get('f2p_success') or []) + len(e.get('f2p_failure') or [])} secs={e.get('secs')} err={e.get('error')}",
          flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.swe_run", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--task", required=True)
    r.add_argument("--cond", choices=CONDITIONS, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--fake-pytest", default="", help="偽物の agy が走らせるテスト（軌跡と t=0 の読みの確認用）")
    r.add_argument("--token-file")
    r.add_argument("--no-t0-check", action="store_true")
    args = ap.parse_args(argv)
    return {"run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
