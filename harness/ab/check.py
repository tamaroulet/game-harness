"""乾式の走行（B4-E5）の配管の検査。受入の合否は問わず、測定と集計が通しで結合したかだけを見る。

    python -m harness.ab.check --run-id dry-01 --through T1 --summary experiments/b4_ab/results/dry/summary.md

見ること：A・B の両方に metrics.jsonl があり、T1〜through の行が 1 つずつ揃い、受入テストが数えられ
（total > 0）、入力トークンが測れていること。summary.md に各タスク × 条件の行があること。
"""
import argparse
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
from ab import common  # noqa: E402

KEYS = ("run_id", "condition", "task", "index", "accepted", "attempts", "acceptance", "build_ok", "p2p_broken",
        "invariants", "diff", "budget_exceeded", "tokens", "tests_tampered", "seconds")


def problems(manifest, run_id, through, summary, out_root=common.OUT_ROOT):
    m = common.load_manifest(manifest)
    ids = [t["id"] for t in common.tasks_through(m, through)]
    found = []
    for cond in common.CONDITIONS:
        path = common.paths(run_id, cond, out_root=out_root)["out"] / "metrics.jsonl"
        if not path.exists():
            found.append(f"{cond}: {path} がありません")
            continue
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        got = [r.get("task") for r in rows]
        if got != ids:
            found.append(f"{cond}: タスクの行が {got}（期待 {ids}）")
        for r in rows:
            where = f"{cond}/{r.get('task')}"
            missing = [k for k in KEYS if k not in r]
            if missing:
                found.append(f"{where}: 欠けた項目 {missing}")
                continue
            if not r["acceptance"].get("total"):
                found.append(f"{where}: 受入テストを数えられていません（total={r['acceptance'].get('total')}）")
            if not isinstance(r["tokens"].get("input"), int):
                found.append(f"{where}: 入力トークンが測れていません")
            if r["attempts"] < 1:
                found.append(f"{where}: 試行が 0 回です")
    text = Path(summary).read_text(encoding="utf-8") if Path(summary).exists() else None
    if text is None:
        found.append(f"{summary} がありません")
    else:
        for t in ids:
            for cond in common.CONDITIONS:
                if f"| {t} | {cond} |" not in text:
                    found.append(f"summary.md に {t} × {cond} の行がありません")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description="乾式の走行の配管の検査")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--through", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    args = ap.parse_args(argv)
    try:
        found = problems(args.manifest, args.run_id, args.through, args.summary, args.out_root)
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT
    for p in found:
        print(f"NG: {p}")
    if found:
        return 1
    print(f"OK: {args.run_id} の A・B が {args.through} まで測定・集計されています")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
