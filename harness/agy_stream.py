"""実装役（agy）の呼び出しの引数と、stream-json の読み取り（docs/design/v2_1_improvement_plan.md §1.1・§1.2）。

pipeline（条件 B）とドライバ（条件 A）が同じものを使う。

**stream-json**：agy は手番（step）が終わるたびに `step_update`（`state: DONE`）の中にその手番の利用量を出し、
最後に `result` で合計を出す。`result` があればそれを使い、無ければ（TTL で打ち切った）終わった手番の利用量を
足し込む。後者は下限なので `partial: true` を付ける。推測で埋めない。

**手番の記録**：手番ごとに種類（step_type）・状態・秒・利用量と、道具の名前（あれば）だけを残す。
ファイルの中身や応答の本文は残さない。

**--print-timeout**：TTL より短くして、agy に先に止まらせる（止まれば `result` が出る）。TTL は安全弁。
"""
import json

STREAM = "stream-json"
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


def parse(text):
    """stream-json の出力 → {"conversation_id", "response", "usage", "steps", "complete"}。

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

    if result is not None and isinstance(result.get("usage"), dict):
        u = result["usage"]
        usage = {k: u.get(k) for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens",
                                       "total_tokens")}
        usage["partial"] = False
    else:
        usage = {k: total(k) for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens")}
        usage["total_tokens"] = (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0) if with_usage else None
        usage["partial"] = True
    usage.update(format="agy-stream", cache_creation_tokens=None, cost_usd=None, model_steps=len(with_usage),
                 lost_steps=sum(1 for s in ordered if s.get("state") not in ("DONE", None) and not s.get("usage")))
    return {"conversation_id": conv, "response": (result or {}).get("response") or "".join(reply),
            "usage": usage, "steps": ordered, "complete": result is not None}
