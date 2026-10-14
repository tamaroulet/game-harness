"""関門と中間関門の並びと、タスクの置き場を台帳（docs/progress.yaml を読んだ辞書）だけから決める。

rung を持つタスクが関門。関門が推移的に待つ rung の無い by: pilot のタスクが中間関門（所属は、それを待つ関門のうち最初のもの）。
タスクは、それを推移的に待つ関門・中間関門のうち最も前のものの下に置く（済みのタスクも同じ）。表示は作らない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402


def _ancestors(by_id):
    """タスク ID → 推移的に待つ前段の ID の集合（自分を含まず、台帳に無い ID は数えない）。"""
    found = {}
    for tid, t in by_id.items():
        seen, stack = set(), list(progress._deps(t))
        while stack:
            d = stack.pop()
            if d in seen or d not in by_id:
                continue
            seen.add(d)
            stack += progress._deps(by_id[d])
        seen.discard(tid)
        found[tid] = seen
    return found


def place(ledger):
    """(関門の列, 現在の関門の ID)。関門は {"id", "tasks", "mids": [{"id", "tasks"}]}。現在の関門は未完了を持つ最初の関門、無ければ None。"""
    by_id = {t["id"]: t for t in ledger.get("tasks") or [] if isinstance(t, dict) and "id" in t}
    anc = _ancestors(by_id)
    order = lambda i: (len(anc[i]), i)  # noqa: E731
    gate_ids = sorted((i for i, t in by_id.items() if t.get("rung")), key=order)
    rank = {g: n for n, g in enumerate(gate_ids)}
    owner = {}
    for i, t in by_id.items():
        if (t.get("by") or "hline") == "pilot" and not t.get("rung"):
            waiting = [g for g in gate_ids if i in anc[g]]
            if waiting:
                owner[i] = waiting[0]
    mid_ids = sorted(owner, key=lambda i: (rank[owner[i]],) + order(i))
    mid_rank = {m: n for n, m in enumerate(mid_ids)}
    gates = {g: {"id": g, "tasks": [], "mids": []} for g in gate_ids}
    mids = {m: {"id": m, "tasks": []} for m in mid_ids}
    for m in mid_ids:
        gates[owner[m]]["mids"].append(mids[m])
    for i in by_id:
        if i in rank or i in owner:
            continue
        found = [(rank[g], 1, 0, g, None) for g in gate_ids if i in anc[g]]
        found += [(rank[owner[m]], 0, mid_rank[m], owner[m], m) for m in mid_ids if i in anc[m]]
        if found:
            _, _, _, g, m = min(found)
            (mids[m] if m else gates[g])["tasks"].append(i)

    def open_(i):
        return by_id[i].get("status") != "completed"

    current = next((g for g in gate_ids if open_(g) or any(open_(i) for i in gates[g]["tasks"])
                    or any(open_(m["id"]) or any(open_(i) for i in m["tasks"]) for m in gates[g]["mids"])), None)
    return [gates[g] for g in gate_ids], current
