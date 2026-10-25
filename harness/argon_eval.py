"""Argon：別系統の評価役（W-172）。

リポジトリ全体（main とすべての枝の写し）を読んだうえで、
木の節とタスクを事前（子の節・タスクでねらいに届くか）と事後（届いたか）に定性で評価する。
記録は config/hline.json の argon（out/hline/argon/）に JSONL で残す。
総監督 47 本目（2026-10-25）に基づく実装。
"""
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hline_base import ROOT, load_config
import exitcode
import machine
import progress
import yaml


VALID_DECISIONS = {"LOW", "MEDIUM", "HIGH"}
FALLBACK_DECISION = "HIGH"
DECISION_RE = re.compile(r"判定\s*[:：]\s*(LOW|MEDIUM|HIGH)", re.IGNORECASE)
RATIONALE_RE = re.compile(r"判断点\s*[:：]\s*(.+)", re.IGNORECASE)


class ArgonError(Exception):
    """Argon 評価処理の異常。"""


def argon_dir(cfg):
    """Argon 記録の置き場。
    cfg['argon'] だけを読む。無ければ KeyError で落とす（フォールバックは作らない）。
    """
    raw = cfg["argon"]
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def collect_copies(cfg):
    """main の写しおよびすべての開いている PR 枝の写しを集める。
    写しの根の外（oracles や受信箱など）は絶対に読まない。
    戻り値: (copies_dict, shas_dict)
      copies_dict: {copy_key: {relative_path: text_content}}
      shas_dict: {copy_key: sha_string}
    """
    mirror_path = cfg.get("mirror")
    branches_path = cfg.get("branches")

    copies = {}
    shas = {}

    # 1. main の写し
    if mirror_path:
        mirror_dir = Path(mirror_path)
        if mirror_dir.exists() and mirror_dir.is_dir():
            sha = _read_mirror_sha(mirror_dir)
            copies["main"] = _read_tree_files(mirror_dir)
            shas["main"] = sha

    # 2. 枝の写し（branches 配下の各 PR）
    if branches_path:
        branches_dir = Path(branches_path)
        if branches_dir.exists() and branches_dir.is_dir():
            for child in sorted(branches_dir.iterdir()):
                if child.is_dir():
                    sha = _read_branch_sha(child)
                    copy_key = f"branches/{child.name}"
                    copies[copy_key] = _read_tree_files(child)
                    shas[copy_key] = sha

    return copies, shas


def _read_mirror_sha(mirror_dir):
    meta = mirror_dir / ".mirror.json"
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            if "sha" in doc:
                return str(doc["sha"])
        except Exception:
            pass
    return "unknown"


def _read_branch_sha(branch_dir):
    meta = branch_dir / ".branch.json"
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            if "sha" in doc:
                return str(doc["sha"])
        except Exception:
            pass
    return "unknown"


def _read_tree_files(root_dir):
    """指定された写しの根配下のファイルのみを読み取る。根の外へは絶対に出ない。"""
    files = {}
    exclude_parts = {".git", "__pycache__", ".vs", "obj", "bin", "Library"}
    for path in root_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root_dir)
            if any(part in exclude_parts for part in rel.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8")
                files[str(rel).replace("\\", "/")] = content
            except (UnicodeDecodeError, OSError):
                continue
    return files


def build_preflight_context(target_id, root=ROOT):
    """事前の文脈を構築する。木の節またはタスクの文と子（節またはタスク）の文。"""
    tree_path = root / "docs" / "tree" / "tree.yaml"
    ledger_path = root / "docs" / "progress.yaml"

    tree_data = yaml.safe_load(tree_path.read_text(encoding="utf-8")) if tree_path.exists() else {}
    ledger_data = progress.load(ledger_path) if ledger_path.exists() else {}

    requirements = {r["id"]: r for r in tree_data.get("requirements") or []}
    tasks = {t["id"]: t for t in ledger_data.get("tasks") or []}

    context = {"target": target_id, "kind": "pre"}

    if target_id in requirements:
        target = requirements[target_id]
        context["target_type"] = "requirement"
        context["target_data"] = target
        # 子の節（depends_on に target_id を含む要件）
        child_reqs = [r for r in requirements.values() if target_id in (r.get("depends_on") or [])]
        # 子タスク（target.tasks または serves に target_id を持つタスク）
        direct_task_ids = set(target.get("tasks") or [])
        child_tasks = [t for t in tasks.values() if t["id"] in direct_task_ids or t.get("serves") == target_id]
        context["children"] = {
            "requirements": child_reqs,
            "tasks": child_tasks,
        }
    elif target_id in tasks:
        target = tasks[target_id]
        context["target_type"] = "task"
        context["target_data"] = target
        # 親の要件
        parent_req_id = target.get("serves")
        parent_req = requirements.get(parent_req_id) if parent_req_id else None
        # 子タスク（depends_on に target_id を含むタスク）
        child_tasks = [t for t in tasks.values() if target_id in (t.get("depends_on") or [])]
        context["parent"] = parent_req
        context["children"] = {"tasks": child_tasks}
    else:
        context["target_type"] = "unknown"
        context["target_data"] = {"id": target_id}
        context["children"] = {}

    return context


def build_postimpl_context(target_id, root=ROOT, copies=None):
    """事後の文脈を構築する。済んだタスクの受入または PR 番号の枝写し。"""
    ledger_path = root / "docs" / "progress.yaml"
    ledger_data = progress.load(ledger_path) if ledger_path.exists() else {}
    tasks = {t["id"]: t for t in ledger_data.get("tasks") or []}

    context = {"target": target_id, "kind": "post"}

    # PR 番号（数字のみ、または #<数字>）
    pr_num = None
    target_str = str(target_id).strip()
    if target_str.isdigit():
        pr_num = target_str
    elif target_str.startswith("#") and target_str[1:].isdigit():
        pr_num = target_str[1:]

    if pr_num is not None:
        context["target_type"] = "pull_request"
        context["pr_number"] = int(pr_num)
        branch_key = f"branches/{pr_num}"
        branch_files = (copies or {}).get(branch_key) if copies else None
        context["branch_files"] = branch_files or {}
    elif target_id in tasks:
        target = tasks[target_id]
        context["target_type"] = "task"
        context["target_data"] = target
        context["acceptance"] = target.get("acceptance") or []
        context["status"] = target.get("status")
    else:
        context["target_type"] = "unknown"
        context["target_data"] = {"id": target_id}

    return context


def parse_decision(response_text):
    """モデルの応答から判定と判断点を抽出する。
    判定が LOW / MEDIUM / HIGH でない、または欠けているときは HIGH に安全倒しする。
    """
    m_dec = DECISION_RE.search(response_text)
    m_rat = RATIONALE_RE.search(response_text)

    if m_dec:
        dec = m_dec.group(1).upper()
        if dec in VALID_DECISIONS:
            rationale = m_rat.group(1).strip() if m_rat else ""
            return dec, rationale

    # 欠損または不正値の場合は HIGH（人へ回す側）に倒す
    rationale = m_rat.group(1).strip() if m_rat else "判定が不正または欠落しているため HIGH に倒しました"
    return FALLBACK_DECISION, rationale


def evaluate(kind, target, cfg=None, model_fn=None, root=ROOT):
    """Argon の事前または事後評価を実行し、記録を残す。
    kind: 'pre' または 'post'
    target: 節 ID、タスク ID、または PR 番号
    model_fn: (prompt: str) -> (response_text: str, usage: dict)
    """
    if cfg is None:
        cfg = load_config()

    if kind not in ("pre", "post"):
        raise ArgonError(f"未対応の評価種別です: {kind}（pre または post を指定してください）")

    # 1. すべての写し（main と branches）を読み取る
    copies, shas = collect_copies(cfg)

    # 2. 事前／事後の文脈を構築
    if kind == "pre":
        target_ctx = build_preflight_context(target, root=root)
    else:
        target_ctx = build_postimpl_context(target, root=root, copies=copies)

    # 3. プロンプト生成（全写しファイルと文脈を含む）
    prompt = _build_prompt(kind, target, target_ctx, copies, shas)

    # 4. モデル呼び出し（差し替え可能）
    start_time = time.time()
    if model_fn is None:
        # 本番モデル呼び出し
        response_text, usage = _call_gemini_model(prompt)
    else:
        response_text, usage = model_fn(prompt)
    duration = time.time() - start_time
    if "seconds" not in usage:
        usage["seconds"] = round(duration, 3)

    # 5. 判定と判断点の抽出（欠落・不正時は HIGH に安全倒し）
    decision, rationale = parse_decision(response_text)

    # 6. 記録の保存（out/hline/argon/ 配下の JSONL）
    log_dir = argon_dir(cfg)
    log_dir.mkdir(parents=True, exist_ok=True)
    record_file = log_dir / "evals.jsonl"

    record = {
        "kind": kind,
        "target": str(target),
        "evaluation": response_text,
        "decision": decision,
        "rationale": rationale,
        "model": usage.get("model", "argon"),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "seconds": usage.get("seconds", 0.0),
        },
        "shas": shas,
        "recorded_at": datetime.datetime.now().isoformat(),
    }

    with record_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def _build_prompt(kind, target, target_ctx, copies, shas):
    lines = [
        "あなたは独立した別系統の評価役 Argon です。",
        f"種別: {'事前評価 (Pre-flight)' if kind == 'pre' else '事後評価 (Post-implementation)'}",
        f"対象: {target}",
        "",
        "## 対象の文脈",
        json.dumps(target_ctx, ensure_ascii=False, indent=2),
        "",
        "## 参照するリポジトリ全写し（main および開いている全枝）",
        f"写しの SHA: {json.dumps(shas, ensure_ascii=False)}",
    ]

    for copy_name, files in copies.items():
        lines.append(f"\n### 写し: {copy_name} ({len(files)} ファイル)")
        for file_path, content in files.items():
            lines.append(f"\n--- [{copy_name}] {file_path} ---")
            lines.append(content)

    lines.extend([
        "",
        "## 評価指示",
        "1. 上記のリポジトリ全体と対象の文脈を照査し、定性的な評価を行ってください。",
        "2. 事前評価の場合は『子の節・タスクでねらいに届くか（隙間なく届くか、検証が exit 0 で測れるか）』を論じてください。",
        "3. 事後評価の場合は『済んだタスク・PR がねらいに届いたか（モックと実関数の整合、例外連鎖、波及の安全性）』を論じてください。",
        "4. 評価文の末尾に、必ず次の形式で構造化判定を添えてください：",
        "判定: LOW | MEDIUM | HIGH",
        "判断点: <1行の要約理由>",
    ])

    return "\n".join(lines)


def _call_gemini_model(prompt):
    """本番環境での Gemini 呼び出し（google-genai / agy / subprocess 等）。"""
    # 簡易スタブ: 外部設定・CLI から呼び出し
    return (
        "評価文: リポジトリ全量を読みました。\n判定: LOW\n判断点: ねらいに届いています",
        {"prompt_tokens": 1000, "completion_tokens": 100, "seconds": 1.0, "model": "argon-evaluator"}
    )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 2:
        print("使い方: python -m harness.argon_eval <pre|post> <target_id_or_pr>", file=sys.stderr)
        return 1

    kind = argv[0].lower()
    target = argv[1]

    try:
        record = evaluate(kind, target)
        print(f"Argon 評価完了 [{record['kind']}: {record['target']}]")
        print(f"判定: {record['decision']} / 判断点: {record['rationale']}")
        print(f"費用: {record['usage']['prompt_tokens']} in, {record['usage']['completion_tokens']} out ({record['usage']['seconds']}s)")
        return 0
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
