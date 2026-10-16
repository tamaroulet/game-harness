"""A/B 実験の事前投資（Layer 2）の実測（docs/design/b4_ab_fairness_audit.md §3.3）。

    python -m harness.ab.precost

凍結したときと同じ入力（要求文 T<k>.md ＋ 全タスク共通の制約 common.md）で、分解役（decompose.py、Claude）を
T1 から順に 1 回ずつ呼び、トークン・費用・秒を測る。凍結物は置き換えない：分解役は base commit から切った
使い捨ての worktree に書き、測り終えたら worktree ごと消す。結果は experiments/b4_ab/results/precost.json。
"""
import json
import sys
import time
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
import project  # noqa: E402
from ab import common  # noqa: E402
from proc import run  # noqa: E402

PROJECT = "falling-blocks"
DECOMPOSE_TTL = 1800
RESULT = common.EXP_DIR / "results" / "precost.json"


def total(calls, key):
    """呼び出しの usage の key の合計。どれかが不明なら None。"""
    vals = [(c.get("usage") or {}).get(key) for c in calls]
    return sum(vals) if vals and all(isinstance(v, (int, float)) for v in vals) else None


def summarize(per_task):
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd")
    out = {k: None for k in keys}
    for k in keys:
        vals = [t[k] for t in per_task]
        out[k] = round(sum(vals), 6) if all(isinstance(v, (int, float)) for v in vals) else None
    out["seconds"] = round(sum(t["seconds"] for t in per_task), 1)
    out["calls"] = sum(t["calls"] for t in per_task)
    out["ok"] = all(t["rc"] == 0 for t in per_task)
    return out


def measure(manifest, wt_root=common.WT_ROOT, out_root=common.OUT_ROOT, resume_from=None, previous=None):
    """resume_from を与えると、その前のタスクは凍結した単位定義を --from-unit で置くだけにして（LLM を呼ばない）
    同じ文脈を作り、測った値は previous（前回の per_task）から引き継ぐ。途中で止まった測定の続き用。"""
    m = common.load_manifest(manifest)
    base = Path(m["_base"])
    common_req = (base / m["common_requirement"]).read_text(encoding="utf-8")
    repo = project.load(PROJECT)["repo_dir"]
    wt = Path(wt_root) / "precost"
    out = Path(out_root) / "precost"
    out.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        raise common.ABError(f"worktree が既にあります: {wt}")
    common.git(["worktree", "add", "-q", "--detach", str(wt), m["base_commit"]], repo, "git worktree add")
    per_task = []
    ids = [t["id"] for t in m["tasks"]]
    if resume_from is not None and resume_from not in ids:
        raise common.ABError(f"タスク {resume_from!r} はマニフェストにありません: {ids}")
    skip = ids[:ids.index(resume_from)] if resume_from else []
    prev = {r["task"]: r for r in (previous or [])}
    missing = [i for i in skip if i not in prev or prev[i]["rc"] != 0]
    if missing:
        raise common.ABError(f"前回の測定に、成功した {missing} の値がありません")
    try:
        for t in m["tasks"]:
            unit = common.unit_of(m, t)
            if t["id"] in skip:
                rc, _, _ = run([sys.executable, str(common.ROOT / "harness" / "decompose.py"), "--project", PROJECT,
                                "--from-unit", str(base / t["unit"]), "--repo-dir", str(wt)],
                               str(common.ROOT), DECOMPOSE_TTL, f"decompose --from-unit（{t['id']}）")
                if rc != 0:
                    raise common.ABError(f"{t['id']} の凍結した単位定義を置けません（rc={rc}）")
                per_task.append(dict(prev[t["id"]], reused=True))
                print(f"{t['id']}: 前回の値を使う（凍結した単位定義を置いた）")
                continue
            src = out / f"{t['id']}.md"
            src.write_text((base / t["requirement"]).read_text(encoding="utf-8") + "\n\n" + common_req,
                           encoding="utf-8")
            tel = out / f"{t['id']}.decompose.json"
            t0 = time.monotonic()
            rc, _, _ = run([sys.executable, str(common.ROOT / "harness" / "decompose.py"), "--project", PROJECT,
                            "--file", str(src), "--id", unit["id"], "--repo-dir", str(wt), "--telemetry", str(tel)],
                           str(common.ROOT), DECOMPOSE_TTL, f"decompose（{t['id']}）")
            wall = round(time.monotonic() - t0, 1)
            doc = json.loads(tel.read_text(encoding="utf-8")) if tel.exists() else {}
            calls = doc.get("calls") or []
            row = {"task": t["id"], "rc": rc, "seconds": wall, "calls": len(calls)}
            for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd"):
                row[k] = total(calls, k)
            per_task.append(row)
            print(f"{t['id']}: rc={rc}、{wall} 秒、呼び出し {len(calls)}、{row['cost_usd']} USD")
    finally:
        common.git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove", check=False)
        common.git(["worktree", "prune"], repo, "git worktree prune", check=False)
    return {"model": "decompose.py（config/decompose.json の cli）", "base_commit": m["base_commit"],
            "per_task": per_task, "total": summarize(per_task)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="事前投資（分解役）の実測")
    ap.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    ap.add_argument("--resume-from", help="このタスクから測り直す（前は前回の precost.json の値を使う）")
    args = ap.parse_args(argv)
    previous = None
    if args.resume_from:
        previous = json.loads(RESULT.read_text(encoding="utf-8"))["per_task"] if RESULT.exists() else []
    try:
        result = measure(args.manifest, resume_from=args.resume_from, previous=previous)
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT
    # 測り直した回の失敗も費用は掛かっている。合計には入れず、捨てた記録として残す
    result["discarded"] = [r for r in (previous or []) if r["rc"] != 0]
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"書き出し: {RESULT}")
    return 0 if result["total"]["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
