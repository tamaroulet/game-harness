"""単位定義 v2 のスキーマ門（docs/design/mechanical_barriers.md §3、ADR-002 防壁②）。

    python harness/unit_schema.py --project falling-blocks --unit tools/units/issue_15.json

**何を拒絶するか**: 分解役（LLM）が単位定義に手計算のオラクルや C# を書き込むこと。
「書かないでください」とプロンプトで頼むのではなく、形として書けないようにする。

- インターフェース（作るものの形）は構造化フィールドだけで表す。C# の生文字列は受けない
- 受入条件は 1 行 = 1 回の状態遷移（horizon = 1）。操作は 1 行に 1 つ
- 引数付きの問い合わせ（query）は、戻り値だけを期待値に書く。状態が変わらないこと（CQS）と
  2 回呼んでも同じ値になること（冪等）は、生成器が必ず検査する（手順 5.5）
- 期待値は GDD に拘束する。変えない / 構造化仕様 §5 の外部パラメーター / ±1 の増減 /
  自明なリテラル（0・1・真偽・null・空の列・列挙のメンバー名）のどれかでなければ拒絶する
- 各行の根拠（rule）は構造化仕様に実在する ID でなければ拒絶する
- 構造化仕様の gdd-sha256 が今の GDD と違えば、引いた値を信用しない

**このスキーマで手計算そのものは消せない**（同 §3.4）。保証するのは「期待値が GDD の文言だけで
1 ステップ分を確かめられる大きさに収まる」ことまで。

**ティック進行の述語**（ADR-003 §3.9、B3.1-2）: 操作に `repeat` を付けると同じ操作を N 回続ける。
N は構造化仕様 §5 から引く（`{"param": "PR-xx"}`、または `"add": ±1` を付けたもの）。独自の数値は書けない。
上限は HORIZON_MAX 回。期待値の ±1 に `at_step` を付けると「at_step 回目より前は変わらず、at_step 回目で
±1 になる」を 1 行で表せる。

**構造体のリテラル**（B4-E2）: given と op.args には、interface で宣言した class / struct の値を
`{"<コンストラクタの引数名>": リテラル, ...}` で書ける。引数名の集合がちょうど一致するコンストラクタで作る。
入れ子は 1 段だけ（リテラルの中にさらに構造体は書けない）。±1 の基準には「型.メンバー.メンバー」で、
given に書いた構造体の成分を指せる（例：`{"given": "GameState.ActiveMino.X", "add": -1}`）。
入力のように複数の真偽値を 1 回の操作に載せたいときは、構造体を引数に取る 1 つの操作（例：`Tick(input)`）に
する。1 行 1 操作（horizon = 1）はそのまま守る。

**時限制約**（ADR-003 §3.3・§3.6、B3.1-2）: config/unit_schema.json の `timed_constraints` にある間だけ効く。
免除した単位（既存の issue_12）以外は、操作を `allowed_calls` に限り、whitelist のパスが base に実在することを求める。

**v1 の扱い**: config/unit_schema.json に内容の sha256 がある既存の単位だけを通す。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import gdd_check
import exitcode
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
                "selftest_forbidden_probe", "task_kind", "max_add_lines", "max_del_lines"}
TASK_KINDS = ("feature", "refactor")
TYPE_KINDS = {"enum", "struct", "class"}
MEMBER_KINDS = {"property", "field", "const", "method", "ctor"}
MEMBER_KEYS = {"name", "kind", "type", "params", "static", "value"}
CASE_KEYS = {"id", "rule", "given", "op", "expect"}
HORIZON_MAX = 1000
PATH_MAX = 3   # 型.メンバー.メンバー まで（入れ子の状態は 1 段だけたどれる）


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


def state_path(members, key):
    """"型.メンバー" または "型.メンバー.メンバー" を、たどったメンバーの列にする。たどれなければ None。

    入れ子の 2 段目は、1 段目の型（末尾の ? を除く）が interface で宣言した class / struct のときだけ引ける。
    どの段も公開状態（property / field / const）でなければならない。
    """
    parts = key.split(".") if isinstance(key, str) else []
    if not 2 <= len(parts) <= PATH_MAX:
        return None
    chain, owner = [], parts[0]
    for name in parts[1:]:
        m = members.get(f"{owner}.{name}")
        if not m or m["kind"] not in ("property", "field", "const"):
            return None
        chain.append(m)
        owner = str(m.get("type", "")).rstrip("?")
    return chain


def _camel(name):
    return name[:1].lower() + name[1:]


def _struct_literal(v, type_name, where, ctx, problems):
    """構造体のリテラル（given・op.args の値）を検査する。問題が無ければ True。"""
    t = ctx["types"].get(str(type_name).rstrip("?"))
    if not t or t["kind"] == "enum":
        problems.append(f"{where}: {type_name} は interface で宣言した class / struct ではないので、"
                        f"構造体のリテラルは書けません: {json.dumps(v, ensure_ascii=False)}")
        return False
    ctors = [m for m in t.get("members", []) if m["kind"] == "ctor"]
    ctor = next((m for m in ctors if {p["name"] for p in m.get("params", [])} == set(v)), None)
    if ctor is None:
        problems.append(f"{where}: {t['name']} に、引数がちょうど {sorted(v)} のコンストラクタが宣言されていません")
        return False
    for k, x in v.items():
        if isinstance(x, dict):
            problems.append(f"{where}.{k}: 構造体のリテラルの入れ子は 1 段までです")
            continue
        _value(x, f"{where}.{k}", ctx, problems, allow_free=True)
    return True


def _is_param_like(v):
    return isinstance(v, dict) and ("param" in v or "same" in v or "given" in v)


def _given_base_ok(key, given):
    """±1 の基準が given にあるか。「型.メンバー」がそのまま無ければ、構造体のリテラルの成分を探す。"""
    if key in given:
        return True
    head, _, name = str(key).rpartition(".")
    return isinstance(given.get(head), dict) and _camel(name) in given[head]


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
        if set(v) in ({"param"}, {"param", "index"}, {"param", "add"}, {"param", "index", "add"}):
            idx = v.get("index")
            if v["param"] not in params:
                problems.append(f"{where}: 構造化仕様 §5 に {v['param']} がありません")
            elif (idx is not None and (isinstance(idx, bool) or not isinstance(idx, int))) \
                    or resolve_param(params[v["param"]], idx) is None:
                problems.append(f"{where}: {v['param']}（{params[v['param']]}）から値を引けません（index: {idx}）")
            elif "add" in v and (isinstance(v["add"], bool) or v["add"] not in (1, -1)
                                 or not isinstance(resolve_param(params[v["param"]], idx), int)):
                # 仕様の値から 1 回の遷移で ±1 だけずれた値（例：出現位置の 1 段下）。整数の値にだけ付けられる
                problems.append(f"{where}: param に付けられる add は、整数の値への ±1 だけです: {v.get('add')!r}")
            return
        if set(v) == {"given", "add"} and given is not None:
            if v["add"] not in (1, -1) or isinstance(v["add"], bool):
                problems.append(f"{where}: 増減は 1 回の遷移で ±1 だけです: {v['add']!r}")
            elif not _given_base_ok(v["given"], given):
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


def _count(v, where, ctx, problems):
    """repeat・at_step の回数。構造化仕様 §5 から引いた整数（±1 まで）。引けなければ None。"""
    params = ctx["params"]
    if not (isinstance(v, dict) and set(v) in ({"param"}, {"param", "add"})):
        problems.append(f"{where}: 回数は {{\"param\": \"PR-xx\"}}（と \"add\": ±1）だけで書けます: "
                        f"{json.dumps(v, ensure_ascii=False)}")
        return None
    add = v.get("add", 0)
    if isinstance(add, bool) or add not in (0, 1, -1) or ("add" in v and add == 0):
        problems.append(f"{where}: add は ±1 だけです: {add!r}")
        return None
    base = resolve_param(params[v["param"]], None) if v["param"] in params else None
    if not isinstance(base, int):
        problems.append(f"{where}: {v['param']} から整数の回数を引けません")
        return None
    n = base + add
    if not 1 <= n <= HORIZON_MAX:
        problems.append(f"{where}: 回数は 1〜{HORIZON_MAX} です（horizon の上限）: {n}")
        return None
    return n


def _cases(acceptance, ctx, problems, refactor=False):
    if not _check_keys(acceptance, {"cases"}, {"required_tests"}, "acceptance", problems):
        return
    cases = acceptance["cases"]
    if refactor:
        # リファクタリングは振る舞いを増やさない。合格の条件は P2P の全件維持だけ（ADR-003 §3.7）
        if cases != []:
            problems.append("acceptance.cases: task_kind が refactor の単位は受入データを持てません（空の列にしてください）")
        return
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
        query = None
        repeat = None
        if isinstance(op, dict) and "repeat" in op:
            if "call" not in op:
                problems.append(f"{where}.op: repeat は call の操作にだけ付けられます")
            else:
                repeat = _count(op["repeat"], f"{where}.op.repeat", ctx, problems)
            op = {k: v for k, v in op.items() if k != "repeat"}
        if isinstance(op, dict) and set(op) == {"construct"}:
            t = ctx["types"].get(op["construct"])
            if not t or t["kind"] == "enum":
                problems.append(f"{where}.op: construct は interface で宣言した class / struct だけです: {op['construct']!r}")
        elif isinstance(op, dict) and (set(op) in ({"call"}, {"call", "args"}) or set(op) in ({"query"}, {"query", "args"})):
            kind = "call" if "call" in op else "query"
            m = ctx["members"].get(op[kind])
            if not m or m["kind"] != "method":
                problems.append(f"{where}.op: {kind} は interface で宣言したメソッドだけです: {op[kind]!r}")
            elif kind == "query" and m.get("type") == "void":
                problems.append(f"{where}.op: query は戻り値のあるメソッドだけです: {op['query']!r}")
            else:
                query = m if kind == "query" else None
                declared = {p["name"] for p in m.get("params", []) if isinstance(p, dict)}
                args = op.get("args", {})
                if not isinstance(args, dict) or set(args) != declared:
                    problems.append(f"{where}.op.args: 引数は宣言どおり {sorted(declared)} をすべて書いてください")
                else:
                    ptypes = {p["name"]: p["type"] for p in m.get("params", []) if isinstance(p, dict)}
                    for k, a in args.items():
                        if isinstance(a, dict) and not _is_param_like(a):
                            _struct_literal(a, ptypes[k], f"{where}.op.args.{k}", ctx, problems)
                        else:
                            _value(a, f"{where}.op.args.{k}", ctx, problems, allow_free=True)
        else:
            problems.append(f"{where}.op: 操作は 1 つだけ（{{construct}}・{{call, args}}・{{query, args}} のどれか）です。列は書けません")
        if query is not None:
            # 問い合わせは戻り値だけを期待値にする。状態の不変（CQS）と冪等は生成器が必ず検査する
            if not isinstance(c["expect"], dict) or set(c["expect"]) != {"return"}:
                problems.append(f"{where}.expect: query の行の期待値は {{\"return\": 値}} だけです"
                                "（状態が変わらないことはハーネスが検査します）")
            else:
                _value(c["expect"]["return"], f"{where}.expect.return", ctx, problems)
            if query.get("static") and c["given"]:
                problems.append(f"{where}.given: static な query の行は given を持てません")
            parts = ("given",)
        else:
            parts = ("given", "expect")
        for part in parts:
            if not isinstance(c[part], dict):
                problems.append(f"{where}.{part}: オブジェクトにしてください")
                continue
            for field, v in c[part].items():
                # 入れ子の状態（型.メンバー.メンバー）は期待値にだけ書ける。given は復元用コンストラクタの引数なので平ら
                chain = state_path(ctx["members"], field) if part == "expect" else None
                m = chain[-1] if chain else (ctx["members"].get(field) if str(field).count(".") == 1 else None)
                if not m or m["kind"] not in ("property", "field", "const"):
                    problems.append(f"{where}.{part}: {field!r} は interface で宣言した状態（property / field / const）ではありません")
                    continue
                if part == "given" and isinstance(v, dict) and not _is_param_like(v):
                    _struct_literal(v, m.get("type"), f"{where}.given.{field}", ctx, problems)
                elif part == "given":
                    _value(v, f"{where}.given.{field}", ctx, problems, allow_free=True)
                elif isinstance(v, dict) and "at_step" in v:
                    if set(v) != {"given", "add", "at_step"}:
                        problems.append(f"{where}.expect.{field}: at_step は ±1 の増減にだけ付けられます")
                        continue
                    if repeat is None:
                        problems.append(f"{where}.expect.{field}: at_step は repeat のある行にだけ書けます")
                        continue
                    k = _count(v["at_step"], f"{where}.expect.{field}.at_step", ctx, problems)
                    if k is not None and k > repeat:
                        problems.append(f"{where}.expect.{field}.at_step: repeat の回数（{repeat}）を超えています: {k}")
                    _value({"given": v["given"], "add": v["add"]}, f"{where}.expect.{field}", ctx, problems,
                           given=c["given"], allow_same=True)
                else:
                    _value(v, f"{where}.expect.{field}", ctx, problems, given=c["given"], allow_same=True)
        if isinstance(c["expect"], dict) and not c["expect"]:
            problems.append(f"{where}.expect: 期待する状態が 1 つもありません")


def _timed_ops(unit, cfg, problems):
    """時限制約：免除していない単位の操作を allowed_calls に限る（ADR-003 §3.3）。"""
    tc = (cfg or {}).get("timed_constraints")
    if not tc or unit.get("id") in tc.get("exempt_units", []) or "allowed_calls" not in tc:
        return
    allowed = tc["allowed_calls"]
    cases = unit.get("acceptance", {}).get("cases", []) if isinstance(unit.get("acceptance"), dict) else []
    for i, c in enumerate(cases):
        op = c.get("op") if isinstance(c, dict) else None
        if not (isinstance(op, dict) and op.get("call") in allowed and not ({"construct", "query"} & set(op))):
            problems.append(f"acceptance.cases[{i}].op: 時限制約（ADR-003 §3.3）により、使える操作は "
                            f"{allowed} の call だけです: {json.dumps(op, ensure_ascii=False)}")


def whitelist_problems(unit, exists, cfg=None):
    """時限制約：免除していない単位の whitelist は、すべて base に実在するパスでなければならない
    （ADR-003 §3.6。新しいファイルが無ければ、新しい .meta も生まれない）。exists(path) -> bool。"""
    cfg = cfg if cfg is not None else project.config("unit_schema")
    tc = cfg.get("timed_constraints")
    if not tc or not tc.get("whitelist_must_exist") or unit.get("id") in tc.get("exempt_units", []):
        return []
    wl = unit.get("whitelist") if isinstance(unit.get("whitelist"), list) else []
    return [f"whitelist: {p} が base にありません。時限制約（ADR-003 §3.6）により、新しいファイルは作れません"
            for p in wl if not exists(p)]


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


def validate(unit, spec_text, gdd_text, cfg=None):
    """schema 2 の単位定義を検査する。問題の一覧（空なら合格）。"""
    cfg = cfg if cfg is not None else project.config("unit_schema")
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
    kind = unit.get("task_kind", "feature")
    if kind not in TASK_KINDS:
        problems.append(f"task_kind は {list(TASK_KINDS)} のどれかです: {kind!r}")
    _cases(unit["acceptance"], ctx, problems, refactor=kind == "refactor")
    _timed_ops(unit, cfg, problems)
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
    problems = validate(unit, read_base_file(cfg["spec_path"]), read_base_file(cfg["gdd_path"]), cfg)
    return "v2", problems + whitelist_problems(unit, lambda p: _exists(read_base_file, p), cfg)


def _exists(read_base_file, path):
    try:
        read_base_file(path)
        return True
    except UnitSchemaError:
        return False


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
    sys.exit(exitcode.normalized(main))
