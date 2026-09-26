"""A/B 実験の共通部品：マニフェストの読み込みと検査、置き場所、git の小さな操作。"""
import hashlib
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import project  # noqa: E402
import unit_schema  # noqa: E402
from proc import run  # noqa: E402

ROOT = project.ROOT
EXP_DIR = ROOT / "experiments" / "b4_ab"
WT_ROOT = Path(r"C:\src\.local\wt\ab")
OUT_ROOT = Path(r"C:\src\.local\out\ab")
CONDITIONS = ("A", "B")
GIT_TTL = 120


class ABError(Exception):
    pass


class ReplicateStop(ABError):
    """その繰り返し（1 つの run-id）を止める。一時的な失敗が続いたとき（docs/design/v2r_protocol.md §10）。

    run-all は、この繰り返しの残りの条件を飛ばして、次の繰り返しへ進む。
    """


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

    if m.get("kind") == "v2":
        # v2：生成物は置くときに作り直して generated_sha256 と照合する（v2_files）
        check(m["contract"], m["contract_sha256"])
        check(m["properties"], m["properties_sha256"])
    else:
        check(m["common_requirement"], m["common_requirement_sha256"])
    for key in ("initial", "retry"):
        check(m["templates"][key], m["templates"][f"{key}_sha256"])
    for t in m["tasks"]:
        check(t["unit"], t["unit_sha256"])
        for name, digest in t.get("tests_sha256", {}).items():
            check(f"{t['tests']}{name}", digest)
    if problems:
        raise ABError("凍結したファイルが sha256 と一致しません（実験の入力が変わっている）: " + ", ".join(problems))
    m["_base"] = str(base)
    return m


def is_v2(m):
    return m.get("kind") == "v2"


def v2_files(m, upto, project_id="falling-blocks"):
    """v2：タスク 1〜upto のときに置く生成物 {リポジトリからの相対パス: 本文}。

    契約と性質の宣言から作り直し、マニフェストの generated_sha256 と 1 つずつ照合する（凍結の検査）。
    性質テストのクラスは T1〜T<upto> のものだけ（まだ実装していないタスクの性質で受入を汚さない）。
    """
    cache = m.setdefault("_v2_files", {})
    if upto in cache:
        return cache[upto]
    from ab import v2prep  # v2prep は common を読むので、ここで読む
    proj = project.load(project_id)
    cfg = project.config("unit_schema")
    read = unit_schema.git_reader(proj["repo_dir"], m["base_commit"], GIT_TTL)
    base = Path(m["_base"])
    files = v2prep.generated(json.loads((base / m["contract"]).read_text(encoding="utf-8")),
                             json.loads((base / m["properties"]).read_text(encoding="utf-8")),
                             read(cfg["spec_path"]), read(cfg["gdd_path"]), proj,
                             tasks={t["id"] for t in m["tasks"][:upto]})
    bad = [rel for rel, text in files.items()
           if hashlib.sha256(text.encode("utf-8")).hexdigest() != m["generated_sha256"].get(rel)]
    if bad:
        raise ABError("生成物が凍結した sha256 と一致しません（生成器か入力が変わっている）: " + ", ".join(bad))
    cache[upto] = files
    return files


def hidden_seeds(run_id, task_id, count):
    """測定器が使う非公開シード。走行とタスクで決まる（測り直せる）。実装役には渡らない。"""
    root, seeds, i = hashlib.sha256(f"measure:{run_id}:{task_id}".encode("utf-8")).digest(), [], 0
    while len(seeds) < count:
        d = hashlib.sha256(root + i.to_bytes(4, "big")).digest()
        seeds.append((int.from_bytes(d[:4], "big") & 0x7FFFFFFF) or 1)
        i += 1
    return seeds


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
