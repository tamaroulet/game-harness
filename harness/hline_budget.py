"""分解役 1 回の上限（思考の量・ターン数・時間）と、呼び出しごとの利用量。上限の値は config/hline.json の decomposer.budget だけに置く。"""
import json
import os
from pathlib import Path

from hline_base import Infra, implementer_args  # isort: skip（harness/ を import の道に足す）

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
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    sub, turns = doc.get("subtype"), doc.get("num_turns")
    if (sub == "error_max_turns" or (doc.get("is_error") and isinstance(sub, str) and "max_turns" in sub)
            or (isinstance(turns, int) and not isinstance(turns, bool) and turns >= lim["max_turns"])):
        return f"ターン数の上限 {lim['max_turns']} で打ち切られました"
    return None


def usage(out):
    return telemetry.cli_usage(out, "claude")


def call(agent, wt, ttl_seconds, label, prompt, log, lim):
    """上限つきで 1 回呼ぶ。(終了コード, 標準出力, 打ち切りの理由か None, 利用量)。モデルの照合は呼び手が行う。"""
    args = agent_args(agent, proc.resolve_cli(agent["cli"]), lim)
    code, out, err = proc.run(args, wt, ttl(ttl_seconds, lim), label, env=agent_env(lim), input=prompt)
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    return code, out, cutoff_reason(code, out, lim), usage(out)
