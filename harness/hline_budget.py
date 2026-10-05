"""分解役 1 回の上限（思考の量・ターン数・時間）と、呼び出しごとの利用量。上限の値は config/hline.json の decomposer.budget だけに置く。"""
import json
import os
from pathlib import Path

from hline_base import Infra, implementer_args, pinned_models  # isort: skip（harness/ を import の道に足す）

import proc  # noqa: E402
import telemetry  # noqa: E402

KEYS = ("max_thinking_tokens", "max_turns", "timeout_seconds")


def limits(agent):
    b = agent.get("budget")
    if not isinstance(b, dict) or any(isinstance(b.get(k), bool) or not isinstance(b.get(k), int) or b[k] < 1 for k in KEYS):
        raise Infra(f"config/hline.json の budget（{', '.join(KEYS)} は 1 以上の整数）が足りないか不正です: {b!r}")
    return {k: b[k] for k in KEYS}


def agent_args(agent, cli, lim):
    return implementer_args(agent, cli) + ["--max-turns", str(lim["max_turns"])]


def agent_env(lim, env=None):
    return dict(os.environ if env is None else env, MAX_THINKING_TOKENS=str(lim["max_thinking_tokens"]))


def ttl(ttl_seconds, lim):
    return min(ttl_seconds, lim["timeout_seconds"])


def cutoff_reason(code, out, lim):
    if code == 124:
        return f"時間の上限 {lim['timeout_seconds']} 秒で打ち切られました"
    return turn_cutoff(out, lim["max_turns"])


# ============================================================ 実装役：ターン数の上限だけ（思考の量・時間は掛けない）

def turn_cap(agent):
    b = agent.get("budget")
    cap = b.get("max_turns") if isinstance(b, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise Infra(f"config/hline.json の budget.max_turns（1 以上の整数）が足りないか不正です: {b!r}")
    return cap


def capped_args(agent, cli, cap):
    return implementer_args(agent, cli) + ["--max-turns", str(cap)]


def _doc(out):
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def turns(out):
    n = _doc(out).get("num_turns")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def turn_cutoff(out, cap):
    """CLI の JSON 出力がターン数の上限での打ち切りなら、その理由。終了コードは見ない。"""
    doc, n = _doc(out), turns(out)
    sub = doc.get("subtype")
    if sub == "error_max_turns" or (doc.get("is_error") and isinstance(sub, str) and "max_turns" in sub) or (n is not None and n >= cap):
        return f"ターン数の上限 {cap} で打ち切られました"
    return None


def outcome(agent, out, cap):
    """(モデルの列, 利用量, 打ち切りの理由, ターン数)。打ち切りのときはモデルを照合せず、空の列にする。"""
    cutoff = turn_cutoff(out, cap)
    return ([] if cutoff else pinned_models(agent, out, "実装役")), usage(out), cutoff, turns(out)


def attempt_record(run, attempt, code, models, ok, rest):
    """実装役の試行 1 回の記録。rest は (利用量, 打ち切りの理由, ターン数) の先頭から。足りない分は None。"""
    usage_, cutoff, n = (list(rest) + [None] * 3)[:3]
    return {"run": run, "attempt": attempt, "cli_exit": code, "models": models, "gate": ok, "usage": usage_, "cutoff": cutoff, "turns": n}


def with_cutoff(cutoff, feedback):
    """前の試行が打ち切られて Gate 1 に落ちたとき、次の試行の feedback に理由と続きから進めてよいことを添える。"""
    if not cutoff:
        return feedback
    return f"前回の実装役はターン数の上限で打ち切られました（{cutoff}）。作業ツリーに残った変更の続きから進めてかまいません。\n\n{feedback}"


READ_TOOLS, READ_KEYS, READ_MAX = ("Read", "Grep", "Glob"), ("file_path", "path"), 40


def _tool_paths(node):
    if isinstance(node, dict):
        inp = node.get("input")
        if node.get("type") == "tool_use" and node.get("name") in READ_TOOLS and isinstance(inp, dict):
            yield from (inp[k] for k in READ_KEYS if isinstance(inp.get(k), str))
        for v in node.values():
            yield from _tool_paths(v)
    elif isinstance(node, list):
        for v in node:
            yield from _tool_paths(v)


def read_files(out):
    """分解役の標準出力にある Read・Grep・Glob の対象（区切りは '/'、重複なし、ソート済み、最大 40 件）。読めなければ空。"""
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        return ()
    return tuple(sorted({p.replace("\\", "/") for p in _tool_paths(doc) if p}))[:READ_MAX]


def usage(out):
    return telemetry.cli_usage(out, "claude")


def call(agent, wt, ttl_seconds, label, prompt, log, lim):
    """上限つきで 1 回呼ぶ。(終了コード, 標準出力, 打ち切りの理由か None, 利用量)。モデルの照合は呼び手が行う。"""
    args = agent_args(agent, proc.resolve_cli(agent["cli"]), lim)
    code, out, err = proc.run(args, wt, ttl(ttl_seconds, lim), label, env=agent_env(lim), input=prompt)
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    return code, out, cutoff_reason(code, out, lim), usage(out)
