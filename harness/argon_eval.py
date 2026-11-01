"""Argon：別系統の評価役（W-172）。

リポジトリ全体（main とすべての開いている PR 枝の写し）を読んだうえで、
木の節とタスクを事前（子の節・タスクでねらいに届くか）と事後（届いたか）に定性で評価する。
記録は config/hline.json の argon（out/hline/argon/）に JSONL で残す。
総監督 47・48 本目（2026-10-25）に基づく実装。

    python -m harness.argon_eval <pre|post> <対象> [--tools] [--with <ファイル>]

**読み方（2026-10-28）**: 既定（inline）は写しの全ファイルの中身を 1 つの頼みに詰める。写しは約 890 万字あり
（10-27 に数えた）、1 回の入力に入らない見込みで、本物の呼び出しは一度も無かった。--tools は中身を詰めず、
写しの場所を教えて agy に道具で読ませる（作業場所を main の写しにし、枝の写しの置き場を --add-dir で足す。
写しの根の外は作業場所に入れない）。事後で対象が PR のときは、その枝と main の違い（diff）を頼みに入れる。
--with は、照らしてほしい仮説と予想（単位の札など）を頼みに足す。
"""
import datetime
import difflib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_pinned
import agy_stream
import exitcode
from hline_base import ROOT, load_config
import model_pin
import proc
import progress
import yaml


READ_MODES = ("inline", "tools")
DIFF_LIMIT = 80_000   # tools のとき頼みに入れる diff の字の上限
DIFF_CUT_NOTE = "（ここで切った）"
INLINE_MAX_CHARS = 1_000_000   # 組んだ頼みの字の上限（読み方と --with を問わない。超えたらモデルを呼ばずに止める）

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


def _pr_number(target):
    s = str(target).strip()
    s = s[1:] if s.startswith("#") else s
    return s if s.isdigit() else None


def _pr_changes(copies, pr_num, limit=DIFF_LIMIT):
    """枝の写しと main の写しの違い：{"changed": [{"path", "change"}], "diff": unified diff（limit 字まで）}。"""
    branch_key = f"branches/{pr_num}"
    if not copies or branch_key not in copies:
        raise ArgonError(f"PR #{pr_num} の枝の写し ({branch_key}) が存在しません。先に枝を同期してください。")
    main = copies.get("main") or {}
    branch = copies.get(branch_key) or {}
    changed, chunks = [], []
    for path in sorted(set(main) | set(branch)):
        before, after = main.get(path), branch.get(path)
        if before == after:
            continue
        change = "added" if before is None else ("deleted" if after is None else "modified")
        changed.append({"path": path, "change": change})
        chunks.append("".join(difflib.unified_diff((before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True),
                                                   fromfile=f"main/{path}", tofile=f"branches/{pr_num}/{path}")))
    diff = "\n".join(c for c in chunks if c)
    if len(diff) > limit:
        diff = diff[:limit] + "\n" + DIFF_CUT_NOTE
    return {"changed": changed, "diff": diff}


def evaluate(kind, target, cfg=None, model_fn=None, root=ROOT, read_mode="inline", focus=None):
    """Argon の事前または事後評価を実行し、記録を残す。
    kind: 'pre' または 'post'
    target: 節 ID、タスク ID、または PR 番号
    model_fn: (prompt: str) -> (response_text: str, usage: dict)
    read_mode: 'inline'（写しの中身を詰める）または 'tools'（写しの場所を教えて道具で読ませる）
    focus: 照らしてほしい仮説と予想の文（頼みに足す）
    """
    if cfg is None:
        cfg = load_config()

    if kind not in ("pre", "post"):
        raise ArgonError(f"未対応の評価種別です: {kind}（pre または post を指定してください）")
    if read_mode not in READ_MODES:
        raise ArgonError(f"未対応の読み方です: {read_mode}（{' または '.join(READ_MODES)}）")

    # 1. すべての写し（main と branches）を読み取る（写し不備時は ArgonError）
    copies, shas = collect_copies(cfg)

    # 2. 事前／事後の文脈を構築
    if kind == "pre":
        target_ctx = build_preflight_context(target, root=root)
    else:
        target_ctx = build_postimpl_context(target, root=root, copies=copies)
        if read_mode == "tools" and target_ctx.get("target_type") == "pull_request":
            target_ctx.pop("branch_files", None)   # 中身は詰めず、違いだけを入れる
            target_ctx["changes"] = _pr_changes(copies, _pr_number(target))

    # 3. プロンプト生成（inline は全写しファイル、tools は写しの場所と数）
    roots = _copy_roots(cfg, copies) if read_mode == "tools" else None
    prompt = _build_prompt(kind, target, target_ctx, copies, shas, roots=roots, focus=focus)
    if len(prompt) > INLINE_MAX_CHARS:
        raise ArgonError(f"頼みが {len(prompt)} 字で、上限の {INLINE_MAX_CHARS} 字を超えます。--with で材料を絞るか、--tools の読み方にしてください")

    # 4. モデル呼び出し（差し替え可能）
    start_time = time.time()
    if model_fn is None:
        # 本物のモデル呼び出し（総監督 48 本目 §3-1、呼べないときは落とす）
        if read_mode == "tools":
            response_text, usage = _call_argon_model(prompt, cfg, cwd=Path(cfg["mirror"]), add_dirs=(Path(cfg["branches"]),))
        else:
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
        "read_mode": read_mode,
        "focus_chars": len(focus or ""),
    }
    if usage.get("tool_calls") is not None:
        record["usage"]["tool_calls"] = usage["tool_calls"]

    with record_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def _copy_roots(cfg, copies):
    """{写しの名前: 根の場所}。main は cfg["mirror"]、枝は cfg["branches"] の下の番号。"""
    return {name: str(Path(cfg["mirror"]) if name == "main" else Path(cfg["branches"]) / name.split("/", 1)[1])
            for name in copies}


def _build_prompt(kind, target, target_ctx, copies, shas, roots=None, focus=None):
    """roots が無ければ（inline）写しの全ファイルの中身を詰める。roots があれば（tools）場所と数だけを書く。"""
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

    if roots is None:
        for copy_name, files in copies.items():
            lines.append(f"\n### 写し: {copy_name} ({len(files)} ファイル)")
            for file_path, content in files.items():
                lines.append(f"\n--- [{copy_name}] {file_path} ---")
                lines.append(content)
    else:
        lines.append("写しの中身はこの頼みに入れていない。下の場所にあるファイルを、ファイルを読む道具と探す道具で、要るだけ自分で読んでよい。"
                     "読むのは写しの根の中だけ。コマンド（端末）は使わない（許しが無く、使うと走りが止まって何も残らない）。")
        for copy_name, files in copies.items():
            lines.append(f"- {copy_name}: {roots[copy_name]}（{len(files)} ファイル、中身の合計 {sum(len(c) for c in files.values())} 字）")

    if focus:
        lines.extend(["", "## 照らしてほしい仮説と予想", focus])

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


def _call_argon_model(prompt, cfg, run_fn=None, cwd=None, add_dirs=()):
    """config/hline.json の evaluator 設定に従い agy CLI 等で本物のモデルを呼ぶ。
    agy_pinned による指紋検査、model_pin によるモデル固定検査、agy_stream.args による引数構築、
    実行後の報告モデル照合を行い、違えば落として記録を書かない（総監督 50 本目 §3、51 本目 §3・§4）。
    プロンプトは標準入力で渡し、引数には載せない。
    cwd は作業場所（既定は ROOT）、add_dirs は `--add-dir` で作業場所に足す場所（tools の読み方）。
    """
    if run_fn is None:
        run_fn = proc.run
    evaluator = cfg.get("evaluator")
    if not evaluator or not isinstance(evaluator, dict):
        raise ArgonError("config/hline.json に evaluator 設定がありません")

    where = "config/hline.json の evaluator"
    cli = evaluator.get("cli")
    if not cli:
        raise ArgonError(f"{where} に cli が指定されていません")

    if not agy_stream.is_stream(evaluator):
        raise ArgonError(f"{where} の output_format_args に stream-json が指定されていません")

    try:
        agy_pinned.require()
    except agy_pinned.AgyDigestError as e:
        raise ArgonError(f"評価役 CLI の指紋照合に失敗しました: {e}") from e
    try:
        model_pin.require_implementer(evaluator, where)
    except model_pin.ModelPinError as e:
        raise ArgonError(str(e)) from e

    cli_cmd = proc.resolve_cli(cli)
    ttl = int(evaluator.get("ttl_seconds", 600))
    cmd = agy_stream.args(evaluator, cli_cmd, prompt, ttl, auto_approve=False)
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    stdin_data = agy_stream.stdin_for(evaluator, prompt)

    start_time = time.time()
    try:
        code, out, err = run_fn(cmd, cwd=cwd if cwd is not None else ROOT, ttl=ttl, label="argon_eval", input=stdin_data)
    except Exception as e:
        raise ArgonError(f"evaluator CLI の実行に失敗しました: {e}") from e
    duration = time.time() - start_time

    if code != 0:
        err_msg = (err or "").strip() or f"exit code {code}"
        raise ArgonError(f"evaluator CLI がエラーで終了しました: {err_msg}")

    stdout_text = (out or "").strip()
    if not stdout_text:
        raise ArgonError("evaluator CLI の出力が空でした")

    parsed = agy_stream.parse(stdout_text)
    response_text = (parsed.get("response") or "").strip()
    if not response_text:
        raise ArgonError("evaluator CLI の応答本文（response）が空でした")

    try:
        reported_model = model_pin.check_reported(evaluator["model_name"], parsed.get("model"), where)
    except model_pin.ModelPinError as e:
        raise ArgonError(str(e)) from e

    usage_info = parsed.get("usage") or {}
    p_tokens = usage_info.get("input_tokens")
    c_tokens = usage_info.get("output_tokens")

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
    usage["tool_calls"] = len([s for s in parsed.get("steps") or [] if s.get("tool")])

    return response_text, usage


USAGE = "使い方: python -m harness.argon_eval <pre|post> <target_id_or_pr> [--tools] [--with <ファイル>]"


def _parse_args(argv):
    """(kind, target, read_mode, focus_path)。形が違えば None。"""
    if len(argv) < 2:
        return None
    kind, target, rest = argv[0].lower(), argv[1], list(argv[2:])
    read_mode, focus_path = "inline", None
    while rest:
        flag = rest.pop(0)
        if flag == "--tools":
            read_mode = "tools"
        elif flag == "--with" and rest:
            focus_path = rest.pop(0)
        else:
            return None
    return kind, target, read_mode, focus_path


def main(argv=None, cfg=None):
    """CLI 入口。形が違う（種類と対象が無い・知らない旗・--with の後にファイルが無い）ときは 1 で終了し記録を書かない。
    -h・--help がどこかにあれば使い方を出して 0、対象が - で始まれば（旗の打ち違い）評価を呼ばずに使い方を出して 2。"""
    if argv is None:
        argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[1].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    parsed = _parse_args(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 1
    kind, target, read_mode, focus_path = parsed

    try:
        focus = Path(focus_path).read_text(encoding="utf-8") if focus_path else None
        record = evaluate(kind, target, cfg=cfg, read_mode=read_mode, focus=focus)
        usage = record.get("usage") or {}
        print(f"Argon 評価完了 [{record.get('kind', kind)}: {record.get('target', target)}]")
        print(f"判定: {record.get('decision')} / 判断点: {record.get('rationale', '')}")
        print(f"費用: {usage.get('seconds')}s (prompt chars: {record.get('prompt_chars')})")
        print(f"読み方: {record.get('read_mode', read_mode)}・道具の使用 {usage.get('tool_calls', '記録なし')} 回")
        return 0
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
