"""実装役（agy）の呼び出しの引数と、stream-json の読み取り（docs/design/v2_1_improvement_plan.md §1.1・§1.2）。

pipeline（条件 B）とドライバ（条件 A）が同じものを使う。

**stream-json**：agy は手番（step）が終わるたびに `step_update`（`state: DONE`）の中にその手番の利用量を出し、
最後に `result` で合計を出す。**この呼び出しの利用量は手番の足し込み**にする。`result` の合計は会話全体の累計で、
会話を続けた呼び出し（`--conversation`）では前の呼び出しの分まで入る（v2-smoke-02 で判明。`conversation_*` に残す）。
`result` が無ければ（TTL で打ち切った）足し込みは下限なので `partial: true` を付ける。推測で埋めない。

**手番の記録**：手番ごとに種類（step_type）・状態・秒・利用量と、道具の名前（あれば）だけを残す。
ファイルの中身や応答の本文は残さない。

**--print-timeout**：TTL より短くして、agy に先に止まらせる（止まれば `result` が出る）。TTL は安全弁。
"""
import json
import re
from pathlib import Path

STREAM = "stream-json"
AGY_BRAIN = Path.home() / ".gemini" / "antigravity-cli" / "brain"
PRINT_TIMEOUT_MARGIN = 20
_TOOL_KEYS = ("tool_name", "tool", "name")


def is_stream(imp):
    return STREAM in (imp.get("output_format_args") or [])


def args(imp, cli, prompt, ttl, conversation_id=None):
    """agy の引数。cli は resolve_cli の結果（リスト）。

    stream-json のときは、プロンプトを引数に載せず標準入力で渡す（stdin_for）。Windows のコマンドラインは
    32767 字までで、v2.1c で契約（GddReference）を埋め込んだら内側ループの 2 回目（反例つき）が超えた
    （v2-dry-05 の B、WinError 206）。
    """
    if is_stream(imp):
        a = list(cli) + [imp["headless_flag"] + "=", "--input-format", STREAM,
                         imp["auto_approve_flag"], imp["model_flag"], imp["model_name"]]
    else:
        a = list(cli) + [imp["headless_flag"], prompt, imp["auto_approve_flag"], imp["model_flag"], imp["model_name"]]
    a += list(imp.get("output_format_args") or [])
    a += list(imp.get("extra_flags") or [])
    if imp.get("effort"):
        a += ["--effort", imp["effort"]]
    if is_stream(imp):
        a += ["--print-timeout", f"{max(int(ttl) - PRINT_TIMEOUT_MARGIN, 30)}s"]
    if conversation_id:
        a += ["--conversation", conversation_id]
    return a


def stdin_for(imp, prompt):
    """標準入力で渡すもの。stream-json のときは 1 行の {"event": "user", "message": {"content": ...}}（実測）。"""
    if not is_stream(imp):
        return None
    return json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"


_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\"'\s|;,]*")


def _tool_summary(params, workdir, own=()):
    """道具の引数の要約（v2.1d）。コマンドの先頭の語・manage_task の Action・作業場所の外を指したか。

    引数の本文（コマンドの全文・ファイルの中身）は残さない。v2-dry-05c では、何のコマンドかが分からず、
    手番の空回りの原因（同じ親の下のほかの走行の作業場所を読んでいた）を後から引数で確かめた。
    """
    out = {}
    cmd = params.get("CommandLine")
    if isinstance(cmd, str) and cmd.strip():
        out["verb"] = cmd.split()[0]
    if isinstance(params.get("Action"), str):
        out["action"] = params["Action"]
    if workdir is not None:
        base = str(workdir).replace("/", "\\").rstrip("\\").lower()
        texts = [v for k, v in params.items() if k in ("CommandLine", "AbsolutePath", "TargetFile")
                 and isinstance(v, str)]
        paths = [p.replace("/", "\\").rstrip("\\").lower() for t in texts for p in _ABS_PATH_RE.findall(t)]
        if paths:
            inside = [base] + [str(o).replace("/", "\\").rstrip("\\").lower() for o in own]
            out["outside"] = any(not any(p == b or p.startswith(b + "\\") for b in inside) for p in paths)
    return out


def own_state(conversation_id):
    """実装役（agy）が自分の会話について持つ場所（その会話の記録）。作業場所の外だが、実装役自身の文脈なので外に数えない。

    v2-smoke-02 の A は、自分の会話の記録（brain/<会話の ID>/.system_generated/logs）を読んだ。ほかの会話の記録は
    ほかの走行の中身を含みうるので、外に数える。
    """
    if not conversation_id:
        return ()
    return (AGY_BRAIN / conversation_id,)


def parse(text, workdir=None):
    """stream-json の出力 → {"conversation_id", "response", "usage", "steps", "complete"}。

    workdir を渡すと、道具の手番に「作業場所の外を指したか」（outside）を付ける。

    usage は telemetry と同じキー（input_tokens・output_tokens・cache_read_tokens・total_tokens）に、
    thinking_tokens・model_steps（利用量を持つ手番の数）・partial（result が無く、手番の足し込みで下限）を足す。
    """
    conv, result, steps, reply = None, None, {}, []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("event")
        if ev == "init":
            conv = e.get("conversation_id") or (e.get("init") or {}).get("conversation_id") or conv
        elif ev == "step_update":
            su = e.get("step_update") or {}
            conv = su.get("conversation_id") or conv
            idx = su.get("step_index")
            rec = steps.setdefault(idx, {"index": idx, "type": su.get("step_type"), "state": None})
            rec["state"] = su.get("state") or rec["state"]
            for k in _TOOL_KEYS:
                if isinstance(su.get(k), str):
                    rec["tool"] = su[k]
            params = (su.get("tool_info") or {}).get("parameters")
            if isinstance(params, dict):
                rec.update(_tool_summary(params, workdir, own_state(conv)))
            if su.get("step_type") == "agent_response" and isinstance(su.get("text_delta"), str):
                reply.append(su["text_delta"])
            if su.get("state") == "DONE":
                if isinstance(su.get("usage"), dict):
                    rec["usage"] = {k: su["usage"].get(k) for k in
                                    ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens")}
                if su.get("duration_seconds") is not None:
                    rec["seconds"] = round(float(su["duration_seconds"]), 1)
        elif ev == "result":
            result = e.get("result") or {}
            conv = result.get("conversation_id") or conv
    ordered = [steps[k] for k in sorted(steps, key=lambda x: (x is None, x))]
    with_usage = [s for s in ordered if s.get("usage")]

    def total(key):
        vals = [s["usage"].get(key) for s in with_usage]
        return sum(v for v in vals if isinstance(v, int)) if with_usage else None

    keys = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens")
    if result is not None and isinstance(result.get("usage"), dict) and with_usage:
        # この呼び出しの利用量は手番の足し込み。result の usage は**会話全体の累計**で、会話を続けた呼び出し
        # （条件 A の --conversation）では前の呼び出しの分まで入る（v2-smoke-02 で判明。新しい会話では両者が一致する）。
        # 累計は監査のために conversation_* に残す
        usage = {k: total(k) for k in keys}
        usage["total_tokens"] = (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0)
        usage["partial"] = False
        usage.update({f"conversation_{k}": result["usage"].get(k) for k in keys})
    elif result is not None and isinstance(result.get("usage"), dict):
        u = result["usage"]
        usage = {k: u.get(k) for k in keys + ("total_tokens",)}
        usage["partial"] = False
    else:
        usage = {k: total(k) for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens")}
        usage["total_tokens"] = (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0) if with_usage else None
        usage["partial"] = True
    usage.update(format="agy-stream", cache_creation_tokens=None, cost_usd=None, model_steps=len(with_usage),
                 lost_steps=sum(1 for s in ordered if s.get("state") not in ("DONE", None) and not s.get("usage")))
    return {"conversation_id": conv, "response": (result or {}).get("response") or "".join(reply),
            "usage": usage, "steps": ordered, "complete": result is not None, "outcome": outcome(result, ordered)}


def outcome(result, steps):
    """呼び出しの終わり方（v2-smoke-04 の後）。{"status", "error", "error_steps", "thinking_only_steps"}。

    - status：agy の result の status（SUCCESS・ERROR）。result が無ければ None（打ち切り）
    - error：agy のエラーの先頭 1 行（実装役の応答の本文ではない。例：出力トークンの上限を超えた）
    - error_steps：手番の中の error_message の数（agy が内部でやり直した回数）
    - thinking_only_steps：出力がすべて思考で、道具も応答も出さなかった手番の数
    v2-smoke-02〜04 では、長い呼び出しの多くが「思考だけの手番 → error_message → やり直し」だった。
    """
    err = (result or {}).get("error")
    return {"status": (result or {}).get("status"),
            "error": err.splitlines()[0][:200] if isinstance(err, str) and err.strip() else None,
            "error_steps": sum(1 for s in steps if s.get("type") == "error_message"),
            "thinking_only_steps": sum(1 for s in steps if (s.get("usage") or {}).get("output_tokens")
                                       and (s["usage"].get("thinking_tokens") or 0) >= s["usage"]["output_tokens"])}
