"""外側の周期の記録の集計：週次の周期が回った週と、改善が計器を良くした件数を数える（W-59、横木 R4 と W-61 の判定の材料）。

    python -m harness.outer_cycles --min <n> [--improved <k>] [--out <H ラインの out>] [--records <リポジトリの根>]

**回った週**: ISO 週 W は、前の週の報告 weekly/<W-1>.json があり、マイルストーン outer の What が W の中（merged_at）にマージされ、
そのマージの head すべてに合格の非劣性の記録（bench/records/regress/<head>.json。hline_merge.regress_check と同じ判定）があるときだけ数える。
**改善**: マージされた外側の What の本文（queue/<名前>.md）の `計器: <識別子> <上|下>` の行の計器が、マージの翌週の報告で
前の 2 週の報告の平均より向きの通りに良くなったもの。識別子は報告の metrics の鍵で、辞書は `.` でたどる。
--min を満たし --improved（既定 0）も満たせば 0、満たさなければ 1。置き場が無くても例外で落ちず、回った週 0 として 1 で終わる。
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_merge  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hline.json"
OUTER = "outer"
METER = re.compile(r"^\s*計器\s*[:：](.*)$")
METER_OK = re.compile(r"^\s*(\S+)\s+(上|下)\s*$")
WEEK = datetime.timedelta(days=7)


def week_name(monday):
    y, w, _ = monday.isocalendar()
    return f"{y}-W{w:02d}"


def monday_of(day):
    return day - datetime.timedelta(days=day.weekday())


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_items(out):
    st = read_json(Path(out) / "queue.json")
    items = st.get("items") if isinstance(st, dict) else None
    return items if isinstance(items, dict) else {}


def merged_day(item):
    try:
        return datetime.datetime.fromisoformat(str(item["merged_at"])).date()
    except (KeyError, ValueError, TypeError):
        return None


def merged_outer(items):
    """マージの日時が読める外側の済みの What。(名前, 項目, マージの日) の一覧。"""
    found = []
    for name, i in sorted(items.items()):
        if isinstance(i, dict) and i.get("milestone") == OUTER and i.get("status") == "done" and i.get("merged"):
            day = merged_day(i)
            if day:
                found.append((name, i, day))
    return found


def report_of(out, monday):
    rep = read_json(Path(out) / "weekly" / f"{week_name(monday)}.json")
    return rep if isinstance(rep, dict) else None


def week_verdict(out, records, monday, entries):
    """(回ったか, 表示の行)。"""
    has_report = report_of(out, monday - WEEK) is not None
    heads = {i.get("merged_head") for _, i, _ in entries}
    why = []
    if not has_report:
        why.append(f"前の週の報告 {week_name(monday - WEEK)}.json が無い")
    if not entries:
        why.append("外側の What のマージが無い")
    for h in sorted(heads, key=str):
        ok, reason, _ = hline_merge.regress_check(records, h)
        if not ok:
            why.append(f"head {str(h)[:7]}: {reason}")
    turned = not why
    shown = "回った" if turned else "回らない（" + "／".join(why) + "）"
    return turned, f"{week_name(monday)}: 報告 {'あり' if has_report else 'なし'}、マージした外側の What {len(entries)} 件、{shown}"


def longest_run(turned):
    best = run = 0
    last = None
    for m in sorted(turned):
        run = run + 1 if last is not None and m - last == WEEK else 1
        best, last = max(best, run), m
    return best


def metric_value(report, ident):
    v = report.get("metrics") if isinstance(report, dict) else None
    for part in ident.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def meter_line(out, name):
    """(識別子, 向き, 理由)。計器の行が無ければ None。読めない行は識別子と向きが None で理由つき。"""
    try:
        text = (Path(out) / "queue" / f"{name}.md").read_text(encoding="utf-8")
    except OSError:
        return None, None, "本文の写しを読めない"
    for line in text.splitlines():
        m = METER.match(line)
        if m:
            ok = METER_OK.match(m.group(1))
            return (ok.group(1), ok.group(2), None) if ok else (None, None, f"計器の行を読めない（{line.strip()}）")
    return None


def judge(out, name, monday):
    """(改善か, 理由)。計器の行が無い What は None。"""
    found = meter_line(out, name)
    if found is None:
        return None
    ident, direction, why = found
    if why:
        return False, why
    after = report_of(out, monday + WEEK)
    before = [report_of(out, monday - WEEK * k) for k in (2, 1)]
    if after is None or None in before:
        return False, f"{ident}: 比べる週の報告が足りない"
    new, old = metric_value(after, ident), [metric_value(r, ident) for r in before]
    if new is None or None in old:
        return False, f"{ident}: 値が数でない（翌週 {new}、前の 2 週 {old}）"
    mean = sum(old) / 2
    better = new > mean if direction == "上" else new < mean
    return better, f"{ident} {direction}: 翌週 {new}、前の 2 週の平均 {mean}" + ("" if better else "（良くなっていない）")


def count_cycles(out, records):
    entries = merged_outer(load_items(out))
    weeks = {}
    for e in entries:
        weeks.setdefault(monday_of(e[2]), []).append(e)
    turned, lines = set(), []
    for monday in sorted(weeks):
        ok, line = week_verdict(out, records, monday, weeks[monday])
        lines.append(line)
        if ok:
            turned.add(monday)
    improved, notes = 0, []
    for name, _, day in entries:
        got = judge(out, name, monday_of(day))
        if got is None:
            continue
        improved += got[0]
        notes.append(f"{name}（{week_name(monday_of(day))} にマージ）: {'改善' if got[0] else '改善に数えない'} {got[1]}")
    return longest_run(turned), len(turned), improved, lines, notes


def default_out():
    cfg = read_json(CONFIG)
    return cfg.get("out") if isinstance(cfg, dict) else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.outer_cycles")
    ap.add_argument("--min", type=int, required=True, dest="minimum", help="続いた週の下限")
    ap.add_argument("--improved", type=int, default=0, help="改善の件数の下限")
    ap.add_argument("--out", help="H ラインの out（queue.json・queue/・weekly/ の親）。省くと config/hline.json の out")
    ap.add_argument("--records", default=str(hline_merge.RECORDS), help="bench/records/regress の親（リポジトリの根）")
    args = ap.parse_args(argv)
    out = args.out or default_out()
    if out:
        run, total, improved, lines, notes = count_cycles(out, args.records)
    else:
        run, total, improved, lines, notes = 0, 0, 0, ["置き場（out）を読めない"], []
    print("\n".join(["## 週ごとの判定"] + (lines or ["外側の What のマージした週が無い"])))
    print("\n".join(["## 改善の候補"] + (notes or ["計器の行のある What が無い"])))
    ok = run >= args.minimum and improved >= args.improved
    print(f"回った週 {total}、続いた週 {run}（下限 {args.minimum}）、改善 {improved}（下限 {args.improved}）: {'満たす' if ok else '満たさない'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
