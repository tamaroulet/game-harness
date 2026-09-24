"""A/B 実験の集計（docs/design/b4_ab_experiment.md §6.2）。metrics.jsonl → summary.md。

    python -m harness.ab.report --prefix ab          ab-01・ab-02・… をまとめる
    python -m harness.ab.report --run-id ab-01       1 回分だけ

判定はしない。タスク × 条件の表と、トークンの伸び（累積の入力トークンの両対数の傾き。仮説は A が 2、B が 1）を出す。
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


def summarize(rows):
    runs = sorted({r["run_id"] for r in rows})
    tasks = sorted({r["task"] for r in rows}, key=lambda t: int(t[1:]) if t[1:].isdigit() else t)
    lines = ["# A/B 実験の集計", "", f"- 走行：{', '.join(runs)}（{len(runs)} 回）",
             "- 値は走行の中央値。受入は通った走行の数 / 走行の数", "",
             "| タスク | 条件 | 受入 | 試行 | P2P の破壊 | 不変条件の違反 | 追加 / 削除 | 予算超過 | 入力トークン |",
             "|:--|:--|:--|:--|:--|:--|:--|:--|:--|"]
    for t in tasks:
        for cond in common.CONDITIONS:
            rs = [r for r in rows if r["task"] == t and r["condition"] == cond]
            if not rs:
                continue
            lines.append("| {} | {} | {}/{} | {} | {} | {} | {} / {} | {} | {} |".format(
                t, cond, sum(r["accepted"] for r in rs), len(rs), _median([r["attempts"] for r in rs]),
                _median([r["p2p_broken"] for r in rs]), _median([r["invariants"]["failures"] for r in rs]),
                _median([r["diff"]["added"] for r in rs]), _median([r["diff"]["deleted"] for r in rs]),
                sum(r["budget_exceeded"] for r in rs), _median([(r.get("tokens") or {}).get("input") for r in rs])))
    lines += ["", "## トークンの伸び（累積の入力トークンの両対数の傾き）", "",
              "| 条件 | 走行ごとの傾き | 中央値 |", "|:--|:--|:--|"]
    for cond in common.CONDITIONS:
        slopes = [loglog_slope(cumulative_inputs([r for r in rows if r["run_id"] == rid and r["condition"] == cond]))
                  for rid in runs]
        slopes = [s for s in slopes if s is not None]
        lines.append(f"| {cond} | {', '.join(str(s) for s in slopes) or '—'} | {_median(slopes) if slopes else '—'} |")
    lines += ["", "## 合計（全タスク・全走行）", "", "| 条件 | P2P の破壊 | 不変条件の違反 | 受入に落ちたタスク |", "|:--|:--|:--|:--|"]
    for cond in common.CONDITIONS:
        rs = [r for r in rows if r["condition"] == cond]
        lines.append(f"| {cond} | {sum(r['p2p_broken'] for r in rs)} | {sum(r['invariants']['failures'] for r in rs)} | "
                     f"{sum(not r['accepted'] for r in rs)} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="A/B 実験の集計")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id", action="append")
    g.add_argument("--prefix")
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    args = ap.parse_args(argv)
    if args.prefix:
        run_ids = sorted(p.name for p in Path(args.out_root).glob(f"{args.prefix}-*") if p.is_dir())
        name = args.prefix
    else:
        run_ids, name = args.run_id, "+".join(args.run_id)
    rows = load(args.out_root, run_ids)
    if not rows:
        print(f"NG: 集計する行がありません（{args.out_root}）")
        return 1
    dst = common.EXP_DIR / "results" / name / "summary.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(summarize(rows), encoding="utf-8", newline="\n")
    print(f"書き出し: {dst}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
