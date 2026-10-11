"""H ライン：Gate 1 の後、統合ブランチに積む前に、変えたパッケージの全件テストを走らせる（G0-2）。
Gate 1 の影響テストの網の外で壊れた変更を積まないための二重の網。全体ではなく、変えたパッケージの組に限る。
  パッケージ P の .py、または P の組に属するテスト → tests/test_<P>.py と tests/test_<P>_*.py のすべて
  harness/・config/ の下、またはどのパッケージにも属さない tests/test_*.py → どのパッケージにも属さない tests/test_*.py のすべて
  どれにも当たらない（文書だけなど）→ 走らせない。複数に当たれば組の和。"""
import time
from pathlib import Path

import fastsuite

NOT_PACKAGE = ("harness", "tests")
OUTSIDE_DIRS = ("harness", "config")


def packages(wt):
    """作業ツリーの根の直下で __init__.py を持つディレクトリの名前（harness と tests を除く）。"""
    return tuple(sorted(d.name for d in Path(wt).iterdir() if d.is_dir() and d.name not in NOT_PACKAGE and (d / "__init__.py").is_file()))


def _belongs(stem, package):
    return stem == f"test_{package}" or stem.startswith(f"test_{package}_")


def _tests(wt):
    d = Path(wt) / "tests"
    return sorted(p.stem for p in d.glob("test_*.py")) if d.is_dir() else []


def select(wt, paths):
    """変えたパスから、走らせるテストのモジュール名（tests.test_x）を、名前の順・重複なしで。"""
    if not Path(wt).is_dir():
        return ()
    pkgs, stems = packages(wt), _tests(wt)
    owned = {s: [p for p in pkgs if _belongs(s, p)] for s in stems}
    wanted, outside = set(), False
    for raw in paths:
        parts = str(raw).replace("\\", "/").split("/")
        if parts[0] in pkgs and parts[-1].endswith(".py") and len(parts) > 1:
            wanted.add(parts[0])
        elif parts[0] in OUTSIDE_DIRS and len(parts) > 1:
            outside = True
        elif len(parts) == 2 and parts[0] == "tests" and parts[1].startswith("test_") and parts[1].endswith(".py"):
            if owned_by := owned.get(parts[1][:-3]):
                wanted.update(owned_by)
            else:
                outside = True
    chosen = {s for s in stems if (outside and not owned[s]) or any(p in wanted for p in owned[s])}
    return tuple(f"tests.{s}" for s in sorted(chosen))


def run(wt, paths, timeout=None):
    """None（走らせるものが無い）か (通ったか, 実装役に返す出力, 試行の記録に残す {"modules", "seconds"})。"""
    modules = select(wt, paths)
    if not modules:
        return None
    started = time.monotonic()
    results, flaky = fastsuite.recheck(fastsuite.run(modules, None, wt, timeout), wt, timeout)
    seconds = time.monotonic() - started
    code, body = fastsuite.report(results, seconds, flaky)
    head = "変えたパッケージの全件テスト: " + ", ".join(modules)
    return code == 0, f"{head}\n{body}", {"modules": list(modules), "seconds": round(seconds, 1)}
