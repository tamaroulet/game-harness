"""H ライン：ハーネス自身の改修の自律ループ（段 0・段 1、docs/design/foundation_v3_review.md 改訂 5 §3〜§5）。

    python -m harness.hline poll     受信箱の What をキューに入れ、依存の順に統合ブランチへ積む（タスク スケジューラが 10 分ごとに、画面を出さずに呼ぶ。harness/hline_cron.py）
    python -m harness.hline setup    受信箱と総監督の部屋（.claude/settings.json・CLAUDE.md）を書く
本体は gate が hline_gate、build_prompt・implement が hline_prompt、run_task・process が hline_task。ここの同名の関数は
このモジュールを host として渡す薄い委譲で、テストの差し替えが効く。終了コード: 0 = 正常、1 = 未収束を記録した、2 = 環境の異常。
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base_whitelist, exitcode, fastsuite, gate_order, hline_gate, hline_prompt, hline_protect, hline_quota, hline_task  # noqa: E402,F401
import infra_retry, model_pin, proc, progress, size_limits, symbolmap  # noqa: E402,F401  テストが hline.proc などを差し替える
from hline_base import CONFIG, RESERVED, ROOT, Infra, acquire_lock, heartbeat, implementer_args, load_config, must, pinned_models, run_agent, slug, title_of  # noqa: E402,F401
from hline_gc import sweep  # noqa: E402
from hline_git import ahead, changed_paths, create_pr, drop_merged_branch, fetch, implementer_room, integrate, integrated, new_worktree, open_pr, pr_state  # noqa: E402,F401
from hline_hygiene import diff_files, problems  # noqa: E402
from hline_queue import by_status, intake, load_state, next_runnable, pick, recover, refresh, save_state, what_path  # noqa: E402,F401
from hline_report import line_modules, mark as _mark, pr_body, pr_title, self_change, today, write_report  # noqa: E402,F401
from hline_room import director_settings, setup  # noqa: E402,F401
from hline_spec import boundary_problems, diff_counts  # noqa: E402,F401


def build_prompt(what, feedback=None, symbol_map=None): return hline_prompt.build_prompt(what, feedback, symbol_map)


def implement(cfg, wt, what, feedback, log): return hline_prompt.implement(sys.modules[__name__], cfg, wt, what, feedback, log)


def gate(cfg, wt, paths, spec=None, task=None, extended=(), warnings=None): return hline_gate.gate(sys.modules[__name__], cfg, wt, paths, spec, task, extended, warnings)


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
    if bad := problems(diff_files(cfg), cfg):   # 汚れた差分では PR を出さない。人間が統合ブランチを直す
        st["hygiene_halt"] = {"files": bad}
        save_state(cfg, st)
        print(f"統合 PR は作りません（差分に不要なファイル: {len(bad)} 件）")
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
    if hline_quota.waiting(st):   # 利用枠の回復待ち。時刻を過ぎた記録は waiting が消し、下の save_state で残る
        write_report(cfg, st)
        print(f"利用枠の回復待ちです。{hline_quota.shown(st)} まで何もしません")
        return 0
    sweep(cfg)   # 前の走行の残骸の掃除。例外は出さず、戻り値も使わない（消せないものは次の起動で再び対象になる）
    st["infra_halt"] = None   # 前の走行の停止は、次の走行の自動の再開を妨げない
    st["hygiene_halt"] = None
    st["sync_conflict"] = None   # 衝突は毎回、作業ツリーを作るときに取り直す
    st["self_change"] = None   # 前の走行の区切りも、次の走行を妨げない
    save_state(cfg, st)
    fetch(cfg)
    recover(cfg, st, lambda n: integrated(cfg, n))
    if st["awaiting_pr"] and not settle_pr(cfg, st):
        write_report(cfg, st)
        print(f"統合 PR のレビュー待ちです。受信箱は取りません: {st['awaiting_pr']}")
        return 0
    refresh(st)
    intake(cfg, st)
    refresh(st)
    mark(cfg, st)
    unconverged = False
    while (name := next_runnable(st)):
        ok = process(cfg, st, name)
        refresh(st)
        mark(cfg, st)
        if st["infra_halt"] or st.get("quota_wait") or st.get("sync_conflict"):
            break
        unconverged |= not ok
        if st.get("self_change"):   # 読み込み済みのコードは古い。次の What は、積んだ変更を読み込んだ次の走行に任せる
            print("ライン自身の変更を積んだので走行を区切った（次の走行で読み込み直す）")
            break
    if st["infra_halt"]:
        write_report(cfg, st)
        return 2
    if st.get("sync_conflict"):   # 待ちに戻した What が残るので統合 PR は出さない。人間が衝突を直すと次の走行が取り直す
        write_report(cfg, st)
        return 2
    if st.get("quota_wait"):   # 待ちに戻した What が残るので統合 PR は出さない。定期起動が再開の時刻の後に続きを取る
        write_report(cfg, st)
        return 0
    if not by_status(st, "waiting") and not by_status(st, "processing"):
        open_integration_pr(cfg, st)
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
