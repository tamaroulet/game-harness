"""目的までの距離：C5・C4 の達成要件に対する量を、横木ごとに 1 行で出す（report.md の先頭の節と同じ）。

    python -m harness.goal_distance

台帳（docs/progress.yaml）と bench/records は、進捗ツリーと同じ ref（統合ブランチ、無ければ config/hline.json の base）の中身から読む。
読み取りに失敗した量は、その行に `取得失敗` と理由を出し、ほかの行を止めない。
"""
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
RUNGS = ("R3-a", "R3-b", "R3-c", "R3", "R4")
PAIR_RUNGS = ("R3-a", "R3-b")
RECORDS = "bench/records"
BENCH_TASKS = 10
TRIALS = 60
CYCLES = 4
WEEKLY_TASK = "W-59"
STATUS_RE = re.compile(r"^- ([A-Za-z0-9-]+): (\S+?)（", re.M)


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
            return ref, yaml.safe_load(show(cfg, ref, progress.REL_PATH))
        except Unreadable as e:
            why.append(str(e))
    raise Unreadable("台帳を読めない: " + "／".join(why))


def module_of(path):
    return Path(path).with_suffix("").as_posix().replace("/", ".")


def passing_modules(cfg):
    """ref が origin/main の unittest の照合で、最新の結果が合格のものの modules（report.md の「## 照合」と同じ結果）。"""
    text = hline_checks.safe_section(cfg)
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
    ids = prerequisites(tasks, rung)
    by_id = {t.get("id"): t for t in tasks}
    done = sum(1 for i in ids if (by_id.get(i) or {}).get("status") == "completed")
    return f"（前提タスク {done}／{len(ids)}）"


# ============================================================ 横木ごとの量

def valid(rec):
    return isinstance(rec, dict) and not rec.get("invalid")


def pairs(cfg, tasks, rung):
    items = [o for t in rung_tasks(tasks, rung) for o in t.get("oracle") or []]
    paths = [o.get("path") for o in items if isinstance(o, dict) and isinstance(o.get("path"), str)]
    passed = passing_modules(cfg)
    return f"判定役の照合が通る課題 {sum(1 for p in paths if module_of(p) in passed)}／{len(paths)}"


def records_of(cfg, ref):
    """{パス: 中身の JSON}（bench/records の下のすべての JSON）。"""
    return {p: json.loads(show(cfg, ref, p)) for p in record_paths(cfg, ref) if p.endswith(".json")}


def exists_word(flag):
    return "あり" if flag else "なし"


def r3c(cfg, ref):
    recs = records_of(cfg, ref)
    per = {}
    for path, rec in recs.items():
        parts = path.split("/")
        if len(parts) == 4:
            per.setdefault(parts[2], {})[parts[3]] = rec
    floor = sum(1 for files in per.values() if (files.get("floor.json") or {}).get("passed") is True)

    def has(files, prefix):
        return any(n.startswith(prefix) and valid(r) for n, r in files.items())
    both = sum(1 for files in per.values() if has(files, "chat-") and has(files, "factory-"))
    return (f"床の検査が合格の課題 {floor}／{BENCH_TASKS}、両条件の記録がそろう課題 {both}／{BENCH_TASKS}、"
            f"凍結 {exists_word(f'{RECORDS}/freeze.json' in recs)}")


def r3(cfg, ref):
    recs = records_of(cfg, ref)
    freeze = recs.get(f"{RECORDS}/freeze.json")
    verdict = exists_word(f"{RECORDS}/verdict.json" in recs)
    if freeze is None:
        return f"凍結の後の記録 0／{TRIALS}、判定 {verdict}"
    n = 0
    for path, rec in recs.items():
        parts = path.split("/")
        if len(parts) == 4 and parts[3].startswith(("chat-", "factory-")) and valid(rec) and rec.get("base") == freeze.get("base"):
            n += 1
    return f"凍結の後の記録 {n}／{TRIALS}、判定 {verdict}"


def r4(cfg, tasks):
    if not any(t.get("id") == WEEKLY_TASK and t.get("status") == "completed" for t in tasks):
        return f"続いた週次の周期 0／{CYCLES}"
    for n in range(CYCLES, 0, -1):
        if cycles_ok(cfg, n):
            return f"続いた週次の周期 {n}／{CYCLES}"
    return f"続いた週次の周期 0／{CYCLES}"


def quantity(cfg, ref, tasks, rung):
    if rung in PAIR_RUNGS:
        return pairs(cfg, tasks, rung)
    return {"R3-c": lambda: r3c(cfg, ref), "R3": lambda: r3(cfg, ref), "R4": lambda: r4(cfg, tasks)}[rung]()


def render(cfg):
    ref, state = load_ledger(cfg)
    tasks = (state or {}).get("tasks") or []
    lines = [TITLE]
    for rung in RUNGS:
        try:
            body = quantity(cfg, ref, tasks, rung)
        except Exception as e:   # noqa: BLE001 - 1 つの量の失敗でほかの行を止めない
            body = hline_probe.failure(f"{type(e).__name__}: {e}" if not isinstance(e, Unreadable) else e)
        lines.append(f"- {rung}: {body}{prerequisite_note(tasks, rung)}")
    return "\n".join(lines) + "\n"


def section(cfg):
    """report.md 用。台帳を読めないなど、節ごと失敗しても、1 行の理由に替えて書き出しを止めない。"""
    try:
        return render(cfg)
    except Exception as e:   # noqa: BLE001
        return f"{TITLE}\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"


def main(argv=None):
    sys.stdout.write(section(load_config()))
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
