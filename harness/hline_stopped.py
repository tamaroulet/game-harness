"""H ラインを止めた印と、止まっている間の report.md（W-144）。

    python -m harness.hline_stopped stop <理由>   止めた印を置き、report.md を書き直す
    python -m harness.hline_stopped report        H ラインの走行と別に、止まっている今の状態で report.md を書き直す
    python -m harness.hline_stopped resume        止めた印を外す（report.md は次の走行が書く）

止めた印は <out>/stopped.json（理由と時刻）。印がある間、hline poll は何もせずに終わる。
止まっている間の report.md は、origin/main の HEAD と台帳を映し、走っている間の状態の語を出さない。
キュー・計器は最後の走行の時点のものとして時刻を添え、照合（checks/）は走らせずに、最後に走った時刻を出す。
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import proc  # noqa: E402
from hline_base import load_config, write_json  # noqa: E402

MARKER = "stopped.json"
STATE = "queue.json"


def marker_path(cfg):
    return Path(cfg["out"]) / MARKER


def marker(cfg):
    """止めた印 {"reason", "at"}。無いか読めなければ None。"""
    try:
        m = json.loads(marker_path(cfg).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) and m.get("reason") else None
    except (OSError, ValueError):
        return None


def stop(cfg, reason, now=None):
    if not str(reason).strip():
        raise ValueError("止める理由を書いてください")
    at = (now or datetime.datetime.now)().astimezone().isoformat(timespec="seconds")
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    write_json(marker_path(cfg), {"reason": " ".join(str(reason).split()), "at": at})


def resume(cfg):
    marker_path(cfg).unlink(missing_ok=True)


def last_run(cfg):
    """H ラインが最後に走った時刻（走行が状態を最後に書いた時刻）。記録が無ければ None。"""
    p = Path(cfg["out"]) / STATE
    return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.is_file() else None


def main_head(cfg, runner):
    """origin/main を取り込み直してから、HEAD の SHA・題・コミットの時刻の 1 行。取れなければ取得失敗の理由。"""
    import hline_probe
    _, failed = hline_probe.git(cfg, ["fetch", "-q", "--prune", cfg["base"].partition("/")[0]], runner)
    note = f"（取り込み直しに失敗。手元の {cfg['base']} を出す: {failed[:80]}）" if failed else ""
    out, why = hline_probe.git(cfg, ["log", "-1", "--format=%H%x1f%s%x1f%cd", "--date=format:%Y-%m-%d %H:%M", cfg["base"]],
                               runner)
    if why:
        return hline_probe.failure(why)
    sha, subject, at = (out.strip().split("\x1f") + ["", ""])[:3]
    return f"{sha}（{subject}／コミット: {at}）{note}"


def main_ledger(cfg, runner):
    """origin/main の台帳。読めなければ None と理由。"""
    import hline_probe
    out, why = hline_probe.git(cfg, ["show", f"{cfg['base']}:docs/progress.yaml"], runner)
    if why:
        return None, why
    try:
        return yaml.safe_load(out), None
    except Exception as e:   # noqa: BLE001
        return None, f"台帳を読めない: {type(e).__name__}"


def render(cfg, mark, runner=None, clock=None):
    """止まっている間の report.md の本文。"""
    import hline_checks
    import hline_probe
    import hline_queue
    import hline_report
    import progress
    runner = runner or proc.run
    clock = time.time() if clock is None else clock
    ran = last_run(cfg) or "記録なし"
    head = [
        "## 書き出し",
        f"- 書いた時刻: {hline_probe.stamp(clock)}（python -m harness.hline_stopped report。H ラインの走行の外）",
        f"- {cfg['base']} の HEAD: {main_head(cfg, runner)}",
        f"- H ライン: 止まっている（理由: {mark['reason']}／止めた時刻: {mark['at']}）",
        f"- H ラインが最後に走った時刻: {ran}",
    ]
    ledger, why = main_ledger(cfg, runner)
    tree = progress.report(ledger) if ledger else f"## 進捗ツリー\n- {hline_probe.failure(why)}\n"
    st = hline_queue.load_state(cfg)
    parts = ["\n".join(head) + "\n", hline_report.goal_section(cfg, checks=hline_checks.cached_section, ref=cfg["base"]),
             hline_report.flow_section(cfg, ref=cfg["base"]), tree,
             hline_report.h_section(cfg, st, stopped_at=ran), hline_probe.safe_section(cfg, runner=runner, clock=clock),
             hline_checks.stopped_section(cfg)]
    return "\n".join(p.rstrip("\n") for p in parts) + "\n"


def write(cfg, runner=None, clock=None):
    mark = marker(cfg)
    if mark is None:
        raise ValueError(f"止めた印（{marker_path(cfg)}）がありません。走っている間の report.md は hline poll が書きます")
    text = render(cfg, mark, runner, clock)
    (Path(cfg["inbox"]) / "report.md").write_text(text, encoding="utf-8")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.hline_stopped", description="H ラインを止めた印と、止まっている間の report.md")
    sub = ap.add_subparsers(dest="action", required=True)
    sp = sub.add_parser("stop")
    sp.add_argument("reason")
    sub.add_parser("report")
    sub.add_parser("resume")
    ap.add_argument("--config", help="設定ファイル（無ければ config/hline.json）")
    args = ap.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    try:
        if args.action == "resume":
            resume(cfg)
            print("止めた印を外しました。report.md は次の走行が書きます")
            return 0
        if args.action == "stop":
            stop(cfg, args.reason)
        write(cfg)
        print(f"report.md を書き直しました: {Path(cfg['inbox']) / 'report.md'}")
        return 0
    except ValueError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
