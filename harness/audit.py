"""audit — 独立監査役（OpenAI API）。

    python harness/audit.py --project unity-2d --file docs/spec/gdd.md
    python harness/audit.py --project unity-2d --file tools/units/issue_12.json
    python harness/audit.py --list-models

--file はゲームのリポジトリからの相対パス。レポートもそのリポジトリの out_dir に書く。

実装役（agy）とも分解役（Claude CLI）とも別のモデルに、盲点だけを挙げさせる。

**なぜ独立性が要るか**: 新規機能では分解役が書いたテストが唯一のオラクルになる。
実装役はホワイトリストでテストを触れないので報酬ハッキングはできないが、
**出題者（テストの著者）が仕様を誤解していれば、実装は誤りに忠実になる**。
その誤りを検出できるのは、テストを書いていない第三者だけである。

依存を増やさないため stdlib の urllib だけで書く（openai パッケージを要求しない）。
設定は config/audit.json が唯一の出所。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import project

CFG = project.config("audit")
ROOT = None  # --project から決める（--list-models では使わない）

# このマシンの標準出力は CP932。監査結果に印字できない文字が入ると落ちる。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def require_key():
    """未設定のまま API を叩くと 401 の生エラーで死ぬ。先に落とす。"""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY が設定されていません。\n"
                 "  PowerShell:  $env:OPENAI_API_KEY = \"sk-...\"\n"
                 "  永続化    :  setx OPENAI_API_KEY \"sk-...\"")
    return key


def post(url, key, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CFG["timeout_seconds"]) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        hint = ""
        if e.code == 401:
            hint = "\n  → OPENAI_API_KEY が無効です。"
        elif e.code == 404 or "model" in body.lower():
            hint = (f"\n  → モデル '{CFG['model']}' が使えない可能性があります。"
                    "\n     `python tools/audit.py --list-models` で一覧を取り、"
                    "tools/audit.config.json の model を直してください。")
        sys.exit(f"API エラー {e.code}: {body[:500]}{hint}")
    except urllib.error.URLError as e:
        sys.exit(f"接続できません: {e.reason}")


def cmd_list_models():
    key = require_key()
    req = urllib.request.Request(
        CFG["models_endpoint"],
        headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=CFG["timeout_seconds"]) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"API エラー {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")

    ids = sorted(m["id"] for m in data.get("data", []))
    print(f"{len(ids)} 件:")
    for i in ids:
        print("  " + i)
    print(f"\n現在の設定: model = {CFG['model']}")
    return 0


def cmd_audit(path):
    key = require_key()
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / path
    if not target.exists():
        sys.exit(f"監査対象がありません: {target}")

    body = target.read_text(encoding="utf-8", errors="replace")
    print(f"対象: {target}  ({len(body)} 文字)")
    print(f"モデル: {CFG['model']}")

    res = post(CFG["endpoint"], key, {
        "model": CFG["model"],
        "messages": [
            {"role": "system", "content": CFG["system_prompt"]},
            {"role": "user",
             "content": f"以下を監査してください。\n\nファイル: {path}\n\n---\n{body}\n---"},
        ],
    })

    try:
        text = res["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        sys.exit("応答の形式が想定と違います: " + json.dumps(res)[:400])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / CFG["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"audit_{stamp}.md"

    usage = res.get("usage", {})
    header = (f"# 監査 {stamp}\n\n"
              f"- 対象: `{path}`\n"
              f"- モデル: `{CFG['model']}`\n"
              f"- トークン: {usage.get('prompt_tokens', '?')} / {usage.get('completion_tokens', '?')}\n\n"
              f"> 実装役（agy）とも分解役（Claude CLI）とも別のモデルによる指摘です。\n"
              f"> 内容は未検証です。採否は人間または分解役が判断してください。\n\n---\n\n")

    out.write_text(header + text, encoding="utf-8")
    print(f"保存: {out}")
    print()
    print(text[:800] + ("\n…（続きはファイル）" if len(text) > 800 else ""))
    return 0


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="projects/<id>。--file のときは必須")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", metavar="PATH", help="監査する文書（GDD / 単位定義 / テスト）")
    g.add_argument("--list-models", action="store_true", help="使えるモデルの一覧")
    ap.add_argument("--repo-dir", help="Issue の worktree。監査レポートをここに書く（既定は project.json の repo_dir）")
    a = ap.parse_args()

    if a.list_models:
        return cmd_list_models()
    if not a.project:
        sys.exit("--file のときは --project が必要です")
    ROOT = Path(a.repo_dir or project.load(a.project)["repo_dir"])
    return cmd_audit(a.file)


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
