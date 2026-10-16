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
import project  # noqa: E402
import testgen  # noqa: E402
from ab import common  # noqa: E402
from adapters import dotnet, unity  # noqa: E402
from proc import resolve_cli, run  # noqa: E402

FAST_TTL = 600
HIDDEN_SUFFIX = "_Hidden"
DIR_RE = re.compile(r" dir=(DEFICIT|SURPLUS)$")


def class_to_task(m, units):
    """生成テストのクラス名 → タスク ID。v2 は性質テストのクラス（Properties<タスク>Cases）。"""
    if common.is_v2(m):
        return {f"Properties{t['id']}Cases": t["id"] for t in m["tasks"]}
    return {testgen.class_name(u): t["id"] for t, u in zip(m["tasks"], units)}


def task_of(test_name, classes):
    """テスト名（名前空間.クラス.メソッド）の属するタスク。実験の外のテスト（既存）は None。"""
    parts = test_name.split(".")
    return classes.get(parts[-2]) if len(parts) >= 2 else None


def place_frozen_tests(m, wt, upto, test_dir):
    """T1〜T<upto> の凍結した生成テストを作業ツリーに置く。置く前に書き換えられていたファイルの一覧を返す。"""
    if common.is_v2(m):
        return place_generated(m, wt, upto)
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


def place_generated(m, wt, upto, project_id="falling-blocks"):
    """v2：契約・探針・参照モデル・T1〜T<upto> の性質テストを置く（v2 §3.4）。書き換えられていたものの一覧を返す。

    Unity のアセットの下に置くもの（契約）には、.meta も決定論的に作って置く。無いと、エンジンの検査で
    Unity が作った .meta が「許可外の変更」になる。
    """
    assets = project.load(project_id)["unity_project_subdir"] + "/Assets/"
    files = dict(common.v2_files(m, upto, project_id))
    for rel in list(files):
        if rel.startswith(assets):
            files[rel + ".meta"] = unity.generated_meta(rel + ".meta")
    tampered = []
    for rel, text in sorted(files.items()):
        dst = Path(wt) / rel
        body = text.encode("utf-8")
        if dst.exists() and dst.read_bytes() != body:
            tampered.append(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(body)
    return tampered


def run_fast(wt, test_project, out, tag, runner=run, env=None):
    """高速検査の全件。({テスト名: 結果} か None, 出力の本文)。None はビルドが通らなかった。

    env は、性質テストの非公開シード（propgen.HIDDEN_ENV）を渡すときだけ。
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    trx = out / f"{tag}.trx"
    if trx.exists():
        trx.unlink()
    rc, stdout, stderr = runner(resolve_cli("dotnet") + [
        "test", test_project, "--nologo", "/nr:false", "--logger", f"trx;LogFileName={tag}.trx",
        "--results-directory", str(out)], wt, FAST_TTL, f"dotnet test ({tag})", **({"env": env} if env else {}))
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


def acceptance(results, classes, task_id, public_only=False):
    """あるタスクの生成テストの (合格数, 総数)。ビルドが通らなければ (0, None)。

    public_only：性質テストの非公開シード版（*_Hidden）を数えない。非公開シードを渡さずに走らせた回
    （条件 A の再試行の判定）では、それらは Ignore になっていて合否を持たないため。
    """
    if results is None:
        return 0, None
    mine = [o for n, o in results.items() if task_of(n, classes) == task_id
            and not (public_only and n.endswith(HIDDEN_SUFFIX))]
    return sum(o == dotnet.PASSED for o in mine), len(mine)


def invariants(wt, out, seeds, impl_dir, decl_path, runner=None):
    """保存則などの不変条件を、固定のシードで回す。{"build_ok", "failures", "dir"}。"""
    decl = json.loads(Path(decl_path).read_text(encoding="utf-8"))
    spec = (Path(wt) / "docs/spec/spec.md").read_text(encoding="utf-8")
    gdd = (Path(wt) / "docs/gdd/source.md").read_text(encoding="utf-8")
    files = invgen.generate(decl, spec, gdd)
    csproj = invrun.materialize(Path(out) / "invariants", wt, impl_dir, files)
    lines, build_ok = [], True
    for k, seed in enumerate(seeds):
        if runner is None:
            env = dict(os.environ, **{invgen.SEED_ENV: str(seed)})
            # シードは環境変数で渡すだけで、成果物は同じ。2 つ目からはビルドを省く
            no_build = ["--no-build"] if k and build_ok else []
            rc, o, e = run(resolve_cli("dotnet") + ["test", str(csproj), "-nologo"] + no_build, csproj.parent,
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
