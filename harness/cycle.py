"""改善ループの見回りの本体（v3 §7-7・§7-9、L7）：状態のファイルを読み、機械の段階を進め、bench のジョブを起こして待つ。マージはしない。

    python -m harness.cycle start --id <id> [--accept-diff]   専用のクローンを origin/main に合わせ、版を固定して周を始める
    python -m harness.cycle tick                              見回りの 1 回（cycle_cron が定期に呼ぶ。専用のクローンから走る）
    python -m harness.cycle status                            今の周の状態を出す
    python -m harness.cycle resume                            止まった周を、同じ段階から続ける
    python -m harness.cycle abort                             今の周をやめる（走っているジョブは止める）

段階：propose → diff → pair → dev → held → pr → records → done。ジョブを待つ段階は、見回りのたびに状態を見て戻る。
- 止まる（Stop）：人が見る。理由を状態に残して通知する。直して resume すれば、同じ段階から続く（済んだステージは走り直さない）。
- 取り込まない（Reject）：機械の判定。集計の記録の PR を出して周を終える。公開に出すのは決まった語だけで、詳しい理由は状態に残す。
状態と専用のクローンは H ラインの out の下に置く（bench は読めも書けもしない）。bench の置き場のものはデータとして読むだけで、
そこの git を動かさない（クローンは作るときだけ。bench が仕込んだフックが操縦士の権限で走りうるため）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_job  # noqa: E402
import cycle_judge as cj  # noqa: E402
import cycle_steps as steps  # noqa: E402
import exitcode  # noqa: E402
import machine  # noqa: E402
import proposer_stage as ps  # noqa: E402
from bench_start import BenchError, git  # noqa: E402
from cycle_base import (ABORTED, ID, ROOT, RUNNING, STATE, STOPPED, Reject, Stop, active, chains_dir, home, load, notify, now_text, runner,  # noqa: E402
                        save, set_active, state_path)

# 基準の版と今の版の違いのうち、実装役の入力とゲートと走り方に効くもの。当たれば、人が読んでから --accept-diff で始める（v3 §7-9 の順 0）
IMPACT = re.compile(r"^(?:harness/(?:hline|gate|agy|proc|job_object|fastsuite|size_limits|impacted|surface|bench_run|bench_chain|bench_start)"
                    r"|config/(?:hline|surface|bench_implementer|agy))")


# ============================================================ 見回りと命令

def rejected(st, e):
    st["reject"] = {"kind": e.kind, "detail": e.detail, "phase": st["phase"]}
    st["outcome"], st["phase"] = f"取り込まない：{e.kind}", "records"
    steps.drop_staged(st)


def advance(st):
    """止まるか、ジョブを待つまで段階を進める。段階ごとに保存する。"""
    while st["status"] == RUNNING:
        try:
            moved = steps.STEPS[st["phase"]](st)
        except Reject as e:
            rejected(st, e)
            moved = True
        save(st)
        if not moved:
            return


def tick():
    """見回りの 1 回。走っている周が無ければ何もしない。専用のクローンから走っていなければ動かない（版を固定するため）。"""
    cid = active()
    if cid is None:
        return 0
    if Path(ROOT).resolve() != runner().resolve():
        print(f"見回りは専用のクローンから走らせてください: {runner()}", file=sys.stderr)
        return 2
    st = load(cid)
    if st["status"] != RUNNING:
        return 0
    try:
        advance(st)
    except steps.KNOWN as e:
        st["status"], st["reason"], st["stopped_at"] = STOPPED, f"{type(e).__name__}: {e}", now_text()
        save(st)
        notify(f"改善ループ {st['id']} が止まりました（{st['phase']}）")
    return 0


def baseline_problems(cfg, rev, accept):
    """始める前の確かめの文の列（空なら始められる）。"""
    base = cfg["baseline"]
    out = []
    for tag in base["dev"]:
        if len(cj.public_rows(runner(), tag)) != 18:
            out.append(f"基準 {tag} の公開の集計が 18 ステージそろっていません")
        if not (Path(base["out"]) / "chains" / tag).is_dir():
            out.append(f"基準 {tag} の走りの記録がありません")
        try:
            steps.staged_of(tag)
        except Stop as e:
            out.append(str(e))
    out += [f"基準 {tag} の記録がありません" for tag in base["held"] if not cj.private_rows(chains_dir() / "runs", tag)]
    if out:
        return out
    sample = next(iter(cj.public_rows(runner(), base["dev"][0])))
    doc = json.loads((runner() / "bench" / "records" / "chains" / base["dev"][0] / f"{sample[0]}.json").read_text(encoding="utf-8"))
    changed = git(runner(), ["diff", "--name-only", doc["rev"], rev, "--", "harness", "config"]).decode().split()
    hits = [p for p in changed if IMPACT.match(p)]
    return [] if accept or not hits else ["基準の版との違いに、実装役の入力とゲートに効くものがあります（読んでから --accept-diff）: " + "、".join(hits)]


def start(cid, accept=False):
    if not ID.fullmatch(cid) or cid == "jobs":
        print(f"id の形が違います（英小文字で始まる英数字）: {cid!r}", file=sys.stderr)
        return 2
    if active():
        print(f"走っている周があります: {active()}", file=sys.stderr)
        return 2
    if state_path(cid).exists():
        print(f"同じ id の周があります: {cid}", file=sys.stderr)
        return 2
    run = runner()
    git(run, ["fetch", "-q", "origin"])
    git(run, ["reset", "-q", "--hard", "origin/main"])
    git(run, ["clean", "-qfdx"])
    rev = git(run, ["rev-parse", "HEAD"]).decode().strip()
    cfg = machine.read_json(run / "config" / "cycle.json")
    if (bad := baseline_problems(cfg, rev, accept)):
        print("始められません:\n- " + "\n- ".join(bad), file=sys.stderr)
        return 2
    save({"id": cid, "status": RUNNING, "phase": "propose", "rev": rev, "started": now_text(), "accept_diff": accept,
          "baseline": cfg["baseline"], "propose": {"attempt": 1}, "jobs": {}})
    set_active(cid)
    print(f"- 周 {cid} を始めた（版 {rev[:10]}）。見回りが進める")
    return 0


def latest():
    """今の周の状態。走っていなければ、いちばん新しい周の状態。無ければ None。"""
    cid = active()
    if cid:
        return load(cid)
    found = sorted(home().glob(f"*/{STATE}"), key=lambda p: p.stat().st_mtime)
    return json.loads(found[-1].read_text(encoding="utf-8")) if found else None


def status():
    st = latest()
    if st is None:
        print("周はまだありません")
        return 0
    print(f"- 周 {st['id']}：{st['status']}・段階 {st['phase']}・版 {st['rev'][:10]}" + (f"・結果 {st['outcome']}" if st.get("outcome") else ""))
    if st.get("reason"):
        print(f"- 止まった理由：{st['reason']}")
    if st.get("reject"):
        print(f"- 取り込まない理由：{st['reject']['kind']}（{st['reject']['detail'][:300]}）")
    for key, job in (st.get("jobs") or {}).items():
        print(f"- ジョブ {key}：{bench_job.status(job['id'])['state'] if job.get('id') else '無し'}（{job['tries']} 回目）")
    return 0


def resume():
    st = latest()
    if st is None or st["status"] != STOPPED:
        print("止まっている周がありません", file=sys.stderr)
        return 2
    if bench_job.task_state()[0] in bench_job.RUNNING:
        print("bench のタスクが走っています。終わるか止めてから続けてください", file=sys.stderr)
        return 2
    for job in st.get("jobs", {}).values():
        job["tries"] += 1
    st["status"], st["reason"] = RUNNING, None
    save(st)
    print(f"- 周 {st['id']} を段階 {st['phase']} から続ける")
    return 0


def abort():
    st = latest()
    if st is None or st["status"] not in (RUNNING, STOPPED):
        print("やめる周がありません", file=sys.stderr)
        return 2
    if bench_job.task_state()[0] in bench_job.RUNNING:
        bench_job.stop()
    dest = (st.get("propose") or {}).get("dest")
    if dest and Path(dest).exists():
        ps.discard(dest)
    steps.drop_staged(st)
    st["status"], st["ended"] = ABORTED, now_text()
    save(st)
    set_active(None)
    print(f"- 周 {st['id']} をやめた")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.cycle", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start")
    s.add_argument("--id", required=True)
    s.add_argument("--accept-diff", action="store_true")
    for name in ("tick", "status", "resume", "abort"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    if a.command == "start":
        try:
            return start(a.id, a.accept_diff)
        except BenchError as e:
            print(f"止めた: {e}", file=sys.stderr)
            return 2
    return {"tick": tick, "status": status, "resume": resume, "abort": abort}[a.command]()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
