"""総監督が読む main の写し（W-150）。

    python -m harness.main_mirror sync     写し（config/hline.json の mirror）を origin/main の HEAD に合わせる

写しは人の作業コピーと別の clone で、読むだけのもの。合わせるたびに、写しの中の変更と未追跡のファイルを捨てる。
写しの根の .mirror.json に、合わせた SHA と時刻を書く。report.md の冒頭は、これと origin/main の HEAD（ls-remote）を比べる。
人の作業コピー（root）からは origin の URL を読むだけで、何も書かない。git はどれも -C で場所を名指しする。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_probe  # noqa: E402
import hline_git  # noqa: E402
import proc  # noqa: E402
from hline_base import ROOT, load_config  # noqa: E402

import shutil

RECORD = ".mirror.json"
RECORD_BRANCH = ".branch.json"


def remote_and_branch(cfg):
    remote, _, branch = cfg["base"].partition("/")
    return remote, branch


def _git(cfg, runner, args):
    out, why = hline_probe.git(cfg, args, runner or proc.run)
    if why:
        raise MirrorError(why)
    return out


class MirrorError(Exception):
    pass


def check_no_overlap(target_path, root_path, label="写しの場所"):
    target_res = Path(target_path).resolve()
    root_res = Path(root_path).resolve()
    if target_res == root_res or root_res in target_res.parents or target_res in root_res.parents:
        raise MirrorError(f"{label} {target_path} が作業コピー {root_path} と重なっています（作業ツリーの保護のため停止します）")


def get_open_prs(cfg, runner=None):
    """開いている PR の一覧（[{"number": 291, "headRefName": "...", "headRefOid": "..."}]）を返す。"""
    return hline_git.list_open_prs(cfg)


def sync_branches(cfg, root=ROOT, mirror=None, remote=None, runner=None, clock=None, open_prs=None):
    """開いている PR の枝の写しを取り出し、閉じた PR の写しを消す。"""
    branches_path = cfg.get("branches")
    if not branches_path:
        return
    branches_dir = Path(branches_path)
    check_no_overlap(branches_dir, root, "枝の写しの場所")
    branches_dir.mkdir(parents=True, exist_ok=True)

    if open_prs is None:
        prs = get_open_prs(cfg, runner)
    elif callable(open_prs):
        prs = open_prs()
    else:
        prs = open_prs

    mirror = Path(cfg["mirror"]) if mirror is None else mirror
    now_stamp = hline_probe.stamp(time.time() if clock is None else clock)

    active_nums = set()
    for pr in prs:
        num = str(pr["number"])
        active_nums.add(num)
        branch = pr.get("headRefName", f"pr-{num}")
        sha = pr.get("headRefOid") or pr.get("sha")
        target = branches_dir / num

        if not (target / ".git").exists():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            _git(cfg, runner, ["clone", "-q", "--shared", str(mirror), str(target)])

        _git(cfg, runner, ["-C", str(target), "reset", "-q", "--hard", sha])
        _git(cfg, runner, ["-C", str(target), "clean", "-q", "-f", "-d", "-x"])

        rec = {
            "number": int(num),
            "branch": branch,
            "sha": sha,
            "at": now_stamp,
        }
        (target / RECORD_BRANCH).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for item in branches_dir.iterdir():
        if item.is_dir() and item.name not in active_nums:
            shutil.rmtree(item, ignore_errors=True)


def sync(cfg, root=ROOT, runner=None, clock=None, open_prs=None):
    """写しを origin/main の HEAD に合わせ、記録（{"sha", "at", "base"}）を返す。合わせられなければ MirrorError。"""
    mirror = Path(cfg["mirror"])
    check_no_overlap(mirror, root, "写しの場所")
    remote, branch = remote_and_branch(cfg)
    if not (mirror / ".git").exists():
        if mirror.exists() and any(mirror.iterdir()):
            raise MirrorError(f"写しの場所 {mirror} に、Git でないファイルがあります（消さずに止めます。中を確かめてから空にしてください）")
        url = _git(cfg, runner, ["-C", str(root), "remote", "get-url", remote]).strip()
        _git(cfg, runner, ["clone", "-q", "--origin", remote, url, str(mirror)])
    _git(cfg, runner, ["-C", str(mirror), "fetch", "-q", "--prune", remote, f"+refs/heads/*:refs/remotes/{remote}/*"])
    _git(cfg, runner, ["-C", str(mirror), "reset", "-q", "--hard", cfg["base"]])
    _git(cfg, runner, ["-C", str(mirror), "clean", "-q", "-f", "-d", "-x"])
    sha = _git(cfg, runner, ["-C", str(mirror), "rev-parse", "HEAD"]).strip()
    rec = {"sha": sha, "at": hline_probe.stamp(time.time() if clock is None else clock), "base": f"{remote}/{branch}"}
    (mirror / RECORD).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sync_branches(cfg, root=root, mirror=mirror, remote=remote, runner=runner, clock=clock, open_prs=open_prs)
    return rec


def record(cfg):
    """写しの記録。無い・壊れている（sha と at の文字列を持たない）なら None（report.md は「なし」として書ける）。"""
    try:
        rec = json.loads((Path(cfg["mirror"]) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(rec, dict) and all(isinstance(rec.get(k), str) and rec[k] for k in ("sha", "at"))
    return rec if ok else None


def base_head(cfg, runner=None):
    """ハーネスの根の origin/main の SHA。読めなければ None。

    report.md の 2 つの書き手は、どちらも書く前に origin を取り込む（止まっている間は main_head、走っている間は走行の
    取り込み）ので、ここは取り込んだ後の値になる。取り込まずに比べると、写しが新しく見えてしまう（Argon の指摘）。"""
    out, why = hline_probe.git(cfg, ["rev-parse", cfg["base"]], runner or proc.run)
    return None if why or not out.strip() else out.strip()


def line(cfg, head, failed=None):
    """report.md の冒頭の 1 行。head は origin/main の HEAD（None なら比べられない）。failed は合わせるのに失敗した理由。"""
    rec = record(cfg)
    note = f"（合わせるのに失敗: {failed[:120]}）" if failed else ""
    if rec is None:
        return f"- main の写し: なし（写しが古い：python -m harness.main_mirror sync を走らせる／場所: {cfg['mirror']}）{note}"
    text = f"- main の写し: {rec['sha']}（合わせた時刻: {rec['at']}／場所: {cfg['mirror']}）"
    if head is None:
        return text + "（origin/main の HEAD を読めず、古いかは分からない）" + note
    if head != rec["sha"]:
        return text + f"（写しが古い：origin/main は {head}）" + note
    return text + note


def section(cfg, runner=None):
    """走っている間の report.md の先頭の節（走っている間は合わせない。合わせる時機は W-156 で決める）。写しの設定が無ければ空。"""
    if not cfg.get("mirror"):
        return ""
    return "## main の写し\n" + line(cfg, base_head(cfg, runner)) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.main_mirror", description="総監督が読む main の写し")
    ap.add_argument("action", choices=["sync"])
    ap.parse_args(argv)
    cfg = load_config()
    try:
        rec = sync(cfg)
    except MirrorError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT
    print(f"写しを合わせました: {cfg['mirror']}（{rec['sha']}）")
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
