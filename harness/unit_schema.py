"""単位定義 v2 のスキーマ門（docs/design/mechanical_barriers.md §3、ADR-002 防壁②）。

    python harness/unit_schema.py --project falling-blocks --unit tools/units/issue_15.json

**何を拒絶するか**: 分解役（LLM）が単位定義に手計算のオラクルや C# を書き込むこと。
「書かないでください」とプロンプトで頼むのではなく、形として書けないようにする。

- インターフェース（作るものの形）は構造化フィールドだけで表す。C# の生文字列は受けない
- 受入条件は 1 行 = 1 回の状態遷移（horizon = 1）。操作は 1 行に 1 つ
- 期待値は GDD に拘束する。変えない / 構造化仕様 §5 の外部パラメーター / ±1 の増減 /
  自明なリテラル（0・1・真偽・null・空の列・列挙のメンバー名）のどれかでなければ拒絶する
- 各行の根拠（rule）は構造化仕様に実在する ID でなければ拒絶する
- 構造化仕様の gdd-sha256 が今の GDD と違えば、引いた値を信用しない

**このスキーマで手計算そのものは消せない**（同 §3.4）。保証するのは「期待値が GDD の文言だけで
1 ステップ分を確かめられる大きさに収まる」ことまで。

**v1 の扱い**: config/unit_schema.json に内容の sha256 がある既存の単位だけを通す。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import gdd_check
import project
from proc import run

SCHEMA = 2
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
IDENT_RE = re.compile(rf"^{IDENT}$")
# 識別子 / 名前空間付き / 識別子<識別子> と、それぞれの末尾の ?。式・本体・属性は書けない
NAME = rf"{IDENT}(?:\.{IDENT})*"
TYPE_RE = re.compile(rf"^{NAME}(?:<{NAME}\??>)?\??$")
SPEC_ID_RE = re.compile(r"^(LP|ST|RL|IF|PR)-\d{2,}$")
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PROMPT_FORBIDDEN = ["```", ";", "{", "}", "=>"]
BACKTICK_RE = re.compile(r"`([^`]*)`")

TOP_REQUIRED = {"schema", "id", "title", "prompt", "interface", "whitelist", "impl_files",
                "acceptance", "human_check_point", "playtest"}
TOP_OPTIONAL = {"required_symbols", "fast_test_project", "forbidden_leftover",
                "forbidden_skip_attribute_regex", "max_impl_lines", "forbidden_patterns",
                "selftest_forbidden_probe"}
TYPE_KINDS = {"enum", "struct", "class"}
MEMBER_KINDS = {"property", "field", "const", "method", "ctor"}
MEMBER_KEYS = {"name", "kind", "type", "params", "static", "value"}
CASE_KEYS = {"id", "rule", "given", "op", "expect"}


class UnitSchemaError(Exception):
    pass


# ============================================================ 構造化仕様

def spec_index(spec_text, gdd_text):
    """(仕様の ID の集合, {PR-xx: 値の文字列}, 問題の一覧)。gdd-sha256 が合わなければ問題に積む。"""
    problems = []
    meta, tables = gdd_check.parse_doc(spec_text, gdd_check.SPEC_META,
                                       [(h, c) for h, _, c in gdd_check.SECTIONS], "spec.md", problems)
    if meta and meta[2] != hashlib.sha256(gdd_text.encode("utf-8")).hexdigest():
        problems.append("spec.md: gdd-sha256 が今の GDD と一致しません。期待値を GDD に拘束できないので拒絶します")
    ids, params = set(), {}
    for head, rows in tables.items():
        for r in rows:
            ids.add(r["ID"])
            if head.startswith("## 5."):
                params[r["ID"]] = r["値"]
    return ids, params, problems


def resolve_param(value, index):
    """構造化仕様 §5 の値の文字列から、期待値に使える値を引く。引けなければ None。"""
    if index is None:
        m = re.fullmatch(r"\s*(-?\d+)(?:\s*\S+)?\s*", value)
        return int(m.group(1)) if m else None
    coord = gdd_check.COORD_RE.fullmatch(value.strip())
    items = [int(coord.group(1)), int(coord.group(2))] if coord else [s.strip() for s in value.split(",")]
    return items[index] if 0 <= index < len(items) and items[index] != "" else None


# ============================================================ 検査

def _check_keys(obj, required, optional, where, problems):
    if not isinstance(obj, dict):
        problems.append(f"{where}: オブジェクトではありません")
        return False
    missing = sorted(required - obj.keys())
    unknown = sorted(obj.keys() - required - optional - {k for k in obj if k.startswith("_")})
    if missing:
        problems.append(f"{where}: 必須キーがありません: {', '.join(missing)}")
    if unknown:
        problems.append(f"{where}: 知らないキーです: {', '.join(unknown)}")
    return not missing


def _interface(iface, problems):
    """{型名: 型}, {"型.メンバー": メンバー}, 列挙のメンバー名の集合"""
    types, members, enum_values = {}, {}, set()
    if not _check_keys(iface, {"types"}, set(), "interface", problems) or not isinstance(iface["types"], list):
        return types, members, enum_values
    for i, t in enumerate(iface["types"]):
        where = f"interface.types[{i}]"
        if not _check_keys(t, {"name", "kind"}, {"values", "members"}, where, problems):
            continue
        if not IDENT_RE.match(str(t["name"])) or t["kind"] not in TYPE_KINDS:
            problems.append(f"{where}: name は識別子、kind は {sorted(TYPE_KINDS)} のどれかにしてください")
            continue
        types[t["name"]] = t
        if t["kind"] == "enum":
            vals = t.get("values")
            if "members" in t or not isinstance(vals, list) or not vals or not all(IDENT_RE.match(str(v)) for v in vals):
                problems.append(f"{where}: enum は values（識別子の列）だけを持ちます")
                continue
            enum_values |= set(vals) | {f"{t['name']}.{v}" for v in vals}
            continue
        for j, m in enumerate(t.get("members", [])):
            mw = f"{where}.members[{j}]"
            if not _check_keys(m, {"name", "kind"}, MEMBER_KEYS - {"name", "kind"}, mw, problems):
                continue
            if not IDENT_RE.match(str(m["name"])) or m["kind"] not in MEMBER_KINDS:
                problems.append(f"{mw}: name は識別子、kind は {sorted(MEMBER_KINDS)} のどれかにしてください")
                continue
            if m["kind"] != "ctor" and not TYPE_RE.match(str(m.get("type", ""))):
                problems.append(f"{mw}: type は型名だけを書けます（C# の式・本体・修飾子は書けません）: {m.get('type')!r}")
            for k, p in enumerate(m.get("params", [])):
                if not (isinstance(p, dict) and set(p) == {"name", "type"}
                        and IDENT_RE.match(str(p["name"])) and TYPE_RE.match(str(p["type"]))):
                    problems.append(f"{mw}.params[{k}]: {{name, type}} の形で、どちらも名前だけにしてください")
            if "static" in m and not isinstance(m["static"], bool):
                problems.append(f"{mw}: static は真偽値です")
            members[f"{t['name']}.{m['name']}"] = m
    return types, members, enum_values


def _is_trivial(v, enum_values):
    if v is None or isinstance(v, bool):
        return True
    if isinstance(v, int):
        return v in (0, 1)
    if isinstance(v, list):
        return v == []
    return isinstance(v, str) and v in enum_values


def _is_literal(v, enum_values):
    """given に書ける値（事前状態）。数値は自由だが、文字列は列挙のメンバー名だけ。"""
    if v is None or isinstance(v, (bool, int)):
        return True
    if isinstance(v, list):
        return all(_is_literal(x, enum_values) for x in v)
    return isinstance(v, str) and v in enum_values


def _value(v, where, ctx, problems, given=None, allow_same=False, allow_free=False):
    enum_values, params = ctx["enum_values"], ctx["params"]
    if isinstance(v, dict):
        if set(v) == {"same"} and v["same"] is True and allow_same:
            return
        if set(v) in ({"param"}, {"param", "index"}):
            idx = v.get("index")
            if v["param"] not in params:
                problems.append(f"{where}: 構造化仕様 §5 に {v['param']} がありません")
            elif (idx is not None and (isinstance(idx, bool) or not isinstance(idx, int))) \
                    or resolve_param(params[v["param"]], idx) is None:
                problems.append(f"{where}: {v['param']}（{params[v['param']]}）から値を引けません（index: {idx}）")
            return
        if set(v) == {"given", "add"} and given is not None:
            if v["add"] not in (1, -1) or isinstance(v["add"], bool):
                problems.append(f"{where}: 増減は 1 回の遷移で ±1 だけです: {v['add']!r}")
            elif v["given"] not in given:
                problems.append(f"{where}: 基準の {v['given']} が given にありません")
            return
        problems.append(f"{where}: 期待値の書き方が不正です: {json.dumps(v, ensure_ascii=False)}")
        return
    if allow_free and _is_literal(v, enum_values):
        return
    if not allow_free and _is_trivial(v, enum_values):
        return
    problems.append(f"{where}: GDD から引けないリテラルです: {json.dumps(v, ensure_ascii=False)}。"
                    '{"param": "PR-xx"} で構造化仕様から引くか、{"same": true} / ±1 の増減で書いてください')


def _cases(acceptance, ctx, problems):
    if not _check_keys(acceptance, {"cases"}, {"required_tests"}, "acceptance", problems):
        return
    cases = acceptance["cases"]
    if not isinstance(cases, list) or not cases:
        problems.append("acceptance.cases: 1 行以上の列にしてください")
        return
    seen = set()
    for i, c in enumerate(cases):
        where = f"acceptance.cases[{i}]"
        if not _check_keys(c, CASE_KEYS, set(), where, problems):
            continue
        if not CASE_ID_RE.match(str(c["id"])) or c["id"] in seen:
            problems.append(f"{where}: id は英数字・_・- だけで、重複できません: {c['id']!r}")
        seen.add(c["id"])
        if not SPEC_ID_RE.match(str(c["rule"])) or c["rule"] not in ctx["spec_ids"]:
            problems.append(f"{where}: rule {c['rule']!r} は構造化仕様に存在する ID ではありません")
        op = c["op"]
        if isinstance(op, dict) and set(op) == {"construct"}:
            t = ctx["types"].get(op["construct"])
            if not t or t["kind"] == "enum":
                problems.append(f"{where}.op: construct は interface で宣言した class / struct だけです: {op['construct']!r}")
        elif isinstance(op, dict) and set(op) in ({"call"}, {"call", "args"}):
            m = ctx["members"].get(op["call"])
            if not m or m["kind"] != "method":
                problems.append(f"{where}.op: call は interface で宣言したメソッドだけです: {op['call']!r}")
            else:
                declared = {p["name"] for p in m.get("params", []) if isinstance(p, dict)}
                args = op.get("args", {})
                if not isinstance(args, dict) or set(args) != declared:
                    problems.append(f"{where}.op.args: 引数は宣言どおり {sorted(declared)} をすべて書いてください")
                else:
                    for k, a in args.items():
                        _value(a, f"{where}.op.args.{k}", ctx, problems, allow_free=True)
        else:
            problems.append(f"{where}.op: 操作は 1 つだけ（{{construct}} か {{call, args}}）です。列は書けません")
        for part in ("given", "expect"):
            if not isinstance(c[part], dict):
                problems.append(f"{where}.{part}: オブジェクトにしてください")
                continue
            for field, v in c[part].items():
                m = ctx["members"].get(field)
                if not m or m["kind"] not in ("property", "field", "const"):
                    problems.append(f"{where}.{part}: {field!r} は interface で宣言した状態（property / field / const）ではありません")
                    continue
                if part == "given":
                    _value(v, f"{where}.given.{field}", ctx, problems, allow_free=True)
                else:
                    _value(v, f"{where}.expect.{field}", ctx, problems, given=c["given"], allow_same=True)
        if isinstance(c["expect"], dict) and not c["expect"]:
            problems.append(f"{where}.expect: 期待する状態が 1 つもありません")


def _prompt(text, ctx, whitelist, problems):
    if not isinstance(text, str):
        problems.append("prompt: 文字列にしてください")
        return
    for tok in PROMPT_FORBIDDEN:
        if tok in text:
            problems.append(f"prompt: コードの記法 {tok!r} は書けません。形は interface に、期待値は cases に書いてください")
    allowed = set(ctx["types"]) | set(ctx["members"]) | {k.split(".", 1)[1] for k in ctx["members"]} \
        | ctx["enum_values"] | set(whitelist)
    for span in BACKTICK_RE.findall(text):
        if span not in allowed and not SPEC_ID_RE.match(span):
            problems.append(f"prompt: `{span}` は interface の識別子・whitelist のパス・仕様 ID のどれでもありません")


def validate(unit, spec_text, gdd_text):
    """schema 2 の単位定義を検査する。問題の一覧（空なら合格）。"""
    problems = []
    if not _check_keys(unit, TOP_REQUIRED, TOP_OPTIONAL, "単位定義", problems):
        return problems
    if unit["schema"] != SCHEMA:
        return [f"schema は {SCHEMA} です: {unit['schema']!r}"]
    spec_ids, params, spec_problems = spec_index(spec_text, gdd_text)
    if spec_problems:
        return spec_problems
    types, members, enum_values = _interface(unit["interface"], problems)
    ctx = dict(spec_ids=spec_ids, params=params, types=types, members=members, enum_values=enum_values)
    for key, m in members.items():
        if m["kind"] == "const":
            _value(m.get("value"), f"interface {key}.value", ctx, problems)
    _cases(unit["acceptance"], ctx, problems)
    _prompt(unit["prompt"], ctx, unit["whitelist"] if isinstance(unit["whitelist"], list) else [], problems)
    return problems


def check(unit_bytes, read_base_file, cfg=None):
    """門の本体。("v1-legacy" | "v2", 問題の一覧)。read_base_file(path) は origin/<base> の本文を返す。"""
    cfg = cfg or project.config("unit_schema")
    try:
        unit = json.loads(unit_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return "v2", [f"JSON として読めません: {e}"]
    if not isinstance(unit, dict) or unit.get("schema") != SCHEMA:
        sha = hashlib.sha256(unit_bytes).hexdigest()
        if sha in cfg["legacy_v1_sha256"]:
            return "v1-legacy", []
        return "v1", [f"schema {SCHEMA} ではない単位定義は、凍結済みの {len(cfg['legacy_v1_sha256'])} 件"
                      f"（config/unit_schema.json）しか受けません。この単位の sha256 は {sha[:12]}… です"]
    return "v2", validate(unit, read_base_file(cfg["spec_path"]), read_base_file(cfg["gdd_path"]))


def git_reader(repo, sha, ttl):
    def read(path):
        rc, out, err = run(["git", "show", f"{sha}:{path}"], repo, ttl, f"git show ({path})")
        if rc != 0:
            raise UnitSchemaError(f"{sha[:8]} に {path} がありません: {(err or out)[:200]}")
        return out
    return read


def main(argv=None):
    ap = argparse.ArgumentParser(description="単位定義のスキーマ門（分解役の自己検査にも使う）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--unit", required=True)
    args = ap.parse_args(argv)
    proj = project.load(args.project)
    unit_path = Path(args.unit)
    if not unit_path.is_absolute() and not unit_path.exists():
        unit_path = Path(proj["repo_dir"]) / args.unit
    ttl = 120
    base = proj["base_branch"]
    run(["git", "fetch", "origin", base], proj["repo_dir"], ttl, "git fetch (unit_schema)")
    rc, out, err = run(["git", "rev-parse", f"origin/{base}"], proj["repo_dir"], ttl, "git rev-parse (unit_schema)")
    if rc != 0:
        print(f"NG: origin/{base} を読めません: {(err or out)[:200]}")
        return 2
    sha = out.strip()
    kind, problems = check(unit_path.read_bytes(), git_reader(proj["repo_dir"], sha, ttl))
    for p in problems:
        print(f"NG: {p}")
    print(f"{'合格' if not problems else '不合格'}（{kind}、{len(problems)} 件）")
    return 0 if not problems else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
