"""H ライン（harness/hline.py）の実装役が agy のときの 1 回の呼び出し（設定の implementer.cli が agy）。
claude の経路（hline_prompt.implement）の行は変えず、そこから分かれて入る。harness.hline・hline_prompt を import しない（循環を作らない）。

生ログ：先頭に 1 行の要約の JSON（session_id・usage・modelUsage・commands）、続けて "\\n--- stderr ---\\n"、agy の標準出力、
"\\n--- stderr ---\\n"、標準エラー。要約は工廠の calls/（bench_run.log_calls）が claude の出力と同じ形で読む。
agy が出さない値（書き）は、足し合わせる要約の JSON では 0、試行の記録の usage では null と書き分ける。
"""
import json
from pathlib import Path

import agy_pinned
import agy_stream
import box
import box_tree  # noqa: F401  （受け取りの記録の形は box_tree.receive）
import hline_quota
import infra_retry
import model_pin
from hline_base import Infra, Quota, save_prompt

LOG_SPLIT = "\n--- stderr ---\n"
SANDBOX = "--sandbox"
TIMEOUT_EXIT = 124
AGY_ERROR = "AGY_ERROR:"          # 1.5.3 が標準エラーに出す構造の行。stream-json の入力では終了コードが 0 のまま出る（11-08 の実測）
PRINT_TIMEOUT = "print_timeout"   # --print-timeout で切れたときの reason
NOTE = "命令の実行の道具（run_command など）は使えません。ファイルの一覧と検索は、読みと検索の道具で行ってください\n"


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def commands(steps):
    """打ち切る命令の実行の手番（CommandLine を持つ道具で、--sandbox に拒まれた ERROR でないもの）。
    [{"step": 番号（最初の手番が 1）, "tool": 道具の名前, "command": 命令の先頭の語}]。"""
    return [{"step": n, "tool": s.get("tool"), "command": s["verb"]}
            for n, s in enumerate(steps, 1) if s.get("verb") and s.get("state") != "ERROR"]


def blocked_commands(steps):
    """--sandbox に拒まれた（実行されなかった）命令の手番の数。"""
    return sum(1 for s in steps if s.get("verb") and s.get("state") == "ERROR")


def agy_error(err):
    """標準エラーの AGY_ERROR の行の中身。無ければ None。読めない行はその文を short_error に入れる。"""
    for line in (err or "").splitlines():
        if line.startswith(AGY_ERROR):
            try:
                doc = json.loads(line[len(AGY_ERROR):])
            except ValueError:
                doc = None
            return doc if isinstance(doc, dict) else {"short_error": line[:300]}
    return None


def summary(parsed, log, boxed=False):
    """生ログの先頭に書く要約の JSON（claude の CLI の出力に似せた形）。箱の走りではコマンドを許すので、commands（試行を打ち切る手がかり）を空にし、
    実行の数だけを commands_run に残す。"""
    u, model = parsed["usage"], parsed["model"]
    row = {"input_tokens": _int(u.get("input_tokens")), "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": _int(u.get("cache_read_tokens")), "output_tokens": _int(u.get("output_tokens"))}
    models = {model: {"inputTokens": row["input_tokens"], "cacheCreationInputTokens": 0,
                      "cacheReadInputTokens": row["cache_read_input_tokens"], "outputTokens": row["output_tokens"]}} if model else {}
    ran = commands(parsed["steps"])
    if boxed:
        return {"session_id": "agy-" + Path(log).stem, "usage": row, "modelUsage": models, "commands": [], "commands_run": len(ran), "blocked_commands": 0}
    return {"session_id": "agy-" + Path(log).stem, "usage": row, "modelUsage": models, "commands": ran,
            "blocked_commands": blocked_commands(parsed["steps"])}


def usage_record(parsed):
    """試行の記録の usage。agy の報告の値をそのまま（足し直さない）。agy が出さない書きは null。"""
    u = parsed["usage"]
    return {"format": "agy-stream", "input_tokens": u.get("input_tokens"), "cache_creation_tokens": None,
            "cache_creation_tokens_null_reason": "agy はこの値を報告しない", "cache_read_tokens": u.get("cache_read_tokens"),
            "output_tokens": u.get("output_tokens"), "total_tokens": u.get("total_tokens")}


def require_pinned(agent):
    """導入先とモデルの設定の照合（起動の前）。外れたら Infra。"""
    try:
        agy_pinned.require()
        return model_pin.require_implementer(agent, "H ラインの実装役")
    except (agy_pinned.AgyDigestError, model_pin.ModelPinError) as e:
        raise Infra(str(e)) from e


def call(host, agent, wt, ttl, prompt):
    """agy を 1 回呼ぶ。(終了コード, 標準出力, 標準エラー)。--sandbox を足し、--disallowedTools・--max-turns は付けない。"""
    require_pinned(agent)
    args = agy_stream.args(agent, host.proc.resolve_cli(agent["cli"]), prompt, ttl)
    if SANDBOX not in args:
        args.append(SANDBOX)
    return host.proc.run(args, wt, ttl, "実装役", input=agy_stream.stdin_for(agent, NOTE + prompt))


def in_box(host, agent, boxed, wt, ttl, prompt):
    """箱で 1 回呼ぶ（v3 §8 の E4）。(終了コード, 標準出力, 標準エラー)。コマンドを許す（--sandbox は付けない）。箱の失敗は Infra。
    受け取らなかったファイルは host.box_dropped に置く（hline_task.run_task が試行の記録と次の試行の入力に入れる）。"""
    try:
        model_pin.require_implementer(agent, "H ラインの実装役")
        cfg = box.settings()
        box.require(cfg)
        box.ensure_net(cfg)
        args = [a for a in agy_stream.args(agent, ["agy"], prompt, ttl) if a != SANDBOX]
        code, out, err, got = box.agent(cfg, args, wt, ttl, agy_stream.stdin_for(agent, prompt) or "", box.new_files_rule(boxed["package"]))
    except (box.BoxError, model_pin.ModelPinError) as e:
        raise Infra(str(e)) from e
    host.box_dropped = got["dropped"] or None
    return code, out, err


def implement(host, cfg, wt, prompt, log):
    """実装役（agy）を 1 回呼ぶ。(終了コード, モデルの列, 利用量, 打ち切りの理由, ターン数)。hline_prompt.implement と同じ順に扱う。"""
    agent, boxed = cfg["implementer"], cfg.get("box")
    ttl = cfg["ttl_seconds"]["implementer"]
    code, out, err = in_box(host, agent, boxed, wt, ttl, prompt) if boxed else call(host, agent, wt, ttl, prompt)
    parsed = agy_stream.parse(out, wt)
    Path(log).write_text(json.dumps(summary(parsed, log, bool(boxed)), ensure_ascii=False) + LOG_SPLIT + out + LOG_SPLIT + err, encoding="utf-8")
    save_prompt(log, prompt)
    host.blocked_commands = None if boxed else blocked_commands(parsed["steps"])   # 試行の記録に入れる（hline_task.map_size が読む）
    if hit := hline_quota.message(out, err, agy=True):
        raise Quota(f"実装役の利用枠が尽きました（終了コード {code}）: {hit}")
    failed = agy_error(err)
    if code == TIMEOUT_EXIT or (failed or {}).get("reason") == PRINT_TIMEOUT:
        if boxed:   # 途中までの利用量と手番の数を残す（時間切れの試行の費用を消さない）
            return (TIMEOUT_EXIT, [parsed["model"]] if parsed["model"] else [], usage_record(parsed), None, len(parsed["steps"]))
        return (code, [], None, None, None)
    if failed:   # モデルや API の失敗で手番が切れた（枠切れは上で見た）。実装役の失敗に数えない
        raise Infra(f"実装役の API の失敗（終了コード {code}）: {str(failed.get('short_error') or failed)[:300]}")
    if code in infra_retry.INFRA_EXIT_CODES:
        raise Infra(f"実装役の CLI が終了コード {code}: {infra_retry.classify_exit(code, err)}")
    try:
        model = model_pin.check_reported(agent["model_name"], parsed["model"], "H ラインの実装役")
    except model_pin.ModelPinError as e:
        raise Infra(str(e)) from e
    return (code, [model], usage_record(parsed), None, len(parsed["steps"]))
