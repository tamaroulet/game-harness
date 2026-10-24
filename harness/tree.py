"""
harness.tree: 目的・要件・タスクの木（docs/tree/tree.yaml）の整合性検査。
"""
import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode
import progress

ALLOWED_STATUSES = {"未達", "仮説", "自己検証のみ", "検証済み"}
ALLOWED_VERIFY_BY = {"test:independent", "reviewer", "human"}
UNCOMPLETED_STATUSES = {"pending", "in_progress", "stopped"}


def check(tree_data: dict, ledger_data: dict) -> list[str]:
    problems = []

    obj = tree_data.get("objective")
    if obj is None:
        problems.append("objective がありません")
        obj_text = ""
    elif isinstance(obj, dict):
        obj_text = obj.get("title", "")
        if not obj_text:
            problems.append("objective に title がありません")
    elif isinstance(obj, list):
        if len(obj) != 1:
            problems.append(f"objective が {len(obj)} 個あります（ちょうど 1 個必要）")
        obj_text = obj[0] if obj else ""
    elif isinstance(obj, str):
        obj_text = obj
    else:
        problems.append(f"objective の型が不正です: {type(obj)}")
        obj_text = ""

    proj_goal = (ledger_data.get("project_goal") or "").strip()
    if not proj_goal:
        problems.append("台帳（docs/progress.yaml）に project_goal が無いか空です")
    elif obj_text and obj_text.strip() != proj_goal:
        problems.append(f"objective と project_goal の文字列が一致しません: objective={obj_text!r} vs project_goal={proj_goal!r}")

    requirements = tree_data.get("requirements", [])
    req_ids = set()
    req_map = {}

    for req in requirements:
        rid = req.get("id")
        if not rid:
            problems.append("id の無い要件があります")
            continue
        if rid in req_ids:
            problems.append(f"要件 id が重複しています: {rid}")
        req_ids.add(rid)
        req_map[rid] = req

        for field in ("title", "achieved_when", "verify_by", "status", "depends_on"):
            if field not in req:
                problems.append(f"要件 {rid} に必須項目 {field} がありません")

        st = req.get("status")
        if st is not None and st not in ALLOWED_STATUSES:
            problems.append(f"要件 {rid} の status {st!r} は許可された値 {ALLOWED_STATUSES} ではありません")

        vb = req.get("verify_by")
        if vb is not None:
            vb_list = vb if isinstance(vb, list) else [vb]
            for v in vb_list:
                if v not in ALLOWED_VERIFY_BY:
                    problems.append(f"要件 {rid} の verify_by {v!r} は許可された値 {ALLOWED_VERIFY_BY} ではありません")

    if isinstance(obj, dict):
        for dep in obj.get("depends_on") or []:
            if dep not in req_ids:
                problems.append(f"objective の depends_on 先 {dep!r} が要件に存在しません")

    for rid, req in req_map.items():
        deps = req.get("depends_on", [])
        for d in deps:
            if d not in req_map:
                problems.append(f"要件 {rid} の depends_on 先 {d!r} が要件に存在しません")

    visited = {}
    def dfs(node: str, path: list[str]) -> bool:
        visited[node] = 0
        for nxt in req_map.get(node, {}).get("depends_on", []):
            if nxt in req_map:
                if visited.get(nxt) == 0:
                    cycle = path + [nxt]
                    problems.append(f"要件の依存に循環があります: {' -> '.join(cycle)}")
                    return True
                elif nxt not in visited:
                    if dfs(nxt, path + [nxt]):
                        return True
        visited[node] = 1
        return False

    for rid in req_map:
        if rid not in visited:
            dfs(rid, [rid])

    ledger_tasks = {t["id"]: t for t in ledger_data.get("tasks", []) if "id" in t}

    task_occurrences = {}
    for req in requirements:
        rid = req.get("id", "unknown")
        for tid in req.get("tasks", []):
            if tid not in ledger_tasks:
                problems.append(f"要件 {rid} のタスク {tid!r} が台帳に存在しません")
            else:
                t = ledger_tasks[tid]
                if t.get("status") == "withdrawn":
                    problems.append(f"要件 {rid} のタスク {tid!r} は withdrawn です（木に載せられません）")
            task_occurrences[tid] = task_occurrences.get(tid, 0) + 1

    for tid, t in ledger_tasks.items():
        if t.get("status") in UNCOMPLETED_STATUSES:
            count = task_occurrences.get(tid, 0)
            if count == 0:
                problems.append(f"未完了タスク {tid!r} ({t.get('status')}) がどの要件にもありません")
            elif count > 1:
                problems.append(f"未完了タスク {tid!r} が複数の要件に重複して現れています（{count} 回）")

    return problems


def main():
    root = ROOT
    tree_path = root / "docs" / "tree" / "tree.yaml"
    if not tree_path.exists():
        print(f"Error: {tree_path} not found", file=sys.stderr)
        return 1

    with open(tree_path, "r", encoding="utf-8") as f:
        tree_data = yaml.safe_load(f)

    ledger_path = root / "docs" / "progress.yaml"
    ledger_data = progress.load(ledger_path)

    problems = check(tree_data, ledger_data)
    if problems:
        print(f"FAILED: {len(problems)} problems found:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("ALL TREE CHECKS PASSED")
    return 0


ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
