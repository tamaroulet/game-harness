"""Unity エンジンのアダプタ。

Unity 固有の知識はここにだけ置く（pipeline.py から移した。実測で得た教訓ごと移している）。

設定:
  project.json  "unity_project_subdir"   Unity プロジェクトの場所（例 "Game"）
  pipeline.json "unity_exe"              Editor の実体（マシン設定へ移す予定。docs/design/contract.md）
                "unity_skip_baseline"    既知の skip 件数（[Explicit] など）
                "golden_dir_env_vars"    ゴールデンの置き場を Unity テストへ渡す環境変数名
                ttl_seconds.engine_tests
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from proc import run

LABEL = "Unity"
PASSED = "Passed"
FAILED = "Failed"
SKIPPED = ("Skipped", "Inconclusive")


def project_dir(c):
    sub = (c.cfg.get("project") or {}).get("unity_project_subdir")
    if not sub:
        sys.exit("ABORT: project.json に unity_project_subdir がありません（Unity アダプタに必要）")
    return c.sandbox / sub


def skip_baseline(c):
    return c.cfg["unity_skip_baseline"]


def run_tests(c, tag):
    """EditMode のテストをバッチモードで実行し、(結果, エラー) を返す。"""
    xml = c.out / f"{tag}.xml"
    log = c.out / f"{tag}.log"
    if xml.exists():
        xml.unlink()

    env = dict(os.environ)
    # 同居する全ランナーが同じステージング先を見る。片方しか設定しないと
    # 他方が「ファイルが無い」で落ちる（実測）。
    for var in c.cfg["golden_dir_env_vars"]:
        env[var] = str(c.stage)

    # -testFilter は付けない。名前空間を指定すると NUnit が [Explicit] を
    # 「明示的な選択」とみなして実行してしまい、Skip されるはずの 20 件が
    # 走って落ちる（実測で発生した）。無指定なら正しく Skip される。
    # -batchmode -nographics で窓を出さない（GUI サブシステムなので CREATE_NO_WINDOW は効かない）。
    rc, _, err = run([c.cfg["unity_exe"], "-batchmode", "-nographics",
                      "-projectPath", str(project_dir(c)),
                      "-runTests", "-testPlatform", "EditMode",
                      "-testResults", str(xml), "-logFile", str(log)],
                     c.sandbox, c.ttl["engine_tests"], f"unity ({tag})", env=env)
    if not xml.exists():
        return None, f"検査系故障: 結果 XML が生成されませんでした（rc={rc} {err[:200]} log={log}）"
    return parse_results(xml), None


def parse_results(xml_path):
    """NUnit3 の結果 XML → {fullname: result}"""
    x = ET.parse(xml_path).getroot()
    return {n.get("fullname"): n.get("result") for n in x.iter("test-case")}


# ---- 付随ファイル（.meta）

def is_companion(path):
    """エンジンが自動で作る付随ファイルか。ホワイトリストの照合では本体と一緒に扱う。"""
    return path.endswith(".meta")


def companions(rel):
    return [rel + ".meta"]


def guid_of(meta_path):
    """.meta から guid を取り出す。読めなければ None。"""
    try:
        for line in Path(meta_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("guid:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def carry_companion(src, dst):
    """サンドボックスの付随ファイルを本体へ運ぶかを決める。

    戻り値: ("skip", "") 運ばない / ("copy", "") 運ぶ / ("abort", 理由)

    .meta は GUID を持つ。無条件に上書きすると、そのクラスを参照する
    Prefab / Scene / .asset がすべて Missing (MonoScript) になる。
      - サンドボックスに無い → 運ばない（.meta を持たないファイルもある）
      - 本体に既にある → 触らない。ただし GUID がずれていたら事故なので ABORT
      - 本体に無い（新規作成） → 運ぶ
    運ばないと、本体で次に Unity を起動した瞬間に .meta が生えて作業ツリーが汚れ、
    require_repo_clean が ABORT する（実測）。
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return "skip", ""
    if dst.exists():
        g_repo, g_sb = guid_of(dst), guid_of(src)
        if g_repo != g_sb:
            return "abort", (f"GUID 不一致: {dst.name} (repo={g_repo} sandbox={g_sb})。"
                             "上書きすると参照が壊れるため中止します")
        return "skip", ""
    return "copy", ""
