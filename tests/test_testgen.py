"""テスト生成器の自己テスト（harness/testgen.py、docs/design/mechanical_barriers.md §5 手順 3）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 生成したテストがコンパイルできないと、実装役はテストを直せないので必ず ABORT になる。
生成器が正しい C# を出すことを、ここで `dotnet build` まで通して保証する（2026-09-21 裁定）。
dotnet が無い環境では skip せずに失敗させる。保証できないまま先へ進まないため。
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import testgen  # noqa: E402
from test_unit_schema import GDD, SPEC, VALID  # noqa: E402

# given から状態を作る入口（引数がちょうど given に一致するコンストラクタ）を足したもの
UNIT = copy.deepcopy(VALID)
UNIT["interface"]["types"][2]["members"].append(
    {"name": "GameState", "kind": "ctor", "params": [{"name": "tickCount", "type": "int"}]})
# Phase だけを given にすると「引数 phase のコンストラクタ」が要る。既定の Ready から始める行にする
UNIT["acceptance"]["cases"][1]["given"] = {}
BODY = json.dumps(UNIT, ensure_ascii=False).encode("utf-8")

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <LangVersion>9.0</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="NUnit" Version="3.14.0" />
  </ItemGroup>
</Project>
"""


def build(files):
    """files を 1 つのプロジェクトにして dotnet build する。(rc, 出力の末尾)"""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise AssertionError("dotnet が見つかりません。生成器の出力がビルドできることを保証できません")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "Gen.csproj").write_text(CSPROJ, encoding="utf-8")
        for name, text in files.items():
            (Path(d) / name).write_bytes(text.encode("utf-8"))
        p = subprocess.run([dotnet, "build", "-nologo", "-v", "q"], cwd=d, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
        return p.returncode, (p.stdout + p.stderr)[-3000:]


def generated():
    return testgen.generate(UNIT, SPEC, GDD, BODY)


class Deterministic(unittest.TestCase):
    def test_same_input_same_bytes(self):
        self.assertEqual(generated(), generated())

    def test_one_file_named_after_unit(self):
        self.assertEqual(list(generated()), ["Issue12Cases.cs"])


class Rejects(unittest.TestCase):
    def test_given_without_restoring_ctor(self):
        with self.assertRaises(testgen.GenerationError) as cm:
            testgen.generate(VALID, SPEC, GDD, b"")
        self.assertIn("コンストラクタ", str(cm.exception))

    def test_unit_that_fails_schema_gate(self):
        bad = copy.deepcopy(UNIT)
        bad["acceptance"]["cases"][0]["expect"]["GameState.TickCount"] = 42
        with self.assertRaises(testgen.GenerationError):
            testgen.generate(bad, SPEC, GDD, b"")


class Builds(unittest.TestCase):
    """生成したテストが、interface の骨組みに対してコンパイルできること。"""

    def test_generated_tests_compile(self):
        files = dict(generated())
        files["Stub.cs"] = testgen.interface_stub(UNIT, SPEC, GDD)
        rc, log = build(files)
        self.assertEqual(rc, 0, log)

    def test_build_check_is_not_vacuous(self):
        # 壊した C# でビルドが落ちることを確かめる。落ちなければ上の検査は何も保証していない
        files = {k: v.replace("Is.EqualTo(", "Is.EqualToo(") for k, v in generated().items()}
        files["Stub.cs"] = testgen.interface_stub(UNIT, SPEC, GDD)
        rc, _ = build(files)
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
