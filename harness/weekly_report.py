"""週次の報告：queue.json と bench/records から、週ごとの計器と前の 2 週との比較を reports/weekly/<年>-W<週>.md と .json に書く（W-54）。

    python -m harness.weekly_report [--week 2026-W42] [--queue <queue.json>] [--records <bench/records>] [--out <書き先>]
    python -m harness.weekly_report --latest-within-days <n>     書き先に n 日以内の報告があれば 0、無ければ 1（報告は書かない）

週は ISO 週（月曜始まり）。省くと前の週。数え方は northstar（日付・トークン）と failure_classes（分類）に揃え、report.md の計器と食い違わせない。
値を出せない計器は null にして、理由を .md に 1 行で書く。H ラインは走行のたびに ensure_previous を呼ぶ（hline.weekly）。
"""
import argparse
import datetime
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import failure_classes  # noqa: E402
import hline_usage  # noqa: E402
import northstar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOTTLENECKS = 3
BACK = 2
WEEK = re.compile(r"^(\d{4})-W(\d{1,2})$")


def week_name(day):
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def parse_week(text):
    """'2026-W42' → その週の月曜の日付。読めなければ ValueError。"""
    m = WEEK.match(text.strip())
    if not m:
        raise ValueError(f"週は 2026-W42 の形で書いてください: {text}")
    return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)


def previous_week(today):
    return today - datetime.timedelta(days=today.weekday() + 7)


def in_week(day, monday):
    return day is not None and monday <= day < monday + datetime.timedelta(days=7)


def factory_runs(records):
    """工廠の走行の記録（bench/records/<課題>/factory-*.json）。(started_at の日付, 記録) の一覧。読めない記録は飛ばす。"""
    out = []
    for p in sorted(Path(records).glob("*/factory-*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            out.append((datetime.date.fromisoformat(str(rec["started_at"])[:10]), rec))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _dated(items, monday, status):
    return {n: i for n, i in items.items() if i.get("status") == status and in_week(northstar.date_of(i), monday)}


def _failures(items, monday):
    tries = failure_classes.counts(items)[1]
    return {c: sum(row.get(c, 0) for d, row in tries.items() if in_week(d, monday)) for c in failure_classes.COLUMNS}


def _done_metrics(done, notes):
    if not done:
        for k in ("first_try_rate", "tokens_per_done", "bottleneck"):
            notes[k] = "その週に済んだ What が無い"
        return {"first_try_rate": None, "tokens_per_done": None, "bottleneck": None}
    tokens = {n: hline_usage.totals(i)[2] for n, i in done.items()}
    once = sum(1 for i in done.values() if hline_usage.totals(i)[0] == 1)
    top = sorted(tokens.items(), key=lambda kv: (-kv[1], kv[0]))[:BOTTLENECKS]
    return {"first_try_rate": once / len(done),
            "tokens_per_done": {"median": statistics.median(tokens.values()), "sum": sum(tokens.values())},
            "bottleneck": [{"name": n, "tokens": t} for n, t in top]}


def _audit_rate(runs, monday, notes):
    here = [r for d, r in runs if in_week(d, monday)]
    if not here:
        notes["audit.revise_rate"] = "その週に始まった工廠の走行が無い"
        return None
    return sum(r.get("intake_revised") or 0 for r in here) / len(here)


def metrics(items, runs, monday):
    """(計器の識別子 → 値, 識別子 → 値を出せない理由)。"""
    notes = {}
    out = _done_metrics(_dated(items, monday, "done"), notes)
    out["unconverged"] = len(_dated(items, monday, "unconverged"))
    out.update({f"failure.{c}": n for c, n in _failures(items, monday).items()})
    out["audit.revise_rate"] = _audit_rate(runs, monday, notes)
    return out, notes


def week_record(items, runs, monday):
    values, notes = metrics(items, runs, monday)
    end = monday + datetime.timedelta(days=6)
    return {"week": week_name(monday), "start": monday.isoformat(), "end": end.isoformat(), "metrics": values, "notes": notes}


def build(items, runs, monday):
    """指定の週の記録に、前の 2 週（新しい順）の記録を足したもの。"""
    prev = [week_record(items, runs, monday - datetime.timedelta(days=7 * k)) for k in range(1, BACK + 1)]
    return {**week_record(items, runs, monday), "previous": prev}


def cell(v):
    if v is None:
        return "null"
    if isinstance(v, dict):
        return " / ".join(f"{k} {cell(x)}" for k, x in v.items())
    if isinstance(v, list):
        return "、".join(f"{x['name']} {x['tokens']}" for x in v) or "なし"
    return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def render(rep):
    weeks = [rep, *rep["previous"]]
    lines = [f"# 週次の報告 {rep['week']}（{rep['start']}〜{rep['end']}）", "",
             "| 計器 | " + " | ".join(w["week"] for w in weeks) + " |", "|:--|" + ":--|" * len(weeks)]
    lines += [f"| {k} | " + " | ".join(cell(w["metrics"].get(k)) for w in weeks) + " |" for k in rep["metrics"]]
    notes = [f"- {w['week']} {k}: null（{why}）" for w in weeks for k, why in w["notes"].items()]
    return "\n".join(lines + [""] + notes) + "\n"


def write(rep, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rep['week']}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / f"{rep['week']}.md").write_text(render(rep), encoding="utf-8")
    return out / f"{rep['week']}.md"


def generate(queue, records, out, monday):
    items = northstar.load_items(queue) if Path(queue).exists() else {}
    return write(build(items, factory_runs(records), monday), out)


def ensure_previous(cfg, today=None, records=None):
    """H ライン用。前の週の報告が cfg["out"]/weekly/ に無ければ書く。書いたパス、あれば None。"""
    out = Path(cfg["out"]) / "weekly"
    monday = previous_week(today or datetime.date.today())
    if (out / f"{week_name(monday)}.json").exists():
        return None
    return generate(Path(cfg["out"]) / "queue.json", records or ROOT / "bench" / "records", out, monday)


def latest_within(out, days, now=None):
    now = time.time() if now is None else now
    return any(now - p.stat().st_mtime <= days * 86400 for p in Path(out).glob("*-W*.json"))


def main(argv=None, today=None, now=None):
    ap = argparse.ArgumentParser(prog="python -m harness.weekly_report")
    ap.add_argument("--week", help="ISO 週（2026-W42）。省くと前の週")
    ap.add_argument("--queue", help="queue.json のパス（省くと設定の out の下）")
    ap.add_argument("--records", default=str(ROOT / "bench" / "records"), help="工廠の走行の記録の置き場")
    ap.add_argument("--out", default=str(ROOT / "reports" / "weekly"), help="書き先")
    ap.add_argument("--latest-within-days", type=float, metavar="N", help="書き先に N 日以内の報告があれば 0、無ければ 1")
    args = ap.parse_args(argv)
    if args.latest_within_days is not None:
        return 0 if latest_within(args.out, args.latest_within_days, now) else 1
    queue = args.queue
    if queue is None:
        import hline_base
        queue = Path(hline_base.load_config()["out"]) / "queue.json"
    monday = parse_week(args.week) if args.week else previous_week(today or datetime.date.today())
    print(generate(queue, args.records, args.out, monday))
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
