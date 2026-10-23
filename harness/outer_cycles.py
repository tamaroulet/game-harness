"""外側の周期の記録の集計：走行の周期が回った件数と、改善が計器を良くした件数を数える（W-59、W-119、横木 R4 と W-61 の判定の材料）。

    python -m harness.outer_cycles --min <n> [--improved <k>] [--out <H ラインの out>] [--records <リポジトリの根>]

周期 k は、周期の報告 k−1（cycles/<番号>.json、cycle_report.py）の終わりの後から、周期の報告 k の終わりまで（k の報告がまだ無ければ今まで）。
マージされた外側の What は、その merged_at が入る周期に数える。番号 0 の報告の終わりより前にマージされたものは数えない。週次の報告は使わない。

**回った周期**: 周期 k は、周期の報告 k−1 があり、マイルストーン outer の What が k の中にマージされ、
そのマージの head すべてに合格の非劣性の記録（bench/records/regress/<head>.json。hline_merge.regress_check と同じ判定）があるときだけ数える。
続いた周期は、番号が続けて回った周期の最長の連なり。
**改善**: マージされた外側の What の本文（queue/<名前>.md）の `計器: <識別子> <上|下>` の行の計器が、周期の報告 k+1 で
周期の報告 k−2・k−1 の平均より向きの通りに良くなったもの。比べる報告が足りなければ数えず理由を出す。識別子は報告の metrics の鍵で、辞書は `.` でたどる。
--min を満たし --improved（既定 0）も満たせば 0、満たさなければ 1。置き場が無くても例外で落ちず、回った周期 0 として 1 で終わる。
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
import machine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hline.json"
OUTER = "outer"
METER = re.compile(r"^\s*計器\s*[:：](.*)$")
METER_OK = re.compile(r"^\s*(\S+)\s+(上|下)\s*$")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_items(out):
    st = read_json(Path(out) / "queue.json")
    items = st.get("items") if isinstance(st, dict) else None
    return items if isinstance(items, dict) else {}


def to_time(text):
    """ISO 8601 の文字列を時刻に。読めなければ None。時差が無ければ今の地域の時刻とみなす。"""
    try:
        t = datetime.datetime.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo else t.astimezone()


def merged_outer(items):
    """マージの日時が読める外側の済みの What。(名前, 項目, マージの時刻) の一覧。"""
    found = []
    for name, i in sorted(items.items()):
        if isinstance(i, dict) and i.get("milestone") == OUTER and i.get("status") == "done" and i.get("merged"):
            at = to_time(i.get("merged_at"))
            if at:
                found.append((name, i, at))
    return found


def load_reports(out):
    """周期の報告 cycles/*.json。{番号: (終わりの時刻, 報告)}。読めないものは飛ばす。"""
    found = {}
    d = Path(out) / "cycles"
    for p in (sorted(d.glob("*.json")) if d.is_dir() else []):
        rec = read_json(p)
        try:
            n, end = int(rec["cycle"]), to_time(rec["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if end:
            found[n] = (end, rec)
    return found


def cycle_of(reports, at):
    """時刻 at が入る周期の番号。k の報告がまだ無ければ最新の報告の次。報告が無ければ 0。"""
    later = [n for n, (end, _) in reports.items() if at <= end]
    if later:
        return min(later)
    return max(reports) + 1 if reports else 0


def cycle_verdict(reports, records, k, entries):
    """(回ったか, 表示の行)。"""
    has_report = (k - 1) in reports
    heads = {i.get("merged_head") for _, i, _ in entries}
    why = []
    if not has_report:
        why.append(f"前の周期の報告 {k - 1} が無い")
    if not entries:
        why.append("外側の What のマージが無い")
    for h in sorted(heads, key=str):
        ok, reason, _ = hline_merge.regress_check(records, h)
        if not ok:
            why.append(f"head {str(h)[:7]}: {reason}")
    turned = not why
    shown = "回った" if turned else "回らない（" + "／".join(why) + "）"
    return turned, f"周期 {k}: 報告 {'あり' if has_report else 'なし'}、マージした外側の What {len(entries)} 件、{shown}"


def longest_run(turned):
    best = run = 0
    last = None
    for k in sorted(turned):
        run = run + 1 if last is not None and k - last == 1 else 1
        best, last = max(best, run), k
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


def judge(reports, out, name, k):
    """(改善か, 理由)。計器の行が無い What は None。"""
    found = meter_line(out, name)
    if found is None:
        return None
    ident, direction, why = found
    if why:
        return False, why
    after = reports.get(k + 1)
    before = [reports.get(k - j) for j in (2, 1)]
    if after is None or None in before:
        return False, f"{ident}: 比べる周期の報告が足りない"
    new, old = metric_value(after[1], ident), [metric_value(r[1], ident) for r in before]
    if new is None or None in old:
        return False, f"{ident}: 値が数でない（次の周期 {new}、前の 2 周期 {old}）"
    mean = sum(old) / 2
    better = new > mean if direction == "上" else new < mean
    return better, f"{ident} {direction}: 次の周期 {new}、前の 2 周期の平均 {mean}" + ("" if better else "（良くなっていない）")


def count_cycles(out, records):
    reports = load_reports(out)
    first = reports[0][0] if 0 in reports else None
    entries = [e for e in merged_outer(load_items(out)) if first is not None and e[2] > first]   # 番号 0 の報告より前は数えない
    cycles = {}
    for e in entries:
        cycles.setdefault(cycle_of(reports, e[2]), []).append(e)
    turned, lines = set(), []
    for k in sorted(cycles):
        ok, line = cycle_verdict(reports, records, k, cycles[k])
        lines.append(line)
        if ok:
            turned.add(k)
    improved, notes = 0, []
    for k in sorted(cycles):
        for name, _, _ in cycles[k]:
            got = judge(reports, out, name, k)
            if got is None:
                continue
            improved += got[0]
            notes.append(f"{name}（周期 {k} にマージ）: {'改善' if got[0] else '改善に数えない'} {got[1]}")
    return longest_run(turned), len(turned), improved, lines, notes


def default_out():
    cfg = read_json(CONFIG)
    return machine.expand(cfg.get("out")) if isinstance(cfg, dict) else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.outer_cycles")
    ap.add_argument("--min", type=int, required=True, dest="minimum", help="続いた周期の下限")
    ap.add_argument("--improved", type=int, default=0, help="改善の件数の下限")
    ap.add_argument("--out", help="H ラインの out（queue.json・queue/・cycles/ の親）。省くと config/hline.json の out")
    ap.add_argument("--records", default=str(hline_merge.RECORDS), help="bench/records/regress の親（リポジトリの根）")
    args = ap.parse_args(argv)
    out = args.out or default_out()
    if out:
        run, total, improved, lines, notes = count_cycles(out, args.records)
    else:
        run, total, improved, lines, notes = 0, 0, 0, ["置き場（out）を読めない"], []
    print("\n".join(["## 周期ごとの判定"] + (lines or ["外側の What のマージした周期が無い"])))
    print("\n".join(["## 改善の候補"] + (notes or ["計器の行のある What が無い"])))
    ok = run >= args.minimum and improved >= args.improved
    print(f"回った周期 {total}、続いた周期 {run}（下限 {args.minimum}）、改善 {improved}（下限 {args.improved}）: {'満たす' if ok else '満たさない'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
