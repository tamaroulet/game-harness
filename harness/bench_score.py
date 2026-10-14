"""採点役：実装役に見えない判定（非公開オラクル）で、木を変えずに通過率を出す。
    python -m harness.bench_score score --task <id> --tree <dir> --out <json> [--oracles <dir>] [--timeout <秒>]
    python -m harness.bench_score hash --task <id> [--oracles <dir>]
終了コード: 0 = 採点した（通過率によらず）／指紋を出した、2 = 指紋が合わない・隠しテストが無い・引数が解せない。
隠しテストは `<oracles>/bench/<id>/hidden/` の下の test_hidden_*.py。指紋は hidden/ の全ファイルの（相対パス、ヌル、中身）の SHA-256。
"""
import argparse
import ast
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hline.json"
TIMEOUT = 600
MARK = "BENCH_SCORE_RESULT "

# 写しの中で、隠しテストだけを探して走らせる。作業ディレクトリ（写し）と tests/ を import の道に足す
RUNNER = '''import io, json, os, sys, unittest
sys.path.insert(0, os.getcwd())
class R(unittest.TextTestResult):
    ok = 0
    def addSuccess(self, test):
        super().addSuccess(test)
        self.ok += 1
suite = unittest.defaultTestLoader.discover("tests", pattern="test_hidden_*.py")
res = unittest.TextTestRunner(stream=io.StringIO(), resultclass=R, verbosity=0).run(suite)
print("%s" + json.dumps({"passed": res.ok, "total": res.testsRun}))
''' % MARK


def oracles_root(cfg_path=CONFIG):
    return Path(json.loads(Path(cfg_path).read_text(encoding="utf-8"))["oracles"])


def hidden_dir(root, task):
    return Path(root) / "bench" / task / "hidden"


def fingerprint(hidden):
    """hidden/ の全ファイルを、相対パス（/ 区切り）の文字の順に並べ、（相対パスの UTF-8、ヌル、中身）をつないだ SHA-256。"""
    base = Path(hidden)
    files = sorted(((p.relative_to(base).as_posix(), p) for p in base.rglob("*") if p.is_file()), key=lambda x: x[0])
    h = hashlib.sha256()
    for rel, p in files:
        h.update(rel.encode("utf-8") + b"\0" + p.read_bytes())
    return h.hexdigest()


def hidden_tests(hidden):
    return sorted(Path(hidden).rglob("test_hidden_*.py"))


def declared_count(files):
    """木を走らせずに数えた隠しテストの数（時間切れの total 用）。読めないファイルは 1 件と数える。"""
    total = 0
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            total += 1
            continue
        total += sum(1 for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                     for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
    return total


def expected_fingerprint(tree, task):
    p = Path(tree) / "docs" / "bench" / task / "oracle.sha256"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


def run_hidden(tree, files, timeout):
    """tree を一時ディレクトリに写し（.git は写さない）、写しの test_hidden_* を消し、隠しテストを置いて別のプロセスで走らせる。"""
    with tempfile.TemporaryDirectory(prefix="bench-score-") as tmp:
        copy = Path(tmp) / "tree"
        shutil.copytree(tree, copy, ignore=shutil.ignore_patterns(".git"))
        for stale in list(copy.rglob("test_hidden_*.py")):
            stale.unlink()
        (copy / "tests").mkdir(exist_ok=True)
        for f in files:
            shutil.copy2(f, copy / "tests" / f.name)
        runner = Path(tmp) / "runner.py"
        runner.write_text(RUNNER, encoding="utf-8")
        code, out, _ = proc.run([sys.executable, str(runner)], copy, timeout, "bench_score")
    if code == 124:
        return 0, declared_count(files), True
    for line in reversed(out.splitlines()):
        if line.startswith(MARK):
            r = json.loads(line[len(MARK):])
            return r["passed"], r["total"], False
    return 0, declared_count(files), False


def score(task, tree, out, oracles=None, timeout=TIMEOUT, err=None):
    err = err or sys.stderr
    hidden = hidden_dir(oracles or oracles_root(), task)
    if not hidden.is_dir():
        print(f"隠しテストの置き場がありません: {hidden}", file=err)
        return 2
    got, want = fingerprint(hidden), expected_fingerprint(tree, task)
    if got != want:
        print(f"非公開オラクルの指紋が docs/bench/{task}/oracle.sha256 と合いません: 実物 {got} / 記録 {want}", file=err)
        return 2
    files = hidden_tests(hidden)
    if not files or declared_count(files) == 0:
        print(f"隠しテストが 0 件です: {hidden}", file=err)
        return 2
    passed, total, timed_out = run_hidden(Path(tree), files, timeout)
    record = {"task": task, "fingerprint": got, "passed": passed, "total": total,
              "rate": passed / total if total else 0.0, "timed_out": timed_out}
    Path(out).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_score")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("score", "hash"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        p.add_argument("--oracles")
    sub.choices["score"].add_argument("--tree", required=True)
    sub.choices["score"].add_argument("--out", required=True)
    sub.choices["score"].add_argument("--timeout", type=float, default=TIMEOUT)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.cmd == "hash":
        hidden = hidden_dir(a.oracles or oracles_root(), a.task)
        if not hidden.is_dir():
            print(f"隠しテストの置き場がありません: {hidden}", file=sys.stderr)
            return 2
        print(fingerprint(hidden))
        return 0
    return score(a.task, a.tree, a.out, a.oracles, a.timeout)


if __name__ == "__main__":
    import exitcode  # noqa: E402
    sys.exit(exitcode.normalized(main))
