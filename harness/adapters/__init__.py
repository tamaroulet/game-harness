"""エンジン別・言語別のアダプタ。

コア（pipeline / scheduler / project / proc / exitcode）は、エンジンや言語に固有の処理を
ここを通してだけ呼ぶ。コアのソースに固有の語（Unity、.meta、dotnet、TRX など）が
無いことは tests/test_adapters.py が確かめる。

種類:
  engine  エンジンの受入テストと、エンジン固有のファイル（例: GUID を持つ付随ファイル）の扱い
  fast    高速検査（Pure なコードのテスト）と、その言語のテスト・スタブ・一覧抽出

選ぶのは projects/<id>/project.json の "adapters"。知らない名前は例外（呼び出し側で ABORT）。
2 つ目のエンジンのアダプタは、そのプロジェクトが実在してから書く（先回りして抽象化しない）。

各アダプタが持つもの（tests/test_adapters.py が機械で確かめる）:
  engine: LABEL, PASSED, FAILED, SKIPPED, run_tests, parse_results, skip_baseline,
          is_companion, companions, carry_companion, build_player
  fast:   LABEL, PASSED, FAILED, run_tests, parse_results, STUB_SOURCE, test_file_glob,
          TEST_PATH_RE, build_output_globs, summarize_files
"""
import importlib

KNOWN = {
    "engine": {"unity": "adapters.unity"},
    "fast": {"dotnet": "adapters.dotnet"},
}

REQUIRED = {
    "engine": ("LABEL", "PASSED", "FAILED", "SKIPPED", "run_tests", "parse_results",
               "skip_baseline", "is_companion", "companions", "carry_companion", "build_player"),
    "fast": ("LABEL", "PASSED", "FAILED", "run_tests", "parse_results", "STUB_SOURCE",
             "test_file_glob", "TEST_PATH_RE", "build_output_globs", "summarize_files"),
}


class AdapterError(Exception):
    pass


def load(kind, name):
    if kind not in KNOWN:
        raise AdapterError(f"アダプタの種類 {kind!r} はありません（既知: {', '.join(sorted(KNOWN))}）")
    if name not in KNOWN[kind]:
        raise AdapterError(f"{kind} アダプタ {name!r} はありません"
                           f"（既知: {', '.join(sorted(KNOWN[kind]))}）")
    mod = importlib.import_module(KNOWN[kind][name])
    missing = [a for a in REQUIRED[kind] if not hasattr(mod, a)]
    if missing:
        raise AdapterError(f"{kind} アダプタ {name} に必要なものがありません: {', '.join(missing)}")
    return mod
