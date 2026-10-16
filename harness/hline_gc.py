"""H ラインの作業ツリーの掃除：落ちた走行の残骸を次の走行の頭で片づける。消すのは worktrees 直下の 'hline-' のディレクトリだけで、
keep・ROOT とその上位・統合ブランチ・base・main・リモートにあるブランチは消さない。git push は呼ばない。sweep は例外を出さない。"""
import datetime
import time
from pathlib import Path

from hline_base import ROOT, write_json

import fileops  # noqa: E402
import proc  # noqa: E402

PREFIX = "hline-"
BRANCH_PREFIX = "hline/"


def _git(cfg, *args):
    return proc.run(["git", *args], ROOT, cfg["ttl_seconds"]["git"], f"git {args[0]} {args[1]}")


def _has(cfg, ref):
    return _git(cfg, "rev-parse", "--verify", "-q", ref)[0] == 0


def targets(cfg, keep=()):
    base = Path(cfg["worktrees"])
    if not base.is_dir():
        return []
    root = Path(ROOT).resolve()
    protected = {Path(k).resolve() for k in keep} | {root, *root.parents}
    base = base.resolve()
    found = [p for p in sorted(base.iterdir(), key=lambda q: q.name) if p.name.startswith(PREFIX) and p.is_dir()]
    return [p for p in found if p.resolve().parent == base and p.resolve() not in protected]


def branch_of(name):
    return BRANCH_PREFIX + name[len(PREFIX):] if name.startswith(PREFIX) else None


def removable_branch(cfg, branch):
    if not branch or not branch.startswith(BRANCH_PREFIX) or branch in (cfg["integration_branch"], cfg["base"], "main"):
        return False
    return not _has(cfg, f"refs/remotes/origin/{branch}")


def remove_worktree(cfg, wt, sleep=None):
    """(消せたか, 残った理由)。ロック系の失敗は fileops.DELAYS の間隔で再試行する。例外は出さない。"""
    wt, reason, sleep = Path(wt), None, sleep or time.sleep
    try:
        for i in range(len(fileops.DELAYS) + 1):
            code, out, err = _git(cfg, "worktree", "remove", "--force", str(wt))
            if not wt.exists():
                break
            reason = (err or out).strip()[-300:] or f"ディレクトリが残っています（終了コード {code}）"
            if i < len(fileops.DELAYS):
                sleep(fileops.DELAYS[i])
        if wt.exists():
            return False, reason
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    try:
        if removable_branch(cfg, branch_of(wt.name)):
            _git(cfg, "branch", "-D", branch_of(wt.name))
    except Exception:
        pass
    return True, None


def sweep(cfg, keep=(), sleep=None):
    """残骸を片づけ、記録を <out>/gc.json に書いて返す。ロックを保った状態で、最初の作業ツリーの前に呼ぶ。"""
    rec = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "removed": [], "branches": [], "kept": [], "pruned": False}
    wt = cfg.get("worktrees")
    try:
        for wt in targets(cfg, keep):
            try:
                branch = branch_of(wt.name)
                had = removable_branch(cfg, branch) and _has(cfg, f"refs/heads/{branch}")
                ok, reason = remove_worktree(cfg, wt, sleep)
                if not ok:
                    rec["kept"].append({"path": str(wt), "reason": reason})
                    continue
                rec["removed"].append(str(wt))
                if had and not _has(cfg, f"refs/heads/{branch}"):
                    rec["branches"].append(branch)
            except Exception as e:
                rec["kept"].append({"path": str(wt), "reason": f"{type(e).__name__}: {e}"})
    except Exception as e:
        rec["kept"].append({"path": str(wt), "reason": f"{type(e).__name__}: {e}"})
    try:
        rec["pruned"] = _git(cfg, "worktree", "prune")[0] == 0
    except Exception:
        pass
    try:
        Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
        write_json(Path(cfg["out"]) / "gc.json", rec)
    except Exception:
        pass
    return rec
