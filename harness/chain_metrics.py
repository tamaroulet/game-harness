"""鎖の段の計装（比べの本番の物差し。v3 §15 の 11-08 その 4 の決め 5・6）：段の走りのディレクトリから、費用・時間・手番・
コマンド・門の働きを数える。

A（bench_solo）と B（bench_chain）の走りのディレクトリは同じ形（factory/run.json・env.json・items/<What>/implementer-*.log）
なので、同じ数え方をする。finish_chain が公開の集計の段の行に足す（cost と gate の欄）。走り直しで段の試しが 2 回以上あれば、
費用は全部の試しを足し、門は最後の試しのもの。試験やファイルの名前は書かず、数だけ。
- 費用：トークンの内訳（入力・出力・キャッシュの読み・書き）と、キャッシュでない分（入力＋出力）。run.json の tokens から。
- 時間：段の走りの実時間（env.json の始めと終わり）、利用枠の待ち、エージェントの時間（実装役の生ログの result の秒。時間切れで
  result が無ければ、手番の秒の和）。ハーネスの時間は、実時間から両方を引いたもの（集計の側で引く）。
- 手番：実装役の生ログの手番（stream-json の手番の数。試行の記録の turns と同じ数え方）と、そのうちモデルの応答の数。
- コマンド：箱の走りで実装役が走らせたコマンドの数（生ログの要約の commands_run。箱でない走りは None）。
- 受け取らなかったファイルの数（A は run.json の dropped、B は試行の記録の box_dropped）。
- 門（B だけ。factory/queue.json の What）：門を通ったか（What が done）、1 回目の試行で通ったか、試行の数、延長、全件で落ちた
  試行。延長の前の試行は tries_before にある（hline_extend.extend。それより前の記録では、延長した What の 1 回目は落ちたとみなす）。
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_run  # noqa: E402
import chain_score  # noqa: E402

TOKENS = (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_tokens"),
          ("cache_creation", "cache_creation_tokens"), ("total", "total"))
SUMS = ("input", "output", "cache_read", "cache_creation", "total", "non_cache", "seconds", "agent_seconds", "wait_seconds",
        "turns", "model_turns", "commands", "calls", "dropped")
FULL_SUITE = "full_suite"   # 全件（変えたパッケージの試験の全部）で落ちた試行の落ちの種類（failure_classes）


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def total(values):
    """数の和。数が 1 つも無ければ None。"""
    got = [v for v in map(num, values) if v is not None]
    return sum(got) if got else None


def span(env):
    """env.json の始めから終わりまでの秒。読めなければ None。"""
    try:
        a, b = (datetime.datetime.fromisoformat(env[k]) for k in ("started_at", "finished_at"))
    except (KeyError, TypeError, ValueError):
        return None
    return round((b - a).total_seconds(), 1)


def stream_facts(text):
    """stream-json の (手番の数, モデルの応答の手番の数, エージェントの秒)。秒は result の duration_seconds、無ければ手番の秒の和。"""
    steps, answers, secs, done = set(), set(), 0.0, None
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        su = e.get("step_update") if isinstance(e, dict) else None
        if isinstance(su, dict):
            steps.add(su.get("step_index"))
            if su.get("state") == "DONE" and isinstance(su.get("usage"), dict):
                answers.add(su.get("step_index"))
            secs += num(su.get("duration_seconds")) or 0
        elif isinstance(e, dict) and isinstance(e.get("result"), dict):
            done = num(e["result"].get("duration_seconds"))
    return len(steps), len(answers), round(done if done is not None else secs, 1)


def log_facts(log):
    """実装役の生ログ 1 つ（要約の JSON・LOG_SPLIT・stream-json・LOG_SPLIT・標準エラー）の手番・時間・コマンド。"""
    parts = chain_score.read_text(log).split(bench_run.LOG_SPLIT)
    try:
        head = json.loads(parts[0])
    except ValueError:
        head = None
    turns, answers, secs = stream_facts(parts[1] if len(parts) > 1 else "")
    return {"turns": turns, "model_turns": answers, "agent_seconds": secs,
            "commands": num(head.get("commands_run")) if isinstance(head, dict) else None}


def item_tries(item):
    return [t for t in (item.get("tries_before") or []) + (item.get("tries") or []) if isinstance(t, dict)]


def first_pass(item):
    tries = item_tries(item)
    return bool(tries) and bool(tries[0].get("gate")) and not item.get("extended")


def gate_facts(rundir):
    """B の門の働き。queue.json が無い（A の走り・H ラインまで進まなかった走り）なら None。"""
    items = bench_run.read_json(Path(rundir) / "factory" / "queue.json", {}).get("items")
    rows = [i for i in items.values() if isinstance(i, dict)] if isinstance(items, dict) else []
    if not rows:
        return None
    tries = [t for i in rows for t in item_tries(i)]
    return {"passed": all(i.get("status") == "done" for i in rows), "first": all(map(first_pass, rows)), "tries": len(tries),
            "extended": sum(1 for i in rows if i.get("extended")),
            "full_suite": sum(1 for t in tries if (t.get("failure") or {}).get("class") == FULL_SUITE)}


def dropped(run, rundir):
    """受け取らなかったファイルの数：A は run.json の dropped、B は試行の記録の box_dropped の数の和。"""
    if num(run.get("dropped")) is not None:
        return run["dropped"]
    items = bench_run.read_json(Path(rundir) / "factory" / "queue.json", {}).get("items")
    tries = [t for i in items.values() if isinstance(i, dict) for t in item_tries(i)] if isinstance(items, dict) else []
    return sum(len(t.get("box_dropped") or []) for t in tries) if tries else None


def run_facts(rundir):
    """段の走り 1 回の費用（SUMS の欄。読めないものは None）。"""
    rundir = Path(rundir)
    run = bench_run.read_json(rundir / "factory" / "run.json", {})
    env = bench_run.read_json(rundir / "env.json", {})
    tokens = run.get("tokens") if isinstance(run.get("tokens"), dict) else {}
    logs = [log_facts(p) for p in sorted((rundir / "items").glob("*/implementer-*.log"))]
    facts = {name: num(tokens.get(key)) for name, key in TOKENS}
    pair = (facts["input"], facts["output"])
    facts.update(non_cache=None if None in pair else sum(pair), seconds=span(env), wait_seconds=num(env.get("quota_wait_seconds")),
                 calls=len(logs), dropped=dropped(run, rundir))
    facts.update({key: total(x[key] for x in logs) for key in ("turns", "model_turns", "agent_seconds", "commands")})
    return facts


def fields(out, base, attempts):
    """段 1 つの公開の集計に足す欄 {cost, gate}。attempts はその段の鎖の記録の試しの全部（走り直しを含む、試しの順）。
    走りのディレクトリが無い試し（中断の残りの無いものなど）は数えない。"""
    dirs = [d for d in (bench_run.run_dir(out, base, st["run_id"]) for st in attempts if st.get("run_id")) if d.is_dir()]
    each = [run_facts(d) for d in dirs]
    cost = {key: total(f[key] for f in each) for key in SUMS}
    cost["runs"] = len(each)
    return {"cost": cost, "gate": gate_facts(dirs[-1]) if dirs else None}
