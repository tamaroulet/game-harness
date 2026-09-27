"""証拠の状態（witness）：性質の前提を、無作為な系列で起きるのを待たずに注入する（原則 P1）。

docs/design/v2r_instrument_redesign.md §4・§6 の 1。性質の宣言の任意のキー `witness` に、前の状態と入力を書く。
生成器（propgen）は、復元用コンストラクタでその状態を作り、1 Tick 進めて then を確かめる決定論のテストを出す。

**書き方**（性質ごとに 1〜MAX_WITNESSES 個の列）

    "witness": [{"board": ["#########.", "####.#####"], "mino": {"type": "I", "x": 7, "y": 2, "rotation": "Right"},
                 "input": ["HardDrop"], "phase": "Playing"}]

- `board`：固定ブロックの行。**最初の文字列が一番下の行（y = 0）**で、上へ向かって並べる。1 行は盤面の幅ちょうどの
  `#`（固定ブロック）と `.`（空き）。書かない行は空き。埋まりきった行は書けない（実際の盤面には残らない）
- `mino`：落ちているミノ（`type`・`x`・`y`・`rotation` は契約の列挙の名前）か `null`。盤面に置けること
- `input`：その Tick で押すキー（TickInput の名前の列）。書かなければ何も押さない
- `phase`：局面（GamePhase の名前）。書かなければ宣言の `start.phase`
- `counters`：得点などの数。今の契約の復元用コンストラクタ（GamePhase, ActiveMino?, IReadOnlyList<Cell>）は数を
  受け取らないので、書くと生成を止める。数を前提に使う性質は、契約を広げる PR の後で書く

**生成の時点の検査**（実装に依らない）：前提（given）を、参照モデル（このファイルの `Evaluator`。C# の PropertyModel と
同じ定義）で証拠の状態と入力について評価し、成り立たなければ生成を止める（`PropertyError`）。前提は before と input だけで
書く（after を使う前提は、証拠の状態では決まらない。P1）。before の数（得点など）も、復元で決まらないので使えない。

**テストの時点**：復元した状態が証拠の状態と違えば `PROPERTY_WITNESS_RESTORE`（復元用コンストラクタの欠陥。実装の失敗）。
前提が成り立たなければ `PROPERTY_WITNESS_INVALID`（生成の時点の評価と C# の評価の食い違い。測定器の故障）。
then が破れれば、ほかの反例と同じ `PROPERTY_FAIL`（ops は `witness<k>:<入力>`）。
"""
MAX_WITNESSES = 10
# TickInput のコンストラクタの引数の順（propgen.INPUT_ORDER と同じ。tests/test_witness.py で一致を確かめる）
INPUT_ORDER = ("Left", "Right", "RotateCw", "RotateCcw", "SoftDrop", "HardDrop")
KEYS = {"board", "mino", "input", "phase", "counters"}
MINO_KEYS = {"type", "x", "y", "rotation"}


class WitnessError(Exception):
    pass


class _Null(Exception):
    """ミノが null のメンバーを読んだ（C# の PropertyNull と同じ扱い：前提は成り立たない）。"""


def uses_after(e):
    """式が実装の結果（after・occupied_after）を使うか。"""
    kind = e[0]
    if kind == "int":
        return False
    if kind == "name":
        return e[1].split(".")[0] == "after"
    if kind == "call":
        return e[1] == "occupied_after" or any(uses_after(a) for a in e[2])
    if kind == "un":
        return uses_after(e[2])
    return uses_after(e[2]) or uses_after(e[3])


def parse(p, w, k, contract, ref, start_phase):
    """宣言の 1 つの証拠 → {"phase", "mino": (型, x, y, 向きの番号) | None, "cells": [(x, y)], "input": {名前: bool}}。"""
    where = f"{p['id']}.witness[{k}]"
    if not isinstance(w, dict) or not set(w) <= KEYS or "board" not in w or "mino" not in w:
        raise WitnessError(f"{where}: キーは board・mino（必須）と input・phase・counters です")
    if "counters" in w:
        raise WitnessError(f"{where}.counters: 契約の復元用コンストラクタ（GamePhase, ActiveMino?, IReadOnlyList<Cell>）は"
                           f"数を受け取らないので、証拠の状態に数を書けません")
    phase = w.get("phase", start_phase)
    if phase not in contract.enums["GamePhase"]:
        raise WitnessError(f"{where}.phase: GamePhase の値です: {phase!r}")
    width, height = ref["width"], ref["height"]
    rows = w["board"]
    if not isinstance(rows, list) or len(rows) > height:
        raise WitnessError(f"{where}.board: 高さ {height} 行以下の文字列の列です")
    cells = []
    for y, row in enumerate(rows):
        if not isinstance(row, str) or len(row) != width or set(row) - {"#", "."}:
            raise WitnessError(f"{where}.board[{y}]: 幅 {width} の # と . の文字列です: {row!r}")
        if "." not in row:
            raise WitnessError(f"{where}.board[{y}]: 埋まりきった行は書けません（実際の盤面には残らない）")
        cells += [(x, y) for x, ch in enumerate(row) if ch == "#"]
    occ = set(cells)
    mino = w["mino"]
    if mino is not None:
        if not isinstance(mino, dict) or set(mino) != MINO_KEYS:
            raise WitnessError(f"{where}.mino: キーは type・x・y・rotation です（null も書けます）")
        if mino["type"] not in contract.enums["MinoType"] or mino["rotation"] not in contract.enums["Rotation"]:
            raise WitnessError(f"{where}.mino: type は MinoType、rotation は Rotation の名前です")
        if any(isinstance(mino[c], bool) or not isinstance(mino[c], int) for c in ("x", "y")):
            raise WitnessError(f"{where}.mino: x・y は整数です")
        mino = (mino["type"], mino["x"], mino["y"], contract.enums["Rotation"].index(mino["rotation"]))
        if not Evaluator(contract, ref, occ, mino, {}, phase).fits(mino):
            raise WitnessError(f"{where}.mino: ミノが盤面に置けません（盤面の外か、固定ブロックに重なる）")
    keys = w.get("input", [])
    order = INPUT_ORDER
    if not isinstance(keys, list) or set(keys) - set(order) or len(set(keys)) != len(keys):
        raise WitnessError(f"{where}.input: TickInput の名前の列です（{list(order)}）")
    return {"phase": phase, "mino": mino, "cells": sorted(cells, key=lambda c: (c[1], c[0])),
            "input": {n: n in keys for n in order}}


class Evaluator:
    """参照モデル（C# の PropertyModel と同じ定義）で、前の状態と入力だけの式を評価する。"""

    def __init__(self, contract, ref, occ, mino, input_, phase):
        self.c, self.ref, self.occ, self.mino, self.input, self.phase = contract, ref, occ, mino, input_, phase

    def err(self, msg):
        raise WitnessError(msg)

    def occupied(self, x, y):
        return x < 0 or y < 0 or x >= self.ref["width"] or y >= self.ref["height"] or (x, y) in self.occ

    def cells(self, m):
        t, x, y, r = m
        return [(x + dx, y + dy) for dx, dy in self.ref["shapes"][t][r]]

    def fits(self, m):
        return not any(self.occupied(x, y) for x, y in self.cells(m))

    def in_board(self, m):
        w, h = self.ref["width"], self.ref["height"]
        return all(0 <= x < w and 0 <= y < h for x, y in self.cells(m))

    @staticmethod
    def moved(m, dx, dy):
        return (m[0], m[1] + dx, m[2] + dy, m[3])

    @staticmethod
    def rotated(m, d):
        return (m[0], m[1], m[2], ((m[3] + d) % 4 + 4) % 4)

    def kick(self, m, d):
        if d not in (1, -1):
            return None
        r = self.rotated(m, d)
        for dx, dy in self.ref["kicks"][m[0]][(m[3], r[3])]:
            c = self.moved(r, dx, dy)
            if self.fits(c):
                return c
        return None

    def full_rows(self, m):
        occ = set(self.occ) | {(x, y) for x, y in self.cells(m)
                               if 0 <= x < self.ref["width"] and 0 <= y < self.ref["height"]}
        return sum(1 for y in range(self.ref["height"]) if all((x, y) in occ for x in range(self.ref["width"])))

    def drop(self, m):
        while self.fits(self.moved(m, 0, -1)):
            m = self.moved(m, 0, -1)
        return m

    def value(self, e):
        """式の値。None は null（C# の null のミノ）。"""
        kind = e[0]
        if kind == "int":
            return e[1]
        if kind == "name":
            return self.name(e[1])
        if kind == "call":
            return self.call(e[1], [self.value(a) for a in e[2]])
        if kind == "un":
            v = self.value(e[2])
            return (not v) if e[1] == "!" else -v
        op = e[1]
        if op == "&&":
            return self.value(e[2]) and self.value(e[3])
        if op == "||":
            return self.value(e[2]) or self.value(e[3])
        left, right = self.value(e[2]), self.value(e[3])
        return {"+": lambda: left + right, "-": lambda: left - right, "<": lambda: left < right,
                "<=": lambda: left <= right, ">": lambda: left > right, ">=": lambda: left >= right,
                "==": lambda: left == right, "!=": lambda: left != right}[op]()

    def name(self, n):
        if n in ("true", "false"):
            return n == "true"
        if n == "null":
            return None
        parts = n.split(".")
        if len(parts) == 2 and parts[0] in self.c.enums and parts[1] in self.c.enums[parts[0]]:
            return (parts[0], parts[1])
        if len(parts) == 1:
            owners = [en for en, vs in self.c.enums.items() if n in vs]
            if len(owners) == 1:
                return (owners[0], n)
        if parts[0] == "after":
            self.err(f"{n}: after は証拠の状態では決まりません（前提は before と input だけで書く。原則 P1）")
        if parts[0] == "input" and len(parts) == 2 and parts[1] in self.input:
            return self.input[parts[1]]
        if parts[0] == "before" and len(parts) >= 2:
            if parts[1] == "Phase" and len(parts) == 2:
                return ("GamePhase", self.phase)
            if parts[1] == "ActiveMino":
                if len(parts) == 2:
                    return self.mino
                if self.mino is None:
                    raise _Null()
                member = parts[2]
                t, x, y, r = self.mino
                return {"Type": ("MinoType", t), "X": x, "Y": y,
                        "Rotation": ("Rotation", self.c.enums["Rotation"][r])}[member]
            self.err(f"{n}: 復元用コンストラクタが受け取らない状態なので、証拠の状態では決まりません")
        self.err(f"{n}: 証拠の状態で評価できない名前です")

    def call(self, fn, args):
        if fn == "occupied_after":
            self.err("occupied_after は証拠の状態では決まりません（原則 P1）")
        if fn == "occupied_before":
            return self.occupied(*args)
        minos = {"fits", "in_board", "moved", "rotated", "kick", "drop", "full_rows"}
        if fn in minos and args[0] is None:
            raise _Null()
        return {"fits": lambda: self.fits(args[0]), "in_board": lambda: self.in_board(args[0]),
                "moved": lambda: self.moved(*args), "rotated": lambda: self.rotated(*args),
                "kick": lambda: self.kick(*args), "drop": lambda: self.drop(args[0]),
                "full_rows": lambda: self.full_rows(args[0])}[fn]()

    def holds(self, e):
        """前提が成り立つか。null のミノのメンバーを読んだら成り立たない（C# と同じ）。"""
        try:
            return bool(self.value(e))
        except _Null:
            return False


def check(p, contract, ref, start_phase, parse_expr):
    """宣言の証拠をすべて読み、前提が成り立つことを確かめる。[parse の結果]。宣言が無ければ []。"""
    ws = p.get("witness")
    if ws is None:
        return []
    if not isinstance(ws, list) or not 1 <= len(ws) <= MAX_WITNESSES:
        raise WitnessError(f"{p['id']}.witness: 1〜{MAX_WITNESSES} 個の列です")
    given = parse_expr(str(p["given"]), f"{p['id']}.given")
    if uses_after(given):
        raise WitnessError(f"{p['id']}.given: 証拠の状態を書く性質の前提に after は使えません（then に移す。原則 P1）")
    out = []
    for k, w in enumerate(ws):
        parsed = parse(p, w, k, contract, ref, start_phase)
        ev = Evaluator(contract, ref, set(parsed["cells"]), parsed["mino"], parsed["input"], parsed["phase"])
        if not ev.holds(given):
            raise WitnessError(f"{p['id']}.witness[{k}]: この状態と入力では前提が成り立ちません（参照モデルで評価）")
        out.append(parsed)
    return out


def cs_array(ws, contract, ns):
    """C# の PropertyRunner.Witness[] の式。"""
    items = []
    for w in ws:
        mino = "null" if w["mino"] is None else (
            f"new {ns}.ActiveMino({ns}.MinoType.{w['mino'][0]}, {w['mino'][1]}, {w['mino'][2]}, "
            f"{ns}.Rotation.{contract.enums['Rotation'][w['mino'][3]]})")
        cells = "new " + ns + ".Cell[] { " + ", ".join(f"new {ns}.Cell({x}, {y})" for x, y in w["cells"]) + " }"
        inp = f"new {ns}.TickInput(" + ", ".join("true" if w["input"][n] else "false" for n in INPUT_ORDER) + ")"
        items.append(f"new PropertyRunner.Witness({ns}.GamePhase.{w['phase']}, {mino}, {cells}, {inp})")
    return "new PropertyRunner.Witness[] { " + ", ".join(items) + " }"


# PropertyRunner に足す C#（証拠を書いた性質があるときだけ出す。無ければ生成物は今までと同じバイト列）
RUNNER_CODE = """
        public sealed class Witness
        {{
            public readonly {ns}.GamePhase Phase;
            public readonly ActiveMino? Mino;
            public readonly {ns}.Cell[] Cells;
            public readonly {ns}.TickInput Input;

            public Witness({ns}.GamePhase phase, ActiveMino? mino, {ns}.Cell[] cells, {ns}.TickInput input)
            {{
                Phase = phase; Mino = mino; Cells = cells; Input = input;
            }}
        }}

        // 証拠の状態（原則 P1）：復元用コンストラクタで作り、1 Tick 進めて確かめる。前提の成立は生成の時点で
        // 参照モデルが確かめてある。given には成り立った回数を足す
        static void RunWitnesses(string id, string rule, Witness[] witnesses, Check check, ref long given)
        {{
            for (int k = 0; k < witnesses.Length; k++)
            {{
                var w = witnesses[k];
                var sut = new {ns}.GameState(w.Phase, w.Mino, w.Cells);
                var b = Snapshot.Of(sut);
                var occ = new bool[PropertyModel.Width, PropertyModel.Height];
                foreach (var c in w.Cells) occ[c.X, c.Y] = true;
                bool same = b.Phase == w.Phase && PropertyModel.Same(b.ActiveMino, w.Mino);
                for (int x = 0; x < PropertyModel.Width && same; x++)
                    for (int y = 0; y < PropertyModel.Height && same; y++)
                        same = b.Occ[x, y] == occ[x, y];
                if (!same)
                    global::NUnit.Framework.Assert.Fail("PROPERTY_WITNESS_RESTORE id=" + id + " rule=" + rule
                        + " witness=" + k + " expected=Phase:" + w.Phase + ",ActiveMino:" + Fmt(w.Mino)
                        + " actual=Phase:" + b.Phase + ",ActiveMino:" + Fmt(b.ActiveMino));
                string op = "witness" + k + ":" + Op(w.Input), expected, actual;
                try {{ sut.Tick(w.Input); }}
                catch (global::System.Exception e)
                {{
                    global::NUnit.Framework.Assert.Fail("PROPERTY_FAIL id=" + id + " rule=" + rule + " ops=[" + op
                        + "] expected=no_exception actual=" + e.GetType().Name);
                }}
                int r = check(b, w.Input, Snapshot.Of(sut), out expected, out actual);
                if (r == 0)
                    global::NUnit.Framework.Assert.Fail("PROPERTY_WITNESS_INVALID id=" + id + " rule=" + rule + " witness=" + k);
                given++;
                if (r == 2)
                    global::NUnit.Framework.Assert.Fail("PROPERTY_FAIL id=" + id + " rule=" + rule + " ops=[" + op
                        + "] expected=" + expected + " actual=" + actual);
            }}
        }}
"""
