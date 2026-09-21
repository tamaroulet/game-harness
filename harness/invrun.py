"""不変条件テストの Outer 段（docs/design/mechanical_barriers.md §4、ADR-002 手順 5b）。

**置き場所**: 不変条件テストは、ゲームのリポジトリにもサンドボックスにも置かない。
- 宣言：harness の projects/<id>/invariants.json（実装役の作業ツリーに現れない）
- 生成物と csproj：pipeline の出力先 out_dir/invariants/ に毎回作る
- ビルド：サンドボックスの実装（impl_dir）を外から Compile Include で参照する

実装役は、見えないテストに合わせて書くことができない（§4.2 の 1）。

**シード**: 候補の差分（whitelist の git diff）の sha256 から決める。同じ差分なら同じ判定、
試行で差分が変われば別のシードになる。反例だけを特別扱いした実装は、次の試行の別シードで落ちる（§4.2 の 3）。

**返すもの**: 破れたら、生成テストが出す `INVARIANT_FAIL …` の行だけを返す。
生ログは出力先に残すが、実装役にも総監督にも渡さない。
ビルドできない・行が読めないのに失敗した場合は、実装役には直せないので ABORT にする。
"""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import invgen
import project
import unit_schema
from proc import resolve_cli, run

FAIL_LINE_RE = re.compile(r"INVARIANT_FAIL id=INV-\d+ rule=[A-Z]{2}-\d+ seed=\d+(?: why=[a-z]+)? ops=[^\s]*")
CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>{tf}</TargetFramework>
    <LangVersion>{lang}</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <IsPackable>false</IsPackable>
    <NoWarn>CS0436</NoWarn>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{impl}" />
  </ItemGroup>
  <ItemGroup>
{packages}
  </ItemGroup>
</Project>
"""


def decl_path(project_id, cfg=None):
    cfg = cfg or project.config("invgen")
    return project.ROOT / "projects" / project_id / cfg["decl_filename"]


def seed_for(diff_text):
    """差分から 1 以上の uint を決める。"""
    return int(hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:8], 16) or 1


def parse_failures(output):
    """dotnet test の出力から反例の行だけを取り出す（重複は除く、順序は保つ）。"""
    seen, lines = set(), []
    for m in FAIL_LINE_RE.finditer(output or ""):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            lines.append(m.group(0))
    return lines


def materialize(workdir, sandbox, impl_dir, files, cfg=None):
    """workdir に csproj と生成テストを書く。サンドボックスの中には何も書かない。"""
    cfg = cfg or project.config("invgen")
    workdir, sandbox = Path(workdir).resolve(), Path(sandbox).resolve()
    if workdir == sandbox or sandbox in workdir.parents:
        raise ValueError(f"不変条件テストをサンドボックスの中に置こうとしました: {workdir}")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    impl = str(sandbox / impl_dir).replace("/", "\\") + "\\**\\*." + "cs"
    packages = "\n".join(f'    <PackageReference Include="{k}" Version="{v}" />' for k, v in cfg["packages"].items())
    (workdir / "Invariants.csproj").write_text(
        CSPROJ.format(tf=cfg["target_framework"], lang=cfg["lang_version"], impl=impl, packages=packages),
        encoding="utf-8")
    for name, text in files.items():
        (workdir / name).write_bytes(text.encode("utf-8"))
    return workdir / "Invariants.csproj"


def run_tests(csproj, seed, ttl):
    env = dict(os.environ, **{invgen.SEED_ENV: str(seed)})
    return run(resolve_cli("dotnet") + ["test", str(csproj), "-nologo"], csproj.parent, ttl,
               "dotnet test (invariants)", env=env)


def check(c):
    """Outer 段の本体。合格か宣言が無ければ None、そうでなければ (verdict, msg)。"""
    path = decl_path(c.cfg["project"]["id"])
    if not path.exists():
        c.tel["invariants"] = {"declared": False}
        print("    宣言なし（projects/<id>/invariants.json が無い）")
        return None

    try:
        decl = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "ABORT", f"不変条件の宣言を読めません: {e}"
    cfg_schema = project.config("unit_schema")
    read = unit_schema.git_reader(c.repo, c.contract["sha"], c.ttl["git"])
    try:
        files = invgen.generate(decl, read(cfg_schema["spec_path"]), read(cfg_schema["gdd_path"]))
    except (invgen.InvariantError, unit_schema.UnitSchemaError) as e:
        return "ABORT", f"不変条件テストを生成できません（宣言が GDD や仕様とずれている）: {e}"

    wl = c.unit["whitelist"]
    run(["git", "add", "-N", "--"] + wl, c.sandbox, c.ttl["git"], "intent-to-add (invariants)")
    rc, diff, err = run(["git", "diff", "HEAD", "--"] + wl, c.sandbox, c.ttl["git"], "diff (invariants seed)")
    if rc != 0:
        return "ABORT", f"シードを決める差分を取れません: {(err or diff)[:200]}"
    seed = seed_for(diff)

    workdir = c.out / "invariants" / f"attempt{c.metrics.get('attempt', 0)}"
    csproj = materialize(workdir, c.sandbox, c.cfg["project"]["impl_dir"], files)
    rc, out, err = run_tests(csproj, seed, c.ttl["fast_tests"])
    (workdir / "dotnet.log").write_text(f"# seed {seed}\n- rc: {rc}\n\n{out}\n{err}\n", encoding="utf-8")
    failures = parse_failures(out + "\n" + err)
    c.tel["invariants"] = {"declared": True, "seed": seed, "rc": rc, "failures": len(failures)}
    if rc == 0:
        print(f"    合格（seed {seed}）")
        return None
    if failures:
        return "REJECT", "不変条件が破れました（Outer 段。テストは見せません）:\n" + "\n".join(failures[:5])
    return "ABORT", f"不変条件テストが反例の行を出さずに失敗しました（ビルド不能など）。記録: {workdir / 'dotnet.log'}"
