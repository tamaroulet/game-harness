"""v2r の R＝1 の 4 走行の健全性の検査（docs/design/v2r_protocol.md §15.13・§15.14。V2R-R の検証コマンド）。

    python -m harness.ab.v2r_runcheck --prefix v2r-r1 [--out-root <走行の親>] [--manifest experiments/v2r/tasks.json]

`driver run-all --repeat 1 --prefix <prefix>` の走行（run-id は `<prefix>-01`）を見る。欠陥の有無は合否に入れない
（結果のデータ）。見るのは、記録が揃い、測定器と環境が健全で、追試に要る来歴があること：

1. 4 条件（A0・A1・B-G・B）のそれぞれに metrics.jsonl があり、マニフェストのタスクがちょうど 1 行ずつある
2. 空虚な性質（前提の成立回数 0）が無い（測定器の健全性。原則 P2）
3. どの行も、要求したモデルと CLI が報告したモデルが一致する（harness/model_pin.py）
4. `<prefix>.env.json` の照合の問題が 0 件
5. `<prefix>.provenance.json` があり、ハーネスが汚れておらず、実装役の全体設定 GEMINI.md の sha256 がある（公開の前提）

再開（fault.json）と guard の停止の記録は、あれば表に出す（再開は不合格にしない。guard の停止で行が欠ければ 1 で落ちる）。
"""
import argparse
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
from ab import common, v2r, v2r_report  # noqa: E402

MANIFEST = common.ROOT / "experiments" / "v2r" / "tasks.json"


def _rows(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def check_condition(out, tasks):
    """(問題の一覧, 表の行)。out は <out-root>/<run-id>/<条件>。"""
    problems, cond = [], out.name
    metrics = out / "metrics.jsonl"
    if not metrics.exists():
        return [f"{cond}：metrics.jsonl がありません"], {"condition": cond, "rows": 0}
    rows = _rows(metrics)
    seen = [r.get("task") for r in rows]
    missing = [t for t in tasks if t not in seen]
    dup = sorted({t for t in seen if seen.count(t) > 1})
    if missing:
        problems.append(f"{cond}：行の無いタスク {', '.join(missing)}")
    if dup:
        problems.append(f"{cond}：行が重なったタスク {', '.join(dup)}")
    vacuous = sorted({v for r in rows for v in (r.get("vacuous_properties") or [])})
    if vacuous:
        problems.append(f"{cond}：空虚な性質 {', '.join(vacuous)}（測定器の故障の疑い）")
    # v2r はどのタスクでも実装役を 1 回以上呼ぶので、報告が空の行も不一致に数える
    bad_model = [r.get("task") for r in rows if not (r.get("model") or {}).get("requested")
                 or (r.get("model") or {}).get("reported") != [(r.get("model") or {}).get("requested")]]
    if bad_model:
        problems.append(f"{cond}：要求と報告のモデルが一致しない行 {', '.join(map(str, bad_model))}")
    stopped = [r.get("task") for r in rows if (r.get("detail") or {}).get("stopped")]
    if stopped:
        problems.append(f"{cond}：実装役を呼ぶ前に門が止めたタスク {', '.join(map(str, stopped))}（門の側の不備。走り直す）")
    table = {"condition": cond, "rows": len(rows), "defects": sum(v2r_report.defect(r) for r in rows),
             "calls": sum(r.get("attempts") or 0 for r in rows), "resumed": (out / "fault.json").exists()}
    return problems, table


def check(out_root, prefix, tasks):
    out_root = Path(out_root)
    run_id = f"{prefix}-01"
    problems, table = [], []
    for cond in v2r.ORDER:
        p, t = check_condition(out_root / run_id / cond, tasks)
        problems += p
        table.append(t)
    env = out_root / f"{prefix}.env.json"
    if not env.exists():
        problems.append(f"{env.name} がありません")
    elif json.loads(env.read_text(encoding="utf-8")).get("problems"):
        problems.append(f"{env.name}：実行環境が固定値と違う")
    prov = out_root / f"{prefix}.provenance.json"
    if not prov.exists():
        problems.append(f"{prov.name} がありません（来歴の無い走行）")
    else:
        rec = json.loads(prov.read_text(encoding="utf-8"))
        if (rec.get("harness") or {}).get("dirty") or not (rec.get("harness") or {}).get("commit"):
            problems.append(f"{prov.name}：ハーネスのコミットが無いか、汚れていた")
        if not (rec.get("agy_global") or {}).get("GEMINI.md"):
            problems.append(f"{prov.name}：GEMINI.md の sha256 が無い（公開の前提）")
    stops = out_root / run_id / "guard_stops.jsonl"
    guard = _rows(stops) if stops.exists() else []
    return {"ok": not problems, "problems": problems, "table": table, "guard_stops": len(guard), "run_id": run_id}


def render(res):
    lines = [f"# 健全性の検査（{res['run_id']}、R＝1）", "", "| 条件 | 行 | 欠陥（データ） | 呼び出し | 再開 |",
             "|:--|:--|:--|:--|:--|"]
    for t in res["table"]:
        lines.append(f"| {t['condition']} | {t['rows']} | {t.get('defects', '—')} | {t.get('calls', '—')} | "
                     f"{'あり' if t.get('resumed') else '—'} |")
    lines += ["", f"- guard の停止：{res['guard_stops']} 件"]
    lines += [f"- NG: {p}" for p in res["problems"]]
    lines.append("合格" if res["ok"] else "不合格")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2r の R＝1 の 4 走行の健全性の検査")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    ap.add_argument("--manifest", default=str(MANIFEST))
    args = ap.parse_args(argv)
    tasks = [t["id"] for t in json.loads(Path(args.manifest).read_text(encoding="utf-8"))["tasks"]]
    res = check(args.out_root, args.prefix, tasks)
    print(render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
