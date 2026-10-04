"""base（実装前）の検査で止めるテストを、合格済みのタスクのものだけに絞る（S2-2）。落ちてよいテストの名前の一覧は持たない。"""
import re

_COMMAND = re.compile(r"^\s*python\s+-m\s+unittest\s+([A-Za-z_][\w.]*)\s*$")


def _pattern(task_id):
    return re.compile(r"(?<!\d)" + "".join("[-_]" if c == "-" else re.escape(c) for c in task_id) + r"(?!\d)")


def belongs(test_name, task_id):
    """前後が数字なら別のタスク（S1-1 と S1-10、T1 と T10）。"""
    return bool(task_id) and _pattern(task_id).search(test_name or "") is not None


def selected(test_names, task_ids):
    ids = [t for t in task_ids if t]
    return tuple(dict.fromkeys(n for n in test_names if any(belongs(n, t) for t in ids)))


def parse_ids(text):
    return tuple(t for t in re.split(r"[,\s]+", text or "") if t)


def _completed(state, target_repo):
    return [t for t in (state or {}).get("tasks") or []
            if t.get("status") == "completed" and (target_repo is None or t.get("target_repo") == target_repo)]


def completed_ids(state, target_repo=None):
    return tuple(t["id"] for t in _completed(state, target_repo))


def completed_modules(state, target_repo=None):
    """検証コマンドが `python -m unittest <モジュール>` の形のものだけ（discover・連結・verification なしは飛ばす）。"""
    found = (_COMMAND.match(str((t.get("verification") or {}).get("command"))) for t in _completed(state, target_repo))
    return tuple(dict.fromkeys(m.group(1) for m in found if m and m.group(1) != "discover"))


def unittest_command(gate_command, modules):
    """gate_command の `-m unittest` までに modules を並べる。走らせるものが無ければ None。"""
    modules = list(modules)
    return list(gate_command[:list(gate_command).index("unittest") + 1]) + modules if modules else None
