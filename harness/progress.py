"""進捗の外部主記憶（docs/progress.yaml）と、その 2 つの見え方（ADR-003 §4、B3.1-PREP）。

    python -m harness.progress anchor          LLM に注入する極小アンカー（現在地・検証・制約だけ）
    python -m harness.progress tree [--all]    人間と PR 本文用のツリー（既定は現在の段だけ展開）
    python -m harness.progress complete <id>   検証コマンドを実行し、期待した終了コードのときだけ完了にする
    python -m harness.progress check           progress.yaml の形を検査する
    python -m harness.progress report          チャット報告の全文（ツリーと状態。20 行以内）
    python -m harness.progress review <url>    現在のタスクを PR のレビュー待ちにする（complete で解除）

**なぜ要るか**: 進捗をチャットやメモリで持つと、表記が崩れ、推測の件数が混ざり、手で「完了」にできてしまう。
状態はファイルに置き、書き換えはこの CLI だけが行う。完了への遷移は、検証コマンドの終了コードを
このスクリプトが実測したときだけ起きる。

**検証コマンドの改ざん防止**: complete は、手元の progress.yaml の検証コマンドが origin/main の版と
一字一句同じであることを確かめてから実行する。検証を差し替えるには、main への PR（人間のレビュー）が要る。
"""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REL_PATH = "docs/progress.yaml"
STATUSES = ("pending", "in_progress", "completed")
TAIL_LINES = 20
REPORT_MAX_LINES = 20
VERIFY_TTL = 1800
GIT_TTL = 120


class ProgressError(Exception):
    pass


# ============================================================ 読み書き

def load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def dump(state):
    return yaml.safe_dump(state, allow_unicode=True, sort_keys=False, width=1000)


def save(state, path):
    Path(path).write_text(dump(state), encoding="utf-8", newline="\n")


def validate(state):
    """問題の一覧（空なら合格）。"""
    problems = []
    if not isinstance(state, dict):
        return ["最上位が辞書ではありません"]
    for key in ("project_goal", "nodes", "tasks", "constraints"):
        if key not in state:
            problems.append(f"{key} がありません")
    if problems:
        return problems
    node_ids = [n.get("id") for n in state["nodes"]]
    if len(set(node_ids)) != len(node_ids):
        problems.append("nodes の id が重複しています")
    for n in state["nodes"]:
        if n.get("parent") is not None and n["parent"] not in node_ids:
            problems.append(f"node {n.get('id')} の parent {n['parent']} がありません")
        if n.get("status") not in (None, "completed"):
            problems.append(f"node {n.get('id')} の status は completed か省略だけです")
    task_ids = [t.get("id") for t in state["tasks"]]
    if len(set(task_ids)) != len(task_ids):
        problems.append("tasks の id が重複しています")
    for t in state["tasks"]:
        tid = t.get("id")
        for key in ("id", "title", "group", "target_repo", "status"):
            if not t.get(key):
                problems.append(f"task {tid} に {key} がありません")
        if t.get("group") not in node_ids:
            problems.append(f"task {tid} の group {t.get('group')} が nodes にありません")
        if t.get("status") not in STATUSES:
            problems.append(f"task {tid} の status {t.get('status')} は {STATUSES} のどれでもありません")
        if t.get("review_pr") is not None and not str(t["review_pr"]).startswith("https://"):
            problems.append(f"task {tid} の review_pr は https の URL です")
        v = t.get("verification")
        if v is not None and (not isinstance(v, dict) or not isinstance(v.get("command"), str)
                              or not isinstance(v.get("expected_exit_code"), int)):
            problems.append(f"task {tid} の verification は command（文字列）と expected_exit_code（整数）を持ちます")
    active = [t["id"] for t in state["tasks"] if t.get("status") == "in_progress"]
    if len(active) > 1:
        problems.append(f"in_progress が {len(active)} 件あります（1 件まで）")
    if state.get("active_task_id") != (active[0] if active else None):
        problems.append(f"active_task_id {state.get('active_task_id')} が in_progress のタスク {active} と一致しません")
    return problems


def task(state, task_id):
    for t in state["tasks"]:
        if t["id"] == task_id:
            return t
    raise ProgressError(f"タスク {task_id} がありません")


# ============================================================ 見え方

def anchor(state):
    """LLM 用。現在地・直前の検証・脱出条件・制約だけを出す。ツリーは出さない。"""
    tid = state.get("active_task_id")
    lines = ["<!-- [SYSTEM-INJECTED TASK ANCHOR] -->"]
    if not tid:
        lines += ["## 📍 CURRENT FOCUS: (none)", "- All tasks are completed. Ask the human for the next goal."]
        return "\n".join(lines) + "\n"
    t = task(state, tid)
    lines += [f"## 📍 CURRENT FOCUS: [{t['id']}] {t['title']}",
              f"- Target Repo: {t['target_repo']}"]
    if t.get("target_files"):
        lines.append(f"- Target Files: {', '.join(t['target_files'])}")
    lines += ["", "## 🛑 MECHANICAL VERIFICATION"]
    v = t.get("verification")
    if v:
        lines += [f"- Verification Command: `{v['command']}`",
                  f"- Exit Condition: Expected Exit Code {v['expected_exit_code']}"]
    else:
        lines.append("- Verification Command: (undefined — define it in docs/progress.yaml via a PR to main; "
                     "complete is refused until then)")
    last = state.get("last_verification")
    if last and last.get("task") == tid:
        lines.append(f"- Last Verification: exit {last['exit_code']} at {last['at']}")
    lines += ["", "## ⛔ CONSTRAINTS"]
    lines += [f"- {c}" for c in state["constraints"]]
    lines.append(f"- Call `python -m harness.progress complete {tid}` ONLY after verification passes.")
    return "\n".join(lines) + "\n"


def _children(state, parent):
    return [n for n in state["nodes"] if n.get("parent") == parent]


def _tasks_under(state, node_id):
    out = [t for t in state["tasks"] if t["group"] == node_id]
    for c in _children(state, node_id):
        out += _tasks_under(state, c["id"])
    return out


def _done(state, node):
    ts = _tasks_under(state, node["id"])
    if ts:
        return all(t["status"] == "completed" for t in ts)
    return node.get("status") == "completed"


def _contains_active(state, node_id):
    return any(t["id"] == state.get("active_task_id") for t in _tasks_under(state, node_id))


def tree(state, expand_all=False):
    """人間と PR 用。既定では現在のタスクを含む枝だけを展開し、他の枝は件数に畳む。"""
    lines = []

    def walk(node, depth):
        ts = _tasks_under(state, node["id"])
        mark = "[x]" if _done(state, node) else "[ ]"
        count = f"（{sum(t['status'] == 'completed' for t in ts)}/{len(ts)}）" if ts else ""
        lines.append(f"{'  ' * depth}- {mark} {node['id']} {node['title']}{count}")
        if not (expand_all or _contains_active(state, node["id"])):
            return
        for c in _children(state, node["id"]):
            walk(c, depth + 1)
        for t in state["tasks"]:
            # 完了したタスクは親の件数に畳む（--all のときだけ出す）
            if t["group"] == node["id"] and (expand_all or t["status"] != "completed"):
                here = " ← 現在地" if t["id"] == state.get("active_task_id") else ""
                tmark = "[x]" if t["status"] == "completed" else "[ ]"
                lines.append(f"{'  ' * (depth + 1)}- {tmark} {t['id']} {t['title']}{here}")

    for root in _children(state, None):
        # 根は常に 1 段目まで開く（全体の位置づけを示すため）
        lines.append(f"- {root['id']} {root['title']}")
        for c in _children(state, root["id"]):
            walk(c, 1)
    return "\n".join(lines) + "\n"


def report(state):
    """チャット報告の全文。ツリーと状態だけで、作文を挟む余地を残さない。"""
    tid = state.get("active_task_id")
    t = task(state, tid) if tid else None
    v = (t or {}).get("verification")
    pr = (t or {}).get("review_pr")
    lines = ["## 進捗ツリー"] + tree(state).splitlines() + [
        "",
        "## 状態",
        f"- active_task: {tid} ({t['title']})" if t else "- active_task: NONE",
        f"- target_repo: {t['target_repo']}" if t else "- target_repo: NONE",
        f"- verification: {v['command'] if v else '(undefined)'}",
        f"- human_action: REVIEW_REQUIRED {pr}" if pr else "- human_action: NONE",
    ]
    if len(lines) > REPORT_MAX_LINES:
        raise ProgressError(f"report が {len(lines)} 行で、上限 {REPORT_MAX_LINES} 行を超えます")
    return "\n".join(lines) + "\n"


def review(task_id, url, path):
    """現在のタスクを PR のレビュー待ちにする。解除は complete だけが行う。"""
    state = load(path)
    if state.get("active_task_id") != task_id:
        raise ProgressError(f"{task_id} は現在のタスクではありません（現在：{state.get('active_task_id')}）")
    if not url.startswith("https://"):
        raise ProgressError("PR の URL（https://…）を渡してください")
    task(state, task_id)["review_pr"] = url
    save(state, path)


# ============================================================ 検証連動の遷移（Gatekeeper）

def main_version(repo_root, ref, fetch=True):
    remote, _, branch = ref.partition("/")
    if fetch:
        r = subprocess.run(["git", "fetch", "-q", remote, branch], cwd=repo_root, capture_output=True, text=True,
                           timeout=GIT_TTL)
        if r.returncode != 0:
            raise ProgressError(f"git fetch に失敗しました: {r.stderr.strip()[:200]}")
    r = subprocess.run(["git", "show", f"{ref}:{REL_PATH}"], cwd=repo_root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=GIT_TTL)
    if r.returncode != 0:
        raise ProgressError(f"{ref} に {REL_PATH} がありません。検証コマンドは main に入ってから有効になります")
    return yaml.safe_load(r.stdout)


def complete(task_id, repo_root=ROOT, ref="origin/main", fetch=True, out=print, now=None):
    """0 = 完了にした、1 = 拒絶（REJECT）。"""
    path = Path(repo_root) / REL_PATH
    state = load(path)
    problems = validate(state)
    if problems:
        raise ProgressError("progress.yaml の形が壊れています: " + " / ".join(problems))
    if state.get("active_task_id") != task_id:
        raise ProgressError(f"{task_id} は現在のタスクではありません（現在：{state.get('active_task_id')}）")
    t = task(state, task_id)
    v = t.get("verification")
    if not v:
        raise ProgressError(f"{task_id} には検証コマンドがありません。main への PR で定義してください")
    try:
        ref_v = task(main_version(repo_root, ref, fetch), task_id).get("verification")
    except ProgressError as e:
        raise ProgressError(f"{ref} の版と照合できません: {e}")
    if ref_v != v:
        raise ProgressError(f"{task_id} の検証が {ref} の版と違います。手元での書き換えは受け付けません")

    out(f"検証: {v['command']}")
    try:
        r = subprocess.run(v["command"], shell=True, cwd=repo_root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=VERIFY_TTL)
        rc, log = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, log = -1, f"{VERIFY_TTL} 秒で打ち切りました"
    at = (now or datetime.datetime.now)().isoformat(timespec="seconds")
    state["last_verification"] = {"task": task_id, "exit_code": rc, "at": at}
    if rc != v["expected_exit_code"]:
        save(state, path)
        tail = log.strip().splitlines()[-TAIL_LINES:]
        out("\n".join(tail))
        out(f"REJECT: 終了コード {rc}（期待 {v['expected_exit_code']}）。{task_id} は完了にしません")
        return 1
    t["status"] = "completed"
    t.pop("review_pr", None)
    nxt = next((x for x in state["tasks"] if x["status"] == "pending"), None)
    if nxt:
        nxt["status"] = "in_progress"
    state["active_task_id"] = nxt["id"] if nxt else None
    save(state, path)
    out(f"完了: {task_id}（終了コード {rc}）。次の現在地：{state['active_task_id'] or 'なし'}")
    out(f"{REL_PATH} の変更をコミットして main に入れてください")
    return 0


# ============================================================ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="進捗の外部主記憶（docs/progress.yaml）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("anchor")
    tp = sub.add_parser("tree")
    tp.add_argument("--all", action="store_true", help="すべての枝を展開する")
    cp = sub.add_parser("complete")
    cp.add_argument("task_id")
    sub.add_parser("check")
    sub.add_parser("report")
    rp = sub.add_parser("review")
    rp.add_argument("task_id")
    rp.add_argument("url")
    args = ap.parse_args(argv)
    path = ROOT / REL_PATH
    try:
        if args.cmd == "complete":
            return complete(args.task_id)
        if args.cmd == "review":
            review(args.task_id, args.url, path)
            print(report(load(path)), end="")
            return 0
        state = load(path)
        problems = validate(state)
        if args.cmd == "check" or problems:
            for p in problems:
                print(f"NG: {p}")
            if args.cmd == "check" and dump(state) != path.read_text(encoding="utf-8"):
                print("NG: 正規の書式ではありません（手で編集した可能性があります）")
                return 1
            print(f"{'合格' if not problems else '不合格'}（{len(problems)} 件）")
            return 0 if not problems else 1
        if args.cmd == "report":
            print(report(state), end="")
        else:
            print(anchor(state) if args.cmd == "anchor" else tree(state, args.all), end="")
    except ProgressError as e:
        print(f"NG: {e}")
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
