"""記録の検め直し（W-47）：工廠の記録の点を信じず、走行の枝から採点し直し、人の介入 0 回を機械で数える。

    python -m harness.bench_verify (--task <id>... | --all) --min-runs <n> [--frozen] [--repo <clone>] [--out <dir>] [--oracles <dir>] [--package <dir>]

課題が合格：floor.json の passed が真でその指紋が今の非公開オラクルと同じ／数える記録が --min-runs 件以上／
数える記録のそれぞれで、走行の bundle から head の木を取り出して採点し直した passed・total が記録と同じ。
数える記録：invalid が偽で、queue_sha256 の値がすべて planner_sha256 の値にあり、replaced が 0 のもの（人の介入 0 回）。
violations のある記録は数えるが、採点し直しの比較から外す。--frozen は freeze.json の base・指紋に合い、
frozen_at より後に始まった記録だけを数える。--package <dir> は走行を <dir>/runs/ から読み、manifest.json の sha256 を照合する（REQ-36）。
終了コード：0 = すべて合格、1 = どれかが不合格、2 = 確かめられない（非公開オラクルの置き場・freeze.json が無いなど）。
"""
import argparse
import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_score  # noqa: E402
import exitcode  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class Unverifiable(Exception):
    pass


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def moment(text):
    try:
        t = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def all_tasks(repo):
    base = Path(repo) / "docs" / "bench"
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("B")) if base.is_dir() else []


def intervened(rec):
    """人の介入があったか：queue_sha256 に、計画役の出力に無い What がある、または置き直しがある。"""
    planner, queue = rec.get("planner_sha256"), rec.get("queue_sha256")
    if not isinstance(planner, dict) or not isinstance(queue, dict):
        return True
    replaced = rec.get("replaced")
    if isinstance(replaced, bool) or replaced != 0:
        return True
    return not set(queue.values()) <= set(planner.values())


def regrade(task, rec, rundir, oracles):
    """bundle から head の木を取り出して採点し直し、(passed, total) を返す。できなければ理由の文字を返す。"""
    bundle = rundir / "git" / "run.bundle"
    if not bundle.is_file():
        return f"bundle がありません: {bundle}"
    with tempfile.TemporaryDirectory(prefix="bench-verify-") as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        try:
            git(tree, ["init", "-q"])
            git(tree, ["fetch", "-q", str(bundle), "refs/heads/tip:refs/heads/tip"])
            git(tree, ["checkout", "-q", str(rec.get("head"))])
        except BenchError as e:
            return f"head の木を取り出せません: {e}"
        out = Path(tmp) / "score.json"
        sink = open(Path(tmp) / "err.txt", "w", encoding="utf-8")
        with sink:
            code = bench_score.score(task, tree, out, oracles, err=sink)
        if code != 0:
            return "採点し直せません（木の oracle.sha256 と非公開オラクルの指紋が合わない等）"
        got = json.loads(out.read_text(encoding="utf-8"))
    return got["passed"], got["total"]


def check_package(rundir, rec, name):
    """展開した束の manifest.json を照合する。食い違いの文の一覧。"""
    problems = []
    manifest = read_json(rundir / "manifest.json")
    if not isinstance(manifest, dict):
        return [f"{name}: manifest.json を読めません"]
    for rel, want in sorted(manifest.items()):
        path = rundir / rel
        if not path.is_file():
            problems.append(f"{name}: manifest.json のファイル {rel} がありません")
        elif sha256_file(path) != want:
            problems.append(f"{name}: {rel} の sha256 が manifest.json と違います")
    listed = set(manifest)
    for p in sorted(rundir.rglob("*")):
        rel = p.relative_to(rundir).as_posix()
        if p.is_file() and rel != "manifest.json" and rel not in listed:
            problems.append(f"{name}: {rel} が manifest.json にありません")
    if sha256_file(rundir / "manifest.json") != rec.get("manifest_sha256"):
        problems.append(f"{name}: manifest_sha256 が manifest.json の sha256 と違います")
    return problems


def verify_task(task, a, repo, runs_root, oracles, freeze):
    """(合格か, 数えた記録の数, 介入のあった記録の名前, 食い違いの一覧)。確かめられなければ Unverifiable。"""
    hidden = bench_score.hidden_dir(oracles, task)
    if not hidden.is_dir():
        raise Unverifiable(f"{task}: 非公開オラクルの置き場がありません: {hidden}")
    now_fp = bench_score.fingerprint(hidden)
    problems = []
    floor = read_json(repo / "bench" / "records" / task / "floor.json")
    if not isinstance(floor, dict) or floor.get("passed") is not True:
        problems.append("floor.json: passed が真ではありません（床の検査が無い・不合格）")
    elif floor.get("fingerprint") != now_fp:
        problems.append("floor.json: fingerprint が今の非公開オラクルと違います")
    if freeze is not None:
        frozen = freeze.get("tasks") if isinstance(freeze.get("tasks"), dict) else {}
        if frozen.get(task) != now_fp:
            problems.append("freeze.json: tasks の指紋が今の非公開オラクルと違います")
        frozen_at = moment(freeze.get("frozen_at"))
    counted, intervention = [], []
    for path in sorted((repo / "bench" / "records" / task).glob("factory-*.json")):
        rec = read_json(path)
        if not isinstance(rec, dict) or rec.get("invalid"):
            continue
        if freeze is not None:
            started = moment(rec.get("started_at"))
            if rec.get("base") != freeze.get("base") or frozen_at is None or started is None or started <= frozen_at:
                continue
        (intervention if intervened(rec) else counted).append((path.stem, rec))
    if len(counted) < a.min_runs:
        problems.append(f"数える記録が {len(counted)} 件で、--min-runs {a.min_runs} に足りません")
    for name, rec in counted:
        rundir = runs_root / task / name
        if a.package:
            problems += check_package(rundir, rec, name)
        if rec.get("violations"):
            continue
        got = regrade(task, rec, rundir, oracles)
        if isinstance(got, str):
            problems.append(f"{name}: {got}")
            continue
        for field, value in zip(("passed", "total"), got):
            if rec.get(field) != value:
                problems.append(f"{name}: {field} が記録 {rec.get(field)} ／ 採点し直し {value}")
    return not problems, len(counted), [name for name, _ in intervention], problems


def verify(a, out=None):
    out = out or sys.stdout
    repo = Path(a.repo) if a.repo else ROOT
    oracles = Path(a.oracles) if a.oracles else bench_score.oracles_root()
    runs_root = (Path(a.package) if a.package else Path(a.out) if a.out else repo / ".bench") / "runs"
    tasks = all_tasks(repo) if a.all else a.task
    if not tasks:
        print("課題がありません", file=sys.stderr)
        return 2
    freeze = None
    if a.frozen:
        freeze = read_json(repo / "bench" / "records" / "freeze.json")
        if not isinstance(freeze, dict):
            print("bench/records/freeze.json がありません", file=sys.stderr)
            return 2
    codes = []
    for task in tasks:
        try:
            ok, counted, humans, problems = verify_task(task, a, repo, runs_root, oracles, freeze)
        except Unverifiable as e:
            print(e, file=sys.stderr)
            codes.append(2)
            continue
        print(f"{task}: {'合格' if ok else '不合格'}（数えた記録 {counted} 件、人の介入のあった記録 {len(humans)} 件）", file=out)
        for name in humans:
            print(f"  人の介入 {task}/{name}", file=out)
        for p in problems:
            print(f"  食い違い {task}/{p}", file=out)
        codes.append(0 if ok else 1)
    return 2 if 2 in codes else 1 if 1 in codes else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_verify", description=__doc__.splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--task", nargs="+")
    which.add_argument("--all", action="store_true")
    ap.add_argument("--min-runs", type=int, required=True)
    ap.add_argument("--frozen", action="store_true")
    ap.add_argument("--repo")
    ap.add_argument("--out")
    ap.add_argument("--oracles")
    ap.add_argument("--package")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return verify(a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
