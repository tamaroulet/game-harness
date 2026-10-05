"""H ライン（harness/hline.py）の 1 件の処理：1 つの作業ツリーでの実装の試行（run_task）と、What 1 件の分解から統合まで（process）。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import datetime
from pathlib import Path

import fastsuite
import infra_retry
from hline_base import Infra
from hline_budget import attempt_record, with_cutoff


def run_task(host, cfg, tid, spec, outdir, first=None, task=None, on_stage=None, run=0):
    """1 つの作業ツリーで実装役を最大 max_attempts 回試す。通った (作業ツリー, ブランチ, 試行の記録) か None。first は分解役の作業ツリー（無ければ作る）。run は 0 = 最初、1 = 作り直し。"""
    on_stage = on_stage or (lambda stage: None)
    tries = []
    wt, branch = first or host.new_worktree(cfg, tid)
    ok, out = host.base_check(cfg, wt)
    if not ok:   # 実装の前から合格済みのテストが落ちている。作業ツリーを替えて呼び直す（試行に数えない）
        raise Infra(f"base（実装前）で合格済みのタスクのテストが落ちています: {out[-500:]}")
    feedback = None
    for attempt in range(1, cfg["max_attempts"] + 1):
        log = outdir / f"implementer-{run}-{attempt}.log"
        on_stage(f"実装 {run + 1}-{attempt}")
        code, models, *rest = host.implement(cfg, wt, spec, with_cutoff(tries and tries[-1]["cutoff"], feedback), log)   # rest は利用量・打ち切りの理由・ターン数
        on_stage(f"Gate 1 {run + 1}-{attempt}")
        ok, feedback = host.gate(cfg, wt, host.changed_paths(wt, cfg), spec, task)
        tries.append({**attempt_record(run, attempt, code, models, ok, rest), "flaky": list(fastsuite.flaky_names(feedback))})
        (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
        if ok:
            return wt, branch, tries
    return None, None, tries


def process(host, cfg, st, name):
    """待ちの What 1 件を、分解 → Gate A → 実装 → Gate 1 → 統合ブランチへ。積めたら True、未収束なら False。
    環境の異常（Infra）は新しい作業ツリーで呼び直す（実装役の試行に数えない）。続けば What を待ちに戻し、infra_halt を立てて False。
    ライン自身の変更を積んだときは st["self_change"] を置く（run_line が走行を区切る）。"""
    item = st["items"][name]
    what = host.what_path(cfg, name).read_text(encoding="utf-8")
    tid = f"{datetime.datetime.now():%Y%m%d-%H%M}-{host.slug(name)}"
    outdir = Path(cfg["out"]) / tid
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "what.md").write_text(what, encoding="utf-8")
    item.pop("infra_retries", None)
    item.pop("started_at", None)   # 前の走行の途中で落ちた項目の開始の時刻を引き継がない
    item.update(status="processing", tid=tid)
    host.mark(cfg, st, name, "分解")
    print(f"[{tid}] {item['title']}")

    def attempt(n):   # 毎回、新しい作業ツリーから（分解役・実装役の試行の数えも 1 からやり直す）
        try:
            wt, branch = host.new_worktree(cfg, tid)
            spec, item["decompose"] = host.decompose(cfg, wt, what, item, outdir)   # Gate A。適合しなければ実装役を呼ばない
            item.update(tries=[], respec=None)
            stage = lambda s: host.mark(cfg, st, name, s)
            if spec is not None:
                host.mark(cfg, st, name, "実装 1-1")
                wt, branch, item["tries"] = host.run_task(cfg, tid, spec, outdir, (wt, branch), item["task"], on_stage=stage)
                if wt is None and cfg["respecs"]:   # 収束しなかった。失敗の要約を分解役に返して作り直し、1 回だけ再実行する
                    host.mark(cfg, st, name, "再分解")
                    wt, branch, _ = host.second_round(cfg, tid, what, item, outdir, spec, host.decompose, host.new_worktree, host.run_task,
                                                      on_stage=stage)
            if spec is None or wt is None: return False
            own = host.self_change(host.changed_paths(wt, cfg))
            host.integrate(cfg, wt, name, item["title"], item["task"])
            if own:
                st["self_change"] = {"name": name, "paths": own}
            return True
        except Infra as e:   # 呼び直しのたびに記録を残す（待った秒数は retry の記録で置き換わる）
            item.setdefault("infra_retries", []).append({"attempt": n, "reason": str(e), "wait": None})
            host.save_state(cfg, st)
            raise

    ic = cfg["infra_retry"]
    try:
        done, records = infra_retry.retry(attempt, Infra, ic["max_retries"], ic["wait_seconds"], log=print)
    except infra_retry.InfraExhausted as e:
        item.update(status="waiting", infra_retries=e.records)
        st["infra_halt"] = {"name": name, "reason": e.records[-1]["reason"], "attempts": len(e.records), "at": host.today()}
        host.mark(cfg, st, name)
        return False
    if records:
        item["infra_retries"] = records
    if done:
        item["status"] = "done"
    else:
        item.update(status="unconverged", at=host.today(), reason=(item["respec"] or {}).get("reason") or item["decompose"]["reason"]
                    or f"{len(item['tries'])} 回の試行で Gate 1 に通らず、パッチを捨てた")
        print(f"[{tid}] 収束しませんでした: {item['reason']}")
    host.mark(cfg, st, name)
    return done
