"""性質の宣言を、決定論的に自然言語の文にする（v2r、docs/design/v2r_protocol.md §2・§3）。

    python -m harness.nlgen experiments/v2/properties.json      全性質を描き、語彙と描画の検査を通す

**なぜ要るか**: v2r の 2 × 2 計画（門の有無 × 仕様の形）では、A0・A1 に自然言語の仕様を渡す。その文を人や LLM が
書くと、書き手の主観と揺れが入り、形式の仕様（B−G・B）との差が「形」ではなく「中身」の差になる。ここでは、
性質の構文木（propgen.parse）から、固定した語句の対応表と型紙だけで文を作る。同じ入力からは常に同じ文が出る。

**情報量の対称性（§2.2）**:
- 事実の集合は同じ：形式の性質 1 件に、自然言語の文がちょうど 1 文（`check_rendering`）
- 語彙の対応は全単射：識別子・関数・演算子は、それぞれ対応表に 1 対 1 で載る。対応表に無いものが出たら止める
  （`NLGenError`）。対応表の語句どうしが重ならないことは `check_vocabulary` が確かめる
- 定義の閉包：性質が使う関数の意味と、語句がどの型のメンバーを指すかを、仕様の中に 1 回だけ書く（`render_spec`）

対応表はこのファイルにだけ置き、変えたら `table_sha256` が変わる（マニフェストに記録する）。v2r の走行の前に
人間が一度だけ査読して凍結する（§13 の U4）。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import propgen  # noqa: E402


class NLGenError(Exception):
    """対応表に無い識別子・関数・構文。黙って飛ばさない（事実の集合が形式側とずれるので）。"""


# ============================================================ 語句の対応表（凍結の対象）

ROOTS = {"before": "前の", "after": "後の"}
STATE = {"Phase": "局面", "ActiveMino": "操作中のミノ", "Score": "得点", "LinesCleared": "消したライン数",
         "TickCount": "経過 Tick 数", "BlockCount": "ブロック数", "LockedMinoCount": "固定したミノの数"}
MINO = {"Type": "形", "X": "X 座標", "Y": "Y 座標", "Rotation": "向き"}
INPUT = {"Left": "左", "Right": "右", "RotateCw": "右回転", "RotateCcw": "左回転", "SoftDrop": "ソフトドロップ",
         "HardDrop": "ハードドロップ"}
ENUMS = {
    "GamePhase": {"Ready": "開始待機", "Playing": "プレイ中", "GameOver": "ゲームオーバー"},
    "MinoType": {t: f"{t} ミノ" for t in ("I", "O", "T", "S", "Z", "J", "L")},
    "Rotation": {"Spawn": "出現向き", "Right": "右向き", "Two": "逆向き", "Left": "左向き"},
}
LITERALS = {"true": "真", "false": "偽", "null": "無し"}

# 比較の型紙（{l}・{r} は値の句）。null との比較は専用の型紙（「ある」「無い」）
COMPARE = {"==": "{l}が{r}と等しい", "!=": "{l}が{r}と等しくない", "<": "{l}が{r}より小さい",
           "<=": "{l}が{r}以下である", ">": "{l}が{r}より大きい", ">=": "{l}が{r}以上である"}
NULL_COMPARE = {"==": "{v}が無い", "!=": "{v}がある"}
ARITH = {"+": "{l}に{r}を足した値", "-": "{l}から{r}を引いた値"}
NEG = "マイナス{v}"
LOGIC = {"&&": "、かつ", "||": "、または"}
GROUP = "（{c}）"
NOT = "（{c}）ではない"
INPUT_CLAUSE = {True: "{m}の入力がある", False: "{m}の入力が無い"}
BOOL_CLAUSE = {True: "{v}が真である", False: "{v}が偽である"}
CONST_CLAUSE = {"true": "常に成り立つ", "false": "決して成り立たない"}

# 関数：値を返すもの（句）と、真偽を返すもの（肯定・否定の節）
VALUE_FUNCS = {
    "moved": "{0}を x に {1}、y に {2} 動かしたもの",
    "rotated": "{0}を {1} 段回したもの",
    "kick": "{0}を {1} 段回して補正候補を試した結果",
    "drop": "{0}を下へ落としきったもの",
}
PRED_FUNCS = {
    "fits": ("{0}が置ける", "{0}が置けない"),
    "in_board": ("{0}が盤面の内側にある", "{0}が盤面の内側に無い"),
    "occupied_before": ("前の盤面の ({0}, {1}) が埋まっている", "前の盤面の ({0}, {1}) が埋まっていない"),
    "occupied_after": ("後の盤面の ({0}, {1}) が埋まっている", "後の盤面の ({0}, {1}) が埋まっていない"),
}
# 関数の意味（propgen.FUNC_DOCS と同じ事実を、記号を使わずに書く）
FUNC_DOCS = {
    "fits": "「m が置ける」：前の盤面で、m の 4 マスがすべて盤面の内側（幅 PR-06、高さ PR-08）にあり、固定ブロックに重ならないこと",
    "in_board": "「m が盤面の内側にある」：m の 4 マスがすべて盤面の内側にあること",
    "moved": "「m を x に dx、y に dy 動かしたもの」：m の X 座標に dx、Y 座標に dy を足したミノ（形と向きは同じ）",
    "rotated": "「m を dir 段回したもの」：m の向きを 1 段回したミノ（位置は同じ）。dir が 1 なら 出現向き → 右向き → "
               "逆向き → 左向き → 出現向き の順に 1 つ進め（時計回り）、-1 なら 1 つ戻す",
    "kick": "「m を dir 段回して補正候補を試した結果」：m を dir 段回したものに、GddReference.Kicks(m の形, m の向き, "
            "回した後の向き) の候補 (dx, dy) を表の順に足し、前の盤面で最初に置けるもの。どれも置けなければ無し",
    "drop": "「m を下へ落としきったもの」：前の盤面で、置ける限り m を下へ 1 マスずつ動かしきったミノ",
    "occupied_before": "「前の盤面の (x, y) が埋まっている」：前の盤面で (x, y) が固定ブロックであること。盤面の外は埋まっているとみなす",
    "occupied_after": "「後の盤面の (x, y) が埋まっている」：後の盤面で (x, y) が固定ブロックであること。盤面の外は埋まっているとみなす",
}
READING = ("- 「前の」「後の」は、その Tick の前と後の状態。「入力」は、その Tick の入力\n"
           "- ミノの 4 マス：GddReference.Shape(形, 向き) の各 (x, y) について (X 座標 + x, Y 座標 + y)。Y 座標が 1 減る向きが下")
SENTENCE = "- {id}（{rule}）：{given}とき、{then}"
FEEDBACK_RE = re.compile(r"^(PROPERTY_(?:FAIL|VACUOUS|UNSATISFIABLE)) id=(\S+)")
FEEDBACK_NOTE = "  （この性質：{s}）"


def table():
    """凍結の対象の全体（決定論の順の表）。"""
    return {"ROOTS": ROOTS, "STATE": STATE, "MINO": MINO, "INPUT": INPUT, "ENUMS": ENUMS, "LITERALS": LITERALS,
            "COMPARE": COMPARE, "NULL_COMPARE": NULL_COMPARE, "ARITH": ARITH, "NEG": NEG, "LOGIC": LOGIC,
            "GROUP": GROUP, "NOT": NOT, "INPUT_CLAUSE": {str(k): v for k, v in INPUT_CLAUSE.items()},
            "BOOL_CLAUSE": {str(k): v for k, v in BOOL_CLAUSE.items()}, "CONST_CLAUSE": CONST_CLAUSE,
            "VALUE_FUNCS": VALUE_FUNCS, "PRED_FUNCS": PRED_FUNCS, "FUNC_DOCS": FUNC_DOCS, "READING": READING,
            "SENTENCE": SENTENCE, "FEEDBACK_NOTE": FEEDBACK_NOTE}


def table_sha256():
    return hashlib.sha256(json.dumps(table(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ============================================================ 描画

class _Renderer:
    def __init__(self, where):
        self.where = where
        self.used_funcs, self.used_names = [], []

    def err(self, msg):
        raise NLGenError(f"{self.where}: {msg}")

    def note(self, bucket, key):
        if key not in bucket:
            bucket.append(key)

    # ---- 値（名詞句）
    def value(self, e):
        kind = e[0]
        if kind == "int":
            return str(e[1])
        if kind == "name":
            return self.name(e[1])
        if kind == "un" and e[1] == "-":
            return NEG.format(v=self.value(e[2]))
        if kind == "bin" and e[1] in ARITH:
            return ARITH[e[1]].format(l=self.value(e[2]), r=self.value(e[3]))
        if kind == "call":
            if e[1] in PRED_FUNCS:
                self.err(f"真偽を返す関数 {e[1]} を値の位置には置けません")
            if e[1] not in VALUE_FUNCS:
                self.err(f"関数 {e[1]} は対応表にありません")
            self.note(self.used_funcs, e[1])
            return VALUE_FUNCS[e[1]].format(*[self.value(a) for a in e[2]])
        self.err(f"値の位置に置けない構文です: {e[0]} {e[1] if len(e) > 1 else ''}")

    def name(self, n):
        if n in LITERALS:
            return LITERALS[n]
        parts = n.split(".")
        if len(parts) == 2 and parts[0] in ENUMS:
            if parts[1] not in ENUMS[parts[0]]:
                self.err(f"列挙の値 {n} は対応表にありません")
            self.note(self.used_names, n)
            return ENUMS[parts[0]][parts[1]]
        if len(parts) == 1:
            owners = [en for en, vs in ENUMS.items() if n in vs]
            if len(owners) != 1:
                self.err(f"{n!r} は対応表の識別子ではありません（列挙の値は 型.値 で書いてください）")
            return self.name(f"{owners[0]}.{n}")
        root, rest = parts[0], parts[1:]
        if root == "input":
            self.err(f"{n} は真偽の入力で、値の位置には置けません")
        if root not in ROOTS:
            self.err(f"{n!r} は before・after・input のメンバー・列挙の値・true・false・null のどれでもありません")
        if rest[0] not in STATE:
            self.err(f"状態のメンバー {rest[0]} は対応表にありません")
        words = [ROOTS[root] + STATE[rest[0]]]
        for m in rest[1:]:
            if m not in MINO:
                self.err(f"ミノのメンバー {m} は対応表にありません")
            words.append(MINO[m])
        self.note(self.used_names, n)
        return "の".join(words)

    # ---- 節（真偽）
    def clause(self, e, parent=None):
        kind = e[0]
        if kind == "bin" and e[1] in LOGIC:
            left, right = self.clause(e[2], e[1]), self.clause(e[3], e[1])
            text = left + LOGIC[e[1]] + right
            return GROUP.format(c=text) if parent in LOGIC and parent != e[1] else text
        if kind == "bin" and e[1] in COMPARE:
            op, l, r = e[1], e[2], e[3]
            if ("name", "null") in (l, r) and op in NULL_COMPARE:
                other = r if l == ("name", "null") else l
                return NULL_COMPARE[op].format(v=self.value(other))
            return COMPARE[op].format(l=self.value(l), r=self.value(r))
        if kind == "un" and e[1] == "!":
            inner = e[2]
            if inner[0] == "name" and inner[1].startswith("input."):
                return self.input_clause(inner[1], False)
            if inner[0] == "call" and inner[1] in PRED_FUNCS:
                return self.pred(inner, False)
            return NOT.format(c=self.clause(inner))
        if kind == "name":
            if e[1] in CONST_CLAUSE:
                return CONST_CLAUSE[e[1]]
            if e[1].startswith("input."):
                return self.input_clause(e[1], True)
            return BOOL_CLAUSE[True].format(v=self.value(e))
        if kind == "call":
            if e[1] not in PRED_FUNCS:
                self.err(f"節の位置に置けるのは真偽を返す関数だけです: {e[1]}")
            return self.pred(e, True)
        self.err(f"節の位置に置けない構文です: {e[0]} {e[1] if len(e) > 1 else ''}")

    def input_clause(self, n, positive):
        member = n.split(".", 1)[1]
        if member not in INPUT:
            self.err(f"入力のメンバー {member} は対応表にありません")
        self.note(self.used_names, n)
        return INPUT_CLAUSE[positive].format(m=INPUT[member])

    def pred(self, e, positive):
        self.note(self.used_funcs, e[1])
        pos, neg = PRED_FUNCS[e[1]]
        return (pos if positive else neg).format(*[self.value(a) for a in e[2]])


def render_expr(text, where="式"):
    """真偽の式 → 自然言語の節。(節, 使った関数の列, 使った識別子の列)。"""
    try:
        tree = propgen.parse(text, where)
    except propgen.PropertyError as e:
        raise NLGenError(str(e))
    r = _Renderer(where)
    return r.clause(tree), r.used_funcs, r.used_names


def render_property(p):
    """性質 1 件 → 1 文。(文, 使った関数の列, 使った識別子の列)。"""
    given, f1, n1 = render_expr(p["given"], f"{p['id']}.given")
    then, f2, n2 = render_expr(p["then"], f"{p['id']}.then")
    funcs = f1 + [f for f in f2 if f not in f1]
    names = n1 + [n for n in n2 if n not in n1]
    return SENTENCE.format(id=p["id"], rule=p["rule"], given=given, then=then), funcs, names


def render_properties(props):
    """{性質の ID: 文}（宣言の順）。"""
    return {p["id"]: render_property(p)[0] for p in props}


# 関数の意味の説明に出る列挙の値（説明を読むのに要るので、その関数を使ったら語句の対応に足す）
FUNC_ENUMS = {"rotated": ("Rotation",), "kick": ("Rotation",)}


def _glossary(names, funcs=()):
    """語句がどの型のメンバーを指すか（定義の閉包）。使った識別子と、使った関数の説明に出る語句だけ、決定論の順で。"""
    names = list(names) + [f"{en}.{v}" for f in funcs for en in FUNC_ENUMS.get(f, ()) for v in ENUMS[en]]
    rows = {}
    for n in names:
        parts = n.split(".")
        if parts[0] in ENUMS:
            rows[f"「{ENUMS[parts[0]][parts[1]]}」"] = n
        elif parts[0] == "input":
            rows[f"「{INPUT[parts[1]]}の入力」"] = f"TickInput.{parts[1]}"
        else:
            rows[f"「{STATE[parts[1]]}」"] = f"IGameState.{parts[1]}"
            for m in parts[2:]:
                rows[f"「{MINO[m]}」"] = f"ActiveMino.{m}"
    return [f"- {k}：{v}" for k, v in sorted(rows.items(), key=lambda kv: kv[1])]


def render_spec(mine, prior=()):
    """単位定義の prompt の仕様の節（自然言語）。v2prep の形式の節と同じ並び（このタスク・前のタスク・読み方）。"""
    parts, funcs, names = ["## このタスクの性質（受入）"], [], []
    rendered = {}
    for p in list(mine) + list(prior):
        s, f, n = render_property(p)
        rendered[p["id"]] = s
        funcs += [x for x in f if x not in funcs]
        names += [x for x in n if x not in names]
    parts.append("\n".join(rendered[p["id"]] for p in mine))
    if prior:
        parts += ["## 前のタスクの性質（守り続ける）", "\n".join(rendered[p["id"]] for p in prior)]
    reading = [READING] + _glossary(names, funcs) + [f"- {FUNC_DOCS[f]}" for f in propgen.FUNCS if f in funcs]
    parts += ["## 性質の文の読み方", "\n".join(reading)]
    return "\n\n".join(parts)


# ============================================================ 門の知らせ（A1）

def annotate_feedback(text, sentences):
    """判定器の知らせの、性質を指す行（PROPERTY_FAIL・VACUOUS・UNSATISFIABLE）の後に、その性質の自然言語の文を添える。

    行そのものは変えない（B と同じ知らせ。§3.1）。対応する文が無い性質は止める（知らせと仕様がずれるので）。
    """
    out = []
    for line in (text or "").splitlines():
        out.append(line)
        m = FEEDBACK_RE.match(line.strip())
        if m:
            if m.group(2) not in sentences:
                raise NLGenError(f"知らせの性質 {m.group(2)} の文がありません")
            s = sentences[m.group(2)]
            out.append(FEEDBACK_NOTE.format(s=s[2:] if s.startswith("- ") else s))
    return "\n".join(out)


# ============================================================ 検査

def _phrases():
    """識別子の語句の全体（{語句: 出どころ}）と、衝突の一覧。"""
    seen, clashes = {}, []

    def put(phrase, key):
        if phrase in seen and seen[phrase] != key:
            clashes.append(f"語句 {phrase!r} が {seen[phrase]} と {key} で重なります")
        seen.setdefault(phrase, key)
    for root, rp in ROOTS.items():
        for s, sp in STATE.items():
            put(rp + sp, f"{root}.{s}")
            if s == "ActiveMino":
                for m, mp in MINO.items():
                    put(f"{rp}{sp}の{mp}", f"{root}.{s}.{m}")
    for en, vals in ENUMS.items():
        for v, vp in vals.items():
            put(vp, f"{en}.{v}")
    for m, mp in INPUT.items():
        put(INPUT_CLAUSE[True].format(m=mp), f"input.{m}")
        put(INPUT_CLAUSE[False].format(m=mp), f"!input.{m}")
    for lit, lp in LITERALS.items():
        put(lp, lit)
    return seen, clashes


def check_vocabulary():
    """対応表の不変条件。問題の一覧（空なら合格）。"""
    problems = []
    _, clashes = _phrases()
    problems += clashes
    for label, d in (("STATE", STATE), ("MINO", MINO), ("INPUT", INPUT), ("ROOTS", ROOTS), ("LITERALS", LITERALS)):
        if len(set(d.values())) != len(d):
            problems.append(f"{label} の語句が重なっています")
    for en, vals in ENUMS.items():
        if len(set(vals.values())) != len(vals):
            problems.append(f"ENUMS.{en} の語句が重なっています")
    for label, d in (("COMPARE", COMPARE), ("ARITH", ARITH), ("LOGIC", LOGIC), ("NULL_COMPARE", NULL_COMPARE),
                     ("VALUE_FUNCS", VALUE_FUNCS), ("PRED_FUNCS（肯定）", {k: v[0] for k, v in PRED_FUNCS.items()}),
                     ("PRED_FUNCS（否定）", {k: v[1] for k, v in PRED_FUNCS.items()})):
        if len(set(d.values())) != len(d):
            problems.append(f"{label} の型紙が重なっています")
    funcs = set(VALUE_FUNCS) | set(PRED_FUNCS)
    if set(VALUE_FUNCS) & set(PRED_FUNCS):
        problems.append("値の関数と真偽の関数に同じ名前があります")
    if funcs != set(propgen.FUNCS):
        problems.append(f"関数の対応が propgen.FUNCS と違います：{sorted(funcs ^ set(propgen.FUNCS))}")
    if set(FUNC_DOCS) != set(propgen.FUNCS):
        problems.append(f"関数の意味の説明が propgen.FUNCS と違います：{sorted(set(FUNC_DOCS) ^ set(propgen.FUNCS))}")
    for n in propgen.FUNCS:
        if propgen.FUNCS[n][1] == "bool" and n not in PRED_FUNCS:
            problems.append(f"{n} は真偽を返すのに、真偽の関数の表にありません")
    for n, (want, ret, _) in propgen.FUNCS.items():
        template = PRED_FUNCS[n][0] if n in PRED_FUNCS else VALUE_FUNCS.get(n, "")
        holes = sorted(set(int(x) for x in re.findall(r"\{(\d)\}", template)))
        if holes != list(range(len(want))):
            problems.append(f"{n} の型紙の穴 {holes} が引数の数 {len(want)} と合いません")
    return problems


def check_contract(interface):
    """契約（propgen.Contract）の状態・ミノ・入力・列挙が、すべて対応表にあること。問題の一覧。"""
    c = propgen.Contract(interface)
    problems = [f"状態のメンバー {m} が対応表にありません" for m in c.state if m not in STATE]
    problems += [f"ミノのメンバー {m} が対応表にありません" for m in c.mino if m not in MINO]
    problems += [f"入力のメンバー {m} が対応表にありません" for m in c.input if m not in INPUT]
    for en, vals in c.enums.items():
        problems += [f"列挙の値 {en}.{v} が対応表にありません" for v in vals if v not in ENUMS.get(en, {})]
    return problems


def check_rendering(props, spec_text):
    """性質と文の 1 対 1、使った関数の意味の説明が仕様にあること。問題の一覧。"""
    problems = []
    ids = [p["id"] for p in props]
    if len(set(ids)) != len(ids):
        problems.append("性質の ID が重なっています")
    lines = [l for l in spec_text.splitlines() if l.startswith("- P-")]
    for pid in ids:
        n = sum(1 for l in lines if l.startswith(f"- {pid}（"))
        if n != 1:
            problems.append(f"性質 {pid} の文が {n} 文あります（1 文のはず）")
    extra = [l for l in lines if not any(l.startswith(f"- {pid}（") for pid in ids)]
    problems += [f"性質に無い文があります：{l[:40]}" for l in extra]
    for p in props:
        for f in render_property(p)[1]:
            if FUNC_DOCS[f] not in spec_text:
                problems.append(f"{p['id']} が使う関数 {f} の意味の説明が仕様にありません")
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    decl = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    props = decl["properties"]
    problems = check_vocabulary()
    spec = render_spec(props)
    problems += check_rendering(props, spec)
    print(spec)
    print(f"\n対応表 sha256: {table_sha256()}")
    for p in problems:
        print(f"NG: {p}")
    print("合格" if not problems else f"不合格（{len(problems)} 件）")
    return 0 if not problems else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
