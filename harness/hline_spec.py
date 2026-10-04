"""H ライン（harness/hline.py）の TaskSpec：分解役が What を TaskSpec JSON に変え、Gate A がスキーマで検査し、
Gate 1 が TaskSpec の編集境界を検査する。

**なぜ要るか**: 実装役に自然言語の What を直接渡すと、解釈の揺らぎがそのまま変更の揺らぎになる。スキーマで拘束した
TaskSpec だけを渡し、適合しなければ実装役を呼ばない（Gate A）。編集境界の外への変更は機械で落とす（Gate 1）。
スキーマは config/taskspec.schema.json。検証器は標準ライブラリだけで書いた最小のもの（使うキーワードだけ）。
"""
import fnmatch
import json
from pathlib import Path

from hline_base import ROOT, must, run_agent, write_json  # isort: skip（harness/ を import の道に足す）

import progress  # noqa: E402

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def load_schema(cfg):
    return json.loads((ROOT / cfg["taskspec_schema"]).read_text(encoding="utf-8"))


def _is(kind, v):
    if kind in ("integer", "number"):
        return (isinstance(v, int) and not isinstance(v, bool)) or (kind == "number" and isinstance(v, float))
    return isinstance(v, TYPES[kind])


def validate(schema, doc, path="$"):
    """違反の一覧（空なら適合）。type・enum・required・properties・additionalProperties・items・minItems・minLength・
    minimum・maximum だけを解く。"""
    if "enum" in schema and doc not in schema["enum"]:
        return [f"{path}: {schema['enum']} のどれかでなければなりません（{doc!r}）"]
    if "type" in schema and not _is(schema["type"], doc):
        return [f"{path}: {schema['type']} でなければなりません（{type(doc).__name__}）"]
    out = []
    if isinstance(doc, dict):
        out += [f"{path}: {k} がありません" for k in schema.get("required", []) if k not in doc]
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            out += [f"{path}: {k} は定義されていません" for k in doc if k not in props]
        for k, sub in props.items():
            if k in doc:
                out += validate(sub, doc[k], f"{path}.{k}")
    if isinstance(doc, list):
        if len(doc) < schema.get("minItems", 0):
            out.append(f"{path}: 要素が {schema['minItems']} 件以上必要です（{len(doc)} 件）")
        for n, v in enumerate(doc):
            out += validate(schema.get("items", {}), v, f"{path}[{n}]")
    if isinstance(doc, str) and len(doc) < schema.get("minLength", 0):
        out.append(f"{path}: 空の文字列です")
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            out.append(f"{path}: {schema['minimum']} 以上でなければなりません（{doc}）")
        if "maximum" in schema and doc > schema["maximum"]:
            out.append(f"{path}: {schema['maximum']} を超えられません（{doc}）")
    return out


def gate_a(schema, spec, command=None):
    """Gate A：TaskSpec がスキーマに適合し、タスクを宣言した What では進捗の検証コマンドを含むこと。違反の一覧。"""
    problems = validate(schema, spec)
    if not problems and command and spec["test_oracle"].get("verification_command") != command:
        problems.append(f"$.test_oracle.verification_command: 進捗の検証コマンド `{command}` をそのまま入れてください")
    return problems


def verification_of(wt, task_id):
    """作業ツリーの docs/progress.yaml にある、タスクの検証 {command, expected_exit_code}。無ければ None。"""
    try:
        return progress.task(progress.load(Path(wt) / progress.REL_PATH), task_id).get("verification")
    except (progress.ProgressError, OSError):
        return None


# ============================================================ 分解役

def decompose_prompt(what, meta, schema, command, problems):
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の分解役です。次の What を、実装役に渡す TaskSpec（JSON）に変えてください。",
         "- 作業ディレクトリのリポジトリを読み、対象シンボルと編集境界を実在のファイルに合わせる（読み取りだけ。ファイルは作らない）",
         "- 実装役には What の本文を渡さない。実装に要る事柄はすべて TaskSpec に書く",
         "- allowed_files には、実装と、合格条件ごとの unittest（tests/）を書くファイルを入れる。max_diff_lines は 300 以下",
         "- JSON だけを返す。前後に説明を書かない。次の JSON Schema に適合させる", "",
         json.dumps(schema, ensure_ascii=False, indent=1)]
    if meta["task"]:
        p += ["", f"この What は進捗のタスク {meta['task']} です。test_oracle.verification_command に、次の検証コマンドをそのまま入れる: "
              f"`{command}`"]
    p += ["", "---", what.strip()]
    if problems:
        p += ["", "---", "前回の TaskSpec はスキーマに適合しませんでした。次の違反を直してください。"] + [f"- {x}" for x in problems]
    return "\n".join(p) + "\n"


def extract_json(text):
    """分解役の返答から JSON を取り出す。(値, None) か (None, 理由)。コードブロックで囲まれていても読む。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0]
    for cand in (s, s[s.find("{"):s.rfind("}") + 1]):
        try:
            return json.loads(cand), None
        except ValueError as e:
            why = str(e)
    return None, f"JSON として読めません: {why}"


def decompose(cfg, wt, what, meta, outdir):
    """分解役を呼んで TaskSpec にする。(TaskSpec か None, 記録)。スキーマに適合しなければ、違反を返してやり直し（上限つき）、
    それでも適合しなければ None を返す。None のときは実装役を呼ばない。"""
    schema, command, record = load_schema(cfg), None, {"attempts": [], "reason": None}
    if meta["task"]:
        v = verification_of(wt, meta["task"])
        if not v:
            record["reason"] = f"進捗のタスク {meta['task']} の検証コマンドが見つかりません"
            return None, record
        command = v["command"]
    problems = None
    for n in range(1, 2 + cfg["spec_retries"]):
        code, models, out = run_agent(cfg["decomposer"], wt, cfg["ttl_seconds"]["decomposer"], "分解役",
                                      decompose_prompt(what, meta, schema, command, problems),
                                      Path(outdir) / f"decomposer-{n}.log")
        result = json.loads(out).get("result")
        spec, why = extract_json(result if isinstance(result, str) else "")
        problems = [why] if why else gate_a(schema, spec, command)
        record["attempts"].append({"attempt": n, "cli_exit": code, "models": models, "valid": not problems})
        if not problems:
            write_json(Path(outdir) / "taskspec.json", spec)
            return spec, record
    record["reason"] = "TaskSpec がスキーマに適合しません: " + " / ".join(problems)
    return None, record


# ============================================================ 編集境界（Gate 1）

def matches(path, pattern):
    path, pattern = path.replace("\\", "/").removeprefix("./"), pattern.replace("\\", "/").removeprefix("./")
    return path.startswith(pattern) if pattern.endswith("/") else (
        fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern + "/"))


def diff_lines(cfg, wt):
    """作業ツリーの HEAD からの差分の行数（追加＋削除。新しいファイルを含む。バイナリは数えない）。"""
    t = cfg["ttl_seconds"]["git"]
    must(["git", "add", "-A", "-N"], wt, t, "git add -N")
    total = 0
    for line in must(["git", "diff", "HEAD", "--numstat"], wt, t, "git diff").splitlines():
        added, deleted, _ = (line.split("\t") + ["", "", ""])[:3]
        total += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
    return total


def boundary_problems(spec, paths, lines):
    """TaskSpec の編集境界の違反（実装役に返す理由）。変えてはならないファイルが、変えてよいファイルより優先する。"""
    b, out = spec["edit_boundary"], []
    forbidden = [p for p in paths if any(matches(p, q) for q in b["forbidden_files"])]
    outside = [p for p in paths if p not in forbidden and not any(matches(p, q) for q in b["allowed_files"])]
    if forbidden:
        out.append(f"変えてはならないファイルを変えています: {', '.join(forbidden)}。元に戻してください。")
    if outside:
        out.append(f"変えてよいファイルの外を変えています: {', '.join(outside)}（許可: {', '.join(b['allowed_files'])}）。"
                   "元に戻してください。")
    if lines > b["max_diff_lines"]:
        out.append(f"差分が {lines} 行で、上限 {b['max_diff_lines']} 行を超えています。小さくしてください。")
    return out

