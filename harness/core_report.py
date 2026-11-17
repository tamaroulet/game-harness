"""制御核の走りの記録（core_record.json）を、課題ごとの 1 行にまとめる（X-02 の結果を読む道具。設計 §5-2 の外す条件に照らす）。

    python -m harness.core_report <ランの置き場>... [--json]

出すもの（課題ごと）：腕・止まった理由・合否・手番の数・秒、手番ごとの C1（見えるテスト）と C2（pyflakes）の落ちの数、
C2 が単独で落ちを拾った手番（C1 が通り C2 が落ちた手番）、C2 が落ちを拾った手番、C2 の落ちが後の手番で消えたか。
"""
import argparse
import json
import sys
from pathlib import Path


def summarize(record):
    rounds = []
    for i, rnd in enumerate(record.get("rounds") or [], 1):
        by = {v["id"]: v for v in (rnd.get("verify") or [])}
        c1, c2 = by.get("C1"), by.get("C2")
        rounds.append({"round": i, "turn_ok": (rnd.get("turn") or {}).get("ok"),
                       "c1_ok": None if c1 is None else c1["ok"], "c1_failed": None if c1 is None else len(c1["failed"]),
                       "c2_ok": None if c2 is None else c2["ok"], "c2_failed": None if c2 is None else len(c2["failed"]),
                       "c2_items": [] if c2 is None else list(c2["failed"])})
    c2_caught = [r["round"] for r in rounds if r["c2_ok"] is False]
    c2_only = [r["round"] for r in rounds if r["c2_ok"] is False and r["c1_ok"] is True]
    fixed = []
    for r in rounds:
        if r["c2_ok"] is False:
            later = [x for x in rounds if x["round"] > r["round"] and x["c2_ok"] is not None]
            fixed.append(bool(later) and later[0]["c2_ok"] is True)
    return {"unit": record.get("unit"), "arm": record.get("arm"), "stop": record.get("stop"),
            "decision": record.get("decision"), "turns": len(rounds), "seconds": record.get("seconds"),
            "c1_failed_by_round": [r["c1_failed"] for r in rounds], "c2_failed_by_round": [r["c2_failed"] for r in rounds],
            "c2_caught_rounds": c2_caught, "c2_only_rounds": c2_only,
            "c2_fixed_next": fixed, "c2_items": sorted({x for r in rounds for x in r["c2_items"]})}


def collect(run_dirs):
    out = []
    for run in map(Path, run_dirs):
        for rec in sorted(run.rglob("core_record.json")):
            out.append(summarize(json.loads(rec.read_text(encoding="utf-8"))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="core_record.json を課題ごとにまとめる")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = collect(a.runs)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        print(f"{r['unit']} | {r['arm']} | stop={r['stop']} decision={r['decision']} turns={r['turns']} s={r['seconds']}\n"
              f"  C1 failed by round {r['c1_failed_by_round']} | C2 failed by round {r['c2_failed_by_round']}\n"
              f"  C2 caught {r['c2_caught_rounds']} | C2 only {r['c2_only_rounds']} | fixed next {r['c2_fixed_next']}")
    only = [r for r in rows if r["c2_only_rounds"]]
    print(f"TOTAL tasks {len(rows)} | tasks with C2-only rounds {len(only)} | C2-only rounds "
          f"{sum(len(r['c2_only_rounds']) for r in rows)} | C2-caught rounds {sum(len(r['c2_caught_rounds']) for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import exitcode  # noqa: E402

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
