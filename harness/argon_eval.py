"""Argon：別系統の評価役（W-172）。

リポジトリ全体（main とすべての開いている PR 枝の写し）を読んだうえで、
木の節とタスクを事前（子の節・タスクでねらいに届くか）と事後（届いたか）に定性で評価する。
記録は config/hline.json の argon（out/hline/argon/）に JSONL で残す。
総監督 47・48 本目（2026-10-25）に基づく実装。
"""
import datetime
import json
from pathlib import Path
import re
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_pinned
import agy_stream
import exitcode
from hline_base import ROOT, load_config
import machine
import model_pin
import proc
import progress
import yaml


VALID_DECISIONS = {"LOW", "MEDIUM", "HIGH"}
FALLBACK_DECISION = "HIGH"
DECISION_LINE_RE = re.compile(r"^判定\s*[:：]\s*(LOW|MEDIUM|HIGH)\s*$", re.IGNORECASE)
RATIONALE_LINE_RE = re.compile(r"^判断点\s*[:：]\s*(.+)$", re.IGNORECASE)


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
    写しが存在しない、または SHA が読めないときは ArgonError で落とす。
    戻り値: (copies_dict, shas_dict)
    """
    if "mirror" not in cfg:
        raise ArgonError("config に mirror がありません")

    mirror_path = cfg["mirror"]
    mirror_dir = Path(mirror_path)
    if not mirror_dir.exists() or not mirror_dir.is_dir():
        raise ArgonError(f"main の写しの場所が存在しません: {mirror_dir}")

    main_sha = _read_mirror_sha(mirror_dir)
    if not main_sha:
        raise ArgonError(f"main の写しの .mirror.json から SHA が読めません: {mirror_dir}")

    copies = {}
    shas = {}

    copies["main"] = _read_tree_files(mirror_dir)
    shas["main"] = main_sha

    # 枝の写し（branches）: 設定に無いのは誤りなので落とす（総監督 49 本目 §4）
    if "branches" not in cfg:
        raise ArgonError("config に branches がありません")

    branches_path = cfg["branches"]
    branches_dir = Path(branches_path)
    if branches_dir.exists() and branches_dir.is_dir():
        for child in sorted(branches_dir.iterdir()):
            if child.is_dir():
                branch_sha = _read_branch_sha(child)
                if not branch_sha:
                    raise ArgonError(f"枝の写し {child.name} の .branch.json から SHA が読めません")
                copy_key = f"branches/{child.name}"
                copies[copy_key] = _read_tree_files(child)
                shas[copy_key] = branch_sha

    return copies, shas


def _read_mirror_sha(mirror_dir):
    meta = mirror_dir / ".mirror.json"
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            sha = doc.get("sha")
            if sha and isinstance(sha, str) and sha.strip():
                return sha.strip()
        except Exception:
            pass
    return None


def _read_branch_sha(branch_dir):
    meta = branch_dir / ".branch.json"
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            sha = doc.get("sha")
            if sha and isinstance(sha, str) and sha.strip():
                return sha.strip()
        except Exception:
            pass
    return None


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
        child_reqs = [r for r in requirements.values() if target_id in (r.get("depends_on") or [])]
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
        parent_req_id = target.get("serves")
        parent_req = requirements.get(parent_req_id) if parent_req_id else None
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
    """モデルの応答から判定と判断点を抽出する（総監督 48 本目 §3-2）。
    - 行の頭から '判定:' で始まり、値が 1 つだけで行が終わる行に限る
    - その行が 1 つも無いとき、2 つ以上あるとき、値が 3 つ以外のときは HIGH（安全倒し）
    """
    decision_matches = []
    rationale_matches = []

    for line in response_text.splitlines():
        line = line.strip()
        m_dec = DECISION_LINE_RE.match(line)
        if m_dec:
            decision_matches.append(m_dec.group(1).upper())
        m_rat = RATIONALE_LINE_RE.match(line)
        if m_rat:
            rationale_matches.append(m_rat.group(1).strip())

    rationale = rationale_matches[-1] if rationale_matches else ""

    # 1 件のみマッチした場合のみ採用
    if len(decision_matches) == 1:
        dec = decision_matches[0]
        if dec in VALID_DECISIONS:
            return dec, rationale

    # 0 件、または複数行の一致、または不正値の場合はすべて HIGH に倒す
    fallback_reason = "判定行が 1 件に特定できないか不正なため HIGH に安全倒し"
    if not rationale:
        rationale = fallback_reason
    else:
        rationale = f"{rationale}（{fallback_reason}）"
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

    # 1. すべての写し（main と branches）を読み取る（写し不備時は ArgonError）
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
        # 本物のモデル呼び出し（総監督 48 本目 §3-1、呼べないときは落とす）
        response_text, usage = _call_argon_model(prompt, cfg)
    else:
        response_text, usage = model_fn(prompt)
    duration = time.time() - start_time
    if "seconds" not in usage:
        usage["seconds"] = round(duration, 3)

    # 5. 判定と判断点の抽出（欠落・重複・不正時は HIGH に安全倒し）
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
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "seconds": usage.get("seconds", 0.0),
            "tokens_estimated": usage.get("tokens_estimated", False),
        },
        "prompt_chars": len(prompt),
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
        "2. 事前評価の場合は『子の節・タスクでねらいに届くか』を論じてください。",
        "3. 事後評価の場合は『済んだタスク・PR がねらいに届いたか』を論じてください。",
        "4. 評価文の末尾に、必ず次の形式で構造化判定を添えてください（行頭から記載、値は 1 つのみ）：",
        "判定: LOW | MEDIUM | HIGH のいずれか 1 つ",
        "判断点: <1行の要約理由>",
    ])

    return "\n".join(lines)


def _call_argon_model(prompt, cfg, run_fn=proc.run):
    """config/hline.json の evaluator 設定に従い agy CLI 等で本物のモデルを呼ぶ。
    agy_pinned による指紋検査、model_pin によるモデル固定検査、agy_stream.args による引数構築、
    実行後の報告モデル照合を行い、違えば落として記録を書かない（総監督 50 本目 §3）。
    プロンプトは標準入力で渡し、引数には載せない。
    """
    evaluator = cfg.get("evaluator")
    if not evaluator or not isinstance(evaluator, dict):
        raise ArgonError("config/hline.json に evaluator 設定がありません")

    where = "config/hline.json の evaluator"
    cli = evaluator.get("cli")
    if not cli:
        raise ArgonError(f"{where} に cli が指定されていません")

    # CLI のパス解決
    exe_path = shutil.which(cli)
    if not exe_path:
        for bin_key in ("agy_bin", "bin"):
            bin_dir = machine.names().get(bin_key)
            if bin_dir:
                candidate = Path(bin_dir) / cli
                if candidate.exists():
                    exe_path = str(candidate)
                    break

    if not exe_path:
        raise ArgonError(f"evaluator の CLI コマンドが見つかりません: {cli}")

    ttl = int(evaluator.get("ttl_seconds", 600))

    if agy_stream.is_stream(evaluator):
        try:
            agy_pinned.require()
        except agy_pinned.AgyDigestError as e:
            raise ArgonError(f"評価役 CLI の指紋照合に失敗しました: {e}") from e
        try:
            model_pin.require_implementer(evaluator, where)
        except model_pin.ModelPinError as e:
            raise ArgonError(str(e)) from e
        cmd = agy_stream.args(evaluator, [exe_path], prompt, ttl)
        stdin_data = agy_stream.stdin_for(evaluator, prompt)
    else:
        model = evaluator.get("model") or evaluator.get("model_name")
        if not model:
            raise ArgonError(f"{where} に model が指定されていません")
        cmd = [exe_path, evaluator.get("headless_flag", "-p"), evaluator.get("model_flag", "--model"), model]
        cmd += list(evaluator.get("output_format_args") or [])
        cmd += list(evaluator.get("extra_flags") or [])
        stdin_data = prompt

    start_time = time.time()
    try:
        code, out, err = run_fn(cmd, cwd=ROOT, ttl=ttl, label="argon_eval", input=stdin_data)
    except Exception as e:
        raise ArgonError(f"evaluator CLI の実行に失敗しました: {e}") from e
    duration = time.time() - start_time

    if code != 0:
        err_msg = (err or "").strip() or f"exit code {code}"
        raise ArgonError(f"evaluator CLI がエラーで終了しました: {err_msg}")

    stdout_text = (out or "").strip()
    if not stdout_text:
        raise ArgonError("evaluator CLI の出力が空でした")

    if agy_stream.is_stream(evaluator):
        parsed = agy_stream.parse(stdout_text)
        response_text = (parsed.get("response") or "").strip()
        if not response_text:
            raise ArgonError("evaluator CLI の応答本文（response）が空でした")

        # 実際に走ったモデルの照合（総監督 50 本目 §3）
        try:
            reported_model = model_pin.check_reported(evaluator["model_name"], parsed.get("model"), where)
        except model_pin.ModelPinError as e:
            raise ArgonError(str(e)) from e

        usage_info = parsed.get("usage") or {}
        p_tokens = usage_info.get("input_tokens")
        c_tokens = usage_info.get("output_tokens")
    else:
        response_text = stdout_text
        reported_model = evaluator.get("model_name") or evaluator.get("model")
        p_tokens = None
        c_tokens = None

    if p_tokens is not None and c_tokens is not None:
        usage = {
            "model": reported_model,
            "seconds": round(duration, 3),
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "tokens_estimated": False,
        }
    else:
        usage = {
            "model": reported_model,
            "seconds": round(duration, 3),
            "prompt_tokens": None,
            "completion_tokens": None,
            "tokens_estimated": True,
        }

    return response_text, usage


def main(argv=None, cfg=None):
    """CLI 入口。引数がちょうど 2 つ（種類と対象）でないときは 1 で終了し記録を書かない。"""
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) != 2:
        print("使い方: python -m harness.argon_eval <pre|post> <target_id_or_pr>", file=sys.stderr)
        return 1

    kind = argv[0].lower()
    target = argv[1]

    try:
        record = evaluate(kind, target, cfg=cfg)
        print(f"Argon 評価完了 [{record['kind']}: {record['target']}]")
        print(f"判定: {record['decision']} / 判断点: {record['rationale']}")
        print(f"費用: {record['usage']['seconds']}s (prompt chars: {record['prompt_chars']})")
        return 0
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
