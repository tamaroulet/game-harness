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
import os
import sys
import time
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import agy_stream  # noqa: E402
import exitcode  # noqa: E402
import implementer_context  # noqa: E402
import pipeline  # noqa: E402
import propgen  # noqa: E402
import project  # noqa: E402
import telemetry  # noqa: E402
import testgen  # noqa: E402
import tool_policy  # noqa: E402
from adapters import dotnet  # noqa: E402
from ab import common, measure  # noqa: E402
from proc import resolve_cli, run  # noqa: E402

PROJECT = "falling-blocks"
# B の最大 9 回の呼び出し（実装役の TTL 300 秒）と門を収める
PIPELINE_TTL = 7200
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
    """実装役を 1 回呼ぶ。会話を続けるときは --conversation を付ける（resume）。

    stream-json のときは、B（pipeline）と同じ引数と読み取り（agy_stream）を使い、手番ごとの記録を返す。
    """
    if agy_stream.is_stream(imp):
        t0 = time.monotonic()
        rc, out, err = runner(agy_stream.args(imp, resolve_cli(imp["cli"]), prompt, ttl, conversation_id),
                              cwd, ttl, "実装AI（条件 A）")
        parsed = agy_stream.parse(out)
        return {"rc": rc, "seconds": round(time.monotonic() - t0, 1),
                "conversation_id": parsed["conversation_id"] or conversation_id, "usage": parsed["usage"],
                "steps": parsed["steps"], "out": out, "err": err}
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


def call_budget(m):
    """条件 A の呼び出しの上限。B の最大（試行 × 内側ループのターン）にそろえる（監査 F1、裁定 1）。"""
    return m["max_attempts"] * pipeline.MAX_INNER_LOOP_TURNS


def failure_list(results, classes, task_id, trx):
    """再試行に渡す落ちたテスト。B の内側ループと同じ整形（性質テストなら反例の 1 行ずつ、最大 5 行。
    そうでなければ名前と期待値の不一致）。TRX が無ければ名前だけ。"""
    if trx is not None and Path(trx).exists():
        text = dotnet.property_lines(trx) or dotnet.failure_digest(trx)
        if text:
            return text
    return "\n".join(f"- {x}" for x in failing_of(results, classes, task_id))


def failing_of(results, classes, task_id):
    return sorted(n for n, o in (results or {}).items()
                  if measure.task_of(n, classes) == task_id and o != "Passed")


def run_task_a(ctx, task, unit, state, call=agy_call, fast=measure.run_fast):
    """条件 A の 1 タスク。{"attempts", "accepted", "calls"}。"""
    m, wt, out = ctx["m"], ctx["wt"], ctx["out"]
    tpl = ctx["templates"]
    calls, accepted, results, text, trx = [], False, None, "", None
    budget = call_budget(m)
    for attempt in range(1, budget + 1):
        if attempt == 1:
            prompt = tpl["initial"].format(task_id=task["id"], title=task["title"], workdir=str(wt),
                                           prompt=unit["prompt"], interface=testgen.render_interface(unit),
                                           whitelist="\n".join(f"- {p}" for p in unit["whitelist"]),
                                           test_dir=m["test_dir"])
            if agy_stream.is_stream(ctx["imp"]):
                # B と同じく、書き換えてよいファイルと契約の中身を埋め込む（v2.1 §1.1 の 2）。A は会話を積むので、
                # 埋め込むのは最初の呼び出しだけ（単一チャットで最初にファイルを貼るのと同じ）
                prompt += "\n\n" + implementer_context.for_unit(wt, unit, ctx["impl_dir"],
                                                               project.config("unit_schema").get("spec_path"))
        else:
            n = m["templates"]["retry_tail_lines"]
            prompt = tpl["retry"].format(task_id=task["id"], attempt=attempt - 1, max_attempts=budget,
                                         failed_tests=failure_list(results, ctx["classes"], task["id"], trx)
                                         or "- （ビルドが通らず、テストを実行できませんでした）",
                                         tail_lines=n, failure_tail="\n".join(text.strip().splitlines()[-n:]))
        # 道具の指示は B と同じ文面（再試行を含めて編集だけ。v2 §7）
        prompt += "\n\n" + tool_policy.text()
        r = call(ctx["imp"], prompt, wt, state.get("conversation_id"), ctx["ttl"])
        state["conversation_id"] = r["conversation_id"]
        calls.append({"attempt": attempt, "rc": r["rc"], "seconds": r["seconds"], "usage": r["usage"],
                      "steps": r.get("steps"), "prompt_chars": len(prompt)})
        (Path(out) / f"{task['id']}_a{attempt}.implementer.log").write_text(
            f"# prompt\n{prompt}\n\n# stdout\n{r['out']}\n\n# stderr\n{r['err']}\n", encoding="utf-8")
        # 実装役がテストを書き換えていても、凍結したものに戻してから測る
        measure.place_frozen_tests(m, wt, ctx["index"], m["test_dir"])
        results, text = fast(wt, ctx["test_project"], out, f"{task['id']}_a{attempt}")
        trx = Path(out) / f"{task['id']}_a{attempt}.trx"
        # 非公開シードは渡していないので、*_Hidden は数えない（B の内側ループと同じ。非公開は最後の測定で見る）
        passed, total = measure.acceptance(results, ctx["classes"], task["id"], public_only=True)
        if total and passed == total:
            accepted = True
            break
    return {"attempts": len(calls), "accepted": accepted, "calls": calls}


# ============================================================ 条件 B

def pipeline_args(unit_path, wt, sandbox, out, tel, known_failures=None):
    # 門の自己検査（--skip-selftest で省く）は門そのものの健全性の検査で、タスクの仕事ではない。
    # dry-01 では 405.9 秒のうち 177.1 秒を占めた。門の健全性は tests/ と pipeline --selftest で別に確かめる
    return [sys.executable, str(common.ROOT / "harness" / "pipeline.py"), "--project", PROJECT,
            "--unit", str(unit_path), "--repo-dir", str(wt), "--local-only", "--skip-selftest",
            "--sandbox", str(sandbox), "--out-dir", str(out), "--telemetry", str(tel)] + (
                ["--known-failures", str(known_failures)] if known_failures else [])


def failing_names(results):
    """測定の結果で Passed でないテストの名前。ビルドが通らなければ None。"""
    return None if results is None else sorted(n for n, o in results.items() if o != "Passed")


def run_task_b(ctx, task, unit, state, runner=run):
    """条件 B の 1 タスク。pipeline の試行・門はそのまま。{"attempts", "accepted", "calls", "pipeline_rc"}。"""
    out = Path(ctx["out"])
    tel = out / f"{task['id']}.pipeline.json"
    unit_path = Path(ctx["m"]["_base"]) / task["unit"]
    # 前のタスクの終わりに落ちていたテストは、base の検査と P2P から外す（S2）。A の測定器も
    # 「前のタスクの終わりに通っていたもの」だけを P2P に数えるので、同じ扱いになる
    known = out / f"{task['id']}.known_failures.json"
    known.write_text(json.dumps(state.get("known_failures") or [], ensure_ascii=False), encoding="utf-8")
    rc, stdout, stderr = runner(pipeline_args(unit_path, ctx["wt"], ctx["sandbox"], out / "pipeline" / task["id"], tel,
                                              known),
                                str(common.ROOT), PIPELINE_TTL, f"pipeline（条件 B、{task['id']}）")
    (out / f"{task['id']}.pipeline.log").write_text(f"{stdout}\n{stderr}\n", encoding="utf-8")
    data, _ = telemetry.read(tel)
    attempts = (data or {}).get("attempts", [])
    # 内側ループの全呼び出しを数える（監査 F2）。implementer_calls の無い古い記録は最後の呼び出しだけ
    calls = [{"attempt": a.get("n"), "verdict": a.get("verdict"), "stage": a.get("stage"), "usage": call.get("usage")}
             for a in attempts for call in (a.get("implementer_calls") or [a.get("implementer") or {}])]
    return {"attempts": len(attempts), "implementer_calls": len(calls), "accepted": rc == 0, "calls": calls,
            "pipeline_rc": rc}


# ============================================================ 1 回の走行

def _tokens(calls):
    """呼び出しの利用量の合計。1 つでも不明なら None（推測で埋めない）。キャッシュ読みも数える（監査 F6）。"""
    def total(key):
        xs = [(c.get("usage") or {}).get(key) for c in calls]
        return sum(xs) if all(isinstance(x, int) for x in xs) else None
    ins = [((c.get("usage") or {}).get("input_tokens")) for c in calls]
    # partial：TTL で打ち切った呼び出しがあり、その分は終わった手番までの足し込み（下限）（v2.1 §1.2）
    return {"input": total("input_tokens"), "output": total("output_tokens"),
            "cache_read": total("cache_read_tokens"), "per_call_input": ins,
            "partial": any((c.get("usage") or {}).get("partial") for c in calls)}


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
           "impl_dir": proj["impl_dir"],
           "classes": measure.class_to_task(m, units),
           "templates": {k: (Path(m["_base"]) / m["templates"][k]).read_text(encoding="utf-8")
                         for k in ("initial", "retry")}}
    task_runner = task_runner or (run_task_a if condition == "A" else run_task_b)
    decl = common.ROOT / "projects" / PROJECT / "invariants.json"
    metrics = p["out"] / "metrics.jsonl"

    results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], "baseline")
    prev = measure.passing(results)
    state = {"conversation_id": None, "known_failures": failing_names(results) or []}
    for i, (task, unit) in enumerate(zip(m["tasks"], units), start=1):
        ctx["index"] = i
        t0 = time.monotonic()
        measure.place_frozen_tests(m, p["wt"], i, m["test_dir"])
        start = common.commit_all(p["wt"], f"ab: {task['id']} の凍結した生成テストを置く")
        rec = task_runner(ctx, task, unit, state)
        seconds = round(time.monotonic() - t0, 1)
        t1 = time.monotonic()
        tampered = measure.place_frozen_tests(m, p["wt"], i, m["test_dir"])
        end = common.commit_all(p["wt"], f"ab: {task['id']} の終わり（条件 {condition}）")
        # 測定は公開と非公開の両方のシードで（v2 §5.3）。非公開シードは走行とタスクで決まり、実装役には渡らない
        env = None
        if common.is_v2(m):
            seeds = common.hidden_seeds(run_id, task["id"], m["measure_hidden_seeds"])
            env = dict(os.environ, **{propgen.HIDDEN_ENV: ",".join(str(x) for x in seeds)})
        results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], f"{task['id']}_final", env=env)
        passed, total = measure.acceptance(results, ctx["classes"], task["id"])
        pub_passed, pub_total = measure.acceptance(results, ctx["classes"], task["id"], public_only=True)
        broken = measure.p2p_broken(prev, results, task.get("superseded_tests", []))
        inv = measure.invariants(p["wt"], p["out"] / task["id"], m["invariant_seeds"], proj["impl_dir"], decl)
        added, deleted = common.numstat(p["wt"], start, end, proj["impl_dir"])
        line = {"run_id": run_id, "condition": condition, "task": task["id"], "index": i,
                "accepted": bool(total) and passed == total, "attempts": rec["attempts"],
                "acceptance": {"passed": passed, "total": total},
                "acceptance_public": {"passed": pub_passed, "total": pub_total}, "build_ok": results is not None,
                "p2p_broken": len(broken), "p2p_broken_tests": broken, "invariants": inv,
                "diff": {"added": added, "deleted": deleted},
                "budget_exceeded": added > BUDGET["added"] or deleted > BUDGET["deleted"],
                "tokens": _tokens(rec["calls"]), "tests_tampered": tampered,
                # seconds は条件の仕事（実装役と B の門）だけ。測定器は両条件に同じなので別に数える
                "seconds": seconds, "seconds_measure": round(time.monotonic() - t1, 1), "detail": {k: v for k, v in rec.items() if k != "calls"}}
        with metrics.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"[{condition}] {task['id']}: 受入 {passed}/{total}、P2P の破壊 {len(broken)}、"
              f"不変条件の違反 {inv['failures']}、試行 {rec['attempts']}")
        prev = measure.passing(results)
        # ビルドが通らなかったときは名前が取れないので、前の一覧のまま
        known = failing_names(results)
        if known is not None:
            state["known_failures"] = known
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
