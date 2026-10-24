"""目的までの距離：C5・C4 の達成要件に対する量を、横木ごとに 1 行で出す（report.md の先頭の節と同じ）。

    python -m harness.goal_distance

台帳（docs/progress.yaml）と bench/records は、進捗ツリーと同じ ref（統合ブランチ、無ければ config/hline.json の base）の中身から読む。
読み取りに失敗した量は、その行に `取得失敗` と理由を出し、ほかの行を止めない。
"""
import datetime
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_checks  # noqa: E402
import hline_probe  # noqa: E402
import proc  # noqa: E402
import progress  # noqa: E402
from hline_base import ROOT, load_config  # noqa: E402
from hline_git import remote_ref  # noqa: E402

TITLE = "## 目的までの距離"
RUNGS = ("R3-a", "R3-b", "R3-c", "R4")
PAIR_RUNGS = ("R3-a", "R3-b")
RECORDS = "bench/records"
BENCH_TASKS = 10
CYCLES = 4
WEEKLY_TASK = "W-59"
STATUS_RE = re.compile(r"^- ([A-Za-z0-9_-]+): (\S+?)（", re.M)


class Unreadable(Exception):
    """量を読めない。理由が行に出る。"""


def git_text(cfg, ref, args, label):
    code, out, err = proc.run(["git", *args], ROOT, cfg["ttl_seconds"]["git"], label)
    if code != 0:
        raise Unreadable(f"{label} が終了コード {code}（{(err.strip().splitlines() or [''])[0]}）")
    return out


def show(cfg, ref, path):
    return git_text(cfg, ref, ["show", f"{ref}:{path}"], f"git show {path}")


def record_paths(cfg, ref):
    out = git_text(cfg, ref, ["ls-tree", "-r", "--name-only", ref, RECORDS], "git ls-tree")
    return [x.strip() for x in out.splitlines() if x.strip()]


def load_ledger(cfg):
    """(ref, 台帳)。統合ブランチに無ければ base。"""
    why = []
    for ref in (remote_ref(cfg), cfg["base"]):
        try:
            def show_fn(rel_path):
                try:
                    return show(cfg, ref, rel_path)
                except Unreadable:
                    if rel_path == progress.REL_PATH:
                        raise
                    return None
            st = progress.read_ref(show_fn)
            if st is not None:
                return ref, st
        except Unreadable as e:
            why.append(str(e))
    raise Unreadable("台帳を読めない: " + "／".join(why))


def module_of(path):
    return Path(path).with_suffix("").as_posix().replace("/", ".")


def passing_modules(cfg, checks=None):
    """ref が origin/main で overlay が無く expect が pass の unittest の照合で、最新の結果が合格のものの modules（report.md の「## 照合」と同じ結果）。
    checks は「## 照合」の本文を返す関数（既定は照合を走らせる safe_section。H ラインが止まっている間は走らせない cached_section）。"""
    text = (checks or hline_checks.safe_section)(cfg)
    status = dict(STATUS_RE.findall(text))
    d = hline_checks.checks_dir(cfg)
    if not status and re.search(r"^- 取得失敗", text, re.M):
        raise Unreadable(text.strip().splitlines()[-1].removeprefix("- ").removeprefix("取得失敗："))
    found = set()
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            decl = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(decl, dict) and decl.get("kind") == "unittest" and decl.get("ref", "origin/main") == "origin/main"
                and "overlay" not in decl and decl.get("expect", "pass") == "pass"
                and status.get(f.stem) == "合格" and isinstance(decl.get("modules"), list)):
            found.update(m for m in decl["modules"] if isinstance(m, str))
    return found


def cycles_ok(cfg, n):
    """H ラインの木で outer_cycles --min n が 0 で終わるか。"""
    code, _, _ = proc.run([sys.executable, "-m", "harness.outer_cycles", "--min", str(n)], ROOT,
                          cfg["ttl_seconds"].get("gate", 1800), "outer_cycles")
    return code == 0


# ============================================================ 前提タスク

def rung_tasks(tasks, rung):
    return [t for t in tasks if t.get("rung") == rung]


def prerequisites(tasks, rung):
    """横木のタスクの depends_on を推移的にたどったタスクの ID（横木のタスク自身は含めない）。"""
    by_id = {t.get("id"): t for t in tasks}
    own = {t.get("id") for t in rung_tasks(tasks, rung)}
    seen, stack = set(), [d for t in rung_tasks(tasks, rung) for d in progress._deps(t)]
    while stack:
        tid = stack.pop()
        if tid in seen:
            continue
        seen.add(tid)
        stack += progress._deps(by_id.get(tid) or {})
    return sorted(seen - own, key=str)


def prerequisite_note(tasks, rung):
    """（前提タスク 完了／分母）。止めた・取り下げたタスクは分母に入れない（依存はその先までたどる）。"""
    by_id = {t.get("id"): t for t in tasks}
    ids = [i for i in prerequisites(tasks, rung) if not progress.is_held(by_id.get(i))]
    done = sum(1 for i in ids if (by_id.get(i) or {}).get("status") == "completed")
    return f"（前提タスク {done}／{len(ids)}）"


# ============================================================ 横木ごとの量

def valid(rec):
    return isinstance(rec, dict) and not rec.get("invalid")


def pairs(cfg, tasks, rung, checks=None):
    items = [o for t in rung_tasks(tasks, rung) for o in t.get("oracle") or []]
    paths = [o.get("path") for o in items if isinstance(o, dict) and isinstance(o.get("path"), str)]
    passed = passing_modules(cfg) if checks is None else passing_modules(cfg, checks)
    return f"判定役の照合が通る課題 {sum(1 for p in paths if module_of(p) in passed)}／{len(paths)}"


def records_of(cfg, ref):
    """{パス: 中身の JSON}（bench/records の下のすべての JSON）。"""
    return {p: json.loads(show(cfg, ref, p)) for p in record_paths(cfg, ref) if p.endswith(".json")}


def exists_word(flag):
    return "あり" if flag else "なし"


def per_task(recs):
    """{課題: {ファイル名: 中身}}（bench/records/<課題>/<ファイル名> の形のものだけ）。"""
    per = {}
    for path, rec in recs.items():
        parts = path.split("/")
        if len(parts) == 4:
            per.setdefault(parts[2], {})[parts[3]] = rec
    return per


def r3c(cfg, ref):
    recs = records_of(cfg, ref)
    floor = sum(1 for files in per_task(recs).values() if (files.get("floor.json") or {}).get("passed") is True)
    return f"床の検査が合格の課題 {floor}／{BENCH_TASKS}、凍結 {exists_word(f'{RECORDS}/freeze.json' in recs)}"


def base_tasks(cfg, ref):
    """工廠の基準の記録（factory-base-*.json で invalid が偽）が 1 つ以上ある課題の数。"""
    return sum(1 for files in per_task(records_of(cfg, ref)).values()
               if any(n.startswith("factory-base-") and n.endswith(".json") and valid(r) for n, r in files.items()))


def r4(cfg, ref, tasks):
    head = f"工廠の基準の記録がそろう課題 {base_tasks(cfg, ref)}／{BENCH_TASKS}、続いた週次の周期"
    if not any(t.get("id") == WEEKLY_TASK and t.get("status") == "completed" for t in tasks):
        return f"{head} 0／{CYCLES}"
    for n in range(CYCLES, 0, -1):
        if cycles_ok(cfg, n):
            return f"{head} {n}／{CYCLES}"
    return f"{head} 0／{CYCLES}"


def quantity(cfg, ref, tasks, rung, checks=None):
    if rung in PAIR_RUNGS:
        return pairs(cfg, tasks, rung, checks)
    return {"R3-c": lambda: r3c(cfg, ref), "R4": lambda: r4(cfg, ref, tasks)}[rung]()


def measure(cfg, checks=None):
    """(台帳のタスク, {横木: 量の文}、前提タスクの注記は含まない)。checks は passing_modules に渡す。"""
    ref, state = load_ledger(cfg)
    tasks = (state or {}).get("tasks") or []
    bodies = {}
    for rung in RUNGS:
        try:
            bodies[rung] = quantity(cfg, ref, tasks, rung, checks)
        except Exception as e:   # noqa: BLE001 - 1 つの量の失敗でほかの行を止めない
            bodies[rung] = hline_probe.failure(f"{type(e).__name__}: {e}" if not isinstance(e, Unreadable) else e)
    return tasks, bodies


def lines_of(tasks, bodies):
    return [TITLE] + [f"- {rung}: {bodies[rung]}{prerequisite_note(tasks, rung)}" for rung in RUNGS]


def render(cfg):
    return "\n".join(lines_of(*measure(cfg))) + "\n"


# ============================================================ 完了と距離の履歴

HISTORY = "goal_history.jsonl"
HISTORY_TITLE = "### 直近の完了と距離の変化"
HISTORY_SHOWN = 5
REGRESSION = "後退"
AMOUNT_RES = {
    "R3-a": (("課題", r"課題 (\d+)／"),),
    "R3-b": (("課題", r"課題 (\d+)／"),),
    "R3-c": (("床", r"床の検査が合格の課題 (\d+)／"), ("凍結", r"凍結 (あり|なし)")),
    "R4": (("基準", r"基準の記録がそろう課題 (\d+)／"), ("周期", r"続いた週次の周期 (\d+)／")),
}


def amounts(bodies):
    """{"R3-a 課題": 数, ...}。前提タスクの数は含めない。取得失敗の量は入れない（比べない）。"""
    found = {}
    for rung, specs in AMOUNT_RES.items():
        body = bodies.get(rung) or ""
        if "取得失敗" in body:
            continue
        for label, pattern in specs:
            m = re.search(pattern, body)
            if m:
                found[f"{rung} {label}"] = {"あり": 1, "なし": 0}.get(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
    return found


def history_path(cfg):
    return Path(cfg["out"]) / HISTORY


def read_history(cfg):
    p = history_path(cfg)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_history(cfg, row):
    p = history_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def completed_ids(tasks):
    return sorted((str(t.get("id")) for t in tasks if t.get("status") == "completed"))


def now_text():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def record_history(cfg, tasks, now):
    """(履歴の行を足したあとの履歴, 比べる相手の行)。足すのは、履歴が無いとき・完了が最後の行の集合より増えたとき。"""
    rows = read_history(cfg)
    last = rows[-1] if rows else None
    done = completed_ids(tasks)
    added = sorted(set(done) - set(last.get("done") or [])) if last else []
    if last is None or added:
        row = {"time": now_text(), "added": added, "before": last.get("after") if last else None,
               "after": now, "done": done}
        append_history(cfg, row)
        rows.append(row)
    return rows, last


def regression_lines(last, now, added):
    if not last:
        return []
    out = []
    for key, was in (last.get("after") or {}).items():
        cur = now.get(key)
        if isinstance(was, int) and isinstance(cur, int) and cur < was:
            out.append(f"{REGRESSION} {key} {was} → {cur}（直前に完了したタスク: {', '.join(added) or 'なし'}）")
    return out


def change_text(row):
    before, after = row.get("before") or {}, row.get("after") or {}
    changes = [f"{k} {before[k]} → {v}" for k, v in after.items() if k in before and before[k] != v]
    ids = ", ".join(row.get("added") or [])
    if row.get("before") is None:
        return f"{row.get('time')} 最初の記録（{'、'.join(f'{k} {v}' for k, v in after.items())}）"
    return f"{row.get('time')} 増えたタスク {ids}：{'、'.join(changes) or '量の変化なし'}"


def history_lines(cfg, tasks, now):
    rows, last = record_history(cfg, tasks, now)
    new_row = rows[-1] if last is not None and rows and rows[-1] is not last else None
    added = (new_row or last or {}).get("added") or []
    out = regression_lines(last, now, added)
    out.append(HISTORY_TITLE)
    out += [f"- {change_text(r)}" for r in reversed(rows[-HISTORY_SHOWN:])]
    return out


def section(cfg, checks=None):
    """report.md 用。台帳を読めないなど、節ごと失敗しても、1 行の理由に替えて書き出しを止めない。checks は passing_modules に渡す。"""
    try:
        tasks, bodies = measure(cfg, checks)
        lines = lines_of(tasks, bodies)
    except Exception as e:   # noqa: BLE001
        return f"{TITLE}\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"
    try:
        lines += history_lines(cfg, tasks, amounts(bodies))
    except Exception as e:   # noqa: BLE001 - 履歴の読み書きの失敗で report.md の書き出しを止めない
        lines += [HISTORY_TITLE, f"- {hline_probe.failure(f'{type(e).__name__}: {e}')}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    sys.stdout.write(section(load_config()))
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
