"""decompose — 分解役（Claude CLI）。Issue から受入テストと単位定義を作る。

    python harness/decompose.py --project unity-2d --issue 12
    python harness/decompose.py --project unity-2d --file drafts/sample.md --id boss_rush   （Issue 無しで試す）

終了コード: 0=書き出した / 1=分解役の出力が要件を満たさない / 2=環境異常

**なぜテストを先に書くのか**

MS1〜3 は「既存実装の挙動をゴールデンに採取し、それと一致するか」で判定できた。
新規機能には既存の正解が無い。したがって仕様からテストを先に作る以外に、
機械判定の道がない。

**なぜ分解役と実装役を分けるのか**

分解役が書いたテストが唯一のオラクルになる。実装役（agy）がテストを触れれば、
テストを甘くして通す（報酬ハッキング）ができてしまう。
ホワイトリストから *Tests.cs を外し、パイプライン側の require_unit_safe が
機械的に弾く。これは推奨ではなく前提条件。

**それでも残る穴**

出題者（分解役）が仕様を誤解していれば、実装は誤りに忠実になる。
実装役は不正できないが、出題者は間違えられる。
その誤りを検出できるのはテストを書いていない第三者だけなので、
生成物は harness/audit.py（別モデル）に通すこと。
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import project
import telemetry

# main() が --project から決める。共通設定（config/decompose.json）に、
# ゲーム固有の値（リポジトリ・テスト置き場など）を project.json から差し込む。
ROOT = None
CFG = None
# テレメトリ（判定には使わない）。--telemetry のとき、終了時に書き出す。
TEL = {"schema": telemetry.SCHEMA, "tool": "decompose"}


def configure(project_id):
    global ROOT, CFG
    p = project.load(project_id)
    CFG = project.config("decompose")
    for k in ("repo", "test_dir", "impl_dir", "fast_test_project", "units_dir"):
        if k in CFG:
            sys.exit(f"config/decompose.json の {k} は project.json にだけ書いてください")
    ROOT = Path(p["repo_dir"])
    CFG.update(project_id=p["id"], repo=p["repo_slug"], test_dir=p["test_dir"],
               impl_dir=p["impl_dir"], fast_test_project=p["fast_test_project"],
               units_dir=p["units_dir"])
    CFG["prompt_file"] = CFG["prompt_file"].format(project=p["id"])

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TEST_PATH_RE = re.compile(r"(Tests?\.cs$|[/\\][Tt]ests?[/\\]|golden.*\.json$)")


def run(args, ttl, label):
    try:
        r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=ttl, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"TTL超過 ({ttl}s): {label}"
    except FileNotFoundError as e:
        return 127, "", f"コマンドが見つかりません: {e}"


def fetch_issue(number):
    rc, out, err = run(["gh", "issue", "view", str(number),
                        "--repo", CFG["repo"], "--json", "title,body"],
                       CFG["ttl_seconds"]["gh"], "gh issue view")
    if rc != 0:
        sys.exit(f"Issue を取得できません: {(err or out)[:300]}")
    d = json.loads(out)
    return d["title"], d["body"]


# ============================================================ 生成

def build_prompt(unit_id, title, body):
    return CFG["prompt_template"].format(
        unit_id=unit_id,
        title=title,
        body=body,
        test_dir=CFG["test_dir"],
        impl_dir=CFG["impl_dir"],
        fast_test_project=CFG["fast_test_project"],
    )


def resolve_cli(name):
    """Windows で npm 導入の CLI は .cmd の shim。subprocess は拡張子なしを解決しない。

    agy で同じことが起きたのと同型。shutil.which で実体を引く。
    """
    import shutil
    for cand in (name + ".cmd", name + ".exe", name):
        p = shutil.which(cand)
        if p:
            return p
    sys.exit(f"{name} CLI が PATH に見つかりません")


def call_claude(prompt):
    """プロンプトはファイルで渡す。引数に載せない。

    claude は npm の .cmd shim なので、起動時に cmd.exe が引数を解釈する。
    **改行を含む引数は最初の改行で切られる**（実測: 1669 文字のうち 1 行目しか
    届かず、分解役が「対象が渡っていません」と応答した）。
    短い 1 行の指示だけを引数に置き、本体はファイルから読ませる。

    リポジトリの外に書く。ここを汚すとパイプラインの require_repo_clean が
    次回 ABORT する。
    """
    pf = Path(CFG["prompt_file"])
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(prompt, encoding="utf-8")

    args = [resolve_cli(CFG["cli"]), CFG["headless_flag"],
            CFG["prompt_arg_template"].format(prompt_file=pf)] + CFG["extra_flags"]
    # 利用量を取るため JSON で受け取り、本文だけを取り出す（テレメトリ。判定には使わない）。
    args += CFG.get("output_format_args", [])
    t0 = time.monotonic()
    rc, out, err = run(args, CFG["ttl_seconds"]["claude"], "claude")
    TEL["seconds"] = round(time.monotonic() - t0, 1)
    if not CFG.get("output_format_args"):
        TEL["usage"] = telemetry.usage_unknown("config/decompose.json に output_format_args が無い")
    else:
        TEL["usage"] = telemetry.cli_usage(out, CFG["usage_format"])
    if rc != 0:
        sys.exit(f"分解役が異常終了 (rc={rc}): {(err or out)[:500]}")
    if not CFG.get("output_format_args"):
        return out
    # 封筒（CLI の JSON）が読めないのは CLI 側の異常。LLM の出力不良（rc=1）とは分けて rc=2 にする。
    text, why = telemetry.response_text(out, CFG["response_key"])
    if text is None:
        sys.exit(f"分解役の CLI の出力を読めません（{why}）: {out[:300]}")
    return text


def reject(msg):
    """分解役の出力が使えない。環境は正常なので rc=1（次の Issue へ進んでよい）。

    文字列で sys.exit すると __main__ で rc=2（環境異常）に正規化される。
    gh・CLI の不在や claude の異常終了はそちらでよいが、LLM の出力不良まで
    環境異常にするとスケジューラが止まってしまうので、ここだけ 1 で出る。
    """
    print(msg, file=sys.stderr)
    sys.exit(1)


def extract_json(text):
    """応答から JSON を取り出す。```json ブロックにも素の JSON にも対応する。"""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    blob = m.group(1) if m else text
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        reject("応答から JSON を取り出せません:\n" + text[:600])
    try:
        return json.loads(blob[start:end + 1])
    except json.JSONDecodeError as e:
        reject(f"JSON として読めません: {e}\n" + blob[start:end + 1][:600])


# ============================================================ 検査

def validate(d):
    """スキーマと安全性を機械で確かめる。LLM の出力を信用しない。"""
    problems = []

    for key in CFG["required_keys"]:
        if key not in d:
            problems.append(f"必須キーがありません: {key}")
    if problems:
        return problems

    wl = d.get("whitelist") or []
    if not wl:
        problems.append("whitelist が空です")

    # 最重要。ここが漏れると実装役がオラクルを書き換えられる。
    for p in wl:
        if TEST_PATH_RE.search(p):
            problems.append(f"whitelist にテストまたはゴールデンが入っています: {p}")

    tests = d.get("test_files") or []
    if not tests:
        problems.append("test_files が空です（正解が無いので判定できません）")
    for t in tests:
        if not TEST_PATH_RE.search(t.get("path", "")):
            problems.append(f"test_files のパスがテストとして認識されません: {t.get('path')}")
        if not t.get("content", "").strip():
            problems.append(f"test_files の中身が空です: {t.get('path')}")

    req = (d.get("acceptance") or {}).get("required_tests") or []
    if not req:
        problems.append("acceptance.required_tests が空です")

    # 自明アサーションの混入を機械で弾く。
    for t in tests:
        for pattern, label in CFG["tautology_patterns"]:
            if re.search(pattern, t.get("content", "")):
                problems.append(f"{label}: {t.get('path')} に「{pattern}」")

    if not d.get("human_check_point", "").strip():
        problems.append("human_check_point が空です（人間が何を見るか書かれていません）")

    return problems


# ============================================================ 書き出し

def write_outputs(d, unit_id):
    written = []
    for t in d["test_files"]:
        p = ROOT / t["path"].replace("/", "\\")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t["content"], encoding="utf-8")
        written.append(t["path"])

    unit = {k: v for k, v in d.items() if k != "test_files"}
    unit.setdefault("id", unit_id)
    unit.setdefault("fast_test_project", CFG["fast_test_project"])
    unit.setdefault("forbidden_leftover", CFG["defaults"]["forbidden_leftover"])
    unit.setdefault("forbidden_skip_attribute_regex",
                    CFG["defaults"]["forbidden_skip_attribute_regex"])
    unit.setdefault("max_impl_lines", CFG["defaults"]["max_impl_lines"])
    unit.setdefault("forbidden_patterns", CFG["defaults"]["forbidden_patterns"])
    unit.setdefault("selftest_forbidden_probe", CFG["defaults"]["selftest_forbidden_probe"])
    unit.setdefault("impl_files", unit["whitelist"])

    up = ROOT / CFG["units_dir"] / f"{unit_id}.json"
    up.parent.mkdir(parents=True, exist_ok=True)
    up.write_text(json.dumps(unit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(str(up.relative_to(ROOT)).replace("\\", "/"))
    return written, up


# ============================================================ エントリ

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="projects/<id>（harness のプロジェクト ID）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", type=int, help="対象の Issue 番号")
    g.add_argument("--file", help="Issue の代わりにローカルの文書を使う（試験用）")
    ap.add_argument("--id", help="単位 ID。--file のときは必須")
    ap.add_argument("--telemetry", help="テレメトリの書き出し先（JSON）。判定には使わない")
    a = ap.parse_args()
    TEL["started"] = datetime.now().isoformat(timespec="seconds")
    rc = None
    try:
        rc = decompose(a)
        return rc
    finally:
        if "usage" not in TEL:
            TEL["usage"] = telemetry.usage_unknown("分解役を呼ぶ前に終了した")
        telemetry.put(TEL, "exit_code", rc, "sys.exit か例外で終了した（終了コードは呼び出し側の記録を見る）")
        if a.telemetry:
            telemetry.write(a.telemetry, TEL)


def decompose(a):
    configure(a.project)

    if a.issue:
        title, body = fetch_issue(a.issue)
        unit_id = a.id or f"issue_{a.issue}"
    else:
        p = Path(a.file)
        if not p.exists():
            sys.exit(f"ファイルがありません: {p}")
        if not a.id:
            sys.exit("--file のときは --id が必要です")
        title, body, unit_id = p.stem, p.read_text(encoding="utf-8"), a.id

    print(f"単位: {unit_id}")
    print(f"題名: {title}")

    out = call_claude(build_prompt(unit_id, title, body))
    d = extract_json(out)

    problems = validate(d)
    if problems:
        print("\n生成物が要件を満たしていません:")
        for x in problems:
            print("  - " + x)
        print("\n書き出さずに終了します。")
        return 1

    written, up = write_outputs(d, unit_id)
    print("\n書き出し:")
    for w in written:
        print("  " + w)
    rel = str(up.relative_to(ROOT)).replace("\\", "/")
    print(f"\n次: python harness/audit.py --project {CFG['project_id']} --file {rel}")
    print(f"    python harness/pipeline.py --project {CFG['project_id']} --unit {rel}")
    return 0


if __name__ == "__main__":
    # 終了コード: 0=書き出した / 1=分解役の出力が要件を満たさない / 2=環境異常
    # sys.exit("...")（gh・CLI・claude の失敗）と未捕捉例外は 2 になる。
    import exitcode
    sys.exit(exitcode.normalized(main))
