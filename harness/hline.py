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
import proc  # noqa: E402,F401  テストが hline.proc を差し替える
from hline_base import (CONFIG, RESERVED, ROOT, Infra, acquire_lock, heartbeat, implementer_args,  # noqa: E402,F401
                        load_config, must, run_agent, slug, title_of)
from hline_git import (ahead, changed_paths, create_pr, drop_merged_branch, fetch, implementer_room,  # noqa: E402,F401
                       integrate, integrated, new_worktree, open_pr, pr_state)
from hline_queue import (blocked, by_status, intake, load_state, next_runnable, pick, recover, refresh,  # noqa: E402,F401
                         save_state, what_path)
from hline_report import pr_body, pr_title, today, write_report  # noqa: E402
from hline_room import director_settings, setup  # noqa: E402,F401
from hline_spec import boundary_problems, decompose, diff_lines, task_verification  # noqa: E402


# ============================================================ 実装役

def build_prompt(spec, feedback=None):
    """実装役に渡す入力。TaskSpec（JSON）だけで、What の本文（自然言語の背景）は渡さない。"""
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
         "次の TaskSpec（JSON）を満たす変更を、作業ディレクトリの中だけで行ってください。",
         "- edit_boundary の allowed_files の外と forbidden_files は変えない。差分は max_diff_lines 行以内",
         "- contracts と test_oracle を満たすことを、unittest のテストを tests/ に書いて示す（既存の書き方に合わせる）",
         "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）",
         "- CLAUDE.md の進捗（anchor・report）と報告の規約は総監督向けで、あなたには適用しない",
         "- 判定は `python -m unittest discover -s tests` の終了コード 0。既存のテストを壊さない",
         "", "---", json.dumps(spec, ensure_ascii=False, indent=2)]
    if feedback:
        p += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(p) + "\n"


def implement(cfg, wt, spec, feedback, log):
    """実装役を 1 回呼ぶ。使ったモデルを照合し、記録する（作業規約：model_pin）。"""
    code, models, _ = run_agent(cfg["implementer"], wt, cfg["ttl_seconds"]["implementer"], "実装役",
                                build_prompt(spec, feedback), log)
    return code, models


def gate(cfg, wt, paths, spec=None, task=None):
    """H ラインの Gate 1。(通ったか, 実装役に返す出力)。TaskSpec があれば編集境界も、タスクがあれば進捗の検証コマンドも見る。"""
    if not paths:
        return False, "作業ツリーに変更がありません。TaskSpec を満たす変更を加えてください。"
    bad = [p for p in paths if any(p == q or p.startswith(q) for q in cfg["protected_paths"])]
    if bad:
        return False, f"変えてはならないパスを変えています: {', '.join(bad)}。元に戻してください。"
    if spec:
        problems = boundary_problems(spec, paths, diff_lines(cfg, wt))
        if problems:
            return False, "\n".join(problems)
    env = dict(os.environ, PYTHONUTF8="1")
    code, out, err = proc.run(cfg["gate_command"], wt, cfg["ttl_seconds"]["gate"], "Gate 1", env=env)
    if code == 0 and task:
        return task_verification(cfg, wt, task)
    return code == 0, (err + out)[-cfg["gate_tail_chars"]:]


# ============================================================ 1 件の処理

def run_task(cfg, tid, spec, outdir, first=None, task=None):
    """作業ツリーを作り直しながら試す。通った (作業ツリー, ブランチ, 試行の記録) か None。作業ツリーは消さない（Detach）。
    first は分解役が使った作業ツリー（最初の 1 回はそれを使う）。"""
    tries = []
    for run in range(1 + cfg["reruns"]):
        wt, branch = first if (run == 0 and first) else new_worktree(cfg, tid)
        feedback = None
        for attempt in range(1, cfg["max_attempts"] + 1):
            log = outdir / f"implementer-{run}-{attempt}.log"
            code, models = implement(cfg, wt, spec, feedback, log)
            ok, feedback = gate(cfg, wt, changed_paths(wt, cfg), spec, task)
            tries.append({"run": run, "attempt": attempt, "cli_exit": code, "models": models, "gate": ok})
            (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
            if ok:
                return wt, branch, tries
    return None, None, tries


def process(cfg, st, name):
    """待ちの What 1 件を、分解 → Gate A → 実装 → Gate 1 → 統合ブランチへ。積めたら True、未収束なら False。
    環境の異常（Infra）では What を失わない：状態は processing のまま残り、次の起動が統合ブランチを見て済みか待ちに戻す。"""
    item = st["items"][name]
    what = what_path(cfg, name).read_text(encoding="utf-8")
    tid = f"{datetime.datetime.now():%Y%m%d-%H%M}-{slug(name)}"
    outdir = Path(cfg["out"]) / tid
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "what.md").write_text(what, encoding="utf-8")
    item.update(status="processing", tid=tid)
    save_state(cfg, st)
    print(f"[{tid}] {item['title']}")
    wt, branch = new_worktree(cfg, tid)
    spec, item["decompose"] = decompose(cfg, wt, what, item, outdir)   # Gate A。適合しなければ実装役を呼ばない
    tries = []
    if spec is not None:
        wt, branch, tries = run_task(cfg, tid, spec, outdir, (wt, branch), item["task"])
    item["tries"] = tries
    if spec is not None and wt is not None:
        integrate(cfg, wt, name, item["title"], item["task"])
        item["status"] = "done"
    else:
        item.update(status="unconverged", at=today(), reason=item["decompose"]["reason"]
                    or f"{len(tries)} 回の試行で Gate 1 に通らず、パッチを捨てた")
        print(f"[{tid}] 収束しませんでした: {item['reason']}")
    save_state(cfg, st)
    return item["status"] == "done"


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
    st = load_state(cfg)
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
    save_state(cfg, st)
    unconverged = False
    while not blocked(cfg, st) and (name := next_runnable(st)):
        unconverged |= not process(cfg, st, name)
        refresh(st)
        save_state(cfg, st)
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
