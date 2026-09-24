"""A/B 実験の自動ドライバ（docs/design/b4_ab_experiment.md §3）。人間はプロンプトを書かない。

    python -m harness.ab.driver run --condition A --run-id ab-01
    python -m harness.ab.driver run --condition B --run-id ab-01
    python -m harness.ab.driver run --condition A --run-id dry-01 --through T1   乾式の走行（B4-E5）
    python -m harness.ab.driver run-all --repeat 3 --prefix ab
    python -m harness.ab.driver cleanup --run-id ab-01

1 回の走行は、base commit から切った専用の worktree で T1〜T5 を直列に回す。
- 条件 A（単一チャット型）：1 つの会話（agy の --conversation）に、固定テンプレートで作った入力を積む。
  作業ツリーはリセットしない。受入に落ちたら、再試行テンプレートに失敗の要約を埋めて同じ会話に送る
- 条件 B（ステートレス防壁型）：`pipeline.py --local-only` をタスクごとに呼ぶ（試行ごとのリセットと門は pipeline のまま）
- どちらも、タスクの終わりに同じ測定器（measure.py）で測り、metrics.jsonl に 1 行を足す
- 3 回とも受入に落ちたタスクは、失敗として記録して次へ進む（2026-09-24 裁定）
"""
import argparse
import json
import sys
import time
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
import project  # noqa: E402
import telemetry  # noqa: E402
import testgen  # noqa: E402
from ab import common, measure  # noqa: E402
from proc import resolve_cli, run  # noqa: E402

PROJECT = "falling-blocks"
PIPELINE_TTL = 3600
BUDGET = {"added": 250, "deleted": 100}


# ============================================================ 実装役（条件 A）

def _json_in(text):
    """出力の中から、usage を持つ最初の JSON オブジェクトを取り出す。"""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text or ""):
        if ch == "{":
            try:
                d, _ = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(d, dict) and "usage" in d:
                return d
    return {}


def agy_call(imp, prompt, cwd, conversation_id, ttl, runner=run):
    """実装役を 1 回呼ぶ。会話を続けるときは --conversation を付ける（resume）。"""
    args = resolve_cli(imp["cli"]) + [imp["headless_flag"], prompt, imp["auto_approve_flag"],
                                      imp["model_flag"], imp["model_name"]] + imp.get("output_format_args", [])
    if conversation_id:
        args += ["--conversation", conversation_id]
    t0 = time.monotonic()
    rc, out, err = runner(args, cwd, ttl, "実装AI（条件 A）")
    doc = _json_in(out)
    return {"rc": rc, "seconds": round(time.monotonic() - t0, 1),
            "conversation_id": doc.get("conversation_id") or conversation_id,
            "usage": telemetry.cli_usage(out, imp["usage_format"]), "out": out, "err": err}


def failing_of(results, classes, task_id):
    return sorted(n for n, o in (results or {}).items()
                  if measure.task_of(n, classes) == task_id and o != "Passed")


def run_task_a(ctx, task, unit, state, call=agy_call, fast=measure.run_fast):
    """条件 A の 1 タスク。{"attempts", "accepted", "calls"}。"""
    m, wt, out = ctx["m"], ctx["wt"], ctx["out"]
    tpl = ctx["templates"]
    calls, accepted, results, text = [], False, None, ""
    for attempt in range(1, m["max_attempts"] + 1):
        if attempt == 1:
            prompt = tpl["initial"].format(task_id=task["id"], title=task["title"], workdir=str(wt),
                                           prompt=unit["prompt"], interface=testgen.render_interface(unit),
                                           whitelist="\n".join(f"- {p}" for p in unit["whitelist"]),
                                           test_dir=m["test_dir"])
        else:
            n = m["templates"]["retry_tail_lines"]
            prompt = tpl["retry"].format(task_id=task["id"], attempt=attempt - 1, max_attempts=m["max_attempts"],
                                         failed_tests="\n".join(f"- {x}" for x in failing_of(results, ctx["classes"], task["id"]))
                                         or "- （ビルドが通らず、テストを実行できませんでした）",
                                         tail_lines=n, failure_tail="\n".join(text.strip().splitlines()[-n:]))
        r = call(ctx["imp"], prompt, wt, state.get("conversation_id"), ctx["ttl"])
        state["conversation_id"] = r["conversation_id"]
        calls.append({"attempt": attempt, "rc": r["rc"], "seconds": r["seconds"], "usage": r["usage"]})
        (Path(out) / f"{task['id']}_a{attempt}.implementer.log").write_text(
            f"# prompt\n{prompt}\n\n# stdout\n{r['out']}\n\n# stderr\n{r['err']}\n", encoding="utf-8")
        # 実装役がテストを書き換えていても、凍結したものに戻してから測る
        measure.place_frozen_tests(m, wt, ctx["index"], m["test_dir"])
        results, text = fast(wt, ctx["test_project"], out, f"{task['id']}_a{attempt}")
        passed, total = measure.acceptance(results, ctx["classes"], task["id"])
        if total and passed == total:
            accepted = True
            break
    return {"attempts": len(calls), "accepted": accepted, "calls": calls}


# ============================================================ 条件 B

def pipeline_args(unit_path, wt, sandbox, out, tel):
    return [sys.executable, str(common.ROOT / "harness" / "pipeline.py"), "--project", PROJECT,
            "--unit", str(unit_path), "--repo-dir", str(wt), "--local-only",
            "--sandbox", str(sandbox), "--out-dir", str(out), "--telemetry", str(tel)]


def run_task_b(ctx, task, unit, state, runner=run):
    """条件 B の 1 タスク。pipeline の試行・門はそのまま。{"attempts", "accepted", "calls", "pipeline_rc"}。"""
    out = Path(ctx["out"])
    tel = out / f"{task['id']}.pipeline.json"
    unit_path = Path(ctx["m"]["_base"]) / task["unit"]
    rc, stdout, stderr = runner(pipeline_args(unit_path, ctx["wt"], ctx["sandbox"], out / "pipeline" / task["id"], tel),
                                str(common.ROOT), PIPELINE_TTL, f"pipeline（条件 B、{task['id']}）")
    (out / f"{task['id']}.pipeline.log").write_text(f"{stdout}\n{stderr}\n", encoding="utf-8")
    data, _ = telemetry.read(tel)
    attempts = (data or {}).get("attempts", [])
    calls = [{"attempt": a.get("n"), "verdict": a.get("verdict"), "stage": a.get("stage"),
              "usage": (a.get("implementer") or {}).get("usage")} for a in attempts]
    return {"attempts": len(attempts), "accepted": rc == 0, "calls": calls, "pipeline_rc": rc}


# ============================================================ 1 回の走行

def _tokens(calls):
    ins = [((c.get("usage") or {}).get("input_tokens")) for c in calls]
    outs = [((c.get("usage") or {}).get("output_tokens")) for c in calls]
    known = [x for x in ins if isinstance(x, int)]
    return {"input": sum(known) if len(known) == len(ins) else None,
            "output": sum(x for x in outs if isinstance(x, int)) if all(isinstance(x, int) for x in outs) else None,
            "per_call_input": ins}


def run_condition(manifest, condition, run_id, wt_root=common.WT_ROOT, out_root=common.OUT_ROOT,
                  task_runner=None, through=None):
    """through を与えると、T1 からそのタスクまでで止める（乾式の走行用）。途中から始めることはしない。"""
    m = common.load_manifest(manifest)
    m["tasks"] = common.tasks_through(m, through)
    units = [common.unit_of(m, t) for t in m["tasks"]]
    proj = project.load(PROJECT)
    cfg = project.pipeline_config(proj)
    p = common.paths(run_id, condition, wt_root, out_root)
    if p["wt"].exists():
        raise common.ABError(f"worktree が既にあります。先に cleanup してください: {p['wt']}")
    p["out"].mkdir(parents=True, exist_ok=True)
    common.git(["worktree", "add", "-q", "-b", p["branch"], str(p["wt"]), m["base_commit"]],
               proj["repo_dir"], "git worktree add")
    ctx = {"m": m, "wt": p["wt"], "sandbox": p["sandbox"], "out": p["out"], "imp": cfg["implementer"],
           "ttl": cfg["ttl_seconds"]["implementer"], "test_project": proj["fast_test_project"],
           "classes": measure.class_to_task(m, units),
           "templates": {k: (Path(m["_base"]) / m["templates"][k]).read_text(encoding="utf-8")
                         for k in ("initial", "retry")}}
    task_runner = task_runner or (run_task_a if condition == "A" else run_task_b)
    decl = common.ROOT / "projects" / PROJECT / "invariants.json"
    metrics = p["out"] / "metrics.jsonl"

    results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], "baseline")
    prev = measure.passing(results)
    state = {"conversation_id": None}
    for i, (task, unit) in enumerate(zip(m["tasks"], units), start=1):
        ctx["index"] = i
        t0 = time.monotonic()
        measure.place_frozen_tests(m, p["wt"], i, m["test_dir"])
        start = common.commit_all(p["wt"], f"ab: {task['id']} の凍結した生成テストを置く")
        rec = task_runner(ctx, task, unit, state)
        tampered = measure.place_frozen_tests(m, p["wt"], i, m["test_dir"])
        end = common.commit_all(p["wt"], f"ab: {task['id']} の終わり（条件 {condition}）")
        results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], f"{task['id']}_final")
        passed, total = measure.acceptance(results, ctx["classes"], task["id"])
        broken = measure.p2p_broken(prev, results, task.get("superseded_tests", []))
        inv = measure.invariants(p["wt"], p["out"] / task["id"], m["invariant_seeds"], proj["impl_dir"], decl)
        added, deleted = common.numstat(p["wt"], start, end, proj["impl_dir"])
        line = {"run_id": run_id, "condition": condition, "task": task["id"], "index": i,
                "accepted": bool(total) and passed == total, "attempts": rec["attempts"],
                "acceptance": {"passed": passed, "total": total}, "build_ok": results is not None,
                "p2p_broken": len(broken), "p2p_broken_tests": broken, "invariants": inv,
                "diff": {"added": added, "deleted": deleted},
                "budget_exceeded": added > BUDGET["added"] or deleted > BUDGET["deleted"],
                "tokens": _tokens(rec["calls"]), "tests_tampered": tampered,
                "seconds": round(time.monotonic() - t0, 1), "detail": {k: v for k, v in rec.items() if k != "calls"}}
        with metrics.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"[{condition}] {task['id']}: 受入 {passed}/{total}、P2P の破壊 {len(broken)}、"
              f"不変条件の違反 {inv['failures']}、試行 {rec['attempts']}")
        prev = measure.passing(results)
    return 0


def cleanup(run_id, wt_root=common.WT_ROOT):
    repo = project.load(PROJECT)["repo_dir"]
    for cond in common.CONDITIONS:
        p = common.paths(run_id, cond, wt_root)
        for wt in (p["sandbox"], p["wt"]):
            if wt.exists():
                common.git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove", check=False)
    common.git(["worktree", "prune"], repo, "git worktree prune", check=False)


def run_all(manifest, repeat, prefix):
    """A と B を交互に回す（時間帯の偏りを散らす）。1 回が失敗しても次へ進む。"""
    rc = 0
    for k in range(1, repeat + 1):
        order = common.CONDITIONS if k % 2 else tuple(reversed(common.CONDITIONS))
        for cond in order:
            try:
                run_condition(manifest, cond, f"{prefix}-{k:02d}")
            except common.ABError as e:
                print(f"ABORT（{prefix}-{k:02d} {cond}）: {e}")
                rc = exitcode.ABORT
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="A/B 実験の自動ドライバ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--condition", required=True, choices=common.CONDITIONS)
    r.add_argument("--run-id", required=True)
    r.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    r.add_argument("--through", help="このタスクまでで止める（例: T1）")
    a = sub.add_parser("run-all")
    a.add_argument("--repeat", type=int, default=3)
    a.add_argument("--prefix", default="ab")
    a.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    c = sub.add_parser("cleanup")
    c.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "run":
            return run_condition(args.manifest, args.condition, args.run_id, through=args.through)
        if args.cmd == "run-all":
            return run_all(args.manifest, args.repeat, args.prefix)
        cleanup(args.run_id)
        return 0
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
