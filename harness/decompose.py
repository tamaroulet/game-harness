"""decompose — 分解役（Claude CLI）。Issue から単位定義 v2 を作り、受入テストを生成する。

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

**分解役は C# を書かない**（docs/design/mechanical_barriers.md §3、ADR-002 手順 4）

分解役が出すのは単位定義 v2（interface と受入データ）だけ。受入テストの C# は、
harness/testgen.py が受入データから機械的に生成する。書き出す前に、パイプラインと同じ
スキーマ門（harness/unit_schema.py）とテスト生成に通す。落ちたら問題の一覧を添えて
分解役に出し直させる（config の self_check_retries 回まで）。それでも落ちれば何も書かずに rc=1。

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
import testgen
import unit_schema

# main() が --project から決める。共通設定（config/decompose.json）に、
# ゲーム固有の値（リポジトリ・テスト置き場など）を project.json から差し込む。
ROOT = None
CFG = None
# テレメトリ（判定には使わない）。--telemetry のとき、終了時に書き出す。
TEL = {"schema": telemetry.SCHEMA, "tool": "decompose"}
# 分解役の生ログの置き場。main が --telemetry から決める。無ければ指示ファイルの隣。
LOG_DIR = None


def configure(project_id, repo_dir=None):
    global ROOT, CFG
    p = project.load(project_id)
    if repo_dir:
        p["repo_dir"] = repo_dir
    CFG = project.config("decompose")
    for k in ("repo", "test_dir", "impl_dir", "fast_test_project", "units_dir"):
        if k in CFG:
            sys.exit(f"config/decompose.json の {k} は project.json にだけ書いてください")
    ROOT = Path(p["repo_dir"])
    CFG.update(project_id=p["id"], repo=p["repo_slug"], test_dir=p["test_dir"], base_branch=p["base_branch"],
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
    except subprocess.TimeoutExpired as e:
        # 打ち切った時点までの出力を捨てない。TimeoutExpired は kill のあとに
        # communicate() した結果を持っている。ここを捨てると、TTL 超過の原因が
        # ハーネスの記録から一切たどれなくなる（実測で起きた。call_claude を見よ）。
        return 124, e.stdout or "", f"TTL超過 ({ttl}s): {label}\n" + (e.stderr or "")
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


def log_dir():
    """分解役の生ログの置き場。テレメトリと同じ所に置く（1 回の実行の記録をばらさない）。"""
    return LOG_DIR if LOG_DIR is not None else Path(CFG["prompt_file"]).parent


def write_decompose_log(prompt, rc, out, err):
    """分解役に渡した指示と、返ってきた生出力をそのまま残す。

    **TTL 超過でも書く。** 2026-09-19 の Issue #14 は claude が 600 秒の TTL に
    かかって rc=124 で落ちたが、当時は打ち切り時の出力を捨てていたため、分解役が
    何をしていたのかがハーネスの記録から一切たどれなかった（実際には使い捨ての
    プロジェクトを立てて出現順を算出していた。判明したのは CLI 自身のセッション記録を
    別に掘ったからで、こちらには何も残っていなかった）。

    指示も一緒に残す。指示ファイルはプロジェクトごとに使い回して上書きされるので、
    後から「何を渡したときの出力か」が分からなくなる。

    観測のための機能であって判定には使わない。書けなくても実行は止めない。
    """
    body = "\n".join([
        "# 分解役",
        f"- cli: {CFG['cli']}",
        f"- rc: {rc}" + ("（TTL 超過。以下は打ち切り時点までの出力）" if rc == 124 else ""),
        f"- cwd: {ROOT}",
        "",
        "=== 指示 ===",
        prompt,
        "",
        "=== stdout ===",
        out,
        "",
        "=== stderr ===",
        err,
        "",
    ])
    path = log_dir() / "decompose_response.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as e:
        print(f"  分解役のログを書けませんでした（判定には影響しません）: {e}")
        return None
    print(f"  分解役の応答: {path}")
    return path


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
    write_decompose_log(prompt, rc, out, err)
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

    if "test_files" in d:
        problems.append("test_files は書けません。受入テストは受入データ（acceptance.cases）からハーネスが生成します")

    if not d.get("human_check_point", "").strip():
        problems.append("human_check_point が空です（人間が何を見るか書かれていません）")

    # プレイ確認の宣言。これでスケジューラが実行ファイルを作るかを決める（H2）。
    # 書き間違いを「none 扱い」にすると、見た目の変更が人間に遊ばれずに通るので弾く。
    if d.get("playtest") not in ("none", "required"):
        problems.append(f"playtest は \"none\" か \"required\" です: {d.get('playtest')!r}")

    return problems


# ============================================================ 書き出し

def build_unit(d, unit_id):
    """分解役の出力に、ハーネスが決める値を足して単位定義 v2 にする。"""
    unit = {"schema": unit_schema.SCHEMA}
    unit.update({k: v for k, v in d.items() if k != "schema"})
    unit.setdefault("id", unit_id)
    unit.setdefault("fast_test_project", CFG["fast_test_project"])
    unit.setdefault("forbidden_leftover", CFG["defaults"]["forbidden_leftover"])
    unit.setdefault("forbidden_skip_attribute_regex",
                    CFG["defaults"]["forbidden_skip_attribute_regex"])
    unit.setdefault("max_impl_lines", CFG["defaults"]["max_impl_lines"])
    unit.setdefault("max_add_lines", CFG["defaults"]["max_add_lines"])
    unit.setdefault("max_del_lines", CFG["defaults"]["max_del_lines"])
    unit.setdefault("forbidden_patterns", CFG["defaults"]["forbidden_patterns"])
    unit.setdefault("selftest_forbidden_probe", CFG["defaults"]["selftest_forbidden_probe"])
    unit.setdefault("impl_files", unit.get("whitelist"))
    # 実装の有無の照合（pipeline の required_symbols）は interface から機械的に作る
    types = (unit.get("interface") or {}).get("types") if isinstance(unit.get("interface"), dict) else None
    if isinstance(types, list):
        typed = [t for t in types if isinstance(t, dict) and "name" in t]
        unit["required_symbols"] = [t["name"] for t in typed] + [
            f"{t['name']}.{m['name']}" for t in typed for m in t.get("members", [])
            if isinstance(m, dict) and "name" in m and m.get("kind") != "ctor"]
    acc = unit.get("acceptance")
    if isinstance(acc, dict) and isinstance(unit.get("id"), str):
        acc["required_tests"] = [testgen.class_name(unit)]
    return unit


def unit_bytes(unit):
    return (json.dumps(unit, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def self_check(unit, read):
    """パイプラインと同じスキーマ門とテスト生成に通す。({ファイル名: C#}, 問題の一覧)"""
    spec, gdd = read("spec"), read("gdd")
    problems = unit_schema.validate(unit, spec, gdd)
    problems += unit_schema.whitelist_problems(unit, lambda p: _exists(read, p))
    if problems:
        return {}, problems
    try:
        return testgen.generate(unit, spec, gdd, unit_bytes(unit)), []
    except testgen.GenerationError as e:
        return {}, [f"受入テストを生成できません: {e}"]


def _exists(read, path):
    try:
        read(path)
        return True
    except unit_schema.UnitSchemaError:
        return False


def base_reader():
    """GDD と構造化仕様を、パイプラインと同じく origin/<base> の先頭から読む。"""
    cfg = project.config("unit_schema")
    read = unit_schema.git_reader(ROOT, f"origin/{CFG['base_branch']}", CFG["ttl_seconds"]["gh"])
    # spec / gdd は名前で、それ以外（whitelist の時限制約）はパスそのままで読む
    return lambda which: read({"spec": cfg["spec_path"], "gdd": cfg["gdd_path"]}.get(which, which))


def write_outputs(unit, files):
    written = []
    gen_dir = f"{CFG['test_dir']}/Generated"
    for name, text in files.items():
        p = ROOT / gen_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        written.append(f"{gen_dir}/{name}")

    up = ROOT / CFG["units_dir"] / f"{unit['id']}.json"
    up.parent.mkdir(parents=True, exist_ok=True)
    up.write_bytes(unit_bytes(unit))
    written.append(str(up.relative_to(ROOT)).replace("\\", "/"))
    return written, up


# ============================================================ エントリ

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="projects/<id>（harness のプロジェクト ID）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", type=int, help="対象の Issue 番号")
    g.add_argument("--file", help="Issue の代わりにローカルの文書を使う（試験用）")
    g.add_argument("--from-unit", help="書き上がった単位定義 v2 から、分解役（LLM）を呼ばずに単位定義と"
                                       "受入テストを書き出す（v1 からの移行用。スキーマ門とテスト生成は同じく通す）")
    ap.add_argument("--id", help="単位 ID。--file のときは必須")
    ap.add_argument("--telemetry", help="テレメトリの書き出し先（JSON）。判定には使わない")
    ap.add_argument("--repo-dir", help="Issue の worktree。テストと単位定義をここに書く（既定は project.json の repo_dir）")
    a = ap.parse_args()
    if a.telemetry:
        global LOG_DIR
        LOG_DIR = Path(a.telemetry).parent
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


def from_unit(path):
    """単位定義 v2 のファイルから書き出す。LLM を呼ばない。門とテスト生成は通常と同じく通す。"""
    try:
        unit = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        reject(f"単位定義を読めません: {e}")
    if not isinstance(unit, dict) or unit.get("schema") != unit_schema.SCHEMA or not isinstance(unit.get("id"), str):
        reject(f"schema {unit_schema.SCHEMA} で id のある単位定義だけを受けます: {path}")
    # task_kind の無い既存の単位（移行前に書かれたもの）は feature とみなす（ADR-003 §3.7）
    unit.setdefault("task_kind", "feature")
    unit = build_unit({k: v for k, v in unit.items() if k != "schema"}, unit["id"])
    problems = validate(unit)
    files = {}
    if not problems:
        try:
            files, problems = self_check(unit, base_reader())
        except unit_schema.UnitSchemaError as e:
            sys.exit(f"スキーマ門に必要なファイルを読めません: {e}")
    TEL["self_check"] = [{"attempt": 1, "problems": len(problems), "from_unit": True}]
    if problems:
        print("\n単位定義が要件を満たしていません:")
        for x in problems:
            print("  - " + x)
        print("\n書き出さずに終了します。")
        return 1
    written, _ = write_outputs(unit, files)
    print("\n書き出し:")
    for w in written:
        print("  " + w)
    return 0


def decompose(a):
    configure(a.project, getattr(a, "repo_dir", None))

    if getattr(a, "from_unit", None):
        return from_unit(a.from_unit)
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

    prompt = build_prompt(unit_id, title, body)
    read = base_reader()
    retries = CFG.get("self_check_retries", 0)
    for attempt in range(retries + 1):
        d = extract_json(call_claude(prompt))
        problems = validate(d)
        files = {}
        if not problems:
            unit = build_unit(d, unit_id)
            try:
                files, problems = self_check(unit, read)
            except unit_schema.UnitSchemaError as e:
                sys.exit(f"スキーマ門に必要なファイルを読めません: {e}")
        TEL.setdefault("self_check", []).append({"attempt": attempt + 1, "problems": len(problems)})
        if not problems:
            break
        print(f"\n生成物が要件を満たしていません（{attempt + 1} 回目）:")
        for x in problems:
            print("  - " + x)
        prompt = (build_prompt(unit_id, title, body)
                  + "\n\n## 前回の出力はハーネスの門で拒絶されました\n\n"
                  + "次の問題をすべて直した JSON を出し直してください。\n\n"
                  + "\n".join(f"- {x}" for x in problems[:30]))
    else:
        print("\n書き出さずに終了します。")
        return 1

    written, up = write_outputs(unit, files)
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
