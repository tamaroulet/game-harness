"""declare — 分解役（Claude CLI）に、v2 の契約（interface）と性質の宣言（properties）を書かせ、事前投資を測る（V2-4）。

    python harness/declare.py --project falling-blocks --out experiments/v2

docs/design/v2_contract_foundry.md §6・§10。分解役が書くのは JSON 1 つだけ（interface と properties）。
C# は contractgen と propgen が機械的に作る。

**事前投資を小さくする仕組み**（§10 の 1）
- 分解役には道具を与えない（`--tools ""`）。v1 の分解役は読み取りの道具で仕様とリポジトリを探索し、
  8.45305 USD の大半がキャッシュ読みだった（game-harness#63 の precost.json）。ここでは要求文・構造化仕様・
  v1 の interface・式の文法をすべてプロンプトに入れて渡す
- プロンプトは標準入力で渡す（cmd.exe の shim は、改行を含む引数を最初の改行で切る。道具が無いのでファイルでも渡せない）
- 書いた JSON は contractgen と propgen の検査に通す。落ちたら、同じ会話（`--resume`）に問題の一覧だけを送って
  1 回だけ出し直させる（前の出力を送り直さない）

**記録**：`<out>/results/precost.json` に、呼び出しごとのトークン・秒・費用（claude の total_cost_usd）と合計。
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import contractgen
import exitcode
import gdd_check
import project
import propgen
import telemetry
import unit_schema

ROOT = Path(__file__).resolve().parent.parent
REQ_DIR = ROOT / "experiments" / "b4_ab" / "requirements"
V1_INTERFACE = ROOT / "experiments" / "b4_ab" / "units" / "T5.json"
TASKS = ("T1", "T2", "T3", "T4", "T5")
REFERENCE = {"board_width": "PR-06", "board_height": "PR-08", "shapes": "PR-17..PR-51", "kicks": "PR-52..PR-68"}
RETRIES = 1
TTL = 1100
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def spec_digest(spec_text):
    """構造化仕様 §1〜§5 を、1 行 1 項目の短い形にする（根拠の行番号は落とす）。"""
    problems = []
    _, tables = gdd_check.parse_doc(spec_text, gdd_check.SPEC_META,
                                    [(h, c) for h, _, c in gdd_check.SECTIONS], "spec.md", problems)
    lines = []
    for head, rows in tables.items():
        if head.startswith("## 6."):
            continue
        lines.append(head)
        for r in rows:
            lines.append(" | ".join(str(v) for k, v in r.items() if k not in ("_line", "根拠")))
    return "\n".join(lines)


def requirements():
    parts = [(REQ_DIR / f"{t}.md").read_text(encoding="utf-8").strip() for t in TASKS]
    return "\n\n".join(parts + [(REQ_DIR / "common.md").read_text(encoding="utf-8").strip()])


def build_prompt(spec_text, gdd_text):
    v1 = contractgen.load_interface(V1_INTERFACE)
    grammar = propgen.__doc__.split("**式**：", 1)[1].split("**性質の強さ**", 1)[0].strip()
    decl_shape = {
        "schema": 1, "gdd_sha256": hashlib.sha256(gdd_text.encode("utf-8")).hexdigest(), "reference": REFERENCE,
        "start": {"phase": "Playing", "locked_max": 12}, "steps": 60, "seeds": {"public": 5, "hidden": 20},
        "properties": [{"id": "P-T1-01", "task": "T1", "rule": "RL-xx", "given": "式", "then": "式"}]}
    return f"""あなたは分解役です。落ちものパズル falling-blocks の T1〜T5 について、契約（interface）と性質の宣言（properties）を
JSON 1 つで返してください。道具は使えません。ここに書いたものだけを根拠にしてください。説明の文章は書かず、JSON だけを返します。

# 返す JSON
{{"interface": {{"types": [...]}}, "properties": {{...}}}}

# interface
下の「v1 の interface」をそのまま使い、次の 3 つだけを足す。ほかの型・メンバーの名前と型は変えない。
- データ型 Cell：{{"name": "Cell", "kind": "struct", "members": [ctor Cell(int x, int y), property int X, property int Y]}}
- GameState のメソッド bool IsOccupied(int x, int y)（盤面外は true）
- GameState の復元用コンストラクタ (GamePhase phase, ActiveMino? activeMino, System.Collections.Generic.IReadOnlyList<Cell> locked)

v1 の interface：
{json.dumps(v1, ensure_ascii=False)}

# properties の形
{json.dumps(decl_shape, ensure_ascii=False)}
- schema・gdd_sha256・reference・seeds はこのとおりに書く。start.locked_max と steps は選んでよい
- 開始状態は、復元用コンストラクタで Playing・固定ブロック 0〜locked_max 個・置けるミノ 1 つ。入力は各ティックでランダム

# 式
{grammar}
- before・after で読めるのは GameState の property のうち int・bool・列挙・ActiveMino? のもの。input は TickInput の 6 つ
- ActiveMino のメンバーは Type・X・Y・Rotation。null と比べられるのは ActiveMino? だけ。方向 dir は 1（右回り）か -1（左回り）

# 守ること
1. 性質は、T1〜T5 がすべて入った GDD v10 の最終形で、どのティックでも真であること。後のタスクで偽になるもの（例：自然落下で Y が変わる）は書かない。
   1 ティックの中で、入力・回転・落下・固定・出現・ライン消去が起きうる（処理順は §1 と §3）。前提（given）で、その性質が見ていない出来事を除く。
   ② の中でも、左右移動 → 回転 → ハードドロップの順に効く。fits・kick・drop は before の位置で計算するので、同じティックのほかの入力で位置が変わる場合は前提で除く
2. 各タスクに、then が「左辺 == 右辺」の性質（結果を一意に決める活性の性質）を 1 つ以上
3. 分岐ごとに前提を分ける（例：キックの要らない回転と、要る回転）。前提がランダム系列の中で一度も成り立たない性質は失敗になる
4. 各タスク 2〜4 個。rule は下の構造化仕様の RL の ID。式は短く

# タスク（要求文）
{requirements()}

# 構造化仕様（GDD v10 から作ったもの）
{spec_digest(spec_text)}
"""


def call_claude(prompt, cfg, session=None):
    """(rc, 応答の本文, 利用量, 秒, session_id)。プロンプトは標準入力で渡す。"""
    args = [_resolve(cfg["cli"]), cfg["headless_flag"], "--tools", ""] + cfg.get("output_format_args", [])
    if session:
        args += ["--resume", session]
    t0 = time.monotonic()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TTL, creationflags=_NO_WINDOW, cwd=str(ROOT))
        rc, out = r.returncode, r.stdout or ""
    except subprocess.TimeoutExpired as e:
        rc, out = 124, e.stdout or ""
    seconds = round(time.monotonic() - t0, 1)
    usage = telemetry.cli_usage(out, cfg["usage_format"])
    text, _ = telemetry.response_text(out, cfg["response_key"])
    try:
        sid = json.loads(out).get("session_id")
    except ValueError:
        sid = None
    return rc, text or "", usage, seconds, sid


def _resolve(name):
    import shutil
    for cand in (name + ".cmd", name + ".exe", name):
        p = shutil.which(cand)
        if p:
            return p
    sys.exit(f"{name} CLI が PATH に見つかりません")


def extract(text):
    """応答から {"interface", "properties"} を取り出す。取れなければ (None, 理由)。"""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    blob = m.group(1) if m else text[text.find("{"):text.rfind("}") + 1]
    try:
        d = json.loads(blob)
    except ValueError as e:
        return None, f"JSON として読めません: {e}"
    if not isinstance(d, dict) or set(d) != {"interface", "properties"}:
        return None, "最上位のキーは interface と properties の 2 つにしてください"
    return d, None


def check(d, spec_text, gdd_text, test_dir, impl_dir):
    """問題の一覧。contractgen と propgen が実際に生成できるところまで確かめる。"""
    problems = contractgen.check(d["interface"])
    if problems:
        return problems
    try:
        contractgen.generate(d["interface"], impl_dir, test_dir)
    except contractgen.ContractError as e:
        return [str(e)]
    problems = propgen.validate(d["properties"], spec_text, gdd_text, d["interface"])
    if problems:
        return problems
    try:
        propgen.generate(d["properties"], spec_text, gdd_text, d["interface"], test_dir)
    except propgen.PropertyError as e:
        return [str(e)]
    return []


def total(calls, key):
    vals = [(c["usage"] or {}).get(key) for c in calls]
    return round(sum(vals), 6) if vals and all(isinstance(v, (int, float)) for v in vals) else None


def declare(project_id, out):
    proj = project.load(project_id)
    cfg_schema, cfg = project.config("unit_schema"), project.config("decompose")
    read = unit_schema.git_reader(proj["repo_dir"], f"origin/{proj['base_branch']}", 120)
    spec_text, gdd_text = read(cfg_schema["spec_path"]), read(cfg_schema["gdd_path"])
    prompt = build_prompt(spec_text, gdd_text)
    calls, session, d, problems = [], None, None, ["未実行"]
    for attempt in range(RETRIES + 1):
        rc, text, usage, seconds, session = call_claude(prompt, cfg, session)
        d, why = extract(text) if rc == 0 else (None, f"分解役が異常終了（rc={rc}）")
        problems = [why] if why else check(d, spec_text, gdd_text, proj["test_dir"], proj["impl_dir"])
        calls.append({"attempt": attempt + 1, "rc": rc, "seconds": seconds, "usage": usage,
                      "prompt_chars": len(prompt), "problems": problems[:10]})
        print(f"呼び出し {attempt + 1}: rc={rc}、{seconds} 秒、{usage.get('cost_usd')} USD、問題 {len(problems)} 件")
        if not problems or rc != 0 or not session:
            break
        prompt = ("書いた JSON は検査に通りませんでした。次の問題を直した JSON 全体を、説明なしで返してください。\n"
                  + "\n".join(f"- {p}" for p in problems[:10]))
    out = Path(out)
    record = {"tool": "declare", "model_note": "config/decompose.json の cli の既定のモデル。道具なし（--tools \"\"）",
              "gdd_sha256": hashlib.sha256(gdd_text.encode("utf-8")).hexdigest(), "ok": not problems,
              "calls": calls,
              "total": {"calls": len(calls), "seconds": round(sum(c["seconds"] for c in calls), 1),
                        **{k: total(calls, k) for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                                         "cache_creation_tokens", "cost_usd")}}}
    (out / "results").mkdir(parents=True, exist_ok=True)
    (out / "results" / "precost.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8", newline="\n")
    if not problems:
        (out / "contract.json").write_text(json.dumps(d["interface"], ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")
        (out / "properties.json").write_text(json.dumps(d["properties"], ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2 の契約と性質の宣言を分解役に書かせ、事前投資を測る")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    rec = declare(args.project, args.out)
    t = rec["total"]
    print(f"{'合格' if rec['ok'] else '不合格'}：呼び出し {t['calls']} 回、{t['seconds']} 秒、{t['cost_usd']} USD")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
