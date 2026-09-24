"""A/B 実験の集計（docs/design/b4_ab_experiment.md §6.2）。metrics.jsonl → summary.md。

    python -m harness.ab.report --prefix ab          ab-01・ab-02・… をまとめる
    python -m harness.ab.report --run-id ab-01       1 回分だけ
    python -m harness.ab.report --prefix ab --cost-model experiments/b4_ab/cost_model.json

判定はしない。タスク × 条件の表（所要時間を含む）と、トークンの伸び（累積の入力トークンの両対数の傾き。
仮説は A が 2、B が 1）を出す。コストモデルを与えると、事前投資を合算した総コストと損益分岐も出す
（docs/design/b4_ab_fairness_audit.md §3）。
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
from ab import common  # noqa: E402


def load(out_root, run_ids):
    rows = []
    for rid in run_ids:
        for cond in common.CONDITIONS:
            path = Path(out_root) / rid / cond / "metrics.jsonl"
            if path.exists():
                rows += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def loglog_slope(ys):
    """y_N（N = 1, 2, …）の、log N に対する log y の最小二乗の傾き。2 点未満か 0 以下の値があれば None。"""
    pts = [(math.log(n), math.log(y)) for n, y in enumerate(ys, start=1) if isinstance(y, (int, float)) and y > 0]
    if len(pts) < 2 or len(pts) != len(ys):
        return None
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return round(sum((x - mx) * (y - my) for x, y in pts) / den, 2) if den else None


def cumulative_inputs(rows):
    """1 回の走行の行（T1〜）から、タスク N までの累積の入力トークンの列。不明が混ざれば None を入れる。"""
    acc, out = 0, []
    for r in sorted(rows, key=lambda r: r["index"]):
        v = (r.get("tokens") or {}).get("input")
        if v is None or acc is None:
            acc = None
        else:
            acc += v
        out.append(acc)
    return out


def _median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def summarize(rows, cost_model=None):
    runs = sorted({r["run_id"] for r in rows})
    tasks = sorted({r["task"] for r in rows}, key=lambda t: int(t[1:]) if t[1:].isdigit() else t)
    lines = ["# A/B 実験の集計", "", f"- 走行：{', '.join(runs)}（{len(runs)} 回）",
             "- 値は走行の中央値。受入は通った走行の数 / 走行の数",
             "- 総入力 = 入力 + キャッシュ読み（実装役が読んだ文脈の全量）。USD は --cost-model の単価で数える", "",
             "| タスク | 条件 | 受入 | 試行 | 呼び出し | P2P の破壊 | 不変条件の違反 | 追加 / 削除 | 予算超過 "
             "| 入力トークン | キャッシュ読み | 総入力 | USD | 秒 |",
             "|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|"]
    prices = (cost_model or {}).get("prices")
    for t in tasks:
        for cond in common.CONDITIONS:
            rs = [r for r in rows if r["task"] == t and r["condition"] == cond]
            if not rs:
                continue
            tok = [(r.get("tokens") or {}) for r in rs]
            usd = _median([call_cost(x, prices) for x in tok]) if _prices_ok(prices) else None
            lines.append("| {} | {} | {}/{} | {} | {} | {} | {} | {} / {} | {} | {} | {} | {} | {} | {} |".format(
                t, cond, sum(r["accepted"] for r in rs), len(rs), _median([r["attempts"] for r in rs]),
                _fmt(_median([implementer_calls(r) for r in rs])),
                _median([r["p2p_broken"] for r in rs]), _median([r["invariants"]["failures"] for r in rs]),
                _median([r["diff"]["added"] for r in rs]), _median([r["diff"]["deleted"] for r in rs]),
                sum(r["budget_exceeded"] for r in rs),
                # 打ち切った呼び出しを含む行は、終わった手番までの足し込み（下限）と分かるようにする（v2.1 §1.2）
                f"{_median([x.get('input') for x in tok])}" + ("（下限）" if any(x.get("partial") for x in tok) else ""),
                _fmt(_median([x.get("cache_read") for x in tok])), _fmt(_median([total_input(x) for x in tok])),
                _fmt(usd, 4) + ("（下限）" if usd is not None and any(x.get("partial") for x in tok) else ""),
                _median([r.get("seconds") for r in rs])))
    lines += ["", "## トークンの伸び（累積の入力トークンの両対数の傾き）", "",
              "| 条件 | 走行ごとの傾き | 中央値 |", "|:--|:--|:--|"]
    for cond in common.CONDITIONS:
        slopes = [loglog_slope(cumulative_inputs([r for r in rows if r["run_id"] == rid and r["condition"] == cond]))
                  for rid in runs]
        slopes = [s for s in slopes if s is not None]
        lines.append(f"| {cond} | {', '.join(str(s) for s in slopes) or '—'} | {_median(slopes) if slopes else '—'} |")
    lines += ["", "## 合計（全タスク・全走行）", "",
              "| 条件 | P2P の破壊 | 不変条件の違反 | 受入に落ちたタスク | 1 走行の秒（中央値） | 全走行の秒 |",
              "|:--|:--|:--|:--|:--|:--|"]
    for cond in common.CONDITIONS:
        rs = [r for r in rows if r["condition"] == cond]
        per_run = [run_total(rows, rid, cond, "seconds") for rid in runs]
        per_run = [x for x in per_run if x is not None]
        lines.append(f"| {cond} | {sum(r['p2p_broken'] for r in rs)} | {sum(r['invariants']['failures'] for r in rs)} | "
                     f"{sum(not r['accepted'] for r in rs)} | {_fmt(_median(per_run))} | "
                     f"{_fmt(round(sum(per_run), 1) if per_run else None)} |")
    if cost_model is not None:
        lines += cost_section(rows, cost_model)
    return "\n".join(lines) + "\n"


def _fmt(x, nd=1):
    if x is None:
        return "—"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def run_total(rows, run_id, cond, key):
    """1 回の走行（1 条件）の全タスクの合計。行が無いか、不明が混ざれば None。"""
    xs = [r.get(key) for r in rows if r["run_id"] == run_id and r["condition"] == cond]
    if not xs or not all(isinstance(x, (int, float)) for x in xs):
        return None
    return round(sum(xs), 1)


# ============================================================ 総コストと損益分岐（監査 §3）

def implementer_calls(row):
    """実装役を呼んだ回数。A は試行の数、B は pipeline の内側ループを含めた数（無い古い行は None）。"""
    if row["condition"] == "A":
        return row.get("attempts")
    return (row.get("detail") or {}).get("implementer_calls")


def total_input(tokens):
    """入力 + キャッシュ読み。キャッシュ読みが不明なら None。"""
    i, cr = (tokens or {}).get("input"), (tokens or {}).get("cache_read")
    return i + cr if isinstance(i, int) and isinstance(cr, int) else None


def _prices_ok(prices):
    return isinstance(prices, dict) and all(isinstance(prices.get(k), (int, float))
                                            for k in ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok"))


def call_cost(tokens, prices):
    """1 行（1 条件 × 1 タスク）の走行コスト（USD）。入力・出力のどちらかが不明なら None。
    cache_read があれば別に数える。形式によって input がキャッシュ分を含むので、
    prices.cache_read_included_in_input が真なら、その分を input の単価から差し引く。"""
    tokens = tokens or {}
    i, o, cr = tokens.get("input"), tokens.get("output"), tokens.get("cache_read")
    if not isinstance(i, int) or not isinstance(o, int):
        return None
    usd = (i * prices["input_per_mtok"] + o * prices["output_per_mtok"]) / 1e6
    if isinstance(cr, int):
        if prices.get("cache_read_included_in_input"):
            usd -= cr * (prices["input_per_mtok"] - prices["cache_read_per_mtok"]) / 1e6
        else:
            usd += cr * prices["cache_read_per_mtok"] / 1e6
    return usd


def fixed_cost(model, cond, key):
    """条件の事前投資。shared は両条件が負い、B_only は B だけが負う。どれかが未計測なら None。"""
    parts = ["shared"] + (["B_only"] if cond == "B" else [])
    vals = [((model.get("fixed") or {}).get(p) or {}).get(key) for p in parts]
    return sum(vals) if all(isinstance(v, (int, float)) for v in vals) else None


def per_task_series(rows, cond, value):
    """タスク番号順の、走行の中央値の列（タスク k の走行コスト c(k)）。"""
    ks = sorted({r["index"] for r in rows if r["condition"] == cond})
    return [_median([value(r) for r in rows if r["condition"] == cond and r["index"] == k]) for k in ks]


def power_fit(ys):
    """c(k) = a·k^α の両対数の最小二乗。(a, α)。2 点未満か 0 以下の値があれば None。"""
    alpha = loglog_slope(ys)
    if alpha is None:
        return None
    pts = [(math.log(n), math.log(y)) for n, y in enumerate(ys, start=1)]
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    return math.exp(my - alpha * mx), alpha


def break_even(fa, ca, fb, cb, horizon=10000):
    """S_X(N) = F_X + Σ_{k≤N} c_X(k) として、S_B(N) ≤ S_A(N) となる最小の N。
    まず実測の列で探し、決まらなければ A を a·k^α、B を実測の平均で外挿する。
    (N, "measured" | "extrapolated") か (None, 理由)。"""
    if fa is None or fb is None or not ca or not cb or None in ca or None in cb:
        return None, "未計測の値がある"
    sa, sb = fa, fb
    for k in range(min(len(ca), len(cb))):
        sa, sb = sa + ca[k], sb + cb[k]
        if sb <= sa:
            return k + 1, "measured"
    fit = power_fit(ca)
    if fit is None:
        return None, "A の伸びを当てはめられない（2 タスク以上が要る）"
    a, alpha = fit
    b = sum(cb) / len(cb)
    for n in range(len(ca) + 1, horizon + 1):
        sa, sb = sa + a * n ** alpha, sb + b
        if sb <= sa:
            return n, "extrapolated"
    return None, f"N ≤ {horizon} では回収しない"


def cost_section(rows, model):
    prices = model.get("prices") or {}
    if not _prices_ok(prices):
        return ["", "## 総コストと損益分岐（事前投資を合算）", "", "- 単価が未設定（cost_model.json の prices）。計算しない"]
    lines = ["", "## 総コストと損益分岐（事前投資を合算）", "",
             f"- 単価（USD / 100 万トークン）：入力 {prices['input_per_mtok']}、出力 {prices['output_per_mtok']}、"
             f"キャッシュ読み {prices['cache_read_per_mtok']}（{prices.get('source', '出典なし')}）",
             "- 事前投資：shared は両条件が使う入力（要求文・単位定義・生成テスト）、B_only は B だけが使う防壁の資産"]
    for part in ("shared", "B_only"):
        f = (model.get("fixed") or {}).get(part) or {}
        lines.append(f"  - {part}：{_fmt(f.get('usd'), 4)} USD、{_fmt(f.get('seconds'))} 秒（{f.get('source', '未計測')}）")
    lines += ["", "| 条件 | 事前投資 USD | 走行 USD（タスク別の中央値） | 総 USD | 事前投資 秒 | 走行 秒 | 総秒 |",
              "|:--|:--|:--|:--|:--|:--|:--|"]
    series = {}
    for cond in common.CONDITIONS:
        cu = per_task_series(rows, cond, lambda r: call_cost(r.get("tokens"), prices))
        cs = per_task_series(rows, cond, lambda r: r.get("seconds"))
        fu, fs = fixed_cost(model, cond, "usd"), fixed_cost(model, cond, "seconds")
        series[cond] = {"usd": (fu, cu), "sec": (fs, cs)}
        ru = sum(cu) if cu and None not in cu else None
        rsec = sum(cs) if cs and None not in cs else None
        lines.append(f"| {cond} | {_fmt(fu, 4)} | {', '.join(_fmt(x, 4) for x in cu) or '—'} | "
                     f"{_fmt(fu + ru if None not in (fu, ru) else None, 4)} | {_fmt(fs)} | {_fmt(rsec)} | "
                     f"{_fmt(fs + rsec if None not in (fs, rsec) else None)} |")
    lines += ["", "| 尺度 | 損益分岐 N*（S_B(N) ≤ S_A(N) となる最小のタスク数） | 根拠 |", "|:--|:--|:--|"]
    for label, key in (("USD", "usd"), ("秒", "sec")):
        (fa, ca), (fb, cb) = series["A"][key], series["B"][key]
        n, why = break_even(fa, ca, fb, cb)
        why = {"measured": "実測の範囲", "extrapolated": "外挿（A は a·k^α、B は実測の平均）"}.get(why, why)
        lines.append(f"| {label} | {_fmt(n)} | {why} |")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="A/B 実験の集計")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id", action="append")
    g.add_argument("--prefix")
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    ap.add_argument("--cost-model", help="単価と事前投資の JSON（監査 §3）。無ければ総コストの節を出さない")
    ap.add_argument("--dest", help="summary.md の書き出し先（既定は experiments/b4_ab/results/<名前>/summary.md）")
    args = ap.parse_args(argv)
    model = json.loads(Path(args.cost_model).read_text(encoding="utf-8")) if args.cost_model else None
    if args.prefix:
        run_ids = sorted(p.name for p in Path(args.out_root).glob(f"{args.prefix}-*") if p.is_dir())
        name = args.prefix
    else:
        run_ids, name = args.run_id, "+".join(args.run_id)
    rows = load(args.out_root, run_ids)
    if not rows:
        print(f"NG: 集計する行がありません（{args.out_root}）")
        return 1
    dst = Path(args.dest) if args.dest else common.EXP_DIR / "results" / name / "summary.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(summarize(rows, model), encoding="utf-8", newline="\n")
    print(f"書き出し: {dst}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
