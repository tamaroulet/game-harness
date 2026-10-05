"""H ライン：ハーネス自身の改修の自律ループ（段 0・段 1、docs/design/foundation_v3_review.md 改訂 5 §3〜§5）。

    python -m harness.hline poll     受信箱の What をキューに入れ、依存の順に統合ブランチへ積む（タスク スケジューラが 15 分ごとに呼ぶ）
    python -m harness.hline setup    受信箱と総監督の部屋（.claude/settings.json・CLAUDE.md）を書く

**なぜ要るか**: 総監督の道具を剥ぐと、ハーネスを改修する者がいなくなる。以後の改修はすべてこのループに流す。

**流れ**: 受信箱の What（マイルストーンの宣言が要る）→ キュー（hline_queue）→ 分解役が TaskSpec JSON にする → Gate A（スキーマ）
→ 実装役（TaskSpec だけを渡す）→ Gate 1（テスト・編集境界・進捗の検証）→ 統合ブランチに積む。積むものが尽きたら、統合ブランチから
main への PR を 1 本だけ出して止まる。同じマイルストーンで未収束が 2 件に達したら BLOCKED で止まる。

終了コード: 0 = 正常（積んだ／取る What が無い／前回が走行中／統合 PR の待ち）、1 = この走行で未収束を TODO.md に記録した、
2 = 環境の異常（What は失わない）。
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import base_whitelist  # noqa: E402
import gate_order  # noqa: E402
import infra_retry  # noqa: E402
import model_pin  # noqa: E402
import proc  # noqa: E402,F401  テストが hline.proc を差し替える
import progress  # noqa: E402
import size_limits  # noqa: E402
import symbolmap  # noqa: E402
from hline_base import (CONFIG, RESERVED, ROOT, Infra, acquire_lock, heartbeat, implementer_args,  # noqa: E402,F401
                        load_config, must, pinned_models, run_agent, slug, title_of)
from hline_budget import usage  # noqa: E402
from hline_gc import sweep  # noqa: E402
from hline_git import (ahead, changed_paths, create_pr, drop_merged_branch, fetch, implementer_room,  # noqa: E402,F401
                       integrate, integrated, new_worktree, open_pr, pr_state)
from hline_queue import (blocked, by_status, intake, load_state, next_runnable, pick, recover, refresh,  # noqa: E402,F401
                         save_state, what_path)
from hline_report import (line_modules, mark as _mark, pr_body, pr_title, self_change, today,  # noqa: E402,F401
                          write_report)
from hline_respec import second_round  # noqa: E402
from hline_room import director_settings, setup  # noqa: E402,F401
from hline_spec import boundary_problems, decompose, diff_counts  # noqa: E402


# ============================================================ 実装役

def build_prompt(spec, feedback=None, symbol_map=None):
    """実装役に渡す入力。TaskSpec（JSON）だけで、What の本文（自然言語の背景）は渡さない。"""
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
         "次の TaskSpec（JSON）を満たす変更を、作業ディレクトリの中だけで行ってください。",
         "- edit_boundary の allowed_files の外と forbidden_files は変えない。差分は max_diff_lines 行以内",
         "- contracts と test_oracle を満たすことを、unittest のテストを tests/ に書いて示す（既存の書き方に合わせる）",
         "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）",
         "- CLAUDE.md の進捗（anchor・report）と報告の規約は総監督向けで、あなたには適用しない",
         "- タスク個別の検証（test_oracle の verification_command。無ければ自分が書いた tests/ のテスト）を手元で走らせて通す。"
         "全件テストはハーネスが走らせるので、自分では走らせない。既存のテストを壊さない",
         *(["", symbol_map] if symbol_map else []),   # 目次は最初の区切りの前に置く（区切りの後は TaskSpec の JSON だけ）
         "", "---", json.dumps(spec, ensure_ascii=False, indent=2)]
    if feedback:
        p += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(p) + "\n"


def implement(cfg, wt, spec, feedback, log):
    """実装役を 1 回呼ぶ。使ったモデルを照合し、記録する（model_pin）。TTL 超過・起動の失敗は Infra（試行にも Gate 1 にも進めない）。"""
    agent = cfg["implementer"]
    code, out, err = proc.run(implementer_args(agent, proc.resolve_cli(agent["cli"])), wt, cfg["ttl_seconds"]["implementer"],
                              "実装役", input=build_prompt(spec, feedback, symbolmap.prompt_text(cfg, wt, symbolmap.spec_modules(spec))))
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    if code in infra_retry.INFRA_EXIT_CODES:
        raise Infra(f"実装役の CLI が終了コード {code}: {infra_retry.classify_exit(code, err)}")
    return code, pinned_models(agent, out, "実装役"), usage(out)


def gate(cfg, wt, paths, spec=None, task=None):
    """H ラインの Gate 1。(通ったか, 実装役に返す出力)。TaskSpec があれば編集境界も、タスクがあれば進捗の検証コマンドも見る。"""
    if not paths:
        return False, "作業ツリーに変更がありません。TaskSpec を満たす変更を加えてください。"
    bad = [p for p in paths if any(p == q or p.startswith(q) for q in cfg["protected_paths"])]
    if bad:
        return False, f"変えてはならないパスを変えています: {', '.join(bad)}。元に戻してください。"
    problems = boundary_problems(spec, paths, diff_counts(cfg, wt)) if spec else []
    problems += size_limits.scan(wt, paths, size_limits.limits(), size_limits.head_source(cfg, wt))
    if problems:
        return False, "\n".join(problems)
    first = gate_order.focused(cfg, wt, paths, task)   # 個別の検証が先。落ちたら全件テストは走らせない
    if first and not first[0]:
        return first
    env = dict(os.environ, PYTHONUTF8="1")
    code, out, err = proc.run(cfg["gate_command"], wt, cfg["ttl_seconds"]["gate"], "Gate 1", env=env)
    return code == 0, gate_order.report(" ".join(cfg["gate_command"]), code, out, err, cfg["gate_tail_chars"])


def base_check(cfg, wt):
    """実装役を呼ぶ前の検査。合格済みのタスクのテストだけを走らせる。(通ったか, 出力の末尾)。走らせるものが無ければ通ったとする。"""
    path = Path(wt) / "docs" / "progress.yaml"
    modules = base_whitelist.completed_modules(progress.load(path), "game-harness") if path.is_file() else ()
    cmd = base_whitelist.unittest_command(cfg["gate_command"], modules)
    if cmd is None:
        return True, ""
    code, out, err = proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], "Base 検査", env=dict(os.environ, PYTHONUTF8="1"))
    return code == 0, (err + out)[-cfg["gate_tail_chars"]:]


# ============================================================ 1 件の処理

def mark(cfg, st, name=None, stage=None):
    """状態を書き、report.md を書き直す（hline_report.mark）。save_state・write_report はこのモジュールの名前で呼ぶ。"""
    _mark(cfg, st, name, stage, save=save_state, write=write_report)


def run_task(cfg, tid, spec, outdir, first=None, task=None, on_stage=None, run=0):
    """1 つの作業ツリーで実装役を最大 max_attempts 回試す。通った (作業ツリー, ブランチ, 試行の記録) か None。first は分解役の作業ツリー（無ければ作る）。run は 0 = 最初、1 = 作り直し。"""
    on_stage = on_stage or (lambda stage: None)
    tries = []
    wt, branch = first or new_worktree(cfg, tid)
    ok, out = base_check(cfg, wt)
    if not ok:   # 実装の前から合格済みのテストが落ちている。作業ツリーを替えて呼び直す（試行に数えない）
        raise Infra(f"base（実装前）で合格済みのタスクのテストが落ちています: {out[-500:]}")
    feedback = None
    for attempt in range(1, cfg["max_attempts"] + 1):
        log = outdir / f"implementer-{run}-{attempt}.log"
        on_stage(f"実装 {run + 1}-{attempt}")
        code, models, *rest = implement(cfg, wt, spec, feedback, log)   # rest は利用量（無い呼び手は None）
        on_stage(f"Gate 1 {run + 1}-{attempt}")
        ok, feedback = gate(cfg, wt, changed_paths(wt, cfg), spec, task)
        tries.append({"run": run, "attempt": attempt, "cli_exit": code, "models": models, "gate": ok, "usage": rest[0] if rest else None})
        (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
        if ok:
            return wt, branch, tries
    return None, None, tries


def process(cfg, st, name):
    """待ちの What 1 件を、分解 → Gate A → 実装 → Gate 1 → 統合ブランチへ。積めたら True、未収束なら False。
    環境の異常（Infra）は新しい作業ツリーで呼び直す（実装役の試行に数えない）。続けば What を待ちに戻し、infra_halt を立てて False。
    ライン自身の変更を積んだときは st["self_change"] を置く（run_line が走行を区切る）。"""
    item = st["items"][name]
    what = what_path(cfg, name).read_text(encoding="utf-8")
    tid = f"{datetime.datetime.now():%Y%m%d-%H%M}-{slug(name)}"
    outdir = Path(cfg["out"]) / tid
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "what.md").write_text(what, encoding="utf-8")
    item.pop("infra_retries", None)
    item.pop("started_at", None)   # 前の走行の途中で落ちた項目の開始の時刻を引き継がない
    item.update(status="processing", tid=tid)
    mark(cfg, st, name, "分解")
    print(f"[{tid}] {item['title']}")

    def attempt(n):   # 毎回、新しい作業ツリーから（分解役・実装役の試行の数えも 1 からやり直す）
        try:
            wt, branch = new_worktree(cfg, tid)
            spec, item["decompose"] = decompose(cfg, wt, what, item, outdir)   # Gate A。適合しなければ実装役を呼ばない
            item.update(tries=[], respec=None)
            stage = lambda s: mark(cfg, st, name, s)
            if spec is not None:
                mark(cfg, st, name, "実装 1-1")
                wt, branch, item["tries"] = run_task(cfg, tid, spec, outdir, (wt, branch), item["task"], on_stage=stage)
                if wt is None and cfg["respecs"]:   # 収束しなかった。失敗の要約を分解役に返して作り直し、1 回だけ再実行する
                    mark(cfg, st, name, "再分解")
                    wt, branch, _ = second_round(cfg, tid, what, item, outdir, spec, decompose, new_worktree, run_task, on_stage=stage)
            if spec is None or wt is None: return False
            own = self_change(changed_paths(wt, cfg))
            integrate(cfg, wt, name, item["title"], item["task"])
            if own:
                st["self_change"] = {"name": name, "paths": own}
            return True
        except Infra as e:   # 呼び直しのたびに記録を残す（待った秒数は retry の記録で置き換わる）
            item.setdefault("infra_retries", []).append({"attempt": n, "reason": str(e), "wait": None})
            save_state(cfg, st)
            raise

    ic = cfg["infra_retry"]
    try:
        done, records = infra_retry.retry(attempt, Infra, ic["max_retries"], ic["wait_seconds"], log=print)
    except infra_retry.InfraExhausted as e:
        item.update(status="waiting", infra_retries=e.records)
        st["infra_halt"] = {"name": name, "reason": e.records[-1]["reason"], "attempts": len(e.records), "at": today()}
        mark(cfg, st, name)
        return False
    if records:
        item["infra_retries"] = records
    if done:
        item["status"] = "done"
    else:
        item.update(status="unconverged", at=today(), reason=(item["respec"] or {}).get("reason") or item["decompose"]["reason"]
                    or f"{len(item['tries'])} 回の試行で Gate 1 に通らず、パッチを捨てた")
        print(f"[{tid}] 収束しませんでした: {item['reason']}")
    mark(cfg, st, name)
    return done


# ============================================================ 走行

def settle_pr(cfg, st):
    """統合 PR が閉じられていたら（マージを含む）停止を解く。まだ開いていれば False。"""
    url = st["awaiting_pr"]
    state = pr_state(cfg, url)
    if state == "OPEN":
        return False
    if state == "MERGED":
        drop_merged_branch(cfg)
        for i in st["items"].values():
            if i["status"] == "done" and i.get("pr") == url:
                i["merged"] = True
    st["awaiting_pr"] = None
    save_state(cfg, st)
    return True


def open_integration_pr(cfg, st):
    """積むものが尽きたとき、統合ブランチに main に無いコミットがあれば、統合 PR をちょうど 1 本にして止まる。"""
    fresh = [i for i in st["items"].values() if i["status"] == "done" and not i.get("pr") and not i.get("merged")]
    if not fresh or ahead(cfg) == 0:
        return
    url = open_pr(cfg) or create_pr(cfg, pr_title(cfg, st), pr_body(cfg, st))
    for i in st["items"].values():
        if i["status"] == "done" and not i.get("merged"):
            i["pr"] = url
    st["awaiting_pr"] = url
    save_state(cfg, st)
    print(f"統合 PR: {url}")


def run_line(cfg):
    sweep(cfg)   # 前の走行の残骸の掃除。例外は出さず、戻り値も使わない（消せないものは次の起動で再び対象になる）
    st = load_state(cfg)
    st["infra_halt"] = None   # 前の走行の停止は、次の走行の自動の再開を妨げない
    st["self_change"] = None   # 前の走行の区切りも、次の走行を妨げない
    save_state(cfg, st)
    fetch(cfg)
    recover(cfg, st, lambda n: integrated(cfg, n))
    if st["awaiting_pr"] and not settle_pr(cfg, st):
        write_report(cfg, st)
        print(f"統合 PR のレビュー待ちです。受信箱は取りません: {st['awaiting_pr']}")
        return 0
    refresh(st)
    was_blocked = bool(blocked(cfg, st))
    intake(cfg, st, replacements_only=was_blocked)   # BLOCKED の間は、未収束の What を直したものだけを取る
    refresh(st)
    if was_blocked and not blocked(cfg, st):
        intake(cfg, st)
        refresh(st)
    mark(cfg, st)
    unconverged = False
    while not blocked(cfg, st) and (name := next_runnable(st)):
        ok = process(cfg, st, name)
        refresh(st)
        mark(cfg, st)
        if st["infra_halt"]:
            break
        unconverged |= not ok
        if st.get("self_change"):   # 読み込み済みのコードは古い。次の What は、積んだ変更を読み込んだ次の走行に任せる
            print("ライン自身の変更を積んだので走行を区切った（次の走行で読み込み直す）")
            break
    if st["infra_halt"]:
        write_report(cfg, st)
        return 2
    if not blocked(cfg, st) and not by_status(st, "waiting") and not by_status(st, "processing"):
        open_integration_pr(cfg, st)
    elif blocked(cfg, st):
        print("BLOCKED：同じマイルストーンで未収束が上限に達しました。統合 PR は作りません")
    write_report(cfg, st)
    return 1 if unconverged else 0


def poll(cfg):
    inbox = Path(cfg["inbox"])
    if not inbox.is_dir():
        raise Infra(f"受信箱がありません: {inbox}（python -m harness.hline setup）")
    stale = cfg["ttl_seconds"]["lock_stale"]
    lock = acquire_lock(inbox, stale)
    if lock is None:
        print("前回の走行が続いています。何もしません")
        return 0
    try:
        with heartbeat(lock, stale / 10):
            return run_line(cfg)
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="H ライン（段 1）")
    ap.add_argument("cmd", choices=["poll", "setup"])
    args = ap.parse_args(argv)
    cfg = load_config()
    try:
        return poll(cfg) if args.cmd == "poll" else setup(cfg)
    except Infra as e:
        print(f"ABORT（環境の異常）: {e}")
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
