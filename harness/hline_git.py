"""H ライン（harness/hline.py）の git・gh：統合ブランチの作業ツリー、積む操作、統合 PR。

Gate 1 を通った変更は統合ブランチ（設定の integration_branch）の先端に積む。作業ツリーも先端から作るので、
後のタスクは前のタスクの変更を含む。タスクごとの PR は作らず、統合 PR を 1 本だけ出す。
"""
import json
import os
import sys
import uuid
from pathlib import Path

from hline_base import ROOT, Fatal, Infra, must

import infra_retry  # noqa: E402
import proc  # noqa: E402
import progress  # noqa: E402

TRAILER = "H-Line-Item"


def remote_ref(cfg):
    return f"origin/{cfg['integration_branch']}"


def fetch(cfg):
    must(["git", "fetch", "-q", "--prune", "origin"], ROOT, cfg["ttl_seconds"]["git"], "git fetch")


def integration_exists(cfg):
    code, _, _ = proc.run(["git", "rev-parse", "--verify", "-q", f"refs/remotes/{remote_ref(cfg)}"], ROOT,
                          cfg["ttl_seconds"]["git"], "git rev-parse")
    return code == 0


def new_worktree(cfg, tid):
    """統合ブランチの先端から UUID の新しい作業ツリーを作る。統合ブランチがまだ無ければ base（main）から作る。"""
    t = cfg["ttl_seconds"]["git"]
    fetch(cfg)
    start = remote_ref(cfg) if integration_exists(cfg) else cfg["base"]
    for _ in range(5):   # 前の走行の残骸（同じ名前のディレクトリ・ブランチ）と重ならない名前を選ぶ
        branch = f"hline/{tid}-{uuid.uuid4().hex[:8]}"
        wt = Path(cfg["worktrees"]) / branch.replace("/", "-")
        if not wt.exists() and proc.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"], ROOT, t,
                                        "git rev-parse")[0] != 0:
            break
    else:
        raise Infra(f"作業ツリーの名前が 5 回とも既存と重なりました: {tid}")
    must(["git", "worktree", "add", "-q", "-b", branch, str(wt), start], ROOT, t, "git worktree add")
    implementer_room(wt, t)
    return wt, branch


def implementer_room(wt, ttl):
    """作業ツリーの .claude/settings.json から、作業ツリーを読ませない総監督の壁を外す（実測：実装役が自分の作業ツリーを
    読めず 6 回空振りした）。git には変更として見せない（skip-worktree。Gate 1 にも当たらず、コミットにも入らない）。"""
    p = Path(wt) / ".claude" / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    perms = s.setdefault("permissions", {})
    perms["deny"] = [r for r in perms.get("deny", []) if ".local/wt" not in r] + ["Read(//c/src/.local/out/**)"]
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    must(["git", "update-index", "--skip-worktree", ".claude/settings.json"], wt, ttl, "git update-index")


def changed_paths(wt, cfg):
    out = must(["git", "status", "--porcelain", "-uall"], wt, cfg["ttl_seconds"]["git"], "git status")
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


# ============================================================ 統合ブランチに積む

def integrate(cfg, wt, name, title, task):
    """Gate 1 を通った変更を統合ブランチに積む。タスクを宣言した What は、harness.progress で完了として記録してから積む。"""
    t = cfg["ttl_seconds"]
    if task:
        code, out, err = proc.run([sys.executable, "-m", "harness.progress", "complete", task], wt, t["gate"],
                                  "harness.progress complete", env={**os.environ, progress.ORDER_FREE_ENV: "1"})
        if code != 0:
            raise Fatal(f"harness.progress complete {task} が終了コード {code} で、進捗の記録が拒まれました。再試行しません: "
                        f"{(out + err)[-800:]}")
    must(["git", "add", "-A"], wt, t["git"], "git add")
    must(["git", "commit", "-q", "-m", f"feat(hline): {title}\n\n{TRAILER}: {name}\n{cfg['commit_trailer']}\n"],
         wt, t["git"], "git commit")
    code, out, err = proc.run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{cfg['integration_branch']}"], wt,
                              t["git"], "git push")
    if code != 0:
        if reason := infra_retry.fatal_push_reason(code, out + err):
            raise Fatal(reason)
        raise Infra(f"git push が失敗しました（終了コード {code}）: {(err or out)[-800:]}")


def integrated(cfg, name):
    """統合ブランチ（main に無いコミット）に、この What を積んだコミットがあるか。強制終了からの復旧で使う。"""
    if not integration_exists(cfg):
        return False
    out = must(["git", "log", f"{cfg['base']}..{remote_ref(cfg)}", f"--format=%(trailers:key={TRAILER},valueonly)"],
               ROOT, cfg["ttl_seconds"]["git"], "git log")
    return name in out.split()


def ahead(cfg):
    """統合ブランチにある、main に無いコミットの数。"""
    if not integration_exists(cfg):
        return 0
    return int(must(["git", "rev-list", "--count", f"{cfg['base']}..{remote_ref(cfg)}"], ROOT,
                    cfg["ttl_seconds"]["git"], "git rev-list").strip())


# ============================================================ 統合 PR

def gh(cfg, args, label):
    return must(proc.resolve_cli("gh") + args, ROOT, cfg["ttl_seconds"]["gh"], label)


def open_pr(cfg):
    """統合ブランチから main への、開いている PR の URL。無ければ None。"""
    out = gh(cfg, ["pr", "list", "--head", cfg["integration_branch"], "--base", "main", "--state", "open",
                   "--json", "url", "--jq", ".[].url"], "gh pr list").split()
    return out[0] if out else None


def create_pr(cfg, title, body):
    """統合 PR を作る。本文はファイルで渡す（コマンドラインの長さの上限を避ける）。URL を返す。"""
    path = Path(cfg["out"]) / "integration-pr.md"
    path.write_text(body, encoding="utf-8")
    out = gh(cfg, ["pr", "create", "--base", "main", "--head", cfg["integration_branch"], "--title", title,
                   "--body-file", str(path)], "gh pr create")
    return out.strip().splitlines()[-1]


def pr_state(cfg, url):
    """OPEN・CLOSED・MERGED。"""
    return gh(cfg, ["pr", "view", url, "--json", "state", "--jq", ".state"], "gh pr view").strip()


def drop_merged_branch(cfg):
    """マージされた統合ブランチを消す（残すと、スカッシュのマージの後に main に無いコミットとして見え、次の作業の土台になる）。"""
    fetch(cfg)
    if integration_exists(cfg):
        must(["git", "push", "-q", "origin", "--delete", cfg["integration_branch"]], ROOT,
             cfg["ttl_seconds"]["git"], "git push --delete")
