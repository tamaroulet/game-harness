"""計画の受け入れの検査（W-76）：計画役の出力（ledger.yaml と P-NN.md）に、タスクの数・依存・粒度・オラクルの規則を掛ける。

    python -m harness.plan_check <dir>

破った規則は <dir>/planner.json の鍵 plan_check に [{"rule": 規則の名前, "task": タスクの ID か null}, …] で書く
（ほかの鍵は消さない。合格なら空の列）。すべて満たせば終了コード 0、どれかを破れば 1。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

LEDGER = "ledger.yaml"
RESULT = "planner.json"
UNREADABLE = "ledger.yaml を読めない"
TASKS = (2, 8)
FILES = (1, 5)
ACCEPTANCE = (1, 4)
MAX_DEPTH = 6


def _list(task, key):
    value = task.get(key)
    return value if isinstance(value, list) else []


def _norm(path):
    return str(path).replace("\\", "/")


def named_tests(command):
    """`python -m unittest <名前>…` の各名前を tests/x.py の形に直した列。その形でなければ空。"""
    words = str(command).split()
    if words[:3] != ["python", "-m", "unittest"]:
        return []
    mods = []
    for name in (w for w in words[3:] if not w.startswith("-")):
        parts = name.split(".")
        for i, part in enumerate(parts):
            if part.startswith("test_"):
                parts = parts[:i + 1]
                break
        mods.append("/".join(parts) + ".py")
    return mods


def load(inbox):
    try:
        tasks = yaml.safe_load((Path(inbox) / LEDGER).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    if not isinstance(tasks, list) or not all(isinstance(t, dict) and isinstance(t.get("id"), str) for t in tasks):
        return None
    return tasks


def what_ids(inbox):
    return {p.stem for p in Path(inbox).iterdir() if p.is_file() and p.name.startswith("P-") and p.suffix == ".md"}


def reach(graph, start):
    """start から depends_on をたどって届くタスクの集まり（start を含まない）。"""
    seen, todo = set(), list(graph[start])
    while todo:
        t = todo.pop()
        if t not in seen:
            seen.add(t)
            todo += graph[t]
    return seen


def graph_problems(graph):
    """(規則, タスク) の列：循環・根から届かない・深さ。"""
    found = []
    cyclic = [t for t in graph if t in reach(graph, t)]
    found += [("依存の循環", t) for t in cyclic]
    roots = {t for t, deps in graph.items() if not deps}
    found += [("根から届かない", t) for t in graph if t not in roots and not (reach(graph, t) & roots)]
    if not cyclic:
        depth = {}

        def deep(t):
            if t not in depth:
                depth[t] = 1 + max((deep(d) for d in graph[t]), default=0)
            return depth[t]
        if max((deep(t) for t in graph), default=0) > MAX_DEPTH:
            found.append(("依存の深さ", None))
    return found


def package_of(tasks):
    """(パッケージか None, 問題)。tests/ の外のパスの先頭のディレクトリ。"""
    tops = {_norm(f).split("/")[0] for t in tasks for f in _list(t, "files") if not _norm(f).startswith("tests/")}
    return (next(iter(tops)), []) if len(tops) == 1 else (None, [("package がそろわない", None)] if tops else [])


def check_tasks(tasks, ids_in_what):
    found = []
    ids = [t["id"] for t in tasks]
    if not TASKS[0] <= len(tasks) <= TASKS[1]:
        found.append(("タスクの数", None))
    if set(ids) != ids_in_what or len(ids) != len(set(ids)):
        found += [("What とタスクが 1 対 1 でない", i) for i in sorted(set(ids) ^ ids_in_what)] or [("What とタスクが 1 対 1 でない", None)]
    graph = {t["id"]: [d for d in _list(t, "depends_on") if d in ids] for t in tasks}
    found += [("depends_on が台帳に無い ID", t["id"]) for t in tasks if any(d not in ids for d in _list(t, "depends_on"))]
    found += graph_problems(graph)
    package, pkg_found = package_of(tasks)
    found += pkg_found
    for t in tasks:
        tid, files = t["id"], [_norm(f) for f in _list(t, "files")]
        if not FILES[0] <= len(files) <= FILES[1]:
            found.append(("files の件数", tid))
        if package and any(not (f.startswith(package + "/") or re.fullmatch(rf"tests/test_{re.escape(package)}[^/]*", f)) for f in files):
            found.append(("files の場所", tid))
        if not ACCEPTANCE[0] <= len(_list(t, "acceptance")) <= ACCEPTANCE[1]:
            found.append(("acceptance の項目数", tid))
        own = set(files)
        for d in reach(graph, tid):
            own |= {_norm(f) for f in _list(next(x for x in tasks if x["id"] == d), "files")}
        verification = t.get("verification")
        mods = named_tests(verification.get("command") if isinstance(verification, dict) else "")
        if not mods or any(m not in own for m in mods):
            found.append(("検証の試験のモジュールの出どころ", tid))
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            same = {_norm(f) for f in _list(a, "files")} & {_norm(f) for f in _list(b, "files")}
            if package and any(f.startswith(package + "/") for f in same) \
                    and a["id"] not in reach(graph, b["id"]) and b["id"] not in reach(graph, a["id"]):
                found.append(("同じファイルを持つタスクが依存し合わない", b["id"]))
    return found


def check(inbox):
    inbox = Path(inbox)
    tasks = load(inbox) if inbox.is_dir() else None
    if tasks is None:
        return [(UNREADABLE, None)]
    return check_tasks(tasks, what_ids(inbox))


def record(inbox, found):
    path = Path(inbox) / RESULT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["plan_check"] = [{"rule": r, "task": t} for r, t in found]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.plan_check", description=__doc__.splitlines()[0])
    ap.add_argument("dir")
    args = ap.parse_args(argv)
    found = check(args.dir)
    if Path(args.dir).is_dir():
        record(args.dir, found)
    for rule, task in found:
        print(f"NG: {rule}（{task or '全体'}）")
    print("合格" if not found else f"不合格（{len(found)} 件）")
    return 0 if not found else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
