"""非劣性の検査（W-57）：<rev> の工廠で凍結の 10 本を 1 回ずつ走らせ、W-89 の基準の記録と比べて点が下がっていないかを決める。

    python -m harness.bench_regress --head <rev> --repo <clone> [--out <dir>] [--oracles <dir>] [--plans <dir>]
                                    [--implementer <名前>] [--records <dir>]

走らせ役は bench_run の stage（W-88）。<rev> は stage の --base に渡し、そのあと --finish で採点する。
判定は 10 本の合計の率（Σ合格 ÷ Σ試験）。課題を単位に復元抽出のブートストラップ（10,000 回・種は固定）を行い、
「新の合計の率 − 基準の合計の率」の 95% 区間の下限が −0.05 を超えれば合格。
基準は <records>/bench/records/<課題>/factory-base-1.json、新しい記録は <repo>/bench/records/<課題>/factory-<tag>-<n>.json。
結果は <records>/bench/records/regress/<rev>.json に書く。
終了コード：合格 0、不合格 1、走れなかった（無効の走行・基準の欠け・stage が 0 以外）は 2（合否は書かない）。
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_run  # noqa: E402
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE_TAG = "base"
ROUNDS = 10_000
SEED = 20261019
FLOOR = -0.05


class NotRun(Exception):
    """走れなかった（終了コード 2）。"""


def safe(rev):
    return re.sub(r"[^A-Za-z0-9._-]", "_", rev)


def tag_of(rev):
    return f"regress-{safe(rev)[:16]}"


def tasks_of(repo, rev):   # 試験が差し替える
    return bench_run.stage_tasks(repo, rev)


def run_stage(argv):   # 試験が差し替える
    return bench_run.main(["stage", *argv])


def records_dir(root, task):
    return Path(root) / "bench" / "records" / task


def load_record(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NotRun(f"記録を読めません: {path}（{e}）")
    if doc.get("invalid"):
        raise NotRun(f"無効の記録です: {path}")
    if not isinstance(doc.get("passed"), int) or not isinstance(doc.get("total"), int) or doc["total"] <= 0:
        raise NotRun(f"passed・total が不正です: {path}")
    return doc


def base_record(root, task):
    path = records_dir(root, task) / f"factory-{BASE_TAG}-1.json"
    if not path.is_file():
        raise NotRun(f"{task}: 基準の記録がありません: {path}")
    return path


def new_record(repo, task, tag):
    pat = re.compile(rf"factory-{re.escape(tag)}-(\d+)\.json")
    found = {int(m.group(1)): p for p in records_dir(repo, task).glob(f"factory-{tag}-*.json") if (m := pat.fullmatch(p.name))}
    if not found:
        raise NotRun(f"{task}: 新しい記録がありません（tag {tag}）")
    return found[max(found)]


def rate(rows, key):
    total = sum(r[key]["total"] for r in rows)
    return sum(r[key]["passed"] for r in rows) / total if total else None


def interval(rows, rounds=ROUNDS, seed=SEED):
    rng = random.Random(seed)
    diffs = []
    for _ in range(rounds):
        pick = [rows[rng.randrange(len(rows))] for _ in rows]
        new, base = rate(pick, "new"), rate(pick, "base")
        if new is not None and base is not None:
            diffs.append(new - base)
    if not diffs:
        raise NotRun("区間を計算できません")
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[min(len(diffs) - 1, int(0.975 * len(diffs)))]


def stage_argv(a, tag, finish):
    argv = ["--tag", tag, "--repo", a.repo, "--base", a.head]
    for flag, value in (("--out", a.out), ("--oracles", a.oracles), ("--plans", a.plans), ("--implementer", a.implementer)):
        if value:
            argv += [flag, value]
    return argv + (["--finish"] if finish else [])


def regress(a):
    tag = tag_of(a.head)
    tasks = tasks_of(a.repo, a.head)
    if not tasks:
        raise NotRun("課題がありません")
    bases = {t: load_record(base_record(a.records, t)) for t in tasks}
    base_names = {t: base_record(a.records, t).name for t in tasks}
    for finish in (False, True):
        code = run_stage(stage_argv(a, tag, finish))
        if code != 0:
            raise NotRun(f"stage{' --finish' if finish else ''} が終了コード {code}")
    paths = {t: new_record(a.repo, t, tag) for t in tasks}
    rows = []
    for t in tasks:
        new = load_record(paths[t])
        rows.append({"task": t, "new": {"passed": new["passed"], "total": new["total"]},
                     "base": {"passed": bases[t]["passed"], "total": bases[t]["total"]}})
    low, high = interval(rows)
    result = {"rev": a.head, "passed": low > FLOOR, "lower": low, "upper": high,
              "new_rate": rate(rows, "new"), "base_rate": rate(rows, "base"),
              "tasks": {r["task"]: {"new": r["new"], "base": r["base"]} for r in rows},
              "records": {t: {"new": paths[t].name, "base": base_names[t]} for t in tasks}}
    out = Path(a.records) / "bench" / "records" / "regress" / f"{safe(a.head)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{'合格' if result['passed'] else '不合格'}: 下限 {low:+.4f}（> {FLOOR} で合格）上限 {high:+.4f}"
          f" 新 {result['new_rate']:.4f} 基準 {result['base_rate']:.4f}")
    return 0 if result["passed"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_regress", description=__doc__.splitlines()[0])
    ap.add_argument("--head", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out")
    ap.add_argument("--oracles")
    ap.add_argument("--plans")
    ap.add_argument("--implementer")
    ap.add_argument("--records", default=str(ROOT))
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        return regress(a)
    except NotRun as e:
        print(f"走れませんでした: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
