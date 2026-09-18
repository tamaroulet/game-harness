"""高速検査（.NET / C#）のアダプタ。

`dotnet test` と TRX の読み取り、C# のスタブ、テストファイルの探し方、
承認依頼に載せる受入テスト一覧の抽出（csharp_tests）をここに置く（pipeline.py から移した）。

設定:
  単位定義 "fast_test_project"   テストプロジェクト（例 "tests/Core.Tests/Core.Tests.csproj"）
  pipeline.json ttl_seconds.fast_tests
"""
import re
import xml.etree.ElementTree as ET

import fileops
from proc import run

from adapters import csharp_tests

LABEL = ".NET"
PASSED = "Passed"
FAILED = "Failed"

TRX_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"

# 単位のホワイトリストに入っていてはいけないパス（テストとゴールデン）。
# 入っていれば、実装役がオラクルを書き換えられる。
TEST_PATH_RE = re.compile(r"(Tests?\.cs$|[/\\][Tt]ests?[/\\]|golden.*\.json$)")

# 自己検査が [A] で一時的に書き込む中身。実装を消して赤が出ることを確かめるため。
# 残っていたら selftest が異常終了している。
STUB_SOURCE = (
    "// selftest が一時的に置いたスタブ。残っていたら selftest が途中で死んでいる。\n"
    "namespace SelftestStub { public class Placeholder { } }\n"
)


def test_file_glob(test_name):
    """受入テストの名前から、それが書かれているファイルを探す glob。

    受入テストの名前は完全修飾クラス名でも単純名でもよい（オラクルの照合は部分一致なので、
    どちらも結果に当たる）。一方でファイル名は名前空間を含まないので、完全修飾名をそのまま
    glob にすると 1 件も当たらない。実測: `StandaloneCore.Tests.InitialStateTests` に対して
    ファイルは `InitialStateTests.cs`（falling-blocks Issue #12、2026-09-18）。

    **「ファイル名はクラスの単純名から決まる」は C# の慣習なので、ここで吸収する。**
    `*<単純名>*.cs` は `NS.Cls.cs` のような名前のファイルにも当たるので、完全修飾名の
    ファイルを別に探す必要はない。
    """
    return f"*{test_name.split('.')[-1]}*.cs"


def build_output_globs(filename):
    """ビルド出力にコピーされたファイルの場所。ソースだけ消しても PreserveNewest で次のランに混入する。"""
    return ["tests/**/bin/**/" + filename]


def summarize_files(paths, root=None):
    return csharp_tests.summarize_files(paths, root=root)


def run_tests(c, tag):
    """`dotnet test` を実行し、(結果, エラー) を返す。件数は c.metrics の fast_* に入れる。"""
    trx = c.out / f"{tag}.trx"
    if trx.exists():
        fileops.unlink(trx)
    # /nr:false: MSBuild のワーカー（ノード再利用）を常駐させない。常駐すると終了後も
    # ビルド出力の .dll を掴み続け、次のビルドやサンドボックスのリセットが
    # ファイルロック（WinError 32）で落ちうる。
    run(["dotnet", "test", c.unit["fast_test_project"],
         "--nologo", "/nr:false", "--logger", f"trx;LogFileName={tag}.trx",
         "--results-directory", str(c.out)],
        c.sandbox, c.ttl["fast_tests"], f"dotnet test ({tag})")
    if not trx.exists():
        return None, "検査系故障: TRX が生成されませんでした（ビルド失敗の可能性）"
    results, counters = parse_results(trx)
    for k, v in counters.items():
        c.metrics[f"fast_{k}"] = v
    return results, None


def parse_results(trx_path):
    """TRX → ({className.methodName: outcome}, {total, passed, failed, notExecuted})

    TRX の testName はメソッド名だけで、クラス名は別要素の className にある。
    required_tests はクラス名で書かれるので、メソッド名だけを探すと
    「1 件も実行されていない」と誤判定する（実測。実装は成功していた）。
    className.methodName の形に組み立ててから照合する。
    """
    root = ET.parse(trx_path).getroot()
    fullname = {}
    for ut in root.iter(f"{TRX_NS}UnitTest"):
        tm = ut.find(f"{TRX_NS}TestMethod")
        if tm is not None and ut.get("name"):
            fullname[ut.get("name")] = f"{tm.get('className', '')}.{ut.get('name')}"

    results = {}
    for r in root.iter(f"{TRX_NS}UnitTestResult"):
        n = r.get("testName")
        results[fullname.get(n, n)] = r.get("outcome")

    counters = {}
    node = root.find(f"{TRX_NS}ResultSummary/{TRX_NS}Counters")
    if node is not None:
        for k in ("total", "passed", "failed", "notExecuted"):
            counters[k] = int(node.get(k, 0))
    return results, counters
