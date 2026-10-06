"""統合 PR を出す前の差分の衛生検査：生ログ・一時ファイル・リポジトリの外の置き場の写し・大きすぎるファイルが混ざっていないか。
パターンと大きさの上限は config/hline.json の pr_hygiene（既定値はコードに持たない）。git は読み取りだけ。
"""
import fnmatch
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_git  # noqa: E402


def matches(path, patterns):
    """path に当たった最初のパターン。当たらなければ None。末尾 "/" はディレクトリの名前、"/" を含むものは全体、含まないものはファイル名。"""
    parts = path.split("/")
    for p in patterns:
        if p.endswith("/"):
            hit = p[:-1] in parts[:-1]
        elif "/" in p:
            hit = fnmatch.fnmatchcase(path, p)
        else:
            hit = fnmatch.fnmatchcase(parts[-1], p)
        if hit:
            return p
    return None


def room_patterns(cfg):
    """リポジトリの外の置き場（受信箱・作業ツリー・生ログ）の末尾のディレクトリ名を、ディレクトリのパターンにしたもの。"""
    names = (re.split(r"[\\/]+", str(cfg[k]).rstrip("\\/"))[-1] for k in ("inbox", "worktrees", "out"))
    return [n + "/" for n in names if n]


def problems(files, cfg):
    """違反した 1 ファイルにつき 1 つの文字列（files の並び）。違反が無ければ空。"""
    hyg = cfg["pr_hygiene"]
    patterns = list(dict.fromkeys(list(hyg["forbidden_globs"]) + room_patterns(cfg)))
    limit = hyg["max_file_bytes"]
    out = []
    for f in files:
        if hit := matches(f["path"], patterns):
            out.append(f"{f['path']}: 禁止のパターンに当たった（{hit}）")
        elif f["size"] > limit:
            out.append(f"{f['path']}: 大きさ {f['size']} バイトが上限 {limit} バイトを超えた")
    return out


def diff_files(cfg):
    """base と統合ブランチの間の差分のうち、消されていないファイルの [{"path", "size"}]（size は統合ブランチ側の blob）。"""
    if not hline_git.integration_exists(cfg):
        return []
    ref, ttl, root = hline_git.remote_ref(cfg), cfg["ttl_seconds"]["git"], hline_git.ROOT
    names = hline_git.must(["git", "diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", f"{cfg['base']}...{ref}"],
                           root, ttl, "git diff").split("\0")
    paths = [n for n in names if n]
    if not paths:
        return []
    tree = hline_git.must(["git", "ls-tree", "-r", "-l", "-z", ref], root, ttl, "git ls-tree")
    sizes = {}
    for entry in tree.split("\0"):
        meta, _, path = entry.partition("\t")
        if path:
            size = meta.split()[-1]
            sizes[path] = int(size) if size.isdigit() else 0
    return [{"path": p, "size": sizes.get(p, 0)} for p in paths]
