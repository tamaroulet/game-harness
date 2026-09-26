"""再実験 v2r の集計（docs/design/v2r_protocol.md §11）。metrics.jsonl → 報告の表（Markdown）。

    python -m harness.ab.v2r_report --prefix v2r-run --cost-model experiments/v2/cost_model.json \\
        [--out-root <展開したアーカイブの走行の親>] [--f-b-only 0 0.5 1 2 5 10 20] [--stat median] [--dest summary.md]

**効果の推定（§11.2、2026-09-26 改定）**：タスク × 繰り返しを単位に、条件の組の差（左 − 右）を並べ、差の統計量と、
ブートストラップ（10,000 回、乱数の種は固定）の 95% 信頼区間を出す。統計量は指標ごとに決める（`STATS`）：欠陥と P2P の
破壊は平均（発生率の差。0 か 1 の差の中央値はほとんど 0 で、効果を見分けられない）、費用は中央値。区間が 0 を含むときは
「差があるとは言えない」と書く。費用の差も同じ扱い（V2-RUN の「0.89 倍」の誤りを繰り返さない）。

| 問い | 組（左 − 右） |
|:--|:--|
| H1 門の効果 | A1 − A0、B − B-G（2 つの組をまとめる） |
| H2 仕様の形の効果 | B − A1、B-G − A0（同上） |

指標：欠陥（タスクの終わりに、受入の不合格・P2P の破壊・不変条件の違反のどれかがあれば 1）、P2P の破壊の件数、費用（USD）。

**会計（§11.4）**：F_B_only を 0 と仮定しない。F_B_only の値ごとに、損益分岐 N*（report.break_even）を出す感度の表。
"""
import argparse
import json
import random
import statistics
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
from ab import common, report, v2r  # noqa: E402

GATE_PAIRS = (("A1", "A0"), ("B", "B-G"))
# 指標ごとの差の統計量（§11.2、2026-09-26 改定。game-harness#111 で提案、承認）
STATS = {"defect": "mean", "p2p": "mean", "usd": "median"}
FORM_PAIRS = (("B", "A1"), ("B-G", "A0"))
DEFAULT_F_B_ONLY = (0, 0.5, 1, 2, 5, 10, 20)
RESAMPLES = 10000
SEED = 20260926


def load(out_root, run_ids):
    rows = []
    for rid in run_ids:
        for cond in v2r.ORDER:
            path = Path(out_root) / rid / cond / "metrics.jsonl"
            if path.exists():
                rows += [dict(json.loads(l), condition=cond) for l in path.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
    return rows


def defect(row):
    """タスクの終わりの欠陥（1 か 0）。"""
    inv = (row.get("invariants") or {}).get("failures") or 0
    return int((not row.get("accepted")) or (row.get("p2p_broken") or 0) > 0 or inv > 0)


def metric(name, prices=None):
    if name == "defect":
        return defect
    if name == "p2p":
        return lambda r: r.get("p2p_broken") or 0
    if name == "usd":
        return lambda r: report.call_cost(r.get("tokens"), prices)
    raise ValueError(name)


def pairs(rows, left, right, value):
    """[(左の値, 右の値)]。同じ（走行, タスク）の組だけ。値が None の組は落とす。"""
    by = {(r["run_id"], r["task"], r["condition"]): r for r in rows}
    out = []
    for (rid, task, cond), r in sorted(by.items()):
        if cond != left or (rid, task, right) not in by:
            continue
        x, y = value(r), value(by[(rid, task, right)])
        if x is not None and y is not None:
            out.append((x, y))
    return out


def _stat(xs, stat):
    return statistics.median(xs) if stat == "median" else statistics.fmean(xs)


def bootstrap(pairs_, stat="median", resamples=RESAMPLES, seed=SEED):
    """組の差（左 − 右）の統計量と 95% 信頼区間。{"n", "point", "lo", "hi", "significant"}。組が無ければ n=0。"""
    diffs = [x - y for x, y in pairs_]
    if not diffs:
        return {"n": 0, "point": None, "lo": None, "hi": None, "significant": False}
    rnd = random.Random(seed)
    k = len(diffs)
    boots = sorted(_stat([diffs[rnd.randrange(k)] for _ in range(k)], stat) for _ in range(resamples))
    lo, hi = boots[int(0.025 * resamples)], boots[min(resamples - 1, int(0.975 * resamples))]
    return {"n": k, "point": _stat(diffs, stat), "lo": lo, "hi": hi, "significant": not (lo <= 0 <= hi)}


def effect(rows, pair_list, value, stat="median"):
    ps = [p for left, right in pair_list for p in pairs(rows, left, right, value)]
    return bootstrap(ps, stat)


def verdict(e):
    if e["n"] == 0:
        return "組が無い"
    return "差がある（区間が 0 を含まない）" if e["significant"] else "差があるとは言えない（区間が 0 を含む）"


def per_task_series(rows, cond, value):
    """タスク番号順の、繰り返しの中央値の列。"""
    ks = sorted({r["index"] for r in rows if r["condition"] == cond})
    out = []
    for k in ks:
        xs = [value(r) for r in rows if r["condition"] == cond and r["index"] == k]
        xs = [x for x in xs if x is not None]
        out.append(statistics.median(xs) if xs else None)
    return out


def sensitivity(rows, prices, f_shared, f_b_only_values, left="B", right="A0"):
    """[(F_B_only, N*, 根拠)]。左（B）の事前投資を F_shared + F_B_only、右を F_shared として損益分岐を求める。"""
    cost = metric("usd", prices)
    cb, ca = per_task_series(rows, left, cost), per_task_series(rows, right, cost)
    return [(f, *report.break_even(f_shared, ca, f_shared + f, cb)) for f in f_b_only_values]


def _fmt(x, nd=4):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def summarize(rows, cost_model, f_b_only_values=DEFAULT_F_B_ONLY, stat=None):
    """stat を渡すと全指標をその統計量にする（既定は STATS の指標ごとの統計量）。"""
    prices = cost_model["prices"]
    lines = ["# v2r の集計", "", f"- 走行：{', '.join(sorted({r['run_id'] for r in rows}))}",
             "- 統計量：" + "、".join(f"{k} は差の{'中央値' if (stat or v) == 'median' else '平均'}" for k, v in STATS.items())
             + f"。ブートストラップ {RESAMPLES:,} 回（種 {SEED}）の 95% 信頼区間",
             "- 差は 左 − 右。欠陥・P2P は小さいほど良い、費用は小さいほど安い", "",
             "## 条件ごと（タスク × 繰り返し）", "", "| 条件 | 行 | 欠陥 | P2P の破壊 | 費用 USD（合計） |", "|:--|:--|:--|:--|:--|"]
    for cond in v2r.ORDER:
        rs = [r for r in rows if r["condition"] == cond]
        usd = [report.call_cost(r.get("tokens"), prices) for r in rs]
        lines.append(f"| {cond} | {len(rs)} | {sum(defect(r) for r in rs)} | {sum(r.get('p2p_broken') or 0 for r in rs)} | "
                     f"{_fmt(sum(usd)) if rs and None not in usd else '—'} |")
    lines += ["", "## 効果", "", "| 問い | 組 | 指標 | 組の数 | 差 | 95% 信頼区間 | 判定 |", "|:--|:--|:--|:--|:--|:--|:--|"]
    for label, plist in (("H1 門", GATE_PAIRS), ("H2 仕様の形", FORM_PAIRS)):
        for name in ("defect", "p2p", "usd"):
            e = effect(rows, plist, metric(name, prices), stat or STATS[name])
            ci = "—" if e["n"] == 0 else f"[{_fmt(e['lo'])}, {_fmt(e['hi'])}]"
            lines.append(f"| {label} | {'、'.join(f'{a} − {b}' for a, b in plist)} | {name} | {e['n']} | "
                         f"{_fmt(e['point'])} | {ci} | {verdict(e)} |")
    f_shared = ((cost_model.get("fixed") or {}).get("shared") or {}).get("usd")
    lines += ["", "## 損益分岐の感度（F_B_only を変える）", "",
              f"- F_shared = {_fmt(f_shared)} USD（両辺）。左 B の事前投資 = F_shared + F_B_only、右 A0 = F_shared",
              "- F_B_only はハーネスの門の開発・保守・手直しの費用で、測れない部分を含む。1 つの N* を主張しない", "",
              "| F_B_only（USD） | N*（B が A0 に追いつく最小のタスク数） | 根拠 |", "|:--|:--|:--|"]
    if f_shared is None:
        lines.append("| — | — | cost_model の fixed.shared.usd が無い |")
    else:
        for f, n, why in sensitivity(rows, prices, f_shared, f_b_only_values):
            lines.append(f"| {f} | {_fmt(n)} | {why} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2r の集計")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--cost-model", required=True)
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    ap.add_argument("--f-b-only", nargs="*", type=float, default=list(DEFAULT_F_B_ONLY))
    ap.add_argument("--stat", choices=("median", "mean"), help="全指標をこの統計量にする（既定は指標ごと：STATS）")
    ap.add_argument("--dest")
    args = ap.parse_args(argv)
    run_ids = sorted(p.name for p in Path(args.out_root).glob(f"{args.prefix}-*") if p.is_dir())
    rows = load(args.out_root, run_ids)
    if not rows:
        print(f"NG: 集計する行がありません（{args.out_root}）")
        return 1
    text = summarize(rows, json.loads(Path(args.cost_model).read_text(encoding="utf-8")), args.f_b_only, args.stat)
    if args.dest:
        Path(args.dest).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dest).write_text(text, encoding="utf-8", newline="\n")
        print(f"書き出し: {args.dest}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
