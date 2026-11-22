"""実装役 agy の実行口（設計 §5-1 のエージェントの実行口。実験と計画 §6-3〜6-5）。ベンチに依らない。

- 設定（config/agy_sandbox.json）：agy の起動の仕方とモデル（model_name で正確な ID を固定。harness/model_pin.py）・
  配布物の固定・コンテナの uid と HOME と作業場・境界の許可リスト・利用枠の下限
- 配布物（取るときに配布の SHA-512 を、走りのたびに展開した実行ファイルの SHA-512 を照らす）・フック（Web の道具と下位エージェントを PreToolUse で止める。root の持ち物で書き換え不可）・
  トークン・偽物の agy（利用枠 0）をコンテナに置く
- AgyPort：start(文, 残り秒) は新しい会話、send(文, 残り秒) は同じ会話の続き（--conversation）。どちらも harness/core.py の
  Turn を返す（run_unit の port の形）。Turn.ok は、agy が手番を終えて答えを返し（終わりの符号 0・result あり・SUCCESS）、
  時間切れでなく、走りの後の検査（モデル・止めた道具・フック）に通ったときだけ True。そうでなければ理由を note に書く。
  ターンごとの詳しい記録は records に残す：終わりの符号・秒・時間切れの種類・stream-json の置き場・報告されたモデルと
  照合の誤り・会話・利用量・道具・止めた道具の手番・フックの SHA-256・有効か
- 時間切れは 2 種類：print_timeout は agy が --print-timeout（残り秒 − 20）で自分で止まったもの（1.5.3 は result の
  finished_reason と終わりの符号 3）、hard は実行口の上限（TimeoutExpired。符号 124。出力は文字列に直して全文を残す）。
  どちらも、コンテナの中の uid のプロセスを止めて残りを数える
- 止めた道具：手番が DONE で終わった（道具が走った）ものだけを ran にする。フックの deny はエラーの手番（hook_denied）
"""
import hashlib
import json
import os
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
import core  # noqa: E402
import model_pin  # noqa: E402
import sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "agy_sandbox.json"
ASSETS = sandbox.ASSETS
# 止める道具（§6-3）。agy 1.5.3 の CLI がモデルに渡す道具は、実行ファイルの oneharness の組（14）と見られ、そのうち止めるのは
# search_web・invoke_subagent・define_subagent・manage_subagents（2026-11-22 の deny の確認と、走りに出た道具の名前から）。
# 残り（init が返す IDE の側の道具の一覧（60）の名前と、実行ファイルの中の名前の read_url・webview_browser）は、今の CLI では
# モデルに渡らないが、版が変わっても止まるよう表に残す。Web の道具は外から取るもの（search_web は検索をサーバーの側で行い、
# コンテナの境界を通らない）とブラウザ系。下位エージェントとワークフローの口は、別のモデルやエージェントに仕事を渡せ、その
# ステップは親の stream-json に出ないので、モデルの照合と走りの後の検査が及ばない
BROWSER_TOOLS = (
    "open_browser_url", "browser_subagent", "webview_browser",
    "browser_click_element", "browser_drag_pixel_to_pixel", "browser_get_dom", "browser_get_network_request",
    "browser_input", "browser_list_network_requests", "browser_mouse_down", "browser_mouse_up", "browser_move_mouse",
    "browser_press_key", "browser_refresh_page", "browser_resize_window", "browser_scroll", "browser_scroll_dom",
    "browser_select_option", "capture_browser_console_logs", "capture_browser_screenshot", "click_browser_pixel",
    "execute_browser_javascript", "list_browser_pages", "read_browser_page")
WEB_TOOLS = ("search_web", "read_url_content", "read_url", "search_marketplace") + BROWSER_TOOLS
SUBAGENT_TOOLS = ("invoke_subagent", "define_subagent", "manage_subagents", "run_workflow")
DENIED_TOOLS = WEB_TOOLS + SUBAGENT_TOOLS
HOOK_DENIED = "denied by pre-tool hook"   # agy 1.5.3 が deny を返す文言（"Tool call denied by pre-tool hook: %s"）
SUBAGENT_HOOK_REASON = "Subagents are not available in this environment."   # deny_subagent.sh の理由の文（試験で照らす）
HARD_TIMEOUT_RC = 124                       # 実行口の上限で止めたとき（TimeoutExpired）に記録する符号
PRINT_TIMEOUT_RC = 3                        # agy 1.5.3 が --print-timeout で切れたときの符号（変更履歴 1.5.0）
PRINT_TIMEOUT_MARK = "[agy] print timeout"  # そのときの stderr の行の頭（同）
HOOKS_DIR = "/opt/agy-hooks"
HOOK_SCRIPTS = ("deny_web.sh", "deny_subagent.sh")
AGY_BIN = "/usr/local/bin/agy"

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
ev("result", {"status": "SUCCESS", "conversation_id": conv, "response": "fake done; net %s" % json.dumps(net),
              "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})
'''


# ---------------------------------------------------------------- 設定・配布物・フック

def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    model_pin.require_implementer(cfg["agent"], f"{path} の agent")
    return cfg


def file_sha512(path):
    """ファイルの SHA-512（1 MiB ずつ読む。agy の実行ファイルは 200 MB を超える）。"""
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_binary(binary, inst):
    """展開した実行ファイルを、設定に固定した SHA-512（agent.install.binary_sha512）と照らす。違うか固定の値が無ければ
    止める（取った後に書き換わった・別の版。root は chmod 555 を越えて書けるので、守りを権限に頼らない）。"""
    want = inst.get("binary_sha512")
    if not want:
        raise RuntimeError("設定に agent.install.binary_sha512 が無い（agy の実行ファイルを照らせない）")
    got = file_sha512(binary)
    if got != want:
        raise RuntimeError(f"agy の実行ファイルの SHA-512 が設定と違う: {binary}（{got[:16]}… ≠ {want[:16]}…）")
    return binary


def ensure_agy(agent):
    """配布物を取り、SHA-512 を照らしてから展開する（agent.install）。展開した実行ファイルは、取った後も呼ばれるたびに
    （走りのたびに）SHA-512 を照らす（check_binary）。展開した実行ファイルのパス。"""
    inst = agent["install"]
    binary = Path(agent["cache"]) / inst["version"] / "antigravity"
    if binary.is_file():
        return check_binary(binary, inst)
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
    return check_binary(binary, inst)


def dirs(sbx):
    home = sbx["home"]
    return {"config": home + "/.gemini/config", "cli": home + "/.gemini/antigravity-cli",
            "token": home + "/.gemini/antigravity-cli/antigravity-oauth-token"}


def install(cfg, box, token_file=None, fake=False):
    """agy（本物か偽物）・フック・トークンをコンテナに置く。フックの SHA-256 を返す。"""
    sbx, d = cfg["sandbox"], dirs(cfg["sandbox"])
    uid, home = sbx["uid"], sbx["home"]
    if fake:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as f:
            f.write(FAKE_AGY)
        sandbox.copy_in(box, f.name, AGY_BIN, "fake agy copy")
        os.unlink(f.name)
    else:
        sandbox.copy_in(box, ensure_agy(cfg["agent"]), AGY_BIN, "agy copy")
    sandbox.must_root(box, f"chmod 755 {AGY_BIN} && mkdir -p {HOOKS_DIR} {d['config']} {d['cli']} && "
                           f"chown -R {uid}:{uid} {home}", "agy home")
    scripts = " ".join(f"{HOOKS_DIR}/{s}" for s in HOOK_SCRIPTS)
    for s in HOOK_SCRIPTS:
        sandbox.copy_in(box, ASSETS / s, f"{HOOKS_DIR}/{s}", "hook copy")
    sandbox.copy_in(box, ASSETS / "hooks.json", f"{d['config']}/hooks.json", "hooks copy")
    sandbox.must_root(box, f"chown root:root {HOOKS_DIR} {scripts} {d['config']} {d['config']}/hooks.json && "
                           f"chmod 755 {HOOKS_DIR} {scripts} && chmod 1777 {d['config']} && "
                           f"chmod 444 {d['config']}/hooks.json", "hooks lock")
    if token_file:
        sandbox.copy_in(box, token_file, d["token"], "token copy")
        sandbox.must_root(box, f"chown {uid}:{uid} {d['token']} && chmod 600 {d['token']}", "token chmod")
    return hashlib.sha256((ASSETS / "hooks.json").read_bytes()).hexdigest()


def hooks_sha(box, sbx):
    out = sandbox.must(["docker", "exec", "--user", "0:0", box, "sha256sum", f"{dirs(sbx)['config']}/hooks.json"], "hooks sha")
    return out.split()[0]


# ---------------------------------------------------------------- 走りの後の検査（docker に依らない）

def _step_updates(stream_text):
    """stream-json の step_update のイベントの中身（dict）を順に返す。"""
    for line in (stream_text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        su = e.get("step_update") if e.get("event") == "step_update" else None
        if isinstance(su, dict):
            yield su


def step_errors(stream_text):
    """stream-json の手番ごとのエラーの文の先頭 1 行（agy は道具のエラーを state: ERROR と tool_info.error で返す）。"""
    out = {}
    for su in _step_updates(stream_text):
        err = (su.get("tool_info") or {}).get("error")
        msg = err.get("message") if isinstance(err, dict) else None
        if isinstance(msg, str) and msg.strip():
            out[su.get("step_index")] = msg.strip().splitlines()[0][:200]
    return out


def subagent_steps(stream_text):
    """下位エージェントの形のイベント（step_type subagent）が出たステップ。{ステップ: subagent_info の subagents の数（無ければ
    None）}。agy 1.5.3 は、フックが止めた invoke_subagent のステップを、ERROR のイベントで step_type subagent・subagents 空で
    返し、tool_info（エラーの文）を付けない（2026-11-22 の deny の確認。モデルには文が届き、返事に引用された）。"""
    out = {}
    for su in _step_updates(stream_text):
        if su.get("step_type") != "subagent":
            continue
        info = su.get("subagent_info")
        subs = info.get("subagents") if isinstance(info, dict) else None
        out[su.get("step_index")] = len(subs) if isinstance(subs, list) else None
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


def turn_valid(rec, hooks_expected):
    """ターンが有効か：止めた道具が走っていない・フックが変わっていない・モデルの照合を通った。"""
    return (not any(s["ran"] for s in rec.get("denied_tools") or []) and rec.get("hooks_sha256_after") == hooks_expected
            and not rec.get("model_error"))


def timeout_kind(code, outcome, text):
    """時間切れの種類（無ければ None）。hard：実行口の上限。print_timeout：agy が --print-timeout で自分で止まった
    （result の finished_reason が print_timeout か、終わりの符号 3 と stderr の印）。"""
    if code == HARD_TIMEOUT_RC:
        return "hard"
    if (outcome or {}).get("finished_reason") == "print_timeout" or (
            code == PRINT_TIMEOUT_RC and PRINT_TIMEOUT_MARK in (text or "")):
        return "print_timeout"
    return None


def turn_problem(rec):
    """ターンを ok にしない理由（無ければ None）。モデルの照合・止めた道具が走った・フックが変わった・時間切れ・終わり方の順。"""
    if rec.get("model_error"):
        return "model: " + rec["model_error"]
    ran = [d.get("tool") for d in rec.get("denied_tools") or [] if d.get("ran")]
    if ran:
        return "invalid: denied tool ran: " + ",".join(map(str, ran))
    if not rec.get("valid"):
        return "invalid: hooks changed"
    agent, out = rec.get("agent") or {}, rec.get("outcome") or {}
    if agent.get("timeout"):
        return "timeout: " + agent["timeout"]
    if agent.get("rc") != 0 or not rec.get("complete") or out.get("status") != "SUCCESS":
        return f"agent: rc={agent.get('rc')} complete={rec.get('complete')} status={out.get('status')} error={out.get('error')}"
    return None


def timeout_output(e):
    """subprocess.TimeoutExpired の出力を文字列に。POSIX では text=True でも bytes で返る（Python の文書のとおり）。"""
    parts = []
    for raw in (e.stdout, e.stderr):
        if isinstance(raw, bytes):
            parts.append(raw.decode("utf-8", "replace"))
        elif isinstance(raw, str):
            parts.append(raw)
    return "".join(parts)


def quota_ok(rec, floor):
    """週と 5 時間の残りが、どちらも下限を切っていないか。(ok, 理由)。"""
    if rec is None:
        return False, "利用枠を読めない"
    for k, low in floor.items():
        frac = ((rec.get("buckets") or {}).get(k) or {}).get("remaining_fraction")
        if not isinstance(frac, (int, float)) or frac < low:
            return False, f"{k} の残り {frac} が下限 {low} を切っている"
    return True, None


def quota_check(cfg):
    """走りの前の利用枠。(記録, ok, 理由)。"""
    rec = agy_quota.read()
    ok, why = quota_ok(rec, cfg["quota_floor"])
    return rec, ok, why


# ---------------------------------------------------------------- deny の確認（実験と計画 §6-5。docker に依らない部分）

# §6-5 の確認で 1 度ずつ呼ぶよう頼む道具：止める道具のうち、今の CLI がモデルに渡す 2 つ（フックのスクリプト 2 つを両方とも
# 通る。操縦士の決め、2026-11-22）
DENY_CHECK_TOOLS = ("search_web", "invoke_subagent")
DENY_CHECK_PROMPT = (
    "This is a tool-permission check, not a coding task. Do not create or change any files.\n"
    "Call each of the following tools exactly once, one after another, even if a call fails: {tools}.\n"
    "Use these arguments: for search_web, search for \"SlopCodeBench\"; "
    "for invoke_subagent, ask the subagent to reply with the word OK. If you are unsure of an argument, use a placeholder "
    "value: the call itself is what is being checked.\n"
    "After the calls, reply with one line per tool saying whether the call succeeded or returned an error, "
    "and quote the error message if there was one. Do not call any other tools.")


def tool_attempts(stream_text, wanted):
    """頼んだ道具ごとのステップ。{道具: [{"index", "state", "error", "hook_denied", "evidence", "subagents", "ran"}]}
    （止める道具の表に無い道具も読む）。フックの deny の印（evidence）は 2 通り：
    - "tool_info"：ステップのエラーの文に agy の deny の文言がある（Web の道具など）
    - "reply_quote"：下位エージェントの形のステップで、エラーの文が無く、ERROR で終わり、下位エージェントが 0 で、モデルの返事に
      deny_subagent.sh の deny の文の引用がある（agy 1.5.3 は、このステップにエラーの文を付けない。§6-3）
    ran の読み方は走りの後の検査（denied_tool_steps）と同じ。"""
    errors, subs = step_errors(stream_text), subagent_steps(stream_text)
    parsed = agy_stream.parse(stream_text)
    quoted = f"{HOOK_DENIED}: {SUBAGENT_HOOK_REASON}".lower() in (parsed["response"] or "").lower()
    out = {t: [] for t in wanted}
    for s in parsed["steps"]:
        if s.get("tool") not in out:
            continue
        i, state = s.get("index"), s.get("state")
        err, n_sub = errors.get(i), subs.get(i)
        if HOOK_DENIED in (err or "").lower():
            evidence = "tool_info"
        elif err is None and state == "ERROR" and n_sub == 0 and quoted:
            evidence = "reply_quote"
        else:
            evidence = None
        out[s["tool"]].append({"index": i, "state": state, "error": err, "hook_denied": evidence is not None,
                               "evidence": evidence, "subagents": n_sub, "ran": state == "DONE" and evidence is None})
    return out


def deny_verdict(attempts):
    """deny の確認の判定。(合格か, 理由)。頼んだ道具がどれも 1 度は呼ばれ、どのステップもフックの deny で止まり（state: ERROR と
    deny の印。印は tool_attempts の evidence）、DONE で終わったステップが無いときだけ合格。呼ばれなかった道具があれば落ち。"""
    missing = [t for t, a in attempts.items() if not a]
    if missing:
        return False, "呼ばれなかった: " + ",".join(missing)
    ran = [t for t, a in attempts.items() if any(x["ran"] for x in a)]
    if ran:
        return False, "フックで止まらずに走った: " + ",".join(ran)
    undenied = [t for t, a in attempts.items() if not all(x["state"] == "ERROR" and x["hook_denied"] for x in a)]
    if undenied:
        return False, "フックの deny の印が無い: " + ",".join(undenied)
    return True, None


# ---------------------------------------------------------------- 実行口

class AgyPort:
    """コンテナの中の agy の 1 つの会話。start は新しい会話、send は同じ会話の続き。stream_path(n) は n 番目のターンの
    stream-json の置き場（ホスト）。"""

    def __init__(self, cfg, box, stream_path, label, fake=False):
        self.cfg, self.box, self.stream_path, self.label, self.fake = cfg, box, stream_path, label, fake
        self.conversation_id = None
        self.hooks_expected = None
        self.records = []

    def install(self, token_file=None):
        self.hooks_expected = install(self.cfg, self.box, token_file=None if self.fake else token_file, fake=self.fake)
        return self.hooks_expected

    def start(self, text, budget_s):
        self.conversation_id = None
        return self._turn(text, budget_s)

    def send(self, text, budget_s):
        return self._turn(text, budget_s)

    def _turn(self, text, budget_s):
        agent, sbx = self.cfg["agent"], self.cfg["sandbox"]
        uid, n, ttl = sbx["uid"], len(self.records) + 1, max(int(budget_s), 1)
        stream = Path(self.stream_path(n))
        argv = agy_stream.args(agent, [agent["cli"]], "", ttl, conversation_id=self.conversation_id)
        cmd = ["docker", "exec", "-i", "--user", f"{uid}:{uid}", "-e", f"HOME={sbx['home']}",
               "-e", "AGY_CLI_DISABLE_AUTO_UPDATE=1", "-w", sbx["workdir"], self.box] + argv
        rec = {"turn": n, "conversation_in": self.conversation_id, "stream": stream.name}
        t0, extra = time.time(), {}
        try:
            code, out = sandbox.sh(cmd, input=agy_stream.stdin_for(agent, text), timeout=ttl)
        except subprocess.TimeoutExpired as e:
            code, out = HARD_TIMEOUT_RC, timeout_output(e)
            extra["left_procs"] = sandbox.stop_user(self.box, sbx)
        stream.write_text(out, encoding="utf-8")
        rec["agent"] = {"rc": code, "seconds": round(time.time() - t0, 1), **extra}
        parsed = agy_stream.parse(out, workdir=sbx["workdir"])
        rec["agent"]["timeout"] = timeout_kind(code, parsed["outcome"], out)
        if rec["agent"]["timeout"] == "print_timeout":   # agy は止まったが、それが起こしたコマンドが残りうる
            rec["agent"]["left_procs"] = sandbox.stop_user(self.box, sbx)
        try:   # init の前に落ちた走りも、ここまでを記録する（止めるのは呼んだ側）
            rec["model_reported"] = model_pin.check_reported(agent["model_name"], parsed["model"], f"{self.label} t{n}")
        except model_pin.ModelPinError as e:
            rec["model_reported"], rec["model_error"] = parsed["model"], str(e)
        if parsed["conversation_id"]:
            self.conversation_id = parsed["conversation_id"]
        rec.update(conversation_id=parsed["conversation_id"], usage=parsed["usage"], outcome=parsed["outcome"],
                   complete=parsed["complete"], tools=sorted({s.get("tool") for s in parsed["steps"] if s.get("tool")}),
                   denied_tools=denied_tool_steps(parsed["steps"], step_errors(out)),
                   hooks_sha256_after=hooks_sha(self.box, sbx))
        rec["valid"] = turn_valid(rec, self.hooks_expected)
        rec["turn_note"] = turn_problem(rec)
        rec["turn_ok"] = rec["turn_note"] is None
        self.records.append(rec)
        return core.Turn(ok=rec["turn_ok"], model=parsed["model"] or "", usage=parsed["usage"],
                         seconds=rec["agent"]["seconds"], response=parsed["response"] or "",
                         conversation_id=parsed["conversation_id"] or "", note=rec["turn_note"] or "")
