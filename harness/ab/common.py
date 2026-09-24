"""A/B 実験の共通部品：マニフェストの読み込みと検査、置き場所、git の小さな操作。"""
import hashlib
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import project  # noqa: E402
from proc import run  # noqa: E402

ROOT = project.ROOT
EXP_DIR = ROOT / "experiments" / "b4_ab"
WT_ROOT = Path(r"C:\src\.local\wt\ab")
OUT_ROOT = Path(r"C:\src\.local\out\ab")
CONDITIONS = ("A", "B")
GIT_TTL = 120


class ABError(Exception):
    pass


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path):
    """マニフェストを読み、凍結したファイルがすべて sha256 と一致することを確かめる。"""
    path = Path(path)
    base = path.parent
    m = json.loads(path.read_text(encoding="utf-8"))
    problems = []

    def check(rel, digest):
        if not (base / rel).exists() or sha256_file(base / rel) != digest:
            problems.append(rel)

    check(m["common_requirement"], m["common_requirement_sha256"])
    for key in ("initial", "retry"):
        check(m["templates"][key], m["templates"][f"{key}_sha256"])
    for t in m["tasks"]:
        check(t["unit"], t["unit_sha256"])
        for name, digest in t["tests_sha256"].items():
            check(f"{t['tests']}{name}", digest)
    if problems:
        raise ABError("凍結したファイルが sha256 と一致しません（実験の入力が変わっている）: " + ", ".join(problems))
    m["_base"] = str(base)
    return m


def tasks_through(m, through=None):
    """マニフェストの先頭から through までのタスク。through が無ければ全部。"""
    ids = [t["id"] for t in m["tasks"]]
    if through is None:
        return list(m["tasks"])
    if through not in ids:
        raise ABError(f"タスク {through!r} はマニフェストにありません: {ids}")
    return m["tasks"][:ids.index(through) + 1]


def unit_of(m, task):
    return json.loads((Path(m["_base"]) / task["unit"]).read_text(encoding="utf-8"))


def paths(run_id, condition, wt_root=WT_ROOT, out_root=OUT_ROOT):
    if condition not in CONDITIONS:
        raise ABError(f"条件は {CONDITIONS} のどちらかです: {condition!r}")
    return {"wt": Path(wt_root) / f"{run_id}-{condition}",
            "sandbox": Path(wt_root) / f"{run_id}-{condition}-sandbox",
            "out": Path(out_root) / run_id / condition,
            "branch": f"ab/{run_id}/{condition}"}


def git(args, cwd, label, check=True):
    rc, out, err = run(["git"] + args, cwd, GIT_TTL, label)
    if check and rc != 0:
        raise ABError(f"{label} に失敗しました: {(err or out)[:300]}")
    return out


def head(cwd):
    return git(["rev-parse", "HEAD"], cwd, "git rev-parse").strip()


def commit_all(cwd, message):
    """作業ツリーをすべて commit する。変更が無ければ何もしない。commit の SHA を返す。"""
    git(["add", "-A"], cwd, "git add")
    if git(["status", "--porcelain"], cwd, "git status").strip():
        git(["commit", "-q", "-m", message], cwd, "git commit")
    return head(cwd)


def numstat(cwd, start, end, under):
    """start..end の、under の下の追加行・削除行。"""
    out = git(["diff", "--numstat", f"{start}..{end}", "--", under], cwd, "git diff --numstat")
    added = deleted = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            added += 0 if parts[0] == "-" else int(parts[0])
            deleted += 0 if parts[1] == "-" else int(parts[1])
    return added, deleted
