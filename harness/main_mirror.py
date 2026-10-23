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
import proc  # noqa: E402
from hline_base import ROOT, load_config  # noqa: E402

RECORD = ".mirror.json"


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


def sync(cfg, root=ROOT, runner=None, clock=None):
    """写しを origin/main の HEAD に合わせ、記録（{"sha", "at", "base"}）を返す。合わせられなければ MirrorError。"""
    mirror = Path(cfg["mirror"])
    remote, branch = remote_and_branch(cfg)
    if not (mirror / ".git").exists():
        if mirror.exists() and any(mirror.iterdir()):
            raise MirrorError(f"写しの場所 {mirror} に、Git でないファイルがあります（消さずに止めます。中を確かめてから空にしてください）")
        url = _git(cfg, runner, ["-C", str(root), "remote", "get-url", remote]).strip()
        _git(cfg, runner, ["clone", "-q", "--origin", remote, url, str(mirror)])
    _git(cfg, runner, ["-C", str(mirror), "fetch", "-q", "--prune", remote])
    _git(cfg, runner, ["-C", str(mirror), "reset", "-q", "--hard", cfg["base"]])
    _git(cfg, runner, ["-C", str(mirror), "clean", "-q", "-f", "-d", "-x"])
    sha = _git(cfg, runner, ["-C", str(mirror), "rev-parse", "HEAD"]).strip()
    rec = {"sha": sha, "at": hline_probe.stamp(time.time() if clock is None else clock), "base": f"{remote}/{branch}"}
    (mirror / RECORD).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def record(cfg):
    try:
        return json.loads((Path(cfg["mirror"]) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
