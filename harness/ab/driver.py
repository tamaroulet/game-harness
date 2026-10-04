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
import re
import os
import sys
import time
import traceback
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import agy_pinned  # noqa: E402
import agy_stream  # noqa: E402
import envcheck  # noqa: E402
import fileops  # noqa: E402
import exitcode  # noqa: E402
import implementer_context  # noqa: E402
import model_pin  # noqa: E402
import narrow_dir  # noqa: E402
import nlgen  # noqa: E402
import pipeline  # noqa: E402
import propaudit  # noqa: E402
import propgen  # noqa: E402
import project  # noqa: E402
import provenance  # noqa: E402
import telemetry  # noqa: E402
import testgen  # noqa: E402
import tool_policy  # noqa: E402
import transient  # noqa: E402
from adapters import dotnet  # noqa: E402
from ab import common, measure, v2r, v2r_preflight  # noqa: E402
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


# agy は起動のたびに、前の確認から 15 分を過ぎていれば裏で自分を最新に置き換える。v2r-r1-01 は起動時の照合（1.2.12）の
# 33 秒後に 1.2.14 に置き換わり、38 回の呼び出しのうち 37 回が 1.2.14 で走った（2026-09-30、agy の cli.log で確かめた）。
# 呼び出しごとに自動更新を止め、直前に版を測って固定値と照合する。値は "true"（"1" では止まらなかった。2026-09-30 に
# agy 1.2.12 で実測：cli.log に「Auto-update disabled via environment variable AGY_CLI_DISABLE_AUTO_UPDATE」が出る）
AGY_ENV = {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"}


def cli_version_guard(imp, measure=envcheck.cli_version, pinned=None):
    """呼び出しの直前に実装役の CLI の版を測り、固定値（config/environment.json）と照合する。版を返す。違えば ABError。"""
    want = ((pinned if pinned is not None else envcheck.load_pinned()).get("cli") or {}).get(imp["cli"])
    got = measure(imp["cli"])
    if want is None or got != want:
        raise common.ABError(f"実装役の CLI の版が固定値と違います：{imp['cli']} 実測 {got!r}、固定 {want!r}"
                             "（呼び出しの直前の照合。走行の途中で版が変わった）")
    return got


def agy_guard(imp, measure=envcheck.cli_version, pinned=None, verify=agy_pinned.require):
    """agy のときは、版を測る（= agy を起動する）より前に、導入先のファイルの SHA-256 を照合する。落ちれば ABError。"""
    if imp["cli"] == "agy":
        try:
            verify()
        except agy_pinned.AgyDigestError as e:
            raise common.ABError(str(e)) from e
    return cli_version_guard(imp, measure, pinned)


def agy_call(imp, prompt, cwd, conversation_id, ttl, runner=run, guard=agy_guard):
    """実装役を 1 回呼ぶ。会話を続けるときは --conversation を付ける（resume）。

    stream-json のときは、B（pipeline）と同じ引数と読み取り（agy_stream）を使い、手番ごとの記録を返す。
    呼ぶ前に版を照合し（guard）、自動更新を止める環境変数を付ける（AGY_ENV）。結果の cli_version に版を残す。
    """
    version = guard(imp) if guard else None
    env = dict(os.environ, **AGY_ENV)
    if agy_stream.is_stream(imp):
        t0 = time.monotonic()
        rc, out, err = runner(agy_stream.args(imp, resolve_cli(imp["cli"]), prompt, ttl, conversation_id),
                              cwd, ttl, "実装AI（条件 A）", env=env, input=agy_stream.stdin_for(imp, prompt))
        parsed = agy_stream.parse(out, cwd)
        return {"rc": rc, "seconds": round(time.monotonic() - t0, 1), "cli_version": version,
                "conversation_id": parsed["conversation_id"] or conversation_id, "usage": parsed["usage"],
                "steps": parsed["steps"], "outcome": parsed["outcome"], "model": parsed["model"],
                "cache": parsed["cache"], "token_problems": parsed["token_problems"], "out": out, "err": err}
    args = resolve_cli(imp["cli"]) + [imp["headless_flag"], prompt, imp["auto_approve_flag"],
                                      imp["model_flag"], imp["model_name"]] + imp.get("output_format_args", [])
    if conversation_id:
        args += ["--conversation", conversation_id]
    t0 = time.monotonic()
    rc, out, err = runner(args, cwd, ttl, "実装AI（条件 A）", env=env)
    doc = _json_in(out)
    return {"rc": rc, "seconds": round(time.monotonic() - t0, 1), "cli_version": version,
            "conversation_id": doc.get("conversation_id") or conversation_id,
            "usage": telemetry.cli_usage(out, imp["usage_format"]), "out": out, "err": err}


# 実装役の 1 回の呼び出しの結果（agy_call の戻り値）から、呼び出しの記録に写すもの。条件 A と v2r で同じ（v2r-smoke-02 は、
# 条件ごとに手で並べた写しに cli_version が無く、agy_call が測った版が metrics で null になった）
CALL_FROM_RESULT = ("rc", "seconds", "usage", "steps", "outcome", "model", "cache", "cli_version")


def call_entry(attempt, r, prompt, parts, **extra):
    """呼び出しの記録（本文は残さない。metrics には call_records でさらに絞って残す）。"""
    return {"attempt": attempt, **{k: r.get(k) for k in CALL_FROM_RESULT}, "prompt_chars": len(prompt),
            "prompt_parts": parts, **extra}


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


def first_dir(out, task):
    """最初の提出の変更の置き場（A も B も同じ）。"""
    return Path(out) / f"{task['id']}.first"


def _pipeline_judge(ctx, task, state):
    """A の判定（B と同じ検査の列）を用意する。(judge(n) -> (verdict, 知らせ), base で止まった理由, サンドボックス)。

    docs/design/v2_6_exam_symmetry.md §3.1。base の測定（establish_base）も B と同じ。pipeline が sys.exit で
    止まる故障（B なら rc 2 でそのタスクが落ちる）は、ABORT として扱う。
    """
    unit_path = Path(ctx["m"]["_base"]) / task["unit"]
    try:
        jc, verdict, msg = pipeline.judge_context(PROJECT, unit_path, ctx["wt"], ctx["judge_sandbox"],
                                                  Path(ctx["out"]) / "judge" / task["id"],
                                                  state.get("known_failures") or [])
    except SystemExit as e:
        return None, f"ABORT: {e.code}", None
    if verdict in ("ABORT", "REJECT"):
        return None, f"{verdict}: {msg}", None

    def judge(n):
        try:
            return pipeline.judge(jc, ctx["wt"], n)
        except SystemExit as e:
            return "ABORT", str(e.code)
    return judge, None, jc.sandbox


def run_task_a(ctx, task, unit, state, call=agy_call, fast=measure.run_fast, judge=None):
    """条件 A の 1 タスク。{"attempts", "accepted", "calls"}。

    v2（ctx["judge"] == "pipeline"）では、呼び出しの後に B と同じ検査の列（pipeline.judge）を通し、落ちたら
    B と同じ知らせを次の入力にする（試験制度の対称化、docs/design/v2_6_exam_symmetry.md）。judge を渡すと
    それを使う（テスト用）。どちらも無ければ、v1 のまま公開の受入だけを見る。
    """
    m, wt, out = ctx["m"], ctx["wt"], ctx["out"]
    tpl = ctx["templates"]
    calls, accepted, results, text, trx = [], False, None, "", None
    budget = call_budget(m)
    stream = agy_stream.is_stream(ctx["imp"])
    judge_sandbox, feedback, stopped = None, "", None
    if judge is None and ctx.get("judge") == "pipeline":
        judge, stopped, judge_sandbox = _pipeline_judge(ctx, task, state)
        if stopped:
            print(f"[A] {task['id']}: base で止まりました（B と同じ扱い）: {stopped[:300]}")
            return {"attempts": 0, "accepted": False, "calls": [], "stopped": stopped[:500]}
    for attempt in range(1, budget + 1):
        # v2.1c：B と同じく、細い作業場所（書き換えてよいファイルと契約と既存の型だけ）で動かし、変わったものを
        # 作業ツリーへ書き戻す。置き場は作業ツリーごとに固定なので、会話を積んでもパスは変わらない
        narrow = placed = None
        if stream:
            narrow, placed = narrow_dir.populate(wt, implementer_context.visible_files(wt, unit, ctx["impl_dir"]))
        workdir = narrow or wt
        parts = {}  # 要素ごとの字数（v2.3、N7。本文は残さない）
        if attempt == 1:
            # B と同じく、細い作業場所では中身を埋め込む型の宣言は描かない（v2.3、N4・F4）
            only = implementer_context.interface_scope(wt, unit, ctx["impl_dir"]) if stream else None
            interface = testgen.render_interface(unit, only=only)
            prompt = tpl["initial"].format(task_id=task["id"], title=task["title"], workdir=str(workdir),
                                           prompt=unit["prompt"], interface=interface,
                                           whitelist="\n".join(f"- {p}" for p in unit["whitelist"]),
                                           test_dir=m["test_dir"])
            parts = {"unit": len(unit["prompt"]), "interface": len(interface),
                     "template": len(prompt) - len(unit["prompt"]) - len(interface)}
            if stream:
                # B と同じく、契約・既存の型・書き換えてよいファイルの中身を埋め込む（v2.1 §1.1 の 2）。A は会話を積むので、
                # 埋め込むのは最初の呼び出しだけ（単一チャットで最初にファイルを貼るのと同じ）
                try:
                    embed = implementer_context.for_unit(wt, unit, ctx["impl_dir"])
                except implementer_context.ContextError as e:
                    raise common.ABError(f"実装役に渡す前提が大きすぎます: {e}")
                parts["embed"] = len(embed)
                prompt += "\n\n" + embed
        elif judge is not None:
            # B と同じ知らせ（内側の反例・診断の射影、外側の門の知らせ）。生の出力の末尾は渡さない
            prompt = tpl["retry"].format(task_id=task["id"], attempt=attempt - 1, max_attempts=budget,
                                         failed_tests=feedback, tail_lines=0, failure_tail="")
            if judge_sandbox is not None:
                prompt = narrow_dir.relocate(prompt, judge_sandbox, narrow)
            prompt = narrow_dir.relocate(prompt, wt, narrow)
        else:
            n = m["templates"]["retry_tail_lines"]
            prompt = tpl["retry"].format(task_id=task["id"], attempt=attempt - 1, max_attempts=budget,
                                         failed_tests=failure_list(results, ctx["classes"], task["id"], trx)
                                         or "- （ビルドが通らず、テストを実行できませんでした）",
                                         tail_lines=n, failure_tail="\n".join(text.strip().splitlines()[-n:]))
            # 失敗の出力の中の作業ツリーのパスは作業場所のパスに置き換える。v2-smoke-01 の A は、ここに出た
            # 作業ツリーのパスをたどって作業場所の外のテストを読み、dotnet を走らせた
            prompt = narrow_dir.relocate(prompt, wt, narrow)
        # 道具の指示は B と同じ文面（再試行を含めて編集だけ。v2 §7）
        if attempt > 1:
            parts["retry"] = len(prompt)
        parts["protocol"] = len(tool_policy.text())
        prompt += "\n\n" + tool_policy.text()
        r, transients = checked_call(ctx, call, prompt, workdir, state.get("conversation_id"), task, attempt, narrow,
                                     "実装役（条件 A）")
        written = None
        if narrow:
            written = narrow_dir.write_back(narrow, wt, placed)
            narrow_dir.discard(narrow)
        state["conversation_id"] = r["conversation_id"]
        calls.append(call_entry(attempt, r, prompt, parts))
        if transients:
            calls[-1]["transient"] = transients
        if narrow:
            calls[-1]["narrow_written"] = written
        if attempt == 1 and judge is not None:
            # 最初の提出（門を通す前）を残す。タスクの後に測定器で測る。実装役には知らせない（V2-6。v1 の走行には無い）
            pipeline.save_changes(wt, first_dir(out, task), common.GIT_TTL)
        (Path(out) / f"{task['id']}_a{attempt}.implementer.log").write_text(
            f"# prompt\n{prompt}\n\n# stdout\n{r['out']}\n\n# stderr\n{r['err']}\n", encoding="utf-8")
        # 実装役がテストを書き換えていても、凍結したものに戻してから測る
        measure.place_frozen_tests(m, wt, ctx["index"], m["test_dir"])
        if judge is not None:
            if r["rc"] != 0:
                # B の内側ループと同じ：異常終了は検査をせず、その知らせを次の入力にする
                verdict = "IMPLEMENTER_FAILED"
                feedback = pipeline.extract_raw_stacktrace(
                    f"実装AI が異常終了 (rc={r['rc']}): {(r['err'] or r['out'])[:400]}", max_lines=40)
            else:
                verdict, feedback = judge(attempt)
            calls[-1]["verdict"] = verdict
            if verdict == "SUCCESS":
                accepted = True
                break
            if verdict == "ABORT":
                print(f"[A] {task['id']}: 検査系の故障で止めました（B と同じ扱い）: {feedback[:300]}")
                break
            continue
        results, text = fast(wt, ctx["test_project"], out, f"{task['id']}_a{attempt}")
        trx = Path(out) / f"{task['id']}_a{attempt}.trx"
        # 非公開シードは渡していないので、*_Hidden は数えない（B の内側ループと同じ。非公開は最後の測定で見る）
        passed, total = measure.acceptance(results, ctx["classes"], task["id"], public_only=True)
        if total and passed == total:
            accepted = True
            break
    return {"attempts": len(calls), "accepted": accepted, "calls": calls}


# ============================================================ v2r の 4 条件（docs/design/v2r_protocol.md §1）

def run_task_v2r(ctx, task, unit, state, call=agy_call, judge=None):
    """v2r の 1 タスク（A0・A1・B-G・B）。全条件ステートレス。{"attempts", "accepted", "calls", "factors"}。

    ctx["v2r"] = {"form": "nl" | "formal", "gate": bool}（ab.v2r.factors）。門ありは、呼び出しの後に B と同じ検査の列
    （pipeline.judge）を通し、落ちたら知らせを付けて呼び直す（最大 call_budget 回）。自然言語の条件は、知らせの性質の行に
    自然言語の文を添える（nlgen.annotate_feedback）。門なしは 1 回だけ呼び、そのまま測定する。judge を渡すとそれを使う（テスト用）。
    """
    m, wt, out, f = ctx["m"], ctx["wt"], ctx["out"], ctx["v2r"]
    spec = v2r.spec_text(m, task, unit, f["form"])
    nl = v2r.sentences(m, task) if f["form"] == "nl" else None
    budget = call_budget(m) if f["gate"] else 1
    calls, accepted, feedback, judge_sandbox = [], False, "", None
    if f["gate"] and judge is None:
        judge, stopped, judge_sandbox = _pipeline_judge(ctx, task, state)
        if stopped:
            print(f"[v2r] {task['id']}: base で止まりました: {stopped[:300]}")
            return {"attempts": 0, "accepted": False, "calls": [], "stopped": stopped[:500], "factors": f}
    for attempt in range(1, budget + 1):
        narrow, placed = narrow_dir.populate(wt, implementer_context.visible_files(wt, unit, ctx["impl_dir"]))
        interface = testgen.render_interface(unit, only=implementer_context.interface_scope(wt, unit, ctx["impl_dir"]))
        try:
            embed = implementer_context.for_unit(wt, unit, ctx["impl_dir"])
        except implementer_context.ContextError as e:
            narrow_dir.discard(narrow)
            raise common.ABError(f"実装役に渡す前提が大きすぎます: {e}")
        fb = ""
        if feedback:
            fb = narrow_dir.relocate(feedback, judge_sandbox, narrow) if judge_sandbox is not None else feedback
            fb = narrow_dir.relocate(fb, wt, narrow)
            if nl is not None:
                try:
                    fb = nlgen.annotate_feedback(fb, nl)
                except nlgen.NLGenError as e:
                    narrow_dir.discard(narrow)
                    raise common.ABError(f"知らせに自然言語の文を添えられません: {e}")
        prompt, parts = v2r.assemble(narrow, spec, interface, embed, fb, tool_policy.text())
        # ステートレス：呼び出しごとに新しい会話（conversation_id を渡さない）
        r, transients = checked_call(ctx, call, prompt, narrow, None, task, attempt, narrow, f"実装役（v2r {ctx['condition']}）")
        written = narrow_dir.write_back(narrow, wt, placed)
        narrow_dir.discard(narrow)
        calls.append(call_entry(attempt, r, prompt, parts, narrow_written=written))
        if transients:
            calls[-1]["transient"] = transients
        if attempt == 1:
            # 最初の提出（門を通す前）。門なしの条件では、これが最終と同じになる
            pipeline.save_changes(wt, first_dir(out, task), common.GIT_TTL)
        (Path(out) / f"{task['id']}_a{attempt}.implementer.log").write_text(
            f"# prompt\n{prompt}\n\n# stdout\n{r['out']}\n\n# stderr\n{r['err']}\n", encoding="utf-8")
        measure.place_frozen_tests(m, wt, ctx["index"], m["test_dir"])
        if not f["gate"]:
            break
        if not written and len(calls) >= 2 and not calls[-2].get("narrow_written"):
            # 2 回続けて何も書かなかった（原則 P4）。同じ作業ツリーを同じ門で測り直しても知らせは変わらないので、
            # 呼び直さずに打ち切る（実装の失敗として数える）。v2r-dry-02 の B の T6 は 3 回目に思考だけで 6.4 万トークン
            calls[-1]["verdict"] = "NO_PROGRESS"
            print(f"[v2r] {task['id']}: 2 回続けて変更が無いので打ち切りました")
            break
        if r["rc"] != 0:
            verdict = "IMPLEMENTER_FAILED"
            feedback = pipeline.extract_raw_stacktrace(
                f"実装AI が異常終了 (rc={r['rc']}): {(r['err'] or r['out'])[:400]}", max_lines=40)
        else:
            verdict, feedback = judge(attempt)
            faults = instrument_faults(feedback, v2r.properties(m))
            if verdict != "SUCCESS" and faults:
                # 実装に依らず作れるはずの前提が成り立たない＝測定器の故障（原則 P2）。実装役に知らせても直せないので
                # 呼び直さない。欠陥として数えないよう、行を書かずに走行を止める（途中で止まった走行は判定しない）
                raise common.ABError(f"{task['id']}: 測定器の故障（前提を実装に依らず作れるはずの性質が空虚）: "
                                     + "; ".join(faults))
        calls[-1]["verdict"] = verdict
        if verdict == "SUCCESS":
            accepted = True
            break
        if verdict == "ABORT":
            print(f"[v2r] {task['id']}: 検査系の故障で止めました: {feedback[:300]}")
            break
    return {"attempts": len(calls), "accepted": accepted, "calls": calls, "factors": f}


FAULT_LINE_RE = re.compile(r"PROPERTY_(VACUOUS|UNSATISFIABLE|WITNESS_INVALID) id=(\S+)")


def instrument_faults(feedback, props):
    """知らせのうち、測定器の故障と決まるもの（原則 P2。docs/design/v2r_instrument_redesign.md）。

    - PROPERTY_UNSATISFIABLE：開始状態の前提を満たす系列が無い。実装を動かす前の検査なので、いつも測定器の故障
    - PROPERTY_WITNESS_INVALID：生成の時点に参照モデルで確かめた前提が、テストの時点に成り立たない（評価の食い違い）。
      いつも測定器の故障。復元の欠陥（PROPERTY_WITNESS_RESTORE）は実装の失敗なので、ここには入れない
    - PROPERTY_VACUOUS：前提が 1 回も成り立たない。前提が実装の結果（after）を使わない性質なら、成り立つかどうかは
      実装に依らないので測定器の故障。after を使う性質（P1 に反する宣言）では、実装が出来事を起こさないせいかもしれず
      見分けられないので、ここでは故障にしない（知らせとして渡す）
    """
    by = {p["id"]: p for p in props}
    out = []
    for kind, pid in FAULT_LINE_RE.findall(feedback or ""):
        p = by.get(pid)
        if kind in ("UNSATISFIABLE", "WITNESS_INVALID") or (
                p is not None and not any(r["outcome"] for r in propaudit.audit([p]))):
            item = f"{kind} {pid}"
            if item not in out:
                out.append(item)
    return out


# ============================================================ 条件 B

def pipeline_args(unit_path, wt, sandbox, out, tel, known_failures=None, first=None):
    # 門の自己検査（--skip-selftest で省く）は門そのものの健全性の検査で、タスクの仕事ではない。
    # dry-01 では 405.9 秒のうち 177.1 秒を占めた。門の健全性は tests/ と pipeline --selftest で別に確かめる
    return [sys.executable, str(common.ROOT / "harness" / "pipeline.py"), "--project", PROJECT,
            "--unit", str(unit_path), "--repo-dir", str(wt), "--local-only", "--skip-selftest",
            "--sandbox", str(sandbox), "--out-dir", str(out), "--telemetry", str(tel)] + (
                ["--known-failures", str(known_failures)] if known_failures else []) + (
                ["--first-submission", str(first)] if first else [])


def failing_names(results):
    """測定の結果で Passed でないテストの名前。ビルドが通らなければ None。"""
    return None if results is None else sorted(n for n, o in results.items() if o != "Passed")


CALL_RECORD = ("attempt", "rc", "seconds", "model", "cli_version", "prompt_chars", "prompt_parts", "outcome",
               "transient", "cache", "verdict")


def call_records(calls):
    """metrics に残す呼び出しの要約（本文・手番の中身は残さない）。"""
    return [{k: c.get(k) for k in CALL_RECORD} for c in calls]


def checked_call(ctx, call, prompt, workdir, conversation_id, task, attempt, narrow, who):
    """実装役を 1 回呼ぶ（一時的な失敗は呼び直す。harness/transient.py）。使ったモデルを設定と照合する（harness/model_pin.py）。

    (結果, 一時的な失敗の記録)。一時的な失敗が続けば ReplicateStop、モデルが違えば ABError。
    """
    out = ctx["out"]
    try:
        r, retries = transient.with_backoff(lambda: call(ctx["imp"], prompt, workdir, conversation_id, ctx["ttl"]),
                                            lambda x: transient.classify(x.get("outcome")))
    except transient.TransientFailure as e:
        log_transients(out, task, attempt, prompt, e.retries)
        if narrow:
            narrow_dir.discard(narrow)
        raise common.ReplicateStop(f"{task['id']} の試行 {attempt}: {e}")
    transients = log_transients(out, task, attempt, prompt, retries)
    if agy_stream.is_stream(ctx["imp"]) and (r.get("conversation_id") or r.get("steps")):
        try:
            model_pin.check_reported(ctx["imp"]["model_name"], r.get("model"), who)
        except model_pin.ModelPinError as e:
            raise common.ABError(str(e))
    return r, transients


def log_transients(out, task, attempt, prompt, retries):
    """一時的な失敗の呼び出しの生の出力を別のログに残し、記録（利用量・理由・待った秒）を返す。guard はこれも費用に数える。"""
    recs = []
    for j, r in enumerate(retries, start=1):
        x = r["result"]
        (Path(out) / f"{task['id']}_a{attempt}_t{j}.implementer.log").write_text(
            f"# prompt\n{prompt}\n\n# stdout\n{x.get('out', '')}\n\n# stderr\n{x.get('err', '')}\n", encoding="utf-8")
        recs.append({"rc": x.get("rc"), "reason": r["reason"], "wait": r["wait"], "usage": x.get("usage"),
                     "outcome": x.get("outcome")})
    return recs


def run_task_b(ctx, task, unit, state, runner=run):
    """条件 B の 1 タスク。pipeline の試行・門はそのまま。{"attempts", "accepted", "calls", "pipeline_rc"}。"""
    out = Path(ctx["out"])
    # テレメトリはタスクごとのディレクトリに置く。pipeline は実装役の生ログをテレメトリと同じ所に書くので、
    # 走行の直下に置くと、T2 以降の implementer_attempt_*.log が T1 のものを上書きしていた（v2-smoke-03）
    (out / task["id"]).mkdir(parents=True, exist_ok=True)
    tel = out / task["id"] / f"{task['id']}.pipeline.json"
    unit_path = Path(ctx["m"]["_base"]) / task["unit"]
    # 前のタスクの終わりに落ちていたテストは、base の検査と P2P から外す（S2）。A の測定器も
    # 「前のタスクの終わりに通っていたもの」だけを P2P に数えるので、同じ扱いになる
    known = out / f"{task['id']}.known_failures.json"
    known.write_text(json.dumps(state.get("known_failures") or [], ensure_ascii=False), encoding="utf-8")
    rc, stdout, stderr = runner(pipeline_args(unit_path, ctx["wt"], ctx["sandbox"], out / "pipeline" / task["id"], tel,
                                              known, first=first_dir(out, task)),
                                str(common.ROOT), PIPELINE_TTL, f"pipeline（条件 B、{task['id']}）")
    (out / f"{task['id']}.pipeline.log").write_text(f"{stdout}\n{stderr}\n", encoding="utf-8")
    data, _ = telemetry.read(tel)
    if (data or {}).get("transient_abort"):
        raise common.ReplicateStop(f"{task['id']}（条件 B）: {data['transient_abort']}")
    attempts = (data or {}).get("attempts", [])
    # 内側ループの全呼び出しを数える（監査 F2）。implementer_calls の無い古い記録は最後の呼び出しだけ
    calls = [{"attempt": a.get("n"), "verdict": a.get("verdict"), "stage": a.get("stage"), "usage": call.get("usage"),
              **{k: call.get(k) for k in CALL_RECORD if k != "attempt"}, "transient": call.get("transient") or []}
             for a in attempts for call in (a.get("implementer_calls") or [a.get("implementer") or {}])]
    return {"attempts": len(attempts), "implementer_calls": len(calls), "accepted": rc == 0, "calls": calls,
            "pipeline_rc": rc}


# ============================================================ 1 回の走行

def _tokens(calls):
    """呼び出しの利用量の合計。1 つでも不明なら None（推測で埋めない）。キャッシュ読みも数える（監査 F6）。

    一時的な失敗の呼び出し（calls[].transient）の利用量も足す（払っているので。harness/transient.py）。
    """
    spent = list(calls) + [t for c in calls for t in (c.get("transient") or [])]

    def total(key):
        xs = [(c.get("usage") or {}).get(key) for c in spent]
        return sum(xs) if all(isinstance(x, int) for x in xs) else None
    ins = [((c.get("usage") or {}).get("input_tokens")) for c in calls]
    # partial：TTL で打ち切った呼び出しがあり、その分は終わった手番までの足し込み（下限）（v2.1 §1.2）
    return {"input": total("input_tokens"), "output": total("output_tokens"),
            "cache_read": total("cache_read_tokens"), "per_call_input": ins,
            "partial": any((c.get("usage") or {}).get("partial") for c in spent),
            "transient_calls": len(spent) - len(calls)}


def run_condition(manifest, condition, run_id, wt_root=common.WT_ROOT, out_root=common.OUT_ROOT,
                  task_runner=None, through=None, resume=False):
    """through を与えると、T1 からそのタスクまでで止める（乾式の走行用）。

    resume（原則 P5。v2r だけ）：落ちた走行を、metrics.jsonl に行のある最後のタスクの終わりから続ける（`resume_point`）。
    作業ツリーをそのタスクの終わりのコミットに戻し、途中だったタスクの出力は aborted/ に移す。想定外の例外で止まるときは、
    どこで何が起きたかを fault.json に残す（`record_fault`）。
    """
    m = common.load_manifest(manifest)
    m["tasks"] = common.tasks_through(m, through)
    units = [common.unit_of(m, t) for t in m["tasks"]]
    proj = project.load(PROJECT)
    cfg = project.pipeline_config(proj)
    # 起動の時点で、実装役のモデルの明示と、使ったモデルを記録できる出力形式を確かめる（harness/model_pin.py）
    try:
        model_pin.require_implementer(cfg["implementer"], f"projects/{PROJECT}/pipeline.json の implementer")
    except model_pin.ModelPinError as e:
        raise common.ABError(str(e))
    p = common.paths(run_id, condition, wt_root, out_root)
    metrics = p["out"] / "metrics.jsonl"
    done, carry = 0, None
    if resume:
        if not common.is_v2r(m):
            raise common.ABError("再開（--resume）は v2r のマニフェストだけです（呼び出しごとに新しい会話なので、途中から続けられる）")
        if not p["wt"].exists():
            raise common.ABError(f"再開する worktree がありません: {p['wt']}")
        done, carry = resume_point(metrics, m["tasks"])
        if done == len(m["tasks"]):
            print(f"[{condition}] {run_id}: すべてのタスクに行があります。再開することはありません")
            return 0
        common.git(["reset", "-q", "--hard", carry["end_commit"] if carry else m["base_commit"]], p["wt"],
                   "git reset（再開）")
        common.git(["clean", "-q", "-fd"], p["wt"], "git clean（再開）")
        moved = set_aside(p["out"], m["tasks"][done]["id"])
        print(f"[{condition}] {run_id}: {m['tasks'][done]['id']} から再開します（途中の出力 {len(moved)} 件を aborted/ に移した）")
    else:
        if p["wt"].exists():
            raise common.ABError(f"worktree が既にあります。先に cleanup するか、続けるなら --resume を付けてください: {p['wt']}")
        p["out"].mkdir(parents=True, exist_ok=True)
        common.git(["worktree", "add", "-q", "-b", p["branch"], str(p["wt"]), m["base_commit"]],
                   proj["repo_dir"], "git worktree add")
    ctx = {"m": m, "wt": p["wt"], "sandbox": p["sandbox"], "out": p["out"], "imp": cfg["implementer"],
           "ttl": cfg["ttl_seconds"]["implementer"], "test_project": proj["fast_test_project"],
           "impl_dir": proj["impl_dir"],
           "classes": measure.class_to_task(m, units),
           "templates": {k: (Path(m["_base"]) / m["templates"][k]).read_text(encoding="utf-8")
                         for k in ("initial", "retry")}}
    ctx["condition"] = condition
    if common.is_v2r(m):
        # v2r：4 条件をステートレスで同じ組み立てで回す。門ありは、条件ごとのサンドボックスで B と同じ検査の列
        ctx["v2r"] = v2r.factors(condition)
        if ctx["v2r"]["gate"]:
            ctx.update(judge="pipeline", judge_sandbox=p["sandbox"])
        task_runner = task_runner or run_task_v2r
    elif condition not in common.CONDITIONS:
        raise common.ABError(f"条件 {condition} は v2r のマニフェストでだけ使えます")
    if condition == "A" and common.is_v2(m) and not common.is_v2r(m):
        # 試験制度の対称化（docs/design/v2_6_exam_symmetry.md）：A にも B と同じ検査の列を、A 専用のサンドボックスで
        ctx.update(judge="pipeline", judge_sandbox=p["sandbox"])
    task_runner = task_runner or (run_task_a if condition == "A" else run_task_b)
    decl = common.ROOT / "projects" / PROJECT / "invariants.json"
    where = {"task": None, "index": None, "phase": "baseline"}
    try:
        _run_tasks(ctx, m, units, proj, p, run_id, condition, task_runner, decl, metrics, wt_root, done, carry, where)
    except BaseException as e:
        record_fault(p["out"], run_id, condition, manifest, where, e)
        raise
    return 0


def resume_point(metrics, tasks):
    """(行のあるタスクの数, 最後の行の再開の情報 | None)。行は T1 から順に途切れずにあること。"""
    rows = []
    if Path(metrics).exists():
        rows = [json.loads(line) for line in Path(metrics).read_text(encoding="utf-8").splitlines() if line.strip()]
    for k, r in enumerate(rows):
        if k >= len(tasks) or r.get("task") != tasks[k]["id"]:
            raise common.ABError(f"metrics.jsonl の行がタスクの順と合いません（{k + 1} 行目：{r.get('task')}）。再開できません")
        if not isinstance(r.get("resume"), dict):
            raise common.ABError(f"metrics.jsonl の {r.get('task')} の行に再開の情報がありません（古い走行）。再開できません")
    return len(rows), (rows[-1]["resume"] if rows else None)


def set_aside(out, task_id):
    """途中だったタスクの出力を aborted/<時刻>/ に移す（消さない。guard は費用を数え続ける）。移したものの名前の列。"""
    out = Path(out)
    dest = out / "aborted" / time.strftime("%Y%m%d-%H%M%S")
    items = [x for x in out.iterdir() if x.name.startswith((f"{task_id}_", f"{task_id}.")) or x.name == task_id]
    judge = out / "judge" / task_id
    items += [judge] if judge.exists() else []
    names = sorted(x.relative_to(out).as_posix() for x in items)
    for x in items:
        target = dest / x.relative_to(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        fileops.replace(x, target)
    return names


def record_fault(out, run_id, condition, manifest, where, e):
    """想定外の停止を fault.json に残す（原則 P5。v2r-dry-02 は止まった理由が記録に無かった）。"""
    try:
        Path(out).mkdir(parents=True, exist_ok=True)
        rec = {"run_id": run_id, "condition": condition, **where, "type": type(e).__name__, "message": str(e)[:2000],
               "traceback": traceback.format_exc()[-4000:], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "resume": (f"python -m harness.ab.driver run --condition {condition} --run-id {run_id} "
                          f"--manifest {manifest} --resume")}
        (Path(out) / "fault.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass   # 記録に失敗しても、元の例外を隠さない


def _run_tasks(ctx, m, units, proj, p, run_id, condition, task_runner, decl, metrics, wt_root, done, carry, where):
    if carry is None:
        results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], "baseline")
        prev = measure.passing(results)
        state = {"conversation_id": None, "known_failures": failing_names(results) or []}
    else:
        # 再開：最後に行を書いたタスクの終わりの測定を、行に残した値から戻す（測り直さない）
        prev = set(carry["passing"])
        state = {"conversation_id": None, "known_failures": list(carry["known_failures"])}
    for i, (task, unit) in enumerate(zip(m["tasks"], units), start=1):
        if i <= done:
            continue
        where.update(task=task["id"], index=i, phase="implement")
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
        where["phase"] = "measure"
        results, _ = measure.run_fast(p["wt"], ctx["test_project"], p["out"], f"{task['id']}_final", env=env)
        hits = dotnet.property_hits(p["out"] / f"{task['id']}_final.trx")
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
                "seconds": seconds, "seconds_measure": round(time.monotonic() - t1, 1), "detail": {k: v for k, v in rec.items() if k != "calls"},
                # 呼び出しごとの字数・要素の字数・終わり方（v2.3、N7。v2-smoke-05 まで A の分はどこにも残っていなかった）
                "calls": call_records(rec["calls"]),
                # 性質ごとの前提の成立回数（v2r §8。0 の性質は空虚に通った）。行が無い生成物（V2）では空の表
                "property_hits": hits, "vacuous_properties": [k for k, v in hits.items() if v == 0],
                # 要求したモデルと、呼び出しで報告されたモデル（2026-09-26 の是正。harness/model_pin.py）
                "model": {"requested": ctx["imp"]["model_name"],
                          "reported": sorted({c["model"] for c in rec["calls"] if c.get("model")})}}
        # 最初の提出（門を通す前）を、同じ測定器・同じ非公開シードで測る（V2-6。実装役には知らせない）
        where["phase"] = "measure_first"
        first = measure_first(ctx, proj, run_id, condition, task, start, prev, env, decl, p["out"], wt_root)
        if first is not None:
            line["first_submission"] = first
        prev = measure.passing(results)
        # ビルドが通らなかったときは名前が取れないので、前の一覧のまま
        known = failing_names(results)
        if known is not None:
            state["known_failures"] = known
        # 再開の情報（原則 P5）：このタスクの終わりのコミットと、次のタスクの P2P の比べる元
        line["resume"] = {"end_commit": end, "passing": sorted(prev), "known_failures": list(state["known_failures"])}
        with metrics.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"[{condition}] {task['id']}: 受入 {passed}/{total}、P2P の破壊 {len(broken)}、"
              f"不変条件の違反 {inv['failures']}、試行 {rec['attempts']}")


def measure_first(ctx, proj, run_id, condition, task, start, prev, env, decl, out, wt_root=common.WT_ROOT):
    """最初の提出を測る。タスクの始めのコミットから一時の worktree を作り、残した変更を当てて、最終と同じ測定をする。

    docs/design/v2_6_exam_symmetry.md §3.2。門の効果を、条件の中の比較（最初の提出 → 最終）として示すための記録。
    残した変更が無ければ None（実装役を呼ばなかったタスク）。
    """
    saved = first_dir(out, task)
    if not saved.exists():
        return None
    repo = proj["repo_dir"]
    fw = Path(wt_root) / f"{run_id}-{condition}-first"
    if fw.exists():
        common.git(["worktree", "remove", "--force", str(fw)], repo, "git worktree remove（最初の提出）", check=False)
    common.git(["worktree", "add", "-q", "--detach", str(fw), start], repo, "git worktree add（最初の提出）")
    try:
        pipeline.restore_changes(saved, fw)
        results, _ = measure.run_fast(fw, ctx["test_project"], out, f"{task['id']}_first", env=env)
        passed, total = measure.acceptance(results, ctx["classes"], task["id"])
        pub_passed, pub_total = measure.acceptance(results, ctx["classes"], task["id"], public_only=True)
        broken = measure.p2p_broken(prev, results, task.get("superseded_tests", []))
        inv = measure.invariants(fw, Path(out) / task["id"] / "first", ctx["m"]["invariant_seeds"],
                                 proj["impl_dir"], decl)
        return {"accepted": bool(total) and passed == total, "acceptance": {"passed": passed, "total": total},
                "acceptance_public": {"passed": pub_passed, "total": pub_total}, "build_ok": results is not None,
                "p2p_broken": len(broken), "p2p_broken_tests": broken, "invariants": inv}
    finally:
        common.git(["worktree", "remove", "--force", str(fw)], repo, "git worktree remove（最初の提出）", check=False)


def cleanup(run_id, wt_root=common.WT_ROOT):
    repo = project.load(PROJECT)["repo_dir"]
    for cond in common.ALL_CONDITIONS:
        p = common.paths(run_id, cond, wt_root)
        for wt in (p["sandbox"], p["wt"]):
            if wt.exists():
                common.git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove", check=False)
    common.git(["worktree", "prune"], repo, "git worktree prune", check=False)


def restart(run_id, condition, wt_root=common.WT_ROOT, out_root=common.OUT_ROOT, git=common.git):
    """1 つの条件を同じ run-id で最初から走り直せるようにする。出力は消さずに `<条件>.superseded-<時刻>/` に移し
    （公開のアーカイブにも入る）、その条件の作業ツリー・サンドボックス・ブランチを外す。移した先を返す（無ければ None）。

    v2r-r1-01 の A1・B は、門の側の不備で T2 を 0 回の試行で飛ばして T10 まで進んだ。T2 の行があるので --resume では
    やり直せない。非公開シードは run-id とタスクで決まるので、同じ run-id で走り直せば A0・B-G と同じシードになる。
    """
    repo = project.load(PROJECT)["repo_dir"]
    p = common.paths(run_id, condition, wt_root, out_root)
    moved = None
    if p["out"].exists():
        moved = p["out"].parent / f"{condition}.superseded-{time.strftime('%Y%m%d-%H%M%S')}"
        fileops.replace(p["out"], moved)
    for wt in (p["sandbox"], p["wt"]):
        if wt.exists():
            git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove（走り直し）", check=False)
    git(["worktree", "prune"], repo, "git worktree prune", check=False)
    git(["branch", "-D", p["branch"]], repo, "git branch -D（走り直し）", check=False)
    return moved


def run_all(manifest, repeat, prefix):
    """A と B を交互に回す（時間帯の偏りを散らす）。1 回が失敗しても次へ進む。"""
    rc = 0
    v2r_run = common.is_v2r(common.load_manifest(manifest)) if Path(manifest).exists() else False
    for k in range(1, repeat + 1):
        if v2r_run:
            order = v2r.order_for(k)
        else:
            order = common.CONDITIONS if k % 2 else tuple(reversed(common.CONDITIONS))
        for cond in order:
            try:
                run_condition(manifest, cond, f"{prefix}-{k:02d}")
            except common.ReplicateStop as e:
                print(f"ABORT（{prefix}-{k:02d} {cond}、繰り返しを止めます）: {e}")
                rc = exitcode.ABORT
                break
            except common.ABError as e:
                print(f"ABORT（{prefix}-{k:02d} {cond}）: {e}")
                rc = exitcode.ABORT
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="A/B 実験の自動ドライバ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--condition", required=True, choices=sorted(set(common.CONDITIONS) | set(v2r.CONDITIONS)),
                   help="A・B（V2）、A0・A1・B-G・B（v2r のマニフェスト）")
    r.add_argument("--run-id", required=True)
    r.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    r.add_argument("--through", help="このタスクまでで止める（例: T1）")
    r.add_argument("--resume", action="store_true",
                   help="落ちた走行を、metrics.jsonl に行のある最後のタスクの次から続ける（v2r だけ。原則 P5）")
    a = sub.add_parser("run-all")
    a.add_argument("--repeat", type=int, default=3)
    a.add_argument("--prefix", default="ab")
    a.add_argument("--manifest", default=str(common.EXP_DIR / "tasks.json"))
    c = sub.add_parser("cleanup")
    c.add_argument("--run-id", required=True)
    rs = sub.add_parser("restart", help="1 つの条件を同じ run-id で最初から走り直せるようにする（出力は superseded に移す）")
    rs.add_argument("--condition", required=True, choices=sorted(set(common.CONDITIONS) | set(v2r.CONDITIONS)))
    rs.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    if args.cmd in ("run", "run-all"):
        # 実行環境を実測し、固定値（config/environment.json）と違えば起動しない。実測値は env.json に残す（v2r §5）
        name = args.run_id if args.cmd == "run" else args.prefix
        try:
            envcheck.require(common.OUT_ROOT / f"{name}.env.json")
        except envcheck.EnvError as e:
            print(f"ABORT: {e}")
            return exitcode.ABORT
        # 来歴（ハーネスのコミット・実装役の全体設定の sha256・環境変数）を provenance.json に残す。ハーネスに
        # コミットしていない変更があれば起動しない（docs/design/v2r_reproducibility.md）
        try:
            provenance.require(common.OUT_ROOT / f"{name}.provenance.json", manifest=args.manifest,
                               implementer=project.pipeline_config(project.load(PROJECT))["implementer"])
        except provenance.ProvenanceError as e:
            print(f"ABORT: {e}")
            return exitcode.ABORT
        # v2r：同じマニフェストで走行の前の検定（原則 P3）に合格していなければ起動しない
        try:
            v2r_preflight.require(args.manifest)
        except common.ABError as e:
            print(f"ABORT: {e}")
            return exitcode.ABORT
    try:
        if args.cmd == "run":
            return run_condition(args.manifest, args.condition, args.run_id, through=args.through,
                                 resume=args.resume)
        if args.cmd == "run-all":
            return run_all(args.manifest, args.repeat, args.prefix)
        if args.cmd == "restart":
            moved = restart(args.run_id, args.condition)
            print(f"[{args.condition}] {args.run_id}: 走り直せます" + (f"（前の出力は {moved}）" if moved else ""))
            return 0
        cleanup(args.run_id)
        return 0
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
