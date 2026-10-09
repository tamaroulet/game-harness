"""H ライン（harness/hline.py）の 1 件の処理：1 つの作業ツリーでの実装の試行（run_task）と、What 1 件の実装から統合まで（process）。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import datetime
from pathlib import Path

import fastsuite
import hline_abort
import hline_boundary
import infra_retry
from hline_base import Fatal, Infra
from hline_budget import attempt_record, with_cutoff


def digest_of(cfg, wt):
    """作業ツリーの差分の digest。git の作業ツリーでない（.git が無い）ときは None で、同じ差分の判定はしない。"""
    return hline_abort.patch_digest(wt, cfg["ttl_seconds"]["git"]) if (Path(wt) / ".git").exists() else None


def run_task(host, cfg, tid, what, outdir, first=None, task=None, on_stage=None, run=0):
    """1 つの作業ツリーで実装役を最大 max_attempts 回試す。通った (作業ツリー, ブランチ, 試行の記録) か None。
    what は実装役に渡す What の本文（段を 1 つにしたので TaskSpec は作らない）。run は 0 = 最初、1 = 作り直し。"""
    on_stage = on_stage or (lambda stage: None)
    tries = []
    wt, branch = first or host.new_worktree(cfg, tid)
    feedback = prev = None
    extended = ()
    for attempt in range(1, cfg["max_attempts"] + 1):
        log = outdir / f"implementer-{run}-{attempt}.log"
        on_stage(f"実装 {run + 1}-{attempt}")
        code, models, *rest = host.implement(cfg, wt, what, with_cutoff(tries and tries[-1]["cutoff"], feedback), log)   # rest は利用量・打ち切りの理由・ターン数
        digest = digest_of(cfg, wt)
        why = hline_abort.abort_reason(log, digest, prev)   # 断念・同じ差分の繰り返しは Gate 1 を呼ばずに打ち切る
        prev = digest
        if why is not None:
            tries.append({**attempt_record(run, attempt, code, models, False, rest), "flaky": [], "warnings": [], "abort": why})
            (outdir / f"gate-{run}-{attempt}.log").write_text(f"Gate 1 は呼ばず、試行を打ち切りました: {why}\n", encoding="utf-8")
            return None, None, tries
        on_stage(f"Gate 1 {run + 1}-{attempt}")
        changed = host.changed_paths(wt, cfg)
        warnings = []   # 不合格にしない指摘（差分の量・編集境界・規模・進捗の不整合）。実装役には返さず、試行の記録に残す
        ok, feedback = host.gate(cfg, wt, changed, None, task, *([extended] if extended else []), warnings=warnings)
        tries.append(rec := {**attempt_record(run, attempt, code, models, ok, rest), "flaky": list(fastsuite.flaky_names(feedback)),
                             "warnings": warnings})
        (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
        if ok:
            return wt, branch, tries
        if attempt < cfg["max_attempts"]:
            _, extended, feedback = widen(wt, None, extended, feedback, changed, rec)
    return None, None, tries


def widen(wt, spec, extended, feedback, changed, rec):
    """Gate 1 が落ちたあと、変えたモジュールを確かめている落ちた既存のテストを編集境界に足す。(spec, 足したファイル全部, 次の試行への feedback)。
    TaskSpec の無い段（C5）では編集境界そのものが無いので何もしない。"""
    if spec is None or not (added := hline_boundary.candidates(wt, spec, feedback, changed)):
        return spec, extended, feedback
    rec["boundary_added"] = list(added)
    return hline_boundary.extend(spec, added), extended + added, f"{feedback}\n{hline_boundary.note(added)}"


def unconverged(item):
    """収束しなかった What の理由：最後の試行の打ち切り・断念の理由、無ければ試行の回数。"""
    return (hline_abort.unconverged_reason(item["tries"])
            or f"{len(item['tries'])} 回の試行で Gate 1 に通らず、パッチを捨てた")


def record_infra(host, cfg, st, item, n, e):
    if getattr(e, "fatal", False):
        return
    item.setdefault("infra_retries", []).append({"attempt": n, "reason": str(e), "wait": None})
    host.save_state(cfg, st)


def stop_fatal(host, cfg, st, name, item, e):
    """待っても直らない異常：呼び直さず、その What を未収束にして理由を残す（infra_halt は立てない）。"""
    item.update(status="unconverged", at=host.today(), reason=str(e))
    print(f"[{item['tid']}] 再試行しません: {item['reason']}")
    host.mark(cfg, st, name)
    return False


def process(host, cfg, st, name):
    """待ちの What 1 件を、実装 → Gate 1 → 統合ブランチへ（段は 1 つ。分解役・再分解は呼ばない）。積めたら True、未収束なら False。
    環境の異常（Infra）は新しい作業ツリーで呼び直す（実装役の試行に数えない）。続けば What を待ちに戻し、infra_halt を立てて False。
    Fatal（進捗の記録の拒否・push の拒否）は呼び直さず、未収束にして False。
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
    host.mark(cfg, st, name, "実装 1-1")
    print(f"[{tid}] {item['title']}")

    def attempt(n):   # 毎回、新しい作業ツリーから（実装役の試行の数えも 1 からやり直す）
        try:
            wt, branch = host.new_worktree(cfg, tid)
            item.update(tries=[])
            stage = lambda s: host.mark(cfg, st, name, s)   # 段階は run_task が試行ごとに置く（実装 1-1 → Gate 1 1-1 → …）
            wt, branch, item["tries"] = host.run_task(cfg, tid, what, outdir, (wt, branch), item["task"], on_stage=stage)
            if wt is None: return False
            own = host.self_change(host.changed_paths(wt, cfg))
            host.integrate(cfg, wt, name, item["title"], item["task"])
            if own:
                st["self_change"] = {"name": name, "paths": own}
            return True
        except Infra as e:   # 呼び直しのたびに記録を残す（待った秒数は retry の記録で置き換わる）。Fatal は記録せずそのまま抜ける
            record_infra(host, cfg, st, item, n, e)
            raise

    ic = cfg["infra_retry"]
    try:
        done, records = infra_retry.retry(attempt, Infra, ic["max_retries"], ic["wait_seconds"], log=print)
    except Fatal as e:
        return stop_fatal(host, cfg, st, name, item, e)
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
        item.update(status="unconverged", at=host.today(), reason=unconverged(item))
        print(f"[{tid}] 収束しませんでした: {item['reason']}")
    host.mark(cfg, st, name)
    return done
