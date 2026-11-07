"""改善ループの機械の判定（v3 §7-7 の 3・4）：基準のステージの分類・対の実行の対象の選び・直した数と壊した数・回帰の守りの線。

読むだけで、何も書かない。行（row）は採点（chain_score.finish・bench_pair.finish）が公開の集計に書く形：
{"k": 段, "invalid": 無効の印か False, "new": [通った, 全部], "kept": [通った, 全部], "tokens": 数, ...}。
線は Git の設定（config/cycle.json）に置く。変えるのは高リスクの PR。

    python -m harness.cycle_judge --repo <リポジトリの根> <tag>...    ランごとの合計を出す（確かめ用）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_pair  # noqa: E402
import exitcode  # noqa: E402
import machine  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "cycle.json"
PASS, FAIL, FLAP, UNKNOWN = "pass", "fail", "flap", "unknown"
PUBLIC_FULL = "public_full.json"


def settings(path=None):
    return machine.read_json(path or CONFIG)


# ============================================================ 行の読み方

def public_rows(repo, tag):
    """公開の集計の {(土台, 段): 行}。開発用のセットと対の実行はここにステージごとの行がある。"""
    return bench_pair.stage_rows(repo, tag)[0]


def private_rows(runs, tag):
    """held-out の {(土台, 段): 行}。公開の集計は合計だけなので、oracles の下の public_full.json から読む（操縦士のアカウントだけが読める）。"""
    rows = {}
    for path in sorted((Path(runs) / tag).glob(f"*/{PUBLIC_FULL}")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows.update({(doc.get("base"), r.get("k")): r for r in doc.get("stages") or []})
    return rows


def totals(rows):
    """ランの合計：通ったステージの数・ステージの数・新しい試験・既存テストの保持・トークン・無効のステージの数。"""
    rows = list(rows.values()) if isinstance(rows, dict) else list(rows)
    def add(key):
        pairs = [r[key] for r in rows if isinstance(r.get(key), list) and len(r[key]) == 2]
        return [sum(p[0] for p in pairs), sum(p[1] for p in pairs)]
    tokens = [r.get("tokens") for r in rows]
    return {"stages": len(rows), "passed": sum(map(bench_pair.passed, rows)), "new": add("new"), "kept": add("kept"),
            "invalid": sum(1 for r in rows if r.get("invalid")),
            "tokens": sum(t for t in tokens if isinstance(t, int) and not isinstance(t, bool))}


# ============================================================ §7-7 の 3：対の実行

def outcome(row):
    """1 回のランのステージの結果。行が無い・無効（前のステージが無効を含む）なら、そのステージの自分の結果は分からない。"""
    if row is None or row.get("invalid"):
        return UNKNOWN
    return PASS if bench_pair.passed(row) else FAIL


def classify(first, second):
    """基準の 2 回の {(土台, 段): pass（2 回とも通った）| fail（2 回とも落ちた）| flap（揺れた）| unknown}。"""
    classes = {}
    for key in set(first) | set(second):
        a, b = outcome(first.get(key)), outcome(second.get(key))
        classes[key] = UNKNOWN if UNKNOWN in (a, b) else (a if a == b else FLAP)
    return classes


def order(keys):
    return sorted(keys, key=lambda x: (str(x[0]), x[1] if isinstance(x[1], int) else -1))


def targets(named, classes):
    """提案が名指しした {(土台, 段)} のうち、結果がそろったステージ（対の実行の対象）。揺れたステージは走らせない（§7-7 の 3）。"""
    return order(k for k in named if classes.get(k) in (PASS, FAIL))


def pair_counts(picked, classes, pairs):
    """pairs は対の実行の (旧版の行, 新版の行) の並び（{(土台, 段): 行} の組）。§7-7 の 3 の数え方：
    直した＝基準で 2 回とも落ちて、旧版が落ち、新版が通った。壊した＝基準で 2 回とも通って、旧版が通り、新版が落ちた。
    旧版が基準と違う結果を出したステージと、どちらかの行が欠けるか無効のものは数えず、理由を残す。"""
    fixed, broke, same, skipped = 0, 0, 0, []
    for old_rows, new_rows in pairs:
        for key in picked:
            want, old, new = classes.get(key), outcome(old_rows.get(key)), outcome(new_rows.get(key))
            if UNKNOWN in (old, new):
                skipped.append({"stage": list(key), "why": "対の行が欠けるか無効"})
            elif old != want:
                skipped.append({"stage": list(key), "why": "旧版が基準と違う"})
            elif want == FAIL and new == PASS:
                fixed += 1
            elif want == PASS and new == FAIL:
                broke += 1
            else:
                same += 1
    return {"fixed": fixed, "broke": broke, "same": same, "skipped": skipped, "ok": fixed > broke}


# ============================================================ §7-7 の 4：回帰の守り

def rate(pair):
    return pair[0] / pair[1] if pair[1] else 0.0


def cost_line(baselines, ratio):
    """費用の線：基準のトークンの平均 × ratio。"""
    return sum(b["tokens"] for b in baselines) / len(baselines) * ratio


def common(run, baselines, guard):
    """両方のセットに当てる線の外れの文の列。"""
    why = []
    if rate(run["kept"]) < guard["kept_min"]:
        why.append(f"既存テストの保持 {run['kept'][0]}/{run['kept'][1]} が {guard['kept_min']:.0%} を下回る")
    line = cost_line(baselines, guard["cost_ratio"])
    if run["tokens"] > line:
        why.append(f"トークン {run['tokens']} が線 {line:.0f} を越える")
    return why


def guard_dev(run, baselines, guard):
    """開発用のセット：通ったステージが、基準の低い方から dev_margin_stages 段まで。"""
    floor = min(b["passed"] for b in baselines) - guard["dev_margin_stages"]
    why = [] if run["passed"] >= floor else [f"通ったステージ {run['passed']} が線 {floor} を下回る"]
    why += common(run, baselines, guard)
    return {"ok": not why, "why": why, "floor": floor}


def guard_held(run, baselines, guard):
    """held-out：通ったステージが held_passed 以上、または held_fallback の 3 つを全部満たす（11-05 の承認）。"""
    back = guard["held_fallback"]
    ok = run["passed"] >= guard["held_passed"] or (run["passed"] >= back["passed"] and run["new"][0] >= back["new"]
                                                   and rate(run["kept"]) >= back["kept_min"])
    why = [] if ok else [f"通ったステージ {run['passed']}・新しい試験 {run['new'][0]}/{run['new'][1]} が線に届かない"]
    why += common(run, baselines, guard)
    return {"ok": not why, "why": why, "floor": guard["held_passed"]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.cycle_judge", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("tag", nargs="+")
    a = ap.parse_args(argv)
    for tag in a.tag:
        print(tag, totals(public_rows(a.repo, tag)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
