"""不変条件テストの宣言の検査と生成（docs/design/mechanical_barriers.md §4、ADR-002 手順 5a）。

    python harness/invgen.py --project falling-blocks --decl projects/falling-blocks/invariants.json --out <dir>

不変条件は期待値を持たない。「どんな操作列のあとでも成り立つ等式」だけを宣言し、
シード付きのランダム操作列でそれを検査する NUnit テストを、固定テンプレートから出力する。

**宣言（harness の projects/<id>/invariants.json）の形**
- `schema: 1`
- `gdd_sha256`：宣言を書いたときの GDD の sha256。GDD が変われば一致しなくなり、見直すまで生成を拒絶する
- `interface`：単位定義 v2 と同じ形（型とメンバー）。等式と操作が参照するものだけでよい
- `subject`：検査対象の作り方。`{"construct": "GameState"}`
- `ops`：ランダムに選ぶ操作。`{"call": "型.メソッド", "args": {"引数": [値の候補, ...]}}`。
  候補はリテラルか `{"param": "PR-xx"}`
- `steps`・`seeds`：1 本の操作列の長さと、シードの本数
- `invariants`：`{"id": "INV-01", "rule": "RL-xx", "equation": "A * 10 + B = 4 * C"}`。
  等式に書けるのは、宣言した整数の状態（型.メンバー）・整数・`+ - * =` だけ（§4.2 の 4）

**シード**：環境変数 HARNESS_INVARIANT_SEED から読む。Outer 段が commit の SHA から決めて渡す（手順 5b）。
無ければ 1。同じシードなら同じ操作列になる。

**反例**：破綻したら、その時点までの操作列（最短の接頭辞）を 1 行で失敗メッセージに出す。
    INVARIANT_FAIL id=INV-01 rule=RL-xx seed=123 ops=GameState.Advance();GameState.InjectSeed(0u)
値・スタックトレースは出さない。Outer 段はこの行だけを実装役に返す（手順 5b）。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import project
import testgen
import unit_schema

SCHEMA = 1
TOP_KEYS = {"schema", "gdd_sha256", "interface", "subject", "ops", "steps", "seeds", "invariants"}
INT_TYPES = {"int", "uint", "long", "short", "byte"}
INV_ID_RE = re.compile(r"^INV-\d{2,}$")
TOKEN_RE = re.compile(r"\s*(?:(\d+)|([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)|([+\-*=]))")
MAX_STEPS, MAX_SEEDS = 10000, 1000
CLASS_NAME = "InvariantsCases"
SEED_ENV = "HARNESS_INVARIANT_SEED"


class InvariantError(Exception):
    pass


def parse_equation(text):
    """等式を字句の列にする。[("int", 10) | ("ref", "A.B") | ("op", "+")]。書けない字があれば None。"""
    tokens, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            return None
        pos = m.end()
        num, ref, op = m.groups()
        tokens.append(("int", int(num)) if num else ("ref", ref) if ref else ("op", op))
    return tokens


def _well_formed(tokens):
    """項 (演算子 項)* = 項 (演算子 項)* の形か。"""
    if [t for t in tokens if t == ("op", "=")] != [("op", "=")]:
        return False
    expect_term = True
    for kind, _ in tokens:
        if expect_term and kind == "op":
            return False
        if not expect_term and kind != "op":
            return False
        expect_term = not expect_term
    return not expect_term


def validate(decl, spec_text, gdd_text):
    """宣言を検査する。問題の一覧（空なら合格）。"""
    problems = []
    if not isinstance(decl, dict):
        return ["宣言がオブジェクトではありません"]
    missing, unknown = sorted(TOP_KEYS - decl.keys()), sorted(decl.keys() - TOP_KEYS - {k for k in decl if k.startswith("_")})
    if missing or unknown:
        return [f"キーが違います（不足: {missing}、不明: {unknown}）"]
    if decl["schema"] != SCHEMA:
        return [f"schema は {SCHEMA} です"]
    spec_ids, params, spec_problems = unit_schema.spec_index(spec_text, gdd_text)
    if spec_problems:
        return spec_problems
    if decl["gdd_sha256"] != hashlib.sha256(gdd_text.encode("utf-8")).hexdigest():
        return ["gdd_sha256 が今の GDD と一致しません。GDD が変わったので、不変条件の宣言を見直してから sha256 を更新してください"]
    types, members, enum_values = unit_schema._interface(decl["interface"], problems)
    ctx = dict(spec_ids=spec_ids, params=params, types=types, members=members, enum_values=enum_values)

    subject = decl["subject"]
    sut = subject.get("construct") if isinstance(subject, dict) and set(subject) == {"construct"} else None
    if not sut or sut not in types or types[sut]["kind"] == "enum":
        problems.append(f"subject は {{\"construct\": \"型\"}} で、interface の class / struct を指してください: {subject!r}")

    for name, limit in (("steps", MAX_STEPS), ("seeds", MAX_SEEDS)):
        v = decl[name]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= limit:
            problems.append(f"{name} は 1 以上 {limit} 以下の整数です: {v!r}")

    if not isinstance(decl["ops"], list) or not decl["ops"]:
        problems.append("ops は 1 つ以上の操作の列です")
    else:
        for i, op in enumerate(decl["ops"]):
            where = f"ops[{i}]"
            if not isinstance(op, dict) or set(op) not in ({"call"}, {"call", "args"}):
                problems.append(f"{where}: {{call, args}} の形にしてください")
                continue
            m = members.get(op["call"])
            if not m or m["kind"] != "method" or m.get("static") or op["call"].split(".")[0] != sut:
                problems.append(f"{where}: {op['call']!r} は subject の型のインスタンスメソッドではありません")
                continue
            declared = [p["name"] for p in m.get("params", []) if isinstance(p, dict)]
            args = op.get("args", {})
            if not isinstance(args, dict) or sorted(args) != sorted(declared):
                problems.append(f"{where}.args: 引数は宣言どおり {declared} をすべて書いてください")
                continue
            for k, domain in args.items():
                if not isinstance(domain, list) or not domain:
                    problems.append(f"{where}.args.{k}: 値の候補の列（1 つ以上）にしてください")
                    continue
                for j, v in enumerate(domain):
                    unit_schema._value(v, f"{where}.args.{k}[{j}]", ctx, problems, allow_free=True)

    if not isinstance(decl["invariants"], list) or not decl["invariants"]:
        problems.append("invariants は 1 つ以上の列です")
        return problems
    seen = set()
    for i, inv in enumerate(decl["invariants"]):
        where = f"invariants[{i}]"
        if not isinstance(inv, dict) or set(inv) != {"id", "rule", "equation"}:
            problems.append(f"{where}: {{id, rule, equation}} の形にしてください")
            continue
        if not INV_ID_RE.match(str(inv["id"])) or inv["id"] in seen:
            problems.append(f"{where}: id は INV-01 の形で、重複できません: {inv['id']!r}")
        seen.add(inv["id"])
        if inv["rule"] not in spec_ids:
            problems.append(f"{where}: rule {inv['rule']!r} は構造化仕様に存在する ID ではありません")
        tokens = parse_equation(str(inv["equation"]))
        if tokens is None or not _well_formed(tokens):
            problems.append(f"{where}: 等式に書けるのは 状態・整数・+ - * = だけで、= はちょうど 1 つです: {inv['equation']!r}")
            continue
        for kind, ref in tokens:
            if kind != "ref":
                continue
            m = members.get(ref)
            if not m or m["kind"] not in ("property", "field", "const") or m.get("type") not in INT_TYPES:
                problems.append(f"{where}: {ref} は interface で宣言した整数の状態ではありません")
            elif ref.split(".")[0] != sut and not (m.get("static") or m["kind"] == "const"):
                problems.append(f"{where}: {ref} は subject の状態でも static でもありません")
    return problems


# ============================================================ 生成

def _render_side(tokens, members):
    out = []
    for kind, v in tokens:
        if kind == "int":
            out.append(f"{v}L")
        elif kind == "op":
            out.append(v)
        else:
            t, name = v.split(".")
            _, m = members[v]
            owner = t if (m.get("static") or m["kind"] == "const") else "sut"
            out.append(f"(long){owner}.{name}")
    return " ".join(out)


def generate(decl, spec_text, gdd_text):
    """{ファイル名: C#}。検査に落ちる宣言は InvariantError。"""
    problems = validate(decl, spec_text, gdd_text)
    if problems:
        raise InvariantError("不変条件の宣言が検査を通りません: " + "; ".join(problems[:5]))
    _, params, _ = unit_schema.spec_index(spec_text, gdd_text)
    model = testgen._Model(decl, params)
    sut = decl["subject"]["construct"]

    cases = []
    for i, op in enumerate(decl["ops"]):
        _, m = model.members[op["call"]]
        body, shown = [], []
        for p in m.get("params", []):
            values = ", ".join(model.literal(v, p["type"], f"ops[{i}]") for v in op["args"][p["name"]])
            body.append(f"var a_{p['name']} = Pick(ref x, new {p['type']}[] {{ {values} }});")
            shown.append(f"a_{p['name']}")
        call_args = ", ".join(shown)
        shown_text = " + \",\" + ".join(shown) if shown else '""'
        body.append(f"trace.Add(\"{op['call']}(\" + {shown_text} + \")\");")
        body.append(f"sut.{m['name']}({call_args});")
        cases.append((i, body))

    out = ["// <auto-generated>",
           "// harness/invgen.py が不変条件の宣言から出力した。手で編集しない。",
           "// </auto-generated>",
           f"using {testgen.CORE_NAMESPACE};", "using NUnit.Framework;", "",
           f"namespace {testgen.TEST_NAMESPACE}", "{",
           f"    public class {CLASS_NAME}", "    {",
           f"        const int Steps = {decl['steps']};",
           f"        const int Seeds = {decl['seeds']};", "",
           "        static uint BaseSeed()", "        {",
           f"            var s = System.Environment.GetEnvironmentVariable(\"{SEED_ENV}\");",
           "            return s != null && uint.TryParse(s, out var v) && v != 0 ? v : 1u;", "        }", "",
           "        static uint Next(ref uint x)", "        {",
           "            x ^= x << 13; x ^= x >> 17; x ^= x << 5;", "            return x;", "        }", "",
           "        static T Pick<T>(ref uint x, T[] values) => values[Next(ref x) % (uint)values.Length];", "",
           "        static void Step(" + sut + " sut, ref uint x, System.Collections.Generic.List<string> trace)",
           "        {",
           f"            switch (Next(ref x) % {len(cases)}u)", "            {"]
    for i, body in cases:
        out.append(f"                case {i}u:")
        out.append("                {")
        out += [f"                    {line}" for line in body]
        out.append("                    break;")
        out.append("                }")
    out += ["            }", "        }", ""]

    for inv in decl["invariants"]:
        tokens = parse_equation(inv["equation"])
        eq = tokens.index(("op", "="))
        lhs, rhs = _render_side(tokens[:eq], model.members), _render_side(tokens[eq + 1:], model.members)
        method = "Invariant_" + inv["id"].replace("-", "_")
        out += ["        [Test]",
                f"        [Description(\"{inv['rule']}\")]",
                f"        public void {method}()", "        {",
                "            var baseSeed = BaseSeed();",
                "            for (uint k = 0; k < Seeds; k++)", "            {",
                "                uint seed = baseSeed + k * 2654435761u;",
                "                if (seed == 0) seed = 1;",
                "                uint x = seed;",
                "                var trace = new System.Collections.Generic.List<string>();",
                f"                var sut = new {sut}();",
                "                for (int step = 0; step <= Steps; step++)", "                {",
                "                    if (step > 0)", "                    {",
                "                        try { Step(sut, ref x, trace); }",
                "                        catch (System.Exception) { Fail(seed, trace, \"exception\"); }",
                "                    }",
                f"                    if (!({lhs} == {rhs})) Fail(seed, trace, null);",
                "                }", "            }", "", "            void Fail(uint seed, System.Collections.Generic.List<string> trace, string? why)",
                "            {",
                f"                Assert.Fail(\"INVARIANT_FAIL id={inv['id']} rule={inv['rule']} seed=\" + seed"
                " + (why == null ? \"\" : \" why=\" + why) + \" ops=\" + string.Join(\";\", trace));",
                "            }", "        }", ""]
    out.pop()
    out += ["    }", "}", ""]
    return {f"{CLASS_NAME}.cs": "\n".join(out)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="不変条件の宣言から NUnit テストを出力する")
    ap.add_argument("--project", required=True)
    ap.add_argument("--decl", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    proj = project.load(args.project)
    decl_path = Path(args.decl)
    if not decl_path.is_absolute() and not decl_path.exists():
        decl_path = Path(proj["repo_dir"]) / args.decl
    cfg = project.config("unit_schema")
    read = unit_schema.git_reader(proj["repo_dir"], f"origin/{proj['base_branch']}", 120)
    try:
        files = generate(json.loads(decl_path.read_text(encoding="utf-8")),
                         read(cfg["spec_path"]), read(cfg["gdd_path"]))
    except InvariantError as e:
        print(f"NG: {e}")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_bytes(text.encode("utf-8"))
        print(f"出力: {name}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
