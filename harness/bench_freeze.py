"""凍結の記録：非劣性の物差し（10 本の課題・非公開オラクル・土台）を、後から変わっていないと確かめられる形で書き残す。
    python -m harness.bench_freeze --freeze [--oracles <dir>] [--repo <dir>]
    python -m harness.bench_freeze --check [--oracles <dir>] [--repo <dir>]
10 本は docs/bench/ の直下の B で始まるディレクトリ。課題が「合う」は、bench/records/<id>/floor.json が passed 真、
今の指紋が floor.json の fingerprint と docs/bench/<id>/oracle.sha256 の両方と同じこと。
--freeze：10 本がすべて合えば bench/records/freeze.json（base・tasks・frozen_at・cli_version・model）を書いて 0。
合わない課題があれば、書かずに課題と理由を出して 1。--check：freeze.json の指紋が今と同じで 10 本が合えば 0、違えば 1。
置き場が読めない・claude --version が 0 で終わらない・git の HEAD が引けないときは環境の異常で 2。
"""
import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_score  # noqa: E402
import proc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = 10
TTL = 60


class Env(Exception):
    """環境の異常（終了コード 2）。"""


def task_ids(repo):
    base = Path(repo) / "docs" / "bench"
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("B")) if base.is_dir() else []


def freeze_path(repo):
    return Path(repo) / "bench" / "records" / "freeze.json"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_fingerprints(root, tasks):
    out = {}
    for t in tasks:
        hidden = bench_score.hidden_dir(root, t)
        if not hidden.is_dir():
            raise Env(f"隠しテストの置き場がありません: {hidden}")
        try:
            out[t] = bench_score.fingerprint(hidden)
        except OSError as e:
            raise Env(f"隠しテストを読めません: {hidden}: {e}")
    return out


def misfits(repo, tasks, prints):
    """合わない課題 → 理由。"""
    bad = {}
    if len(tasks) != EXPECTED:
        bad["(課題の数)"] = f"docs/bench/ の B* が {len(tasks)} 本（{EXPECTED} 本が要ります）"
    for t in tasks:
        floor = read_json(Path(repo) / "bench" / "records" / t / "floor.json")
        pinned = bench_score.expected_fingerprint(repo, t)
        if not isinstance(floor, dict):
            bad[t] = "floor.json がありません"
        elif floor.get("passed") is not True:
            bad[t] = "floor.json の passed が真ではありません"
        elif floor.get("fingerprint") != prints[t]:
            bad[t] = "指紋が floor.json の fingerprint と違います"
        elif pinned != prints[t]:
            bad[t] = "指紋が oracle.sha256 と違います"
    return bad


def cli_version():
    exe = shutil.which("claude.cmd") or shutil.which("claude.exe") or shutil.which("claude")
    if not exe:
        raise Env("claude CLI が PATH にありません")
    code, out, err = proc.run([exe, "--version"], ROOT, TTL, "claude --version")
    if code != 0:
        raise Env(f"claude --version が {code} で終わりました: {err.strip()}")
    return out.strip()


def model_id():
    cfg = json.loads(bench_score.CONFIG.read_text(encoding="utf-8"))
    return cfg["implementer"]["model"]


def implementer_cli_version(repo=ROOT):
    cfg_path = Path(repo) / "config" / "bench_implementer_agy.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "version" in cfg:
                return str(cfg["version"])
        except (OSError, ValueError):
            pass
    return "1.2.14"


def implementer_model_id(repo=ROOT):
    cfg_path = Path(repo) / "config" / "bench_implementer_agy.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "model" in cfg:
                return str(cfg["model"])
        except (OSError, ValueError):
            pass
    return "gemini-3.8-flash-medium"


def head_sha(repo):
    code, out, err = proc.run(["git", "rev-parse", "HEAD"], repo, TTL, "git rev-parse HEAD")
    if code != 0 or not out.strip():
        raise Env(f"git の HEAD を引けません: {err.strip()}")
    return out.strip()


def report(bad, err):
    for t, why in bad.items():
        print(f"合いません: {t}: {why}", file=err)


def freeze(repo, root, err):
    tasks = task_ids(repo)
    prints = current_fingerprints(root, tasks)
    bad = misfits(repo, tasks, prints)
    if bad:
        report(bad, err)
        return 1
    record = {"base": head_sha(repo), "tasks": prints,
              "frozen_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "cli_version": cli_version(), "model": model_id(),
              "implementer_cli_version": implementer_cli_version(repo),
              "implementer_model": implementer_model_id(repo)}
    out = freeze_path(repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def check(repo, root, err):
    tasks = task_ids(repo)
    prints = current_fingerprints(root, tasks)
    rec = read_json(freeze_path(repo))
    if not isinstance(rec, dict) or not isinstance(rec.get("tasks"), dict):
        print(f"freeze.json がありません（または読めません）: {freeze_path(repo)}", file=err)
        return 1
    bad = {}
    for t in sorted(set(rec["tasks"]) | set(prints)):
        if t not in rec["tasks"]:
            bad[t] = "freeze.json に記録がありません"
        elif t not in prints:
            bad[t] = "docs/bench/ から無くなっています"
        elif rec["tasks"][t] != prints[t]:
            bad[t] = "指紋が freeze.json の記録と違います"
    for t, why in misfits(repo, tasks, prints).items():
        bad.setdefault(t, why)
    report(bad, err)
    return 1 if bad else 0


def main(argv=None, err=None):
    err = err or sys.stderr
    ap = argparse.ArgumentParser(prog="harness.bench_freeze")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--check", action="store_true")
    ap.add_argument("--oracles")
    ap.add_argument("--repo", default=str(ROOT))
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        root = Path(a.oracles) if a.oracles else bench_score.oracles_root()
        if not root.is_dir():
            raise Env(f"非公開オラクルの置き場がありません: {root}")
        return (freeze if a.freeze else check)(Path(a.repo), root, err)
    except Env as e:
        print(str(e), file=err)
        return 2
    except (OSError, KeyError, ValueError) as e:
        print(f"環境の異常: {e}", file=err)
        return 2


if __name__ == "__main__":
    import exitcode  # noqa: E402
    sys.exit(exitcode.normalized(main))
