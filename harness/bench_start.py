"""ベンチの開始のコミット（W-44）：本体と公開の判定役を除き、計画役の台帳を入れた、親の無い単独のコミットを作る。

    python -m harness.bench_start --repo <clone> --base <rev> --task <id> [--ledger <ledger.yaml>]

<package> は <rev> の docs/bench/<id>/spec.md の「リポジトリの根の `<名前>` パッケージ」の <名前>。
木は <rev> の木から <package>/・tests/<package>_judge.py・tests/test_<package>* を除いたもの。
--ledger があれば docs/progress.yaml を、計画の監修のループの ledger の段と同じ欄を補った台帳に替える。
作るのはコミットのオブジェクトだけ（<clone> の作業ツリー・ブランチ・HEAD・索引は変えない）。
成功すれば完全な SHA を標準出力の最後の行に出して 0、台帳が progress.validate に落ちれば 1、入力の異常は 2。
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import progress  # noqa: E402

GROUP = "bench"
LEDGER_PATH = "docs/progress.yaml"
SPEC_PACKAGE = re.compile(r"リポジトリの根の\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*パッケージ")
TTL = 120


class BenchError(Exception):
    pass


def git(repo, args, env=None, data=None):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, env=env,
                           stdin=None if data is not None else subprocess.DEVNULL, timeout=TTL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BenchError(f"git {' '.join(args[:2])} を実行できません: {e}")
    if r.returncode != 0:
        raise BenchError(f"git {' '.join(args[:2])} が {r.returncode} で終わりました: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def package_of(repo, base, task):
    try:
        spec = git(repo, ["show", f"{base}:docs/bench/{task}/spec.md"]).decode("utf-8", "replace")
    except BenchError as e:
        raise BenchError(f"{base} に docs/bench/{task}/spec.md を読めません: {e}")
    m = SPEC_PACKAGE.search(spec)
    if not m:
        raise BenchError(f"docs/bench/{task}/spec.md に「リポジトリの根の `<名前>` パッケージ」がありません")
    return m.group(1)


def excluded(path, package):
    name = path.rsplit("/", 1)[-1]
    return (path.startswith(package + "/") or path == f"tests/{package}_judge.py"
            or (path.startswith("tests/") and path.count("/") == 1 and name.startswith(f"test_{package}")))


def ledger_text(path):
    """(台帳の本文, 問題の列)。問題があれば本文は None。"""
    try:
        tasks = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        tasks = None
    if not isinstance(tasks, list) or not all(isinstance(t, dict) and isinstance(t.get("id"), str) for t in tasks):
        return None, [f"{path} を台帳のタスクの列として読めません"]
    return tasks, []


def ledger_state(tasks, package):
    rows = [{**{"group": GROUP, "target_repo": package, "status": "pending"}, **t} for t in tasks]
    return {"project_goal": GROUP, "nodes": [{"id": GROUP, "title": GROUP, "parent": None}], "tasks": rows, "constraints": []}


def build(repo, base, task, ledger):
    package = package_of(repo, base, task)
    blob = None
    if ledger is not None:
        tasks, problems = ledger_text(ledger)
        if tasks is not None:
            state = ledger_state(tasks, package)
            problems = progress.validate(state)
        if problems:
            return None, problems
        blob = yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode("utf-8")
    listing = git(repo, ["ls-tree", "-r", "-z", "--full-tree", base]).split(b"\0")
    lines = []
    for entry in filter(None, listing):
        meta, _, raw = entry.partition(b"\t")
        path = raw.decode("utf-8", "surrogateescape")
        if excluded(path, package) or (blob is not None and path == LEDGER_PATH):
            continue
        mode, _kind, sha = meta.decode().split()
        lines.append(f"{mode} {sha}\t{path}\0")
    if blob is not None:
        sha = git(repo, ["hash-object", "-w", "--stdin"], data=blob).decode().strip()
        lines.append(f"100644 {sha}\t{LEDGER_PATH}\0")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index"),
               "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@localhost",
               "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@localhost"}
        git(repo, ["update-index", "--add", "-z", "--index-info"], env=env,
            data="".join(lines).encode("utf-8", "surrogateescape"))
        tree = git(repo, ["write-tree"], env=env).decode().strip()
        sha = git(repo, ["commit-tree", tree, "-m", f"bench start: {task}"], env=env).decode().strip()
    return sha, []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_start", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--ledger")
    args = ap.parse_args(argv)
    try:
        sha, problems = build(args.repo, args.base, args.task, args.ledger)
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    if problems:
        for p in problems:
            print(f"NG: {p}", file=sys.stderr)
        return 1
    print(sha)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
