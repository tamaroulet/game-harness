"""A/B 実験の測定器（docs/design/b4_ab_experiment.md §6）。両条件に同じものを当てる。

作業ツリーを読むだけで、実装を直さない。結果は数だけを返し、生のログは出力先のファイルに残す
（実装役にも総監督にも渡さない。条件 A の再試行テンプレートに埋める失敗の末尾だけは例外で、
これはドライバが実装役に渡す）。
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import invgen  # noqa: E402
import invrun  # noqa: E402
import testgen  # noqa: E402
from adapters import dotnet  # noqa: E402
from proc import resolve_cli, run  # noqa: E402

FAST_TTL = 600
DIR_RE = re.compile(r" dir=(DEFICIT|SURPLUS)$")


def class_to_task(m, units):
    """生成テストのクラス名 → タスク ID。"""
    return {testgen.class_name(u): t["id"] for t, u in zip(m["tasks"], units)}


def task_of(test_name, classes):
    """テスト名（名前空間.クラス.メソッド）の属するタスク。実験の外のテスト（既存）は None。"""
    parts = test_name.split(".")
    return classes.get(parts[-2]) if len(parts) >= 2 else None


def place_frozen_tests(m, wt, upto, test_dir):
    """T1〜T<upto> の凍結した生成テストを作業ツリーに置く。置く前に書き換えられていたファイルの一覧を返す。"""
    tampered = []
    dst_dir = Path(wt) / test_dir
    dst_dir.mkdir(parents=True, exist_ok=True)
    for t in m["tasks"][:upto]:
        for name in t["tests_sha256"]:
            src, dst = Path(m["_base"]) / t["tests"] / name, dst_dir / name
            if dst.exists() and dst.read_bytes() != src.read_bytes():
                tampered.append(name)
            shutil.copyfile(src, dst)
    return tampered


def run_fast(wt, test_project, out, tag, runner=run):
    """高速検査の全件。({テスト名: 結果} か None, 出力の本文)。None はビルドが通らなかった。"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    trx = out / f"{tag}.trx"
    if trx.exists():
        trx.unlink()
    rc, stdout, stderr = runner(resolve_cli("dotnet") + [
        "test", test_project, "--nologo", "/nr:false", "--logger", f"trx;LogFileName={tag}.trx",
        "--results-directory", str(out)], wt, FAST_TTL, f"dotnet test ({tag})")
    text = (stdout or "") + ("\n" + stderr if stderr else "")
    (out / f"{tag}.dotnet.log").write_text(text, encoding="utf-8")
    if not trx.exists():
        return None, text
    results, _ = dotnet.parse_results(trx)
    return results, text


def passing(results):
    return {n for n, o in (results or {}).items() if o == dotnet.PASSED}


def p2p_broken(prev_passing, results, superseded=()):
    """前の測定で通っていて、今は通らない（消えたものも含む）テストの一覧。仕様どおりに変わるものは除く。"""
    now = passing(results)
    return sorted(n for n in prev_passing if n not in now and n.split(".")[-1] not in set(superseded))


def acceptance(results, classes, task_id):
    """あるタスクの生成テストの (合格数, 総数)。ビルドが通らなければ (0, None)。"""
    if results is None:
        return 0, None
    mine = [o for n, o in results.items() if task_of(n, classes) == task_id]
    return sum(o == dotnet.PASSED for o in mine), len(mine)


def invariants(wt, out, seeds, impl_dir, decl_path, runner=None):
    """保存則などの不変条件を、固定のシードで回す。{"build_ok", "failures", "dir"}。"""
    decl = json.loads(Path(decl_path).read_text(encoding="utf-8"))
    spec = (Path(wt) / "docs/spec/spec.md").read_text(encoding="utf-8")
    gdd = (Path(wt) / "docs/gdd/source.md").read_text(encoding="utf-8")
    files = invgen.generate(decl, spec, gdd)
    csproj = invrun.materialize(Path(out) / "invariants", wt, impl_dir, files)
    lines, build_ok = [], True
    for seed in seeds:
        if runner is None:
            env = dict(os.environ, **{invgen.SEED_ENV: str(seed)})
            rc, o, e = run(resolve_cli("dotnet") + ["test", str(csproj), "-nologo"], csproj.parent,
                           FAST_TTL, "dotnet test (ab invariants)", env=env)
        else:
            rc, o, e = runner(seed)
        found = invrun.parse_failures((o or "") + "\n" + (e or ""))
        if rc != 0 and not found:
            build_ok = False
        lines += found
    dirs = {"DEFICIT": 0, "SURPLUS": 0}
    for line in lines:
        mm = DIR_RE.search(line)
        if mm:
            dirs[mm.group(1)] += 1
    return {"build_ok": build_ok, "failures": len(lines), "dir": dirs}
