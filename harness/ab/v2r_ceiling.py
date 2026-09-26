"""再実験 v2r の天井の検査（docs/design/v2r_protocol.md §11.3、§13 の U5、§15.4）。A0 の metrics.jsonl → 判定。

    python -m harness.ab.v2r_ceiling --run-id v2r-dry-01 [--out-root <走行の親>] [--manifest experiments/v2r/tasks.json]

**判定**：A0 の走行が、マニフェストのすべてのタスクをちょうど 1 行ずつ持ち、タスクの終わりの欠陥（受入の不合格・P2P の
破壊・不変条件の違反のどれか。v2r_report.defect と同じ）が 1 件以上あれば合格（終了コード 0）。欠陥が 0 件なら、系列が
易しすぎるので本走に入らない（終了コード 1）。行が欠けた・重なった走行も 1（途中で止まった走行で天井を判定しない）。

**空虚な性質**（前提の成立回数 0。§15.4）はタスクごとに並べて出す。判定には入れない（空虚さの扱いは人間が決める）。
"""
import argparse
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
from ab import common, v2r_report  # noqa: E402

CONDITION = "A0"
MANIFEST = common.ROOT / "experiments" / "v2r" / "tasks.json"


def task_ids(manifest):
    return [t["id"] for t in json.loads(Path(manifest).read_text(encoding="utf-8"))["tasks"]]


def rows_of(out_root, run_id):
    path = Path(out_root) / run_id / CONDITION / "metrics.jsonl"
    if not path.exists():
        return None
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def check(rows, tasks):
    """{"ok", "defects", "problems", "table"}。problems が空で defects が 1 以上なら ok。"""
    problems = []
    if rows is None:
        return {"ok": False, "defects": 0, "problems": [f"{CONDITION} の metrics.jsonl がありません"], "table": []}
    seen = [r.get("task") for r in rows]
    missing = [t for t in tasks if t not in seen]
    dup = sorted({t for t in seen if seen.count(t) > 1})
    extra = sorted({t for t in seen if t not in tasks})
    if missing:
        problems.append(f"行の無いタスク：{', '.join(missing)}")
    if dup:
        problems.append(f"行が重なったタスク：{', '.join(dup)}")
    if extra:
        problems.append(f"マニフェストに無いタスク：{', '.join(extra)}")
    by = {r.get("task"): r for r in rows}
    table = []
    for t in tasks:
        r = by.get(t)
        if r is None:
            continue
        acc = r.get("acceptance") or {}
        table.append({"task": t, "accepted": bool(r.get("accepted")),
                      "acceptance": f"{acc.get('passed', 0)}/{acc.get('total', 0)}",
                      "p2p_broken": r.get("p2p_broken") or 0,
                      "invariant_failures": (r.get("invariants") or {}).get("failures") or 0,
                      "defect": v2r_report.defect(r), "vacuous": list(r.get("vacuous_properties") or [])})
    defects = sum(x["defect"] for x in table)
    if not problems and defects == 0:
        problems.append("A0 の欠陥が 0 件（系列が易しすぎる。本走に入らない）")
    return {"ok": not problems, "defects": defects, "problems": problems, "table": table}


def render(res, run_id):
    lines = [f"# 天井の検査（{run_id}、{CONDITION}）", "",
             "| タスク | 受入 | P2P の破壊 | 不変条件の違反 | 欠陥 | 空虚な性質 |", "|:--|:--|:--|:--|:--|:--|"]
    for x in res["table"]:
        lines.append(f"| {x['task']} | {x['acceptance']} | {x['p2p_broken']} | {x['invariant_failures']} | "
                     f"{x['defect']} | {', '.join(x['vacuous']) or '—'} |")
    lines += ["", f"- 欠陥：{res['defects']} 件（{len(res['table'])} タスク中）"]
    lines += [f"- NG: {p}" for p in res["problems"]]
    lines.append("合格" if res["ok"] else "不合格")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2r の天井の検査（A0 の欠陥が 1 件以上）")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    ap.add_argument("--manifest", default=str(MANIFEST))
    args = ap.parse_args(argv)
    res = check(rows_of(args.out_root, args.run_id), task_ids(args.manifest))
    print(render(res, args.run_id))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
