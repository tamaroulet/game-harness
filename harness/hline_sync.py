"""H ライン（harness/hline.py）の統合ブランチの同期：作業ツリーを作る前に、統合ブランチが base（origin/main）に遅れていたら追いつかせる。

人間の PR が main に入った後も統合ブランチが古い先端のまま残ると、後の What は main の変更を含まない土台で作られる。
- 統合ブランチに main に無いコミットが無い：base の先端へ進める（早送りの push）
- main に無いコミットがある：一時の作業ツリーで base をマージし、そのコミットを push する
- マージが衝突する：push せず SyncConflict（衝突したパスつき）。作業ツリーは作られない
- base が既に統合ブランチの祖先なら何もしない
どの場合も force push はしない（push は常に通常の push。早送りにならなければリモートが拒む）。
"""
import uuid
from pathlib import Path

from hline_base import Fatal, Infra, SyncConflict, must

import fileops  # noqa: E402
import infra_retry  # noqa: E402
import proc  # noqa: E402


def _git(cfg, root, args, label):
    return proc.run(["git", *args], root, cfg["ttl_seconds"]["git"], label)


def _is_ancestor(cfg, root, older, newer):
    return _git(cfg, root, ["merge-base", "--is-ancestor", older, newer], "git merge-base")[0] == 0


def _push(cfg, root, source, label):
    code, out, err = _git(cfg, root, ["push", "-q", "origin", f"{source}:refs/heads/{cfg['integration_branch']}"], label)
    if code != 0:
        if reason := infra_retry.fatal_push_reason(code, out + err):
            raise Fatal(reason)
        raise Infra(f"{label} が失敗しました（終了コード {code}）: {(err or out)[-800:]}")


def _remove(cfg, root, wt):
    if _git(cfg, root, ["worktree", "remove", str(wt)], "git worktree remove")[0] != 0:
        fileops.rmtree(wt)
        _git(cfg, root, ["worktree", "prune"], "git worktree prune")


def merge_base_into_integration(cfg, root, tip, base):
    wt = Path(cfg["worktrees"]) / f"hline-sync-{uuid.uuid4().hex[:8]}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    must(["git", "worktree", "add", "-q", "--detach", str(wt), tip], root, cfg["ttl_seconds"]["git"], "git worktree add")
    try:
        message = f"merge: {base} を {cfg['integration_branch']} に取り込む\n\n{cfg['commit_trailer']}\n"
        code, out, err = _git(cfg, wt, ["merge", "--no-edit", "-m", message, base], "git merge")
        if code != 0:
            paths = _git(cfg, wt, ["diff", "--name-only", "--diff-filter=U"], "git diff")[1].split()
            _git(cfg, wt, ["merge", "--abort"], "git merge --abort")
            if not paths:
                raise Infra(f"git merge が失敗しました（終了コード {code}）: {(err or out)[-800:]}")
            raise SyncConflict(paths)
        _push(cfg, wt, "HEAD", "git push（merge）")
    finally:
        _remove(cfg, root, wt)


def sync(cfg, root):
    """統合ブランチ（origin）が有り、base がその祖先でないときに追いつかせる。fetch は呼び出し側が済ませておく。"""
    tip, base = f"origin/{cfg['integration_branch']}", cfg["base"]
    if _git(cfg, root, ["rev-parse", "--verify", "-q", f"refs/remotes/{tip}"], "git rev-parse")[0] != 0:
        return
    if _is_ancestor(cfg, root, base, tip):
        return
    if _is_ancestor(cfg, root, tip, base):   # 統合ブランチに main に無いコミットが無い
        _push(cfg, root, base, "git push（早送り）")
        return
    merge_base_into_integration(cfg, root, tip, base)
