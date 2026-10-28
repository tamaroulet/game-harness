"""今の構成の実測（段 A3、W-142）：部品ごとに、費用（コードと試験の量・直した回数）と成果（走行の記録）を数える。

    python -m harness.arch_inventory [--since 2026-09-01] [--write docs/design/architecture_inventory.md]
    python -m harness.arch_inventory --reach     部品ごとの判定と、入口から import で届くモジュールの数（W-136）

**なぜ要るか**: 上位の設計（ADR-005）で、部品を残すか・置き換えるか・捨てるかを決める。思い込みでなく数字で決めるため、
部品（config/arch_components.json）ごとに、リポジトリ（git）と走行の記録（H ラインの out・受信箱・bench/records）から機械で数える。
手で書いた数字は入れない。走行の記録の無い PC（CI）では、走行の欄は「記録なし」になる。

**判定（W-136）**: 部品ごとに、残す（keep）・今捨てる（remove_now）・この後捨てる（remove_later）・仮説として一回走らせる
（hypothesis）のどれかと、補う弱さ・決める条件・決める台帳のタスクを config に持つ。外の実践との照らしは docs/design/harness_evaluation.md。
「使われていない」は、入口（config の entrypoints）から import の文で届かないことで数える。文字列の中の名前と、子プロセスで
起こす命令は辿らないので、届かないことは「消してよい」の必要条件で、十分条件ではない（試験と、ほかの部品の試験が依っていないかも見る）。
"""
import argparse
import ast
import datetime
import fnmatch
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_start  # noqa: E402
import exitcode  # noqa: E402
import hline_usage  # noqa: E402
import machine  # noqa: E402
import progress  # noqa: E402
import weekly_report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "arch_components.json"
HARNESS = ROOT / "harness"


def load(path=CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))["components"]


def modules(root=ROOT):
    """harness/ の下の .py（harness/ からの相対、/ 区切り）。テンプレートと __pycache__ は除く。"""
    base = Path(root) / "harness"
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.py")
                  if "__pycache__" not in p.parts and "templates" not in p.relative_to(base).parts)


def _matches(rel, pattern):
    """名前・ディレクトリ（/ で終わる）・型（* を含む。config/*.json に監査の語を書けない名前があるので型で引く）。"""
    return rel == pattern or (pattern.endswith("/") and rel.startswith(pattern)) or ("*" in pattern and fnmatch.fnmatch(rel, pattern))


def assign(comps, mods):
    """({モジュール: 部品}, 部品の無いもの, 2 つ以上の部品に入るもの)。"""
    owner, unassigned, doubled = {}, [], []
    for m in mods:
        hits = [name for name, c in comps.items() if any(_matches(m, p) for p in c["modules"])]
        if not hits:
            unassigned.append(m)
        elif len(hits) > 1:
            doubled.append((m, hits))
        else:
            owner[m] = hits[0]
    return owner, unassigned, doubled


VERDICTS = ("keep", "remove_now", "remove_later", "hypothesis")   # 残す・今捨てる・この後捨てる・仮説として一回走らせる


def load_config(path=CONFIG):
    """ファイル全体（entrypoints・removed・components）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verdict_of(comp, module):
    """モジュールの判定。部品の中で違うものは module_verdicts に書き、無ければ部品の判定。"""
    mv = (comp.get("module_verdicts") or {}).get(module)
    return mv["verdict"] if mv else comp.get("verdict")


def _module_index(base):
    """{点で区切った名前: harness/ からの相対}。パッケージは __init__.py を名前に持つ（例 "ab" → "ab/__init__.py"）。"""
    idx = {}
    for p in base.rglob("*.py"):
        rel = p.relative_to(base)
        if "__pycache__" in rel.parts or "templates" in rel.parts:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            idx[".".join(parts)] = rel.as_posix()
    return idx


def _longest(name, idx):
    """name かその頭のうち、harness/ の中にある一番長いもの（import a.b.c の a.b.c → a.b → a）。"""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in idx:
            return cand
    return None


def _imports(tree, cur, idx):
    """1 つのファイルの import の文が指す、harness/ の中のモジュール（点で区切った名前）。"""
    out = set()
    is_pkg = idx.get(cur, "").endswith("__init__.py")
    pkg = cur.split(".") if is_pkg else cur.split(".")[:-1]   # 相対の import の基準のパッケージ
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.name[len("harness."):] if a.name.startswith("harness.") else a.name
                hit = _longest(name, idx)
                if hit:
                    out.add(hit)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[:len(pkg) - (node.level - 1)] + (node.module.split(".") if node.module else [])
                mod = ".".join(base)
            else:
                mod = node.module or ""
                mod = "" if mod == "harness" else (mod[len("harness."):] if mod.startswith("harness.") else mod)
            for a in node.names:
                full = f"{mod}.{a.name}" if mod else a.name
                if full in idx:                      # from p import x の x がモジュール
                    out.add(full)
                elif mod and _longest(mod, idx):     # from c import 関数 は c を使う
                    out.add(_longest(mod, idx))
    out.discard(cur)
    return out


def _spawned(tree, cur, idx):
    """子プロセスで起こすモジュール：リストかタプルの中で、"-m" の直後に "harness.x" の文字列が並ぶもの。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            vals = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in node.elts]
            for a, b in zip(vals, vals[1:]):
                if a == "-m" and b and b.startswith("harness."):
                    hit = _longest(b[len("harness."):], idx)
                    if hit and hit != cur:
                        out.add(hit)
    return out


def reach(roots, root=ROOT, follow_spawn=False):
    """roots（harness/ からの相対名）から import の文で辿って届く、harness/ の中のモジュールの相対名の集合。
    follow_spawn=True のときは、子プロセスで起こすモジュール（["-m", "harness.x"]）も辿る（文字列の中の名前はそれ以外は辿らない）。"""
    base = Path(root) / "harness"
    idx = _module_index(base)
    rel_to_name = {v: k for k, v in idx.items()}
    seen, stack = set(), [rel_to_name[r] for r in roots if r in rel_to_name]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            tree = ast.parse((base / idx[name]).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        stack.extend(_imports(tree, name, idx) - seen)
        if follow_spawn:
            stack.extend(_spawned(tree, name, idx) - seen)
    return {idx[n] for n in seen}


def verdicts_report(root=ROOT, ledger=None):
    """部品ごとの判定・届くモジュールの数・決めるタスクの状態と、決まりに反するもの（core から届くのに keep でない）。"""
    cfg = load_config(Path(root) / "config" / "arch_components.json")
    comps, entry = cfg["components"], cfg.get("entrypoints") or {}
    owner, _, _ = assign(comps, modules(root))
    reached = {k: reach(v, root) for k, v in entry.items()}
    state = ledger if ledger is not None else progress.load_with_archive(Path(root) / progress.REL_PATH)
    status = {t["id"]: t.get("status") for t in state.get("tasks") or [] if isinstance(t, dict) and "id" in t}
    rows, problems = [], []
    for name, c in comps.items():
        mine = sorted(m for m, o in owner.items() if o == name)
        per = {m: verdict_of(c, m) for m in mine}
        tasks = {c.get("decide_task")} | {mv.get("decide_task") for mv in (c.get("module_verdicts") or {}).values()}
        tasks.discard(None)
        rows.append({"component": name, "verdict": c.get("verdict"), "modules": len(mine),
                     "lines": sum(_lines(Path(root) / "harness" / m) for m in mine),
                     "reached": {k: len([m for m in mine if m in v]) for k, v in reached.items()},
                     "by_verdict": {v: len([m for m in mine if per[m] == v]) for v in VERDICTS if any(per[m] == v for m in mine)},
                     "tasks": {t: status.get(t, "台帳に無い") for t in sorted(tasks)}})
        problems += [f"{m}（{name}）は core から届くのに判定が {per[m]}" for m in mine if m in reached.get("core", ()) and per[m] != "keep"]
    # 残すものが依るもの（import と、子プロセスで起こすもの）。keep でないものは、捨てる前に依り先を移す要がある
    kept = [m for m, o in owner.items() if verdict_of(comps[o], m) == "keep"]
    needed = reach(kept, root, follow_spawn=True)
    held = sorted(f"{m}（{owner[m]}・{verdict_of(comps[owner[m]], m)}）" for m in needed
                  if m in owner and verdict_of(comps[owner[m]], m) != "keep")
    return {"rows": rows, "problems": problems, "removed": cfg.get("removed") or {}, "held_by_keep": held}


def render_verdicts(rep):
    lines = ["| 部品 | 判定 | モジュール | 行 | 入口から届く | モジュールの判定 | 決めるタスク |", "|:--|:--|--:|--:|:--|:--|:--|"]
    for r in rep["rows"]:
        reached = "・".join(f"{k} {v}" for k, v in r["reached"].items())
        mixed = "・".join(f"{k} {v}" for k, v in r["by_verdict"].items())
        tasks = "・".join(f"{t}（{s}）" for t, s in r["tasks"].items()) or "—"
        lines.append(f"| {r['component']} | {r['verdict']} | {r['modules']} | {r['lines']} | {reached} | {mixed} | {tasks} |")
    if rep["removed"]:
        lines += ["", "消したモジュール：" + "・".join(rep["removed"])]
    lines += ["", f"決まりに反するもの：{rep['problems'] or 'なし'}"]
    held = rep.get("held_by_keep") or []
    lines += ["", f"残すもの（keep）が import か子プロセスで依るのに、keep でないもの（{len(held)}）："
              + ("・".join(held) if held else "なし")]
    return "\n".join(lines) + "\n"


def _lines(path):
    try:
        return len(Path(path).read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return 0


def tests_of(mods, root=ROOT):
    """部品のモジュールを名前で引く試験のファイル（tests/test_<名前>*.py）。"""
    stems = {Path(m).stem for m in mods if Path(m).stem != "__init__"}
    return sorted(p for p in (Path(root) / "tests").glob("test_*.py")
                  if any(p.stem == f"test_{s}" or p.stem.startswith(f"test_{s}_") for s in stems))


def git_log(since, root=ROOT, git=bench_start.git):
    """[(日付, 件名, [harness/ の下の変えたファイル])]（harness/ を触ったコミット、マージを除く）。"""
    text = git(root, ["log", f"--since={since}", "--no-merges", "--format=@@%ad%x09%s", "--date=short", "--name-only", "--",
                      "harness"]).decode("utf-8")
    out = []
    for line in text.splitlines():
        if line.startswith("@@"):
            day, _, subject = line[2:].partition("\t")
            out.append((day, subject, []))
        elif line.strip() and out and line.startswith("harness/"):
            out[-1][2].append(line.strip()[len("harness/"):])
    return out


def churn(log, owner):
    """{部品: {"commits", "fixes", "last"}}。fix は件名が fix で始まるもの。"""
    out = {}
    for day, subject, files in log:
        for comp in {owner[f] for f in files if f in owner}:
            s = out.setdefault(comp, {"commits": 0, "fixes": 0, "last": ""})
            s["commits"] += 1
            s["fixes"] += subject.startswith("fix")
            s["last"] = max(s["last"], day)
    return out


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def hline_outcome(queue):
    items = list(((queue or {}).get("items") or {}).values())
    if not items:
        return None
    status = {}
    for it in items:
        status[it.get("status")] = status.get(it.get("status"), 0) + 1
    totals = [hline_usage.totals(it) for it in items]
    return {"items": len(items), "status": status, "merged": sum(1 for it in items if it.get("merged")),
            "tries": sum(t[0] for t in totals), "tokens": sum(t[2] for t in totals),
            "tries_without_usage": sum(t[4] for t in totals)}


def director_outcome(out_dir, inbox, queue):
    runs = [d for d in map(_read_json, sorted(Path(out_dir, "director").glob("*.json"))) if isinstance(d, dict)]
    drafts = sorted(p.stem for p in Path(inbox, "drafts", "what").glob("*.md")) if Path(inbox, "drafts", "what").is_dir() else []
    tasks = {it.get("task") for it in ((queue or {}).get("items") or {}).values()}
    usage = [r.get("usage") or {} for r in runs if r.get("launched")]
    return {"launches": sum(1 for r in runs if r.get("launched")), "skipped": sum(1 for r in runs if not r.get("launched")),
            "read_chars": sum(r.get("total_chars") or 0 for r in runs if r.get("launched")),
            "output_tokens": sum(u.get("output_tokens") or 0 for u in usage),
            "cost_usd": round(sum(u.get("cost_usd") or 0 for u in usage), 2),
            "what_drafts": len(drafts), "what_drafts_queued": sum(1 for d in drafts if d in tasks)}


def factory_outcome(records):
    runs = weekly_report.factory_runs(records)
    return {"runs": len(runs), "last": max((d.isoformat() for d, _ in runs), default="")} if runs else None


def outer_outcome(queue):
    outer = (queue or {}).get("outer") or {}
    return {"awaiting_pr": outer.get("awaiting_pr"), "cycles": len(outer.get("cycles") or [])} if queue else None


def measure(since, root=ROOT, cfg=None, git=bench_start.git):
    comps = load(Path(root) / "config" / "arch_components.json")
    mods = modules(root)
    owner, unassigned, doubled = assign(comps, mods)
    log = churn(git_log(since, root, git), owner)
    cfg = cfg if cfg is not None else machine.read_json(Path(root) / "config" / "hline.json")
    queue = _read_json(Path(cfg["out"]) / "queue.json")
    runtime = {"hline": hline_outcome(queue), "director": director_outcome(cfg["out"], cfg["inbox"], queue) if queue else None,
               "factory": factory_outcome(Path(root) / "bench" / "records"), "outer": outer_outcome(queue)}
    rows = []
    for name, c in comps.items():
        mine = [m for m, o in owner.items() if o == name]
        tests = tests_of(mine, root)
        rows.append({"component": name, "title": c["title"], "modules": len(mine),
                     "code_lines": sum(_lines(Path(root) / "harness" / m) for m in mine),
                     "test_files": len(tests), "test_lines": sum(_lines(t) for t in tests),
                     **{k: (log.get(name) or {}).get(k, 0 if k != "last" else "") for k in ("commits", "fixes", "last")},
                     "runtime": runtime.get(name)})
    return {"since": since, "measured_at": datetime.date.today().isoformat(), "rows": rows,
            "unassigned": unassigned, "doubled": doubled}


def _runtime_cell(rt):
    if rt is None:
        return "記録なし"
    return "・".join(f"{k} {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}" for k, v in rt.items())


def render(rep):
    total = sum(r["code_lines"] for r in rep["rows"]) or 1
    lines = ["# 今の構成の実測（段 A3、W-142）", "",
             f"- `python -m harness.arch_inventory --since {rep['since']} --write …` が {rep['measured_at']} に作った。手で編集しない",
             f"- 直した回数：{rep['since']} 以降の、件名が fix で始まるコミット。部品の割り当ては config/arch_components.json",
             "", "| 部品 | モジュール | コード行 | 割合 | 試験ファイル | 試験行 | コミット | fix | 最後の変更 | 走行の記録 |",
             "|:--|--:|--:|--:|--:|--:|--:|--:|:--|:--|"]
    lines += [f"| {r['component']}（{r['title']}） | {r['modules']} | {r['code_lines']} | {100 * r['code_lines'] / total:.0f}% | "
              f"{r['test_files']} | {r['test_lines']} | {r['commits']} | {r['fixes']} | {r['last']} | {_runtime_cell(r['runtime'])} |"
              for r in rep["rows"]]
    if rep["unassigned"] or rep["doubled"]:
        lines += ["", f"- 部品の無いモジュール：{rep['unassigned']}", f"- 2 つ以上の部品に入るモジュール：{rep['doubled']}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="今の構成の部品ごとの費用と成果")
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--write", help="Markdown の書き出し先")
    ap.add_argument("--reach", action="store_true", help="部品ごとの判定と、入口から届くモジュールの数（W-136）")
    args = ap.parse_args(argv)
    if args.reach:
        vrep = verdicts_report()
        print(render_verdicts(vrep), end="")
        return 1 if vrep["problems"] else 0
    rep = measure(args.since)
    text = render(rep)
    if args.write:
        Path(args.write).write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 1 if rep["unassigned"] or rep["doubled"] else 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
