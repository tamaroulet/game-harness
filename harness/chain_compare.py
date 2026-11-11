"""比べの本番の集計（v3 §15 の 11-08 その 4 の決め 5・6）：A と B の鎖の公開の集計から、主と副の物差し・段の対の 4 分割表・
土台ごとの表・土台で層にした対の並べ替えの検定・散布図の数を出す。

    python -m harness.chain_compare --a <dir> [<dir> ...] --b <dir> [<dir> ...] [--draws 50000] [--seed 1] [--json <path>]

<dir> は走り 1 回（タグ 1 つ）の公開の集計の置き場。開発の組は <clone>/bench/records/chains/<tag>/（<土台>.json に段の行）。
保留と取っておく組は、公開には合計しか無いので、非公開の oracles の chains/runs/<tag>/（<土台>/public_full.json）を渡す。
--a に素のエージェント（A）、--b にハーネス（B）の走りを、腕ごとに 1 つ以上並べる。走りの数が両腕でそろえば i 番目どうしを
4 分割表の対にし、そろわなければ全部の組み合わせにする。B の走りどうしを並べれば、揺れの見積もりになる。

主（判定に使う物差し。事前登録の対象）
1. 累積の完了（complete）：段 k が無効でなく、段 k の新しい隠しテスト（new）と段 0〜k−1 の隠しテスト（old。置き換えた分を除く）が
   全部通る。bench_pair.passed（保ち kept は前の段で通っていた分だけが分母）と違い、前の段で壊れた要件もずっと数える。
   分母は土台ごとの予定の段（記録の planned の最大。無ければ、読んだ走りでその土台に見えた段の最大）で、記録の無い段は完了でない。
2. 費用：段あたりのキャッシュでない分（入力＋出力。chain_metrics の cost.non_cache）。
差は、同じ土台と段の組（項目）で、腕ごとの走りの平均の差（B − A）。検定は「腕に違いが無い」を帰無にした、項目ごとの差の
符号の入れ替え（腕の札を項目の中で入れ替えるので、土台で層になる）。差が細かい格子の上（完了の率）なら全部の入れ替えを数えて
厳密に、そうでなければ（トークン）seed を決めた draws 回の無作為の入れ替え。優越の 2 つ（品質・費用）は Holm で調整する。
品質の非劣性（B − A > −δ）の検定の方法は、規約で決める。差に δ を足して符号を入れ替える方法は使わない：対の 2 値の差では
差 0 の項目が多く、足した δ がどれも同じ向きに数えられて過大に有意になる（B どうしの e4-dev1 と e4-dev2 で、18 項目の
うち 17 が差 0 のとき p = 3.8e-6）。判定の手順・δ・回数は規約で事前に決める。この部品は数を出すだけで、判定はしない。
"""
import argparse
import json
import random
import sys
from fractions import Fraction
from math import lcm
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_pair  # noqa: E402
import bench_run  # noqa: E402
import chain_metrics  # noqa: E402
import exitcode  # noqa: E402
import hline_base  # noqa: E402

EXACT_SPAN = 200_000   # 厳密に数える和の幅の上限（格子の目の数）。超えたら無作為の入れ替え
TABLE = ("both", "b_only", "a_only", "neither")
FAILURES = (("missing", "記録無し（鎖が止まった後）"), ("invalid", "無効か境界の違反"), ("limit", "時間かトークンの切れ"),
            ("gate", "B の門の不合格"), ("new", "新しい要件の未達"), ("old", "前の要件の退行"))
COST_KEYS = chain_metrics.SUMS


# ============================================================ 読み

def load_run(path):
    """走り 1 回の公開の集計：{tag, set, arm, rev, path, bases: {土台: {planned, stopped, rows: {段: 行}}}}。"""
    path = Path(path)
    docs = [d for d in (bench_run.read_json(p, {}) for p in sorted(path.glob("*.json"))) if isinstance(d.get("stages"), list)]
    docs += [bench_run.read_json(p, {}) for p in sorted(path.glob("*/public_full.json"))]
    docs = [d for d in docs if isinstance(d, dict) and d.get("base") and isinstance(d.get("stages"), list)]
    if not docs:
        raise ValueError(f"段の行のある公開の集計がありません: {path}")
    if len({d.get("tag") for d in docs}) > 1:
        raise ValueError(f"タグの違う集計が混じっています: {path}")
    head = docs[0]
    run = {"tag": head.get("tag"), "set": head.get("set"), "arm": head.get("arm"), "rev": head.get("rev"), "path": str(path), "bases": {}}
    for d in docs:
        rows = {r["k"]: r for r in d["stages"] if isinstance(r, dict) and isinstance(r.get("k"), int)}
        run["bases"][d["base"]] = {"planned": d.get("planned"), "stopped": d.get("stopped"), "rows": rows}
    return run


def plan(runs):
    """項目（土台, 段）の列。土台ごとの予定の段は、記録の planned の最大、無ければその土台に見えた段の最大。"""
    planned = {}
    for run in runs:
        for base, b in run["bases"].items():
            n = b["planned"] if isinstance(b["planned"], int) else max(b["rows"], default=0)
            planned[base] = max(planned.get(base, 0), n)
    return [(base, k) for base in sorted(planned) for k in range(1, planned[base] + 1)]


def row_of(run, base, k):
    return ((run["bases"].get(base) or {}).get("rows") or {}).get(k)


# ============================================================ 段の物差し

def complete(row):
    """累積の完了（docstring の主 1）。行が無い段は完了でない。"""
    return bool(row) and not row.get("invalid") and bench_pair.full(row.get("new")) and bench_pair.full(row.get("old"))


def non_cache(row):
    return chain_metrics.num(((row or {}).get("cost") or {}).get("non_cache"))


def claimed(row):
    """腕が段を終えたと言ったか：B は門を通った（gate.passed）、A は正常に終えた（stop が done）。門の欄の無い古い行は None。
    B の stop は門に通らなくても done になるので、B を stop では判じない。"""
    if not row or "gate" not in row:
        return None
    return bool(row["gate"].get("passed")) if isinstance(row["gate"], dict) else row.get("stop") == "done"


def failure(row):
    """完了でない段の落ちの種類（FAILURES の印。上から先に当たったもの）。"""
    if not row:
        return "missing"
    if row.get("invalid") or row.get("violations"):
        return "invalid"
    if row.get("stop") in ("time", "tokens"):
        return "limit"
    if isinstance(row.get("gate"), dict) and not row["gate"].get("passed"):
        return "gate"
    return "new" if not bench_pair.full(row.get("new")) else "old"


def ratio(a, b):
    return round(a / b, 4) if b else None


def rows_of(runs, items):
    return [row_of(r, b, k) for r in runs for b, k in items]


# ============================================================ 腕ごとの記述

def quality(runs, items):
    rows = rows_of(runs, items)
    done = sum(map(complete, rows))
    tests = {key: [sum(x[key][i] for x in rows if x and is_rate(x.get(key))) for i in (0, 1)] for key in ("new", "old", "kept")}
    return {"stages": len(rows), "complete": done, "complete_rate": ratio(done, len(rows)),
            "passed": sum(1 for x in rows if x and bench_pair.passed(x)), "tests": tests}


def is_rate(value):
    return isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value)


def chains(runs, items):
    """走りと土台ごとの、s1 から続けて完了した段の数の平均と、最後の段まで完了した鎖の数。段の位置ごとの完了の率。"""
    ks = {}
    for b, k in items:
        ks.setdefault(b, []).append(k)
    streaks, last = [], 0
    for run in runs:
        for base, have in ks.items():
            flags = [complete(row_of(run, base, k)) for k in sorted(have)]
            streaks.append(next((i for i, f in enumerate(flags) if not f), len(flags)))
            last += flags[-1]
    curve = {}
    for k in sorted({k for _, k in items}):
        at = [(b, kk) for b, kk in items if kk == k]
        curve[str(k)] = ratio(sum(map(complete, rows_of(runs, at))), len(runs) * len(at))
    return {"chains": len(streaks), "streak_mean": ratio(sum(streaks), len(streaks)), "last_complete": last, "by_position": curve}


def stability(runs, items):
    """項目ごとに、全部の走りで完了・どの走りでも完了でない・揺れた、の数（走りが 2 回以上のときに意味がある）。"""
    flags = [[complete(row_of(r, b, k)) for r in runs] for b, k in items]
    return {"all": sum(all(f) for f in flags), "none": sum(not any(f) for f in flags), "mixed": sum(any(f) and not all(f) for f in flags)}


def costs(runs, items):
    """費用の和（COST_KEYS）と、ハーネスの時間（実時間 − エージェントの時間 − 利用枠の待ち）、段あたりのキャッシュでない分。"""
    have = [x["cost"] for x in rows_of(runs, items) if x and isinstance(x.get("cost"), dict)]
    sums = {key: chain_metrics.total(c.get(key) for c in have) for key in COST_KEYS}
    spent = [sums[k] for k in ("seconds", "agent_seconds", "wait_seconds")]
    sums["harness_seconds"] = None if None in spent else round(spent[0] - spent[1] - spent[2], 1)
    counted = sum(1 for c in have if chain_metrics.num(c.get("non_cache")) is not None)
    return {"stages": len(have), "sums": sums, "non_cache_per_stage": ratio(sums["non_cache"], counted) if counted else None}


def mechanism(runs, items):
    """B の仕組みの働き（門の欄のある段だけ）：門を通った・1 回目で通った・差し戻し（試行 2 回以上）・延長・全件で落ちた試行のある段。"""
    gates = [x["gate"] for x in rows_of(runs, items) if x and isinstance(x.get("gate"), dict)]
    return {"stages": len(gates), "passed": sum(bool(g.get("passed")) for g in gates), "first": sum(bool(g.get("first")) for g in gates),
            "retried": sum((g.get("tries") or 0) >= 2 for g in gates), "extended": sum(bool(g.get("extended")) for g in gates),
            "full_suite": sum(bool(g.get("full_suite")) for g in gates)}


def trust(rows):
    """終えたと言った段（claimed）・そのうち累積の完了でない段・言ったかが分からない段（門の欄の無い古い行）と、落ちの種類。"""
    said = [(claimed(x), complete(x)) for x in rows if x]
    fails = dict.fromkeys((k for k, _ in FAILURES), 0)
    for x in rows:
        if not complete(x):
            fails[failure(x)] += 1
    return {"claimed": sum(c is True for c, _ in said), "claimed_broken": sum(c is True and not d for c, d in said),
            "claimed_unknown": sum(c is None for c, _ in said), "failures": fails}


def health(runs, items):
    """信頼と健全性：trust に、記録無し・無効の理由・境界・道の検め・漏れ・調べもの・上限の切れ・走り直し・
    開始の木で新しい試験が通っていた段（課題の健全さ）を足したもの。"""
    rows = rows_of(runs, items)
    have = [x for x in rows if x]
    invalid = {}
    for x in have:
        if x.get("invalid"):
            invalid[str(x["invalid"])] = invalid.get(str(x["invalid"]), 0) + 1
    start = [x.get("new_at_start") for x in have if is_rate(x.get("new_at_start")) and x["new_at_start"][1]]
    return {**trust(rows), "missing": len(rows) - len(have), "invalid": invalid,
            "violations": sum(1 for x in have if x.get("violations")), "strays": chain_metrics.total(x.get("strays") for x in have),
            "leaks": chain_metrics.total(x.get("leaks") for x in have), "web": chain_metrics.total(x.get("web") for x in have),
            "limit": sum(1 for x in have if x.get("stop") in ("time", "tokens")),
            "retried": sum(1 for x in have if (chain_metrics.num(x.get("attempts")) or 1) > 1),
            "new_at_start_full": sum(1 for s in start if s[0] == s[1]), "new_at_start_some": sum(1 for s in start if s[0])}


def describe(runs, items):
    return {"runs": [{k: r[k] for k in ("tag", "set", "arm", "rev", "path")} for r in runs], "quality": quality(runs, items),
            "chains": chains(runs, items), "stability": stability(runs, items), "cost": costs(runs, items),
            "mechanism": mechanism(runs, items), "health": health(runs, items), "points": points(runs, items)}


def points(runs, items):
    """散布図の点（走りごと）：累積の完了の率と、段あたりのキャッシュでない分。"""
    out = []
    for r in runs:
        rows = [row_of(r, b, k) for b, k in items]
        spent = [v for v in map(non_cache, rows) if v is not None]
        out.append({"tag": r["tag"], "complete_rate": ratio(sum(map(complete, rows)), len(rows)),
                    "non_cache_per_stage": round(sum(spent) / len(spent)) if spent else None})
    return out


# ============================================================ 対と検定

def mean(values):
    return Fraction(sum(values), len(values))


def pairs(runs_a, runs_b, items):
    """項目ごとの {base, k, a, b（完了の列）, a_cost, b_cost（キャッシュでない分の列）, quality（完了の率の差 B − A）,
    cost（費用の差。どちらかの腕の走りに費用の無いものがあれば None）}。差は Fraction。"""
    out = []
    for base, k in items:
        ra, rb = [row_of(r, base, k) for r in runs_a], [row_of(r, base, k) for r in runs_b]
        ca, cb = [complete(x) for x in ra], [complete(x) for x in rb]
        xa, xb = [non_cache(x) for x in ra], [non_cache(x) for x in rb]
        out.append({"base": base, "k": k, "a": ca, "b": cb, "a_cost": xa, "b_cost": xb, "quality": mean(cb) - mean(ca),
                    "cost": mean(xb) - mean(xa) if None not in xa + xb else None})
    return out


def four_way(run_a, run_b, items):
    t = dict.fromkeys(TABLE, 0)
    for b, k in items:
        x, y = complete(row_of(run_a, b, k)), complete(row_of(run_b, b, k))
        t["both" if x and y else "b_only" if y else "a_only" if x else "neither"] += 1
    return t


def tables(runs_a, runs_b, items):
    """段の対の 4 分割表（両方完了・B だけ・A だけ・両方落ち）。走りの数がそろえば i 番目どうし、そろわなければ全部の組み合わせ。"""
    combos = list(zip(runs_a, runs_b)) if len(runs_a) == len(runs_b) else [(a, b) for a in runs_a for b in runs_b]
    each = [{"a": a["tag"], "b": b["tag"], **four_way(a, b, items)} for a, b in combos]
    return {"pairs": each, "sum": {key: sum(t[key] for t in each) for key in TABLE}}


def per_base(runs_a, runs_b, items):
    """土台ごとの表：腕ごとの累積の完了の数と分母、キャッシュでない分の和。"""
    out = {}
    for base in sorted({b for b, _ in items}):
        at = [(b, k) for b, k in items if b == base]
        out[base] = {arm: {"complete": sum(map(complete, rows_of(runs, at))), "of": len(runs) * len(at),
                           "non_cache": chain_metrics.total(map(non_cache, rows_of(runs, at)))}
                     for arm, runs in (("a", runs_a), ("b", runs_b))}
    return out


def exact_counts(values, observed):
    """整数の列の符号の入れ替え（2^n 通り）の和のうち、observed 以上・以下の数と全体の数。"""
    dist = {0: 1}
    for v in values:
        nxt = {}
        for s, c in dist.items():
            for t in (s + v, s - v):
                nxt[t] = nxt.get(t, 0) + c
        dist = nxt
    return sum(c for s, c in dist.items() if s >= observed), sum(c for s, c in dist.items() if s <= observed), 2 ** len(values)


def sampled_counts(values, observed, draws, seed):
    """無作為の入れ替え draws 回。観測そのものを 1 回に数える（p が 0 にならない）。"""
    rnd, ge, le = random.Random(seed), 1, 1
    for _ in range(draws):
        bits = rnd.getrandbits(len(values)) if values else 0
        s = sum(v if bits >> i & 1 else -v for i, v in enumerate(values))
        ge, le = ge + (s >= observed), le + (s <= observed)
    return ge, le, draws + 1


def signflip(diffs, draws=50_000, seed=1):
    """差の列の符号を入れ替える検定（帰無：腕に違いが無い）。{n, mean, greater（入れ替えた和が観測以上の割合）, less, two, method}。
    差 0 の項目は和を変えないので、入れ替えに数えない。差が無ければ None。"""
    if not diffs:
        return None
    vals = [v for v in map(Fraction, diffs) if v]
    scale = lcm(*(v.denominator for v in vals)) if vals else 1
    ints = [int(v * scale) for v in vals]
    observed, mags = sum(ints), [abs(v) for v in ints]
    exact = sum(mags) <= EXACT_SPAN
    ge, le, n = exact_counts(mags, observed) if exact else sampled_counts(mags, observed, draws, seed)
    greater, less = ge / n, le / n
    return {"n": len(diffs), "mean": float(sum(map(Fraction, diffs)) / len(diffs)), "greater": greater, "less": less,
            "two": min(1.0, 2 * min(greater, less)), "method": "exact" if exact else f"sampled {draws}（seed {seed}）"}


def holm(ps):
    """{名: p}（None は除く）の Holm の調整。"""
    named, out, top = sorted((p, n) for n, p in ps.items() if p is not None), {}, 0.0
    for i, (p, n) in enumerate(named):
        top = max(top, min(1.0, (len(named) - i) * p))
        out[n] = top
    return out


def verdicts(rows, draws, seed):
    """品質の優越（差 > 0）・費用の優越（差 < 0。費用の無い項目は除く）の検定と、その 2 つの Holm。両腕のどの走りでも完了した
    項目の費用の差の平均（記述）。"""
    q = [p["quality"] for p in rows]
    c = [p["cost"] for p in rows if p["cost"] is not None]
    both = [p["cost"] for p in rows if p["cost"] is not None and all(p["a"] + p["b"])]
    tests = {"quality": signflip(q, draws, seed), "cost": signflip(c, draws, seed)}
    adjusted = holm({"quality": (tests["quality"] or {}).get("greater"), "cost": (tests["cost"] or {}).get("less")})
    return {"tests": tests, "holm": adjusted, "cost_items": len(c), "cost_excluded": len(rows) - len(c),
            "cost_both_complete": {"items": len(both), "mean": float(sum(both) / len(both)) if both else None}}


def warnings_of(runs_a, runs_b):
    out = []
    if len({tuple(sorted(r["bases"])) for r in runs_a + runs_b}) > 1:
        out.append("走りによって土台がそろっていません（無い土台の段は完了でないと数える）")
    if len({r["set"] for r in runs_a + runs_b}) > 1:
        out.append("組（set）の違う走りが混じっています")
    if any(r["arm"] != "solo" for r in runs_a) or any(r["arm"] == "solo" for r in runs_b):
        out.append("腕の名が --a（solo）・--b（名無し）と合いません（B どうしの揺れの見積もりなら、そのままでよい）")
    return out


def compare(runs_a, runs_b, draws=50_000, seed=1):
    items = plan(runs_a + runs_b)
    rows = pairs(runs_a, runs_b, items)
    shown = [{**p, "quality": float(p["quality"]), "cost": None if p["cost"] is None else float(p["cost"])} for p in rows]
    return {"items": len(items), "warnings": warnings_of(runs_a, runs_b), "a": describe(runs_a, items), "b": describe(runs_b, items),
            "verdicts": verdicts(rows, draws, seed), "four_way": tables(runs_a, runs_b, items),
            "per_base": per_base(runs_a, runs_b, items), "pairs": shown}


# ============================================================ 出力

def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def p_text(test, key):
    return "—" if not test else f"p={test[key]:.4g}"


def summary_lines(doc):
    a, b, v = doc["a"], doc["b"], doc["verdicts"]
    qa, qb = a["quality"], b["quality"]
    t = v["tests"]
    lines = [f"A：{', '.join(r['tag'] for r in a['runs'])}／B：{', '.join(r['tag'] for r in b['runs'])}（項目 {doc['items']}）",
             f"累積の完了：A {qa['complete']}/{qa['stages']}（{pct(qa['complete_rate'])}）・B {qb['complete']}/{qb['stages']}"
             f"（{pct(qb['complete_rate'])}）・差の平均 {(t['quality'] or {}).get('mean', 0) * 100:+.1f} ポイント",
             f"今までの数え方（保ち）の通過：A {qa['passed']}・B {qb['passed']}",
             f"段あたりのキャッシュでない分：A {a['cost']['non_cache_per_stage']}・B {b['cost']['non_cache_per_stage']}"
             f"（費用の対 {v['cost_items']}、除いた項目 {v['cost_excluded']}）",
             "4 分割表（両方完了・B だけ・A だけ・両方落ち）：" + "・".join(str(doc["four_way"]["sum"][k]) for k in TABLE),
             f"検定（帰無：腕に違いが無い）：品質の優越 {p_text(t['quality'], 'greater')}・費用の優越 {p_text(t['cost'], 'less')}"
             f"・Holm {json.dumps(v['holm'], ensure_ascii=False)}"]
    lines += [f"土台 {base}：A {x['a']['complete']}/{x['a']['of']}・B {x['b']['complete']}/{x['b']['of']}" for base, x in doc["per_base"].items()]
    return lines + [f"注意：{w}" for w in doc["warnings"]]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.chain_compare", description=__doc__.splitlines()[0])
    ap.add_argument("--a", nargs="+", required=True)
    ap.add_argument("--b", nargs="+", required=True)
    ap.add_argument("--draws", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        doc = compare([load_run(p) for p in a.a], [load_run(p) for p in a.b], a.draws, a.seed)
    except (OSError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(Path(a.json), doc)
    print("\n".join(summary_lines(doc)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(exitcode.normalized(main))
