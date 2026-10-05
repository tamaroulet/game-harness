"""H ライン：ハーネス自身の改修の自律ループ（段 0・段 1、docs/design/foundation_v3_review.md 改訂 5 §3〜§5）。

    python -m harness.hline poll     受信箱の What をキューに入れ、依存の順に統合ブランチへ積む（タスク スケジューラが 15 分ごとに呼ぶ）
    python -m harness.hline setup    受信箱と総監督の部屋（.claude/settings.json・CLAUDE.md）を書く
本体は gate・base_check が hline_gate、build_prompt・implement が hline_prompt、run_task・process が hline_task。ここの同名の関数は
このモジュールを host として渡す薄い委譲で、テストの差し替えが効く。終了コード: 0 = 正常、1 = 未収束を記録した、2 = 環境の異常。
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base_whitelist, exitcode, fastsuite, gate_order, hline_gate, hline_prompt, hline_protect, hline_task  # noqa: E402,F401
import infra_retry, model_pin, proc, progress, size_limits, symbolmap  # noqa: E402,F401  テストが hline.proc などを差し替える
from hline_base import CONFIG, RESERVED, ROOT, Infra, acquire_lock, heartbeat, implementer_args, load_config, must, pinned_models, run_agent, slug, title_of  # noqa: E402,F401
from hline_gc import sweep  # noqa: E402
from hline_git import ahead, changed_paths, create_pr, drop_merged_branch, fetch, implementer_room, integrate, integrated, new_worktree, open_pr, pr_state  # noqa: E402,F401
from hline_queue import blocked, by_status, intake, load_state, next_runnable, pick, recover, refresh, save_state, what_path  # noqa: E402,F401
from hline_report import line_modules, mark as _mark, pr_body, pr_title, self_change, today, write_report  # noqa: E402,F401
from hline_respec import second_round  # noqa: E402,F401
from hline_room import director_settings, setup  # noqa: E402,F401
from hline_spec import boundary_problems, decompose, diff_counts  # noqa: E402,F401


def build_prompt(spec, feedback=None, symbol_map=None): return hline_prompt.build_prompt(spec, feedback, symbol_map)


def implement(cfg, wt, spec, feedback, log): return hline_prompt.implement(sys.modules[__name__], cfg, wt, spec, feedback, log)


def gate(cfg, wt, paths, spec=None, task=None): return hline_gate.gate(sys.modules[__name__], cfg, wt, paths, spec, task)


def base_check(cfg, wt): return hline_gate.base_check(sys.modules[__name__], cfg, wt)


def mark(cfg, st, name=None, stage=None):
    """状態を書き、report.md を書き直す（hline_report.mark）。save_state・write_report はこのモジュールの名前で呼ぶ。"""
    _mark(cfg, st, name, stage, save=save_state, write=write_report)


def run_task(cfg, tid, spec, outdir, first=None, task=None, on_stage=None, run=0): return hline_task.run_task(sys.modules[__name__], cfg, tid, spec, outdir, first, task, on_stage, run)


def process(cfg, st, name): return hline_task.process(sys.modules[__name__], cfg, st, name)


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
