"""Unity エンジンのアダプタ。

Unity 固有の知識はここにだけ置く（pipeline.py から移した。実測で得た教訓ごと移している）。

設定:
  project.json  "unity_project_subdir"   Unity プロジェクトの場所（例 "Game"）
  pipeline.json "unity_exe"              Editor の実体（マシン設定へ移す予定。docs/design/contract.md）
                "unity_skip_baseline"    既知の skip 件数（[Explicit] など）
                "golden_dir_env_vars"    ゴールデンの置き場を Unity テストへ渡す環境変数名
                ttl_seconds.engine_tests
"""
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import fileops
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
        fileops.unlink(xml)

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


def build_player(c, project_root, exe_path, log_path):
    """プレイ確認用の Windows ビルド（H2）。戻り値 (rc, 理由)。

    ビルドの中身はゲーム側の Editor スクリプト（pipeline.json の playtest_build_method）が持つ。
    ここは起動の仕方だけを決める: -playtestOutput に exe のパスを渡し、終了コードで成否を返させる。
    設定が足りないときは AdapterError（環境の問題。ビルドの失敗とは分ける）。
    """
    import adapters
    method = c.cfg.get("playtest_build_method")
    sub = (c.cfg.get("project") or {}).get("unity_project_subdir")
    if not method:
        raise adapters.AdapterError("pipeline.json に playtest_build_method がありません（Unity アダプタのビルドに必要）")
    if not sub:
        raise adapters.AdapterError("project.json に unity_project_subdir がありません（Unity アダプタに必要）")
    Path(exe_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    # -quit: ビルド関数が例外で抜けて Exit を呼ばなかったときも、エディタを残さない
    rc, out, err = run([c.cfg["unity_exe"], "-batchmode", "-nographics", "-quit",
                        "-projectPath", str(Path(project_root) / sub),
                        "-buildTarget", "Win64", "-executeMethod", method,
                        "-playtestOutput", str(exe_path), "-logFile", str(log_path)],
                       project_root, c.ttl["playtest_build"], "unity build (playtest)")
    return rc, ("" if rc == 0 else (err or out)[:200])


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


META_TEMPLATE_SCRIPT = """fileFormatVersion: 2
guid: {guid}
MonoImporter:
  externalObjects: {{}}
  serializedVersion: 2
  defaultReferences: []
  executionOrder: 0
  icon: {{instanceID: 0}}
  userData:
  assetBundleName:
  assetBundleVariant:
"""
META_TEMPLATE_DEFAULT = """fileFormatVersion: 2
guid: {guid}
DefaultImporter:
  externalObjects: {{}}
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def guid_for(rel):
    """リポジトリからの相対パスから GUID を決める（ADR-003 §3.6）。Unity を起動しない。

    同じパスなら常に同じ GUID になる。Unity の GUID と同じく 32 桁の 16 進。
    """
    return hashlib.md5(rel.replace("\\", "/").encode("utf-8")).hexdigest()


def generated_meta(rel):
    """付随ファイル rel（<本体>.meta）の中身。C# は MonoImporter、それ以外は DefaultImporter。"""
    body = rel[:-len(".meta")] if rel.endswith(".meta") else rel
    template = META_TEMPLATE_SCRIPT if body.endswith("." + "cs") else META_TEMPLATE_DEFAULT
    return template.format(guid=guid_for(rel))


def carry_companion(src, dst, rel=None):
    """サンドボックスの付随ファイルを本体へ運ぶかを決める。

    戻り値: ("skip", "") 運ばない / ("copy", "") 運ぶ / ("generate", 中身) 作って置く / ("abort", 理由)

    rel（付随ファイルのリポジトリからの相対パス）を渡すと、サンドボックスにも本体にも無い付随ファイルを
    パスから決定論的に作る（ADR-003 §3.6）。実装役は Unity を持たないので、新しいファイルの .meta は
    サンドボックスに生えない。作らないと、本体で次に Unity を起動したときに GUID がばらばらに生える。

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
        body = Path(str(dst)[:-len(".meta")]) if str(dst).endswith(".meta") else None
        if rel and not dst.exists() and body is not None and body.exists():
            return "generate", generated_meta(rel)
        return "skip", ""
    if dst.exists():
        g_repo, g_sb = guid_of(dst), guid_of(src)
        if g_repo != g_sb:
            return "abort", (f"GUID 不一致: {dst.name} (repo={g_repo} sandbox={g_sb})。"
                             "上書きすると参照が壊れるため中止します")
        return "skip", ""
    return "copy", ""
