"""関門と中間関門の並びと、タスクの置き場を台帳（docs/progress.yaml を読んだ辞書）だけから決める。

rung を持つタスクが関門。関門が推移的に待つ rung の無い by: pilot のタスクが中間関門（所属は、それを待つ関門のうち最初のもの）。
タスクは、それを推移的に待つ関門・中間関門のうち最も前のものの下に置く（済みのタスクも同じ）。表示は作らない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402


def _ancestors(by_id):
    """タスク ID → 推移的に待つ前段の ID の集合（自分を含まず、台帳に無い ID は数えない）。
    止めた・取り下げたタスクは含めるが、その先はたどらない（その依存は、もう関門の前段ではない）。"""
    found = {}
    for tid, t in by_id.items():
        seen, stack = set(), list(progress._deps(t))
        while stack:
            d = stack.pop()
            if d in seen or d not in by_id:
                continue
            seen.add(d)
            if not progress.is_held(by_id[d]):
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
        return by_id[i].get("status") in progress.OPEN

    current = next((g for g in gate_ids if open_(g) or any(open_(i) for i in gates[g]["tasks"])
                    or any(open_(m["id"]) or any(open_(i) for i in m["tasks"]) for m in gates[g]["mids"])), None)
    return [gates[g] for g in gate_ids], current


TITLE = "## 関門ごとの流れ"
MAX_LINES = 60


def _one(text):
    return " ".join(str(text or "").split())


def _state(by_id, i):
    """'done' / 'ready'（depends_on がすべて完了）/ 'wait'。"""
    t = by_id[i]
    if t.get("status") == "completed":
        return "done"
    if progress.is_held(t):
        return "held"
    ok = all(d in by_id and by_id[d].get("status") == "completed" for d in progress._deps(t))
    return "ready" if ok else "wait"


def _counts(by_id, ids):
    states = [_state(by_id, i) for i in ids]
    held = f"・止めた／取り下げた {states.count('held')}" if "held" in states else ""
    return f"済み {states.count('done')}・着手可 {states.count('ready')}・待ち {states.count('wait')}{held}"


def _group(by_id, heading, ids, short):
    """見出しと、着手可・待ちのタスクの行。short なら待ちの行を「待ち N 件」に替える。"""
    out = [f"{heading}: {_counts(by_id, ids)}"]
    waiting = 0
    for i in ids:
        s, t = _state(by_id, i), by_id[i]
        if s == "ready":
            after = [j for j, u in by_id.items() if i in progress._deps(u)]
            out.append(f"- {i} {_one(t.get('title'))}（by: {t.get('by') or 'hline'}）後段: {'・'.join(after) or 'なし'} — {_one(t.get('purpose'))}")
        elif s == "wait":
            waiting += 1
            if not short:
                undone = [d for d in progress._deps(t) if d not in by_id or by_id[d].get("status") != "completed"]
                out.append(f"- {i} {_one(t.get('title'))} ← 未完了の前段: {'・'.join(undone)}")
    if short and waiting:
        out.append(f"- 待ち {waiting} 件")
    return out


def render(ledger):
    """「## 関門ごとの流れ」の節の本文（行の列）。現在の関門だけ詳しく、60 行を超えるときは縮約する。"""
    gates, current = place(ledger)
    by_id = {t["id"]: t for t in ledger.get("tasks") or [] if isinstance(t, dict) and "id" in t}

    def gate_ids(g):
        return [g["id"]] + g["tasks"] + [m for mid in g["mids"] for m in [mid["id"]] + mid["tasks"]]

    def build(short, collapse_others):
        lines = [TITLE] + ([] if current else ["- 現在の関門: なし（どの関門にも、止めた・取り下げたもののほかに未完了のタスクが無い。立て直しは進捗ツリーを見る）"])
        others = []
        for g in gates:
            if g["id"] != current:
                others.append(g)
                continue
            lines.append(f"### 現在の関門 {g['id']} {_one(by_id[g['id']].get('title'))}: {_counts(by_id, gate_ids(g))}")
            lines += _group(by_id, "#### 関門直下", [g["id"]] + g["tasks"], short)
            for mid in g["mids"]:
                lines += _group(by_id, f"#### 中間関門 {mid['id']} {_one(by_id[mid['id']].get('title'))}", [mid["id"]] + mid["tasks"], short)
        if others and collapse_others:
            lines.append(f"- ほかの {len(others)} 関門: {_counts(by_id, [i for g in others for i in gate_ids(g)])}")
        else:
            lines += [f"- {g['id']} {_one(by_id[g['id']].get('title'))}: {_counts(by_id, gate_ids(g))}" for g in others]
        return lines

    for short, collapse in ((False, False), (True, False), (True, True)):
        lines = build(short, collapse)
        if len(lines) <= MAX_LINES:
            break
    return lines


def section(cfg, ref=None):
    """report.md 用。台帳を読めないなど、節ごと失敗しても、1 行の理由に替えて書き出しを止めない。ref は goal_distance.load_ledger に渡す。"""
    import goal_distance
    try:
        _, ledger = goal_distance.load_ledger(cfg, ref)
        return "\n".join(render(ledger)) + "\n"
    except Exception as e:   # noqa: BLE001
        return f"{TITLE}\n- 取得失敗：{type(e).__name__}: {e}\n"


def main(argv=None):
    from hline_base import load_config
    sys.stdout.write(section(load_config()))
    return 0


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
