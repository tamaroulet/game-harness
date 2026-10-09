"""H ライン（harness/hline.py）の編集境界（Gate 1）と、パスの照合・進捗のタスクの検証コマンド・差分の行数。

**なぜ要るか**: 編集境界の外への変更は機械で落とす（Gate 1）。
"""
import fnmatch
from pathlib import Path

from hline_base import must  # isort: skip（harness/ を import の道に足す）
from size_limits import added_over_limit  # noqa: E402

import progress  # noqa: E402


def verification_of(wt, task_id):
    """作業ツリーの docs/progress.yaml にある、タスクの検証 {command, expected_exit_code}。無ければ None。"""
    try:
        return progress.task(progress.load(Path(wt) / progress.REL_PATH), task_id).get("verification")
    except (progress.ProgressError, OSError):
        return None


# ============================================================ 編集境界（Gate 1）

def matches(path, pattern):
    path, pattern = path.replace("\\", "/").removeprefix("./"), pattern.replace("\\", "/").removeprefix("./")
    return path.startswith(pattern) if pattern.endswith("/") else (
        fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern + "/"))


def diff_counts(cfg, wt):
    """作業ツリーの HEAD からの差分：追加・削除の行数（新しいファイルを含む。バイナリは数えない）と、丸ごと消えたファイル。"""
    t = cfg["ttl_seconds"]["git"]
    must(["git", "add", "-A", "-N"], wt, t, "git add -N")
    total, gone = [0, 0], []
    for line in must(["git", "diff", "HEAD", "--numstat", "--summary"], wt, t, "git diff").splitlines():
        if line.startswith(" delete mode "):
            gone.append(line.split(" ", 4)[4].strip('"'))
        for i, n in enumerate((line.split("\t") + ["", ""])[:2]):
            total[i] += int(n) if n.isdigit() else 0
    return {"added": total[0], "deleted": total[1], "deleted_files": tuple(gone)}


def boundary_problems(spec, paths, counts):
    """TaskSpec の編集境界の違反（実装役に返す理由）。変えてはならないファイルが優先する。上限は追加した行だけを数え、消してよいのは deletable_files だけ。"""
    b, out = spec["edit_boundary"], []
    gone, deletable = counts["deleted_files"], b.get("deletable_files", [])
    forbidden = [p for p in paths if any(matches(p, q) for q in b["forbidden_files"])]
    outside = [p for p in paths if p not in forbidden
               and not any(matches(p, q) for q in b["allowed_files"] + (deletable if p in gone else []))]
    stray = [p for p in gone if p not in forbidden + outside and not any(matches(p, q) for q in deletable)]
    if forbidden:
        out.append(f"変えてはならないファイルを変えています: {', '.join(forbidden)}。元に戻してください。")
    if outside:
        out.append(f"変えてよいファイルの外を変えています: {', '.join(outside)}（許可: {', '.join(b['allowed_files'])}）。"
                   "元に戻してください。")
    if stray:
        out.append(f"消してはならないファイルを消しています: {', '.join(stray)}（消してよい: {', '.join(deletable) or 'なし'}）。")
    if (why := added_over_limit(counts["added"], b["max_diff_lines"])):
        out.append(why)
    return out
