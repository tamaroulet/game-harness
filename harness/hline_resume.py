"""H ライン（harness/hline.py）の再開：未収束の What の最後の試行の変更と失敗の出力を生ログの置き場に残し、
同じ名前の What を置き直したとき、その最初の試行を残した変更を載せた作業ツリーから始める。
harness.hline・hline_task を import しない（循環を作らない）。依存は host（harness.hline 自身）の属性として読む。"""
import shutil
from pathlib import Path

from hline_base import must, slug

PATCH, FAILURE, PREVIOUS = "last.patch", "last-failure.txt", "previous-failure.txt"   # 前の 2 つは試行の生ログの置き場に、最後は再開の渡し口


def resume_dir(cfg, name):
    return Path(cfg["out"]) / "resume" / slug(name)


def keep(cfg, wt, outdir, failure):
    """最後の試行の作業ツリーの変更（新しいファイルを含む）と失敗の出力を outdir に書く。試行のたびに上書きするので、最後の試行のものが残る。
    残せなくても試行の扱いは変えない（例外は出さない）。"""
    try:
        Path(outdir, FAILURE).write_text(failure or "", encoding="utf-8")
        if (Path(wt) / ".git").exists():
            ttl = cfg["ttl_seconds"]["git"]
            must(["git", "add", "-A", "-N"], wt, ttl, "git add -N")
            Path(outdir, PATCH).write_bytes(must(["git", "diff", "HEAD", "--binary"], wt, ttl, "git diff").encode("utf-8"))
    except Exception:   # noqa: BLE001
        pass


def forget_attempt(outdir):
    for n in (PATCH, FAILURE, PREVIOUS):
        Path(outdir, n).unlink(missing_ok=True)


def clear(cfg, name):
    shutil.rmtree(resume_dir(cfg, name), ignore_errors=True)


def store(cfg, name, outdir):
    """収束しなかった What の、最後の試行の変更と失敗の出力を、What の名前の置き場に移す（前のものは捨てる）。"""
    clear(cfg, name)
    if not Path(outdir, FAILURE).exists():
        return
    dest = resume_dir(cfg, name)
    dest.mkdir(parents=True, exist_ok=True)
    for n in (PATCH, FAILURE):
        if Path(outdir, n).exists():
            shutil.copyfile(Path(outdir, n), dest / n)


def material(cfg, name):
    """What の名前の置き場に、再開の材料（差分と失敗の出力）が残っているか。{"kept": bool, "lines": 差分の行数}。"""
    src = resume_dir(cfg, name)
    patch = src / PATCH
    if not (src / FAILURE).exists() or not patch.exists() or patch.stat().st_size == 0:
        return {"kept": False, "lines": 0}
    return {"kept": True, "lines": len(patch.read_text(encoding="utf-8", errors="replace").splitlines())}


def begin(host, cfg, name, wt, outdir):
    """置き直した What の最初の試行の前に、残した変更を作業ツリーに載せる。試行の記録に残す辞書
    {"applied": bool, "reason": 当たらなかった理由}。前の失敗の出力は outdir の PREVIOUS に置く（run_task が読む）。"""
    src = resume_dir(cfg, name)
    patch, failure = src / PATCH, src / FAILURE
    if not failure.exists():
        return {"applied": False, "reason": "残した変更が無い"}
    if not patch.exists() or patch.stat().st_size == 0:
        return {"applied": False, "reason": "残した変更が無い（前の最後の試行は何も変えていない）"}
    ok, why = host.apply_patch(cfg, wt, patch)
    if not ok:
        return {"applied": False, "reason": f"残した変更が今の統合ブランチに当たらない: {why}"}
    Path(outdir, PREVIOUS).write_text(failure.read_text(encoding="utf-8"), encoding="utf-8")
    return {"applied": True, "reason": None}


def previous(outdir):
    """実装役の最初の試行に渡す、前回の失敗の出力。無ければ None。"""
    p = Path(outdir, PREVIOUS)
    if not p.exists():
        return None
    return ("前回（置き直す前）の最後の試行の失敗の出力です。作業ツリーには、そのときの変更を載せてあります。"
            f"続きから直してください。\n\n{p.read_text(encoding='utf-8')}")
