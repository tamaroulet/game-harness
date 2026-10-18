"""H ライン（harness/hline.py）の 1 件の処理：1 つの作業ツリーでの実装の試行（run_task）と、What 1 件の実装から統合まで（process）。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import datetime
from pathlib import Path

import failure_classes
import fastsuite
import hline_abort
import hline_boundary
import hline_extend
import hline_followup
import hline_outer
import hline_quota
import hline_suite
import hline_queue
import hline_resume
import infra_retry
from hline_base import Fatal, Infra, Quota
from hline_budget import attempt_record, with_cutoff


def digest_of(cfg, wt):
    """作業ツリーの差分の digest。git の作業ツリーでない（.git が無い）ときは None で、同じ差分の判定はしない。"""
    return hline_abort.patch_digest(wt, cfg["ttl_seconds"]["git"]) if (Path(wt) / ".git").exists() else None


def map_size(host):
    """直前の実装役の呼び出しで渡した目次の字数（host.map_chars）。記録が無い host では空。"""
    n, cap = getattr(host, "map_chars", None), getattr(host, "turn_cap_used", None)
    blocked = getattr(host, "blocked_commands", None)   # agy の試行だけ（--sandbox に拒まれた命令の手番の数）
    return {**({"map_chars": n} if isinstance(n, int) and not isinstance(n, bool) else {}),
            **({"blocked_commands": blocked} if isinstance(blocked, int) and not isinstance(blocked, bool) else {}),
            **({"max_turns": cap} if isinstance(cap, int) and not isinstance(cap, bool) else {})}   # max_turns はどちらの上限で呼んだか


def run_task(host, cfg, tid, what, outdir, first=None, task=None, on_stage=None, run=0):
    """1 つの作業ツリーで実装役を最大 max_attempts 回試す。通った (作業ツリー, ブランチ, 試行の記録) か None。
    what は実装役に渡す What の本文（段を 1 つにしたので TaskSpec は作らない）。run は 0 = 最初、1 = 作り直し。"""
    on_stage = on_stage or (lambda stage: None)
    tries = []
    wt, branch = first or host.new_worktree(cfg, tid)
    feedback, prev = hline_resume.previous(outdir), None   # 置き直した What の最初の試行だけ、前回の失敗の出力を受け取る
    extended = ()
    cmd = None if task or not isinstance(what, str) else hline_queue.parse_header(what)["verify"]   # タスクを宣言しない What の `検証:`（キューに残した本文の写しから読む）
    verify = {"verify": cmd} if cmd else {}
    milestone = hline_queue.parse_header(what)["milestone"] if isinstance(what, str) else None   # 基盤のパスの検査（Gate 1）に渡す
    verify = {**verify, "milestone": milestone} if milestone else verify
    for attempt in range(1, cfg["max_attempts"] + 1):
        log = outdir / f"implementer-{run}-{attempt}.log"
        on_stage(f"実装 {run + 1}-{attempt}")
        host.blocked_commands = None   # agy の呼び出しだけが置く
        host.follow_up, host.turn_cap_used = attempt > 1 or feedback is not None, None   # 前の差分から続ける試行は、上限を下げて失敗の出力を絞る
        shown = hline_followup.narrow(feedback, cfg.get("gate_tail_chars", 4000)) if host.follow_up else feedback
        code, models, *rest = host.implement(cfg, wt, what, with_cutoff(tries and tries[-1]["cutoff"], shown), log)   # rest は利用量・打ち切りの理由・ターン数
        if code == 124:   # 実装役の時間切れ：Gate 1 も残りの試行も行わず、未収束にする（呼び直さない）
            secs = getattr(host, "implement_seconds", None)
            secs = secs if isinstance(secs, (int, float)) and not isinstance(secs, bool) else cfg["ttl_seconds"]["implementer"]
            why = f"実装役が時間切れになりました（終了コード 124、経過 {secs:g} 秒）。残りの試行を行わずに打ち切りました"
            tries.append({**attempt_record(run, attempt, code, models, False, rest), "flaky": [], "warnings": [], "timeout": why, **map_size(host)})
            tries[-1]["failure"] = failure_classes.describe(tries[-1], None)
            tries[-1].update(hline_extend.test_counts(tries[-1], None))
            (outdir / f"gate-{run}-{attempt}.log").write_text(f"Gate 1 は呼ばず、試行を打ち切りました: {why}\n", encoding="utf-8")
            hline_resume.keep(cfg, wt, outdir, f"試行を打ち切りました: {why}")
            return None, None, tries
        digest = digest_of(cfg, wt)
        why = hline_abort.abort_reason(log, digest, prev)   # 断念・同じ差分の繰り返しは Gate 1 を呼ばずに打ち切る
        prev = digest
        if why is not None:
            tries.append({**attempt_record(run, attempt, code, models, False, rest), "flaky": [], "warnings": [], "abort": why, **map_size(host)})
            tries[-1]["failure"] = failure_classes.describe(tries[-1], None)
            tries[-1].update(hline_extend.test_counts(tries[-1], None))
            (outdir / f"gate-{run}-{attempt}.log").write_text(f"Gate 1 は呼ばず、試行を打ち切りました: {why}\n", encoding="utf-8")
            hline_resume.keep(cfg, wt, outdir, f"試行を打ち切りました: {why}")
            return None, None, tries
        on_stage(f"Gate 1 {run + 1}-{attempt}")
        changed = host.changed_paths(wt, cfg)
        warnings = []   # 不合格にしない指摘（差分の量・編集境界・規模・進捗の不整合）。実装役には返さず、試行の記録に残す
        ok, feedback = host.gate(cfg, wt, changed, None, task, *([extended] if extended else []), warnings=warnings, **verify)
        tries.append(rec := {**attempt_record(run, attempt, code, models, ok, rest), "flaky": list(fastsuite.flaky_names(feedback)),
                             "warnings": warnings, **map_size(host)})
        suites = getattr(host, "hline_suite", None)   # host（harness.hline）が持つものを使う。持たない差し替えの host では走らせない
        if ok and suites and (suite := suites.run(wt, changed, (cfg.get("ttl_seconds") or {}).get("gate"))) is not None:   # 積む前に、変えたパッケージの全件
            ok, text, rec["suite"] = suite
            feedback = f"{feedback}\n\n{text}"
            rec["gate"] = ok
        if ok and task and (record := getattr(host, "record", None)):   # 最後の段：進捗の記録。落ちたら積まない
            code, text = record(cfg, wt, task)
            if code == 1:   # 検証コマンドが落ちた：試行の不合格にして、出力を次の試行に渡す
                ok, feedback = False, host.record_refused(task, code, text)
                rec["gate"] = False
            elif code != 0:   # 台帳の状態の異常：呼び直しても直らない
                hline_resume.keep(cfg, wt, outdir, reason := host.record_refused(task, code, text))
                raise Fatal(reason)
        rec.update(hline_extend.test_counts(rec, feedback))
        if not ok:
            rec["failure"] = failure_classes.describe(rec, feedback)
        (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
        if ok:
            return wt, branch, tries
        hline_resume.keep(cfg, wt, outdir, feedback)
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
    kept = (item.get("material") or {}).get("kept")
    return (hline_abort.unconverged_reason(item["tries"])
            or f"{len(item['tries'])} 回の試行で Gate 1 に通らず、{'差分を残した' if kept else 'パッチを捨てた'}")


def record_infra(host, cfg, st, item, n, e):
    if getattr(e, "fatal", False):
        return
    item.setdefault("infra_retries", []).append({"attempt": n, "reason": str(e), "wait": None})
    host.save_state(cfg, st)


def stop_fatal(host, cfg, st, name, item, e):
    """待っても直らない異常：呼び直さず、その What を未収束にして理由を残す（infra_halt は立てない）。"""
    item.update(status="unconverged", at=host.today(), reason=str(e), failure_class=failure_classes.last_class(item))
    print(f"[{item['tid']}] 再試行しません: {item['reason']}")
    host.mark(cfg, st, name)
    return False


def wait_quota(host, cfg, st, name, item, e):
    """利用枠切れ：その試行を数えず、What を待ちに戻す。再開してよい時刻を st["quota_wait"] に残す（run_line が走行を終える）。"""
    item.update(status="waiting", tries=[])
    item.pop("infra_retries", None)
    hline_quota.record(st, name, e.reason)
    print(f"[{item['tid']}] {e.reason}。試行に数えず待ちに戻します（再開: {hline_quota.shown(st)} 以降）")
    host.mark(cfg, st, name)
    return False


def after_warning(tasks):
    return f"after の上流 {', '.join(tasks)} が未収束のまま処理しました"


def process(host, cfg, st, name):
    """待ちの What 1 件を、実装 → Gate 1 → 統合ブランチへ（段は 1 つ。分解役・再分解は呼ばない）。積めたら True、未収束なら False。
    環境の異常（Infra）は新しい作業ツリーで呼び直す（実装役の試行に数えない）。続けば What を待ちに戻し、infra_halt を立てて False。
    Fatal（進捗の記録の拒否・push の拒否）は呼び直さず、未収束にして False。利用枠切れ（Quota）は待ちに戻して False（wait_quota）。
    ライン自身の変更を積んだときは st["self_change"] を置く（run_line が走行を区切る）。"""
    item = st["items"][name]
    bcfg = hline_outer.branch_cfg(cfg, item.get("milestone"))   # 作業ツリーを作る・積む統合ブランチ（外側の What は hline/outer）
    what = host.what_path(cfg, name).read_text(encoding="utf-8")
    tid = f"{datetime.datetime.now():%Y%m%d-%H%M}-{host.slug(name)}"
    outdir = Path(cfg["out"]) / tid
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "what.md").write_text(what, encoding="utf-8")
    item.pop("infra_retries", None)
    item.pop("failure_class", None)
    item.pop("material", None)
    item.pop("started_at", None)   # 前の走行の途中で落ちた項目の開始の時刻を引き継がない
    item.update(status="processing", tid=tid)
    item["after_unconverged"] = hline_queue.after_unconverged(st, item)   # 未収束の after を待たずに処理した警告
    host.mark(cfg, st, name, "実装 1-1")
    print(f"[{tid}] {item['title']}")

    def attempt(n):   # 毎回、新しい作業ツリーから（実装役の試行の数えも 1 からやり直す）
        try:
            wt, branch = host.new_worktree(bcfg, tid)
            item.update(tries=[])
            hline_resume.forget_attempt(outdir)
            resume = hline_resume.begin(host, cfg, name, wt, outdir) if item.get("replaced") else None   # 置き直し：残した変更を載せる
            item.pop("resume", None)
            if resume:
                item["resume"] = resume
            stage = lambda s: host.mark(cfg, st, name, s)   # 段階は run_task が試行ごとに置く（実装 1-1 → Gate 1 1-1 → …）
            wt, branch, item["tries"] = host.run_task(cfg, tid, what, outdir, (wt, branch), item["task"], on_stage=stage)
            if resume and item["tries"]:
                item["tries"][0]["resume"] = resume
            if item.get("extended") and item["tries"]:
                item["tries"][0]["extended"] = item["extended"]   # 延長して続けた最初の試行にも印を残す
            if item["tries"] and item["after_unconverged"]:
                item["tries"][-1].setdefault("warnings", []).append(after_warning(item["after_unconverged"]))
            if wt is None: return False
            own = host.self_change(host.changed_paths(wt, cfg))
            warnings = item["tries"][-1].setdefault("warnings", []) if item["tries"] else []   # 進捗の不整合もここに積む
            host.integrate(bcfg, wt, name, item["title"], None, warnings=warnings)   # 進捗の記録は run_task の最後の段で済んでいる
            if own:
                st["self_change"] = {"name": name, "paths": own}
            return True
        except Infra as e:   # 呼び直しのたびに記録を残す（待った秒数は retry の記録で置き換わる）。Fatal は記録せずそのまま抜ける
            record_infra(host, cfg, st, item, n, e)
            raise

    ic = cfg["infra_retry"]
    try:
        done, records = infra_retry.retry(attempt, Infra, ic["max_retries"], ic["wait_seconds"], log=print)
    except Quota as e:
        return wait_quota(host, cfg, st, name, item, e)
    except Fatal as e:
        hline_resume.store(cfg, name, outdir)
        item["material"] = hline_resume.material(cfg, name)
        return stop_fatal(host, cfg, st, name, item, e)
    except infra_retry.InfraExhausted as e:
        item.update(status="waiting", infra_retries=e.records, failure_class=failure_classes.INFRA)
        st["infra_halt"] = {"name": name, "reason": e.records[-1]["reason"], "attempts": len(e.records), "at": host.today()}
        host.mark(cfg, st, name)
        return False
    if records:
        item["infra_retries"] = records
    if done:
        hline_queue.mark_done(item)
        hline_resume.clear(cfg, name)
    else:
        hline_resume.store(cfg, name, outdir)
        item["material"] = hline_resume.material(cfg, name)
        item.update(status="unconverged", at=host.today(), reason=unconverged(item), failure_class=failure_classes.last_class(item))
        print(f"[{tid}] 収束しませんでした: {item['reason']}")
        if got := hline_extend.decide(cfg, name, item):   # 前進が確かなら、置き直したのと同じに 1 回だけ続ける
            hline_extend.extend(item, got)
            print(f"[{tid}] 通った試験が {got['previous_passed']} → {got['passed']} に増えたので、残した差分を載せて 1 回だけ続けます（落ちた {got['failed']}）")
            done = True   # 待ちに戻したので未収束ではない（続きの結果が次の処理で決まる）
    host.mark(cfg, st, name)
    return done
