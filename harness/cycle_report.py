"""周期の報告：外側の周期の区切りを暦の週から走行の周期に替える（W-118）。H ラインの out の下の cycles/<番号>.json と .md に書く。

    前の周期の報告の後に済みになった What（内側・外側の両方）が cfg["cycle_min_done"]（既定 10）件以上になり、
    外側の統合 PR が開いていないとき、次の番号の報告を書く（ensure_next）。報告が 1 つも無いときは、今までに済みになった
    What の直近の下限の件数ぶんで番号 0 の報告を書き、周期 1 をそこから始める。

中身は、番号・区切りの始まりと終わり・数えた What の名前と、週次の報告（weekly_report）と同じ鍵・同じ数え方の計器を、その周期の What だけで数えたもの。
外側の統合 PR は周期に 1 本（hline_outer.open_pr）。週次の報告は別に書き続ける。harness.hline を import しない。
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import failure_classes  # noqa: E402
import northstar  # noqa: E402
import weekly_report  # noqa: E402

MIN_DONE = 10
NONE = -1   # 周期の報告がまだ無いときの番号（st["outer"]["cycle"] にも入る）


def minimum(cfg):
    return int(cfg.get("cycle_min_done", MIN_DONE))


def cycles_dir(cfg):
    return Path(cfg["out"]) / "cycles" if cfg.get("out") else None


def latest(cfg):
    """最新の周期の報告（dict）。無い・読めないときは None。"""
    out = cycles_dir(cfg)
    found = []
    for p in (out.glob("*.json") if out and out.is_dir() else []):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            found.append((int(rec["cycle"]), rec))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(found, key=lambda x: x[0])[1] if found else None


def latest_number(cfg):
    rec = latest(cfg)
    return rec["cycle"] if rec else NONE


def _utc(text):
    return northstar.ended_at({"status": "done", "done_at": text})


def done_since(items, last):
    """前の周期の報告の終わりより後に済みになった What の (済みの時刻, 名前) を時刻の順に。last が None なら全部。"""
    cut = _utc(last["end"]) if last else None
    found = [(t, n) for n, i in items.items() if i.get("status") == "done" and (t := northstar.ended_at(i)) is not None]
    return sorted(x for x in found if cut is None or x[0] > cut)


def state(cfg, st):
    """今の周期：番号・済みの件数・下限・この周期に外側の統合 PR を出したか。"""
    last = latest(cfg)
    n = last["cycle"] if last else NONE
    sent = (st.get("outer") or {}).get("cycle") == n and bool((st.get("outer") or {}).get("opened_at"))
    return {"number": n + 1 if last else 0, "done": len(done_since(st["items"], last)), "min": minimum(cfg), "sent": sent}


def metrics(items, names, runs, start, end):
    """週次の報告と同じ鍵の計器を、names の What だけで数える。(値, 値を出せない理由)。"""
    notes = {}
    mine = {n: items[n] for n in names}
    out = weekly_report._done_metrics(mine, notes)
    lo, hi = _utc(start), _utc(end)
    stuck = {n: i for n, i in items.items() if i.get("status") == "unconverged" and (t := northstar.ended_at(i)) and lo < t <= hi}
    out["unconverged"] = len(stuck)
    tries = failure_classes.counts({**mine, **stuck})[1]
    out.update({f"failure.{c}": sum(row.get(c, 0) for row in tries.values()) for c in failure_classes.COLUMNS})
    here = [r for d, r in runs if lo.date() <= d <= hi.date()]
    if here:
        out["audit.revise_rate"] = sum(r.get("intake_revised") or 0 for r in here) / len(here)
    else:
        out["audit.revise_rate"] = None
        notes["audit.revise_rate"] = "その周期に始まった工廠の走行が無い"
    return out, notes


def render(rec):
    lines = [f"# 周期の報告 {rec['cycle']}（{rec['start']} 〜 {rec['end']}）", "", f"数えた What: {len(rec['names'])} 件", ""]
    lines += [f"- {n}" for n in rec["names"]] + ["", "| 計器 | 値 |", "|:--|:--|"]
    lines += [f"| {k} | {weekly_report.cell(v)} |" for k, v in rec["metrics"].items()]
    return "\n".join(lines + [""] + [f"- {k}: null（{why}）" for k, why in rec["notes"].items()]) + "\n"


def write(cfg, rec):
    out = cycles_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rec['cycle']:03d}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / f"{rec['cycle']:03d}.md").write_text(render(rec), encoding="utf-8")
    return out / f"{rec['cycle']:03d}.md"


def ensure_next(cfg, st, records=None, now=None):
    """条件がそろえば次の周期の報告を書いてそのパスを返す。そろっていなければ None（外側の統合 PR が開いている間は書かない）。"""
    if (st.get("outer") or {}).get("awaiting_pr") or not cfg.get("out"):
        return None
    last, floor = latest(cfg), minimum(cfg)
    done = done_since(st["items"], last)
    if len(done) < floor:
        return None
    if last is None:
        done = done[-floor:]
    names = [n for _, n in done]
    stamp = (now or datetime.datetime.now().astimezone()).isoformat(timespec="seconds")
    start = last["end"] if last else st["items"][names[0]]["done_at"]
    runs = weekly_report.factory_runs(records or weekly_report.ROOT / "bench" / "records")
    values, notes = metrics(st["items"], names, runs, start, stamp)
    return write(cfg, {"cycle": last["cycle"] + 1 if last else 0, "start": start, "end": stamp, "names": names, "metrics": values, "notes": notes})
