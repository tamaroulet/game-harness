"""全件テストを、モジュールごとに別のプロセスで並列に走らせる（H ライン Gate 1 の gate_command）。
    python -m harness.fastsuite discover -s tests [-j N]  /  python -m harness.fastsuite tests.test_a tests.test_b [-j N]
終了コード: 0 = 全部通った、1 = 1 件以上落ちた、2 = 引数が解せない・テストが無い。子プロセスは一時ディレクトリを共有しない。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

_RAN = re.compile(r"\bRan (\d+) tests?\b")
_MODULE = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")


def discover(start_dir):
    """start_dir の直下の test_*.py を `<ディレクトリ名>.<stem>` に直す（import しない。名前の順）。"""
    d = Path(start_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"テストのディレクトリがありません: {d}")
    return tuple(sorted({f"{d.resolve().name}.{p.stem}" for p in d.glob("test_*.py")}))


def module_command(module):
    return [sys.executable, "-m", "unittest", module]


def ran_count(output):
    return sum(int(n) for n in _RAN.findall(output or ""))


def run_module(module, root, worker, timeout):
    """1 モジュールを cwd=root の別プロセスで走らせる。時間切れは子を殺して code 124。例外は外に出さない。"""
    tmp = tempfile.mkdtemp(prefix=f"fastsuite-{worker}-")
    env = dict(os.environ, PYTHONUTF8="1", TMP=tmp, TEMP=tmp, TMPDIR=tmp, HARNESS_TEST_WORKER=str(worker))
    log, started = Path(tmp) / "output.log", time.monotonic()
    try:
        try:
            with open(log, "wb") as f:
                code = subprocess.run(module_command(module), cwd=str(root), env=env, stdout=f, stderr=subprocess.STDOUT,
                                      timeout=timeout).returncode
            note = ""
        except subprocess.TimeoutExpired:
            code, note = 124, f"\n{module} は {timeout} 秒で時間切れになり、強制終了しました\n"
        except OSError as e:
            code, note = 127, f"\n{module} を起動できません: {e}\n"
        output = (log.read_bytes().decode("utf-8", "replace") if log.is_file() else "") + note
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"module": module, "code": code, "output": output, "ran": ran_count(output), "seconds": time.monotonic() - started}


def _weight(module, root):
    try:
        return (Path(root) / (module.replace(".", "/") + ".py")).stat().st_size
    except OSError:
        return 0


def run(modules, jobs=None, root=".", timeout=None):
    """modules を jobs 個までの子プロセスで同時に走らせる。戻りは完了の順に依らず、modules の並び。"""
    jobs = max(1, os.cpu_count() or 1) if jobs is None else jobs
    workers = Queue()
    for n in range(jobs):
        workers.put(n)

    def one(module):
        worker = workers.get()
        try:
            return run_module(module, root, worker, timeout)
        finally:
            workers.put(worker)

    order = sorted(range(len(modules)), key=lambda i: -_weight(modules[i], root))   # 大きいファイルから始める（壁時計は遅いモジュールで決まる）
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {i: pool.submit(one, modules[i]) for i in order}
        return [futures[i].result() for i in range(len(modules))]


def report(results, seconds):
    """(終了コード, 本文)。落ちたモジュールの出力を丸ごと入れ、総数・落ちた一覧・秒数を足す。"""
    failed = [r for r in results if r["code"] != 0]
    parts = [f"===== 失敗: {r['module']}（終了コード {r['code']}）=====\n{r['output']}" for r in failed]
    parts += [f"走ったテスト: {sum(r['ran'] for r in results)} 件（{len(results)} モジュール）",
              "落ちたモジュール: " + (", ".join(r["module"] for r in failed) or "なし"), f"壁時計: {seconds:.1f} 秒"]
    return (1 if failed else 0), "\n".join(parts)


def _parse(argv):
    """(モジュール一覧 or None, jobs or None, 解せたか)。"""
    args, jobs = list(argv), None
    if "-j" in args:
        i = args.index("-j")
        if i + 1 >= len(args) or not args[i + 1].isdigit() or int(args[i + 1]) < 1:
            return None, None, False
        jobs = int(args[i + 1])
        del args[i:i + 2]
    if len(args) == 3 and args[:2] == ["discover", "-s"]:
        return args[2], jobs, True
    ok = bool(args) and args[0] != "discover" and all(_MODULE.fullmatch(a) for a in args)
    return (list(dict.fromkeys(args)) if ok else None), jobs, ok


def main(argv=None):
    target, jobs, ok = _parse(sys.argv[1:] if argv is None else argv)
    if not ok:
        print("使い方: python -m harness.fastsuite (discover -s <ディレクトリ> | <モジュール名>...) [-j <数>]", file=sys.stderr)
        return 2
    try:
        modules = discover(target) if isinstance(target, str) else tuple(target)
        if not modules:
            raise FileNotFoundError("テストが 1 件も見つかりません")
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    started = time.monotonic()
    code, body = report(run(modules, jobs, Path.cwd()), time.monotonic() - started)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(body)
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import exitcode  # noqa: E402
    sys.exit(exitcode.normalized(main))
