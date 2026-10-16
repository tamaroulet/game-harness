"""計画の監修のループ（W-43）：計画役 → 確かめ（6 つの段）→ 落ちた項目だけを計画役に返す。差し戻しは 2 回まで。

    python -m harness.plan_loop --spec <spec.md> --package <名前> --inbox <dir> [--config <path>]

段：planner・plan_check・reqcov・ledger・what・review（review は、前の 5 つがすべて通ったときだけ）。
すべて通れば確定で終了コード 0、3 回目でも落ちれば 1。環境の異常（計画役の Infra・モデルの照合の失敗・
監修役の Unavailable・仕様を読めない）は回に数えず 2。<dir>/planner.json の鍵 loop に結果を書く（ほかの鍵は消さない）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import hline_queue  # noqa: E402
import hline_review  # noqa: E402
import model_pin  # noqa: E402
import plan_check  # noqa: E402
import planner  # noqa: E402
import progress  # noqa: E402
import reqcov  # noqa: E402

MAX_ROUNDS = 3
GROUP = "bench"


def item(stage, target, reason):
    return {"stage": stage, "target": target, "reason": reason}


def whats(inbox):
    return planner.what_files(inbox)


def stage_plan_check(inbox):
    found = plan_check.check(inbox)
    if Path(inbox).is_dir():
        plan_check.record(inbox, found)
    return [item("plan_check", t, r) for r, t in found]


def stage_reqcov(inbox, defined):
    seen, out = set(), []
    for p in whats(inbox):
        ids = reqcov.ids_in(p, "whats")
        seen |= ids
        out += [item("reqcov", p.stem, f"仕様に無い要件 ID {i} が出ています") for i in sorted(ids - set(defined))]
    out += [item("reqcov", None, f"要件 {i} が、どの What の `要件:` の行にも出ていません") for i in defined if i not in seen]
    return out


def verification_problem(task):
    """検証コマンドが、python -m unittest で始まる 1 つの命令で、oracle の各 path をモジュールの形で名指すか。理由か None。"""
    v = task.get("verification")
    command = v.get("command") if isinstance(v, dict) else None
    if not isinstance(command, str) or not command.startswith("python -m unittest") or re.search(r"&&|;|\|", command):
        return "verification.command が、`python -m unittest` で始まる 1 つの命令ではありません"
    names = command.split()[3:]
    for o in task.get("oracle") or []:
        path = o.get("path") if isinstance(o, dict) else o
        mod = str(path).replace("\\", "/").removesuffix(".py").replace("/", ".")
        if not any(n == mod or n.startswith(mod + ".") for n in names):
            return f"verification.command が oracle の {path} を {mod} の形で名指していません"
    return None


def stage_ledger(inbox, package):
    tasks = plan_check.load(inbox)
    if tasks is None:
        return [item("ledger", None, "ledger.yaml を読めません")]
    rows = [{**{"group": GROUP, "target_repo": package, "status": "pending"}, **t} for t in tasks]
    state = {"project_goal": "bench", "nodes": [{"id": GROUP, "parent": None}], "tasks": rows, "constraints": []}
    out = []
    for p in progress.validate(state):
        m = re.search(r"task (P-\d+)", p)
        out.append(item("ledger", m.group(1) if m else None, p))
    for t in tasks:
        if (why := verification_problem(t)):
            out.append(item("ledger", t["id"], why))
    return out


def stage_what(inbox):
    out = []
    for p in whats(inbox):
        text = p.read_text(encoding="utf-8", errors="replace")
        h = hline_queue.parse_header(text)
        if h["milestone"] != GROUP:
            out.append(item("what", p.stem, f"マイルストーンが {GROUP} ではありません"))
        if h["task"] != p.stem:
            out.append(item("what", p.stem, f"タスクの行が {p.stem} ではありません"))
        if h["verify"] is not None:
            out.append(item("what", p.stem, "`検証:` の行があります"))
        if (why := hline_queue.shape_problem(text)):
            out.append(item("what", p.stem, why))
    return out


def stage_review(cfg, inbox):
    tasks = plan_check.load(inbox) or []
    ledger = {t["id"]: {"row": t} for t in tasks}
    instr = hline_review.instruction(cfg)
    out = []
    for p in whats(inbox):
        row = ledger.get(p.stem, {}).get("row") or {}
        result = hline_review.decide(cfg, instr, p.read_text(encoding="utf-8", errors="replace"), p.stem, row,
                                     hline_review.rung_peers(ledger, p.stem))
        if result["verdict"] != "pass":
            out.append(item("review", p.stem, hline_review.reason_of(result)))
    return out


def verify(cfg, spec, package, inbox, problems):
    """1 回の確かめ。落ちた項目の列（通れば空）。"""
    defined = reqcov.definitions(spec)
    failed = [item("planner", None, p) for p in problems]
    failed += stage_plan_check(inbox)
    failed += stage_reqcov(inbox, defined)
    failed += stage_ledger(inbox, package)
    failed += stage_what(inbox)
    return failed or stage_review(cfg, inbox)


def write_loop(inbox, loop):
    path = Path(inbox) / planner.RESULT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["loop"] = loop
    Path(inbox).mkdir(parents=True, exist_ok=True)
    hline_base.write_json(path, data)


def run_loop(cfg, spec, package, inbox):
    """終了コードを返す。"""
    rounds, failed = 0, []
    try:
        reqcov.definitions(spec)
        while rounds < MAX_ROUNDS:
            problems = planner.plan(cfg, spec, package, inbox, feedback=failed or None)
            rounds += 1
            failed = verify(cfg, spec, package, inbox, problems)
            if not failed:
                break
    except (hline_base.Infra, model_pin.ModelPinError, hline_review.Unavailable, reqcov.InputError, OSError) as e:
        write_loop(inbox, {"rounds": rounds, "confirmed": False, "failed": [], "error": str(e)})
        print(f"ABORT: {e}")
        return 2
    write_loop(inbox, {"rounds": rounds, "confirmed": not failed, "failed": failed})
    for f in failed:
        print(f"NG: {f['stage']}（{f['target'] or '全体'}）: {f['reason']}")
    print("確定" if not failed else f"不合格（{len(failed)} 件、{rounds} 回）")
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.plan_loop", description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--config", default=str(hline_base.CONFIG))
    args = ap.parse_args(argv)
    try:
        cfg = hline_base.load_config(args.config)
        model_pin.require(cfg["planner"], "設定の planner")
        model_pin.auxiliary_prefixes(cfg["planner"], "設定の planner")
    except (OSError, ValueError, KeyError, model_pin.ModelPinError) as e:
        print(f"ABORT: {e}")
        return 2
    return run_loop(cfg, args.spec, args.package, args.inbox)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
