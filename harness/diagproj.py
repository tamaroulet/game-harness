"""ビルド診断の射影（ADR-003 §3.11、B3.2）。

実装役の差分でビルドが通らないとき、コンパイラ（Roslyn）の診断をそのまま渡すと、実装役は
「どこが原因か」を取り違えたまま試行を重ねる（因果盲目）。特に、生成テスト側に出るエラーは
「テストが壊れている」と読まれやすいが、実際の原因は実装が interface の契約を満たしていないことにある。

そこで診断を次の形に射影し、生のメッセージは渡さない。

- 生成テスト側のエラー：メッセージ中の識別子を interface の宣言と突き合わせ、契約の不整合として
  `BUILD_DIAG kind=contract symbol=<型.メンバー> code=<CSxxxx>` の 1 行にする。
  interface の識別子を含まないエラーは、契約の不整合から派生した連鎖とみなして捨てる
- 実装側（impl_dir の下）のエラー：`BUILD_DIAG kind=impl file=<パス> line=<行> code=<CSxxxx>` の 1 行にする
- 同じ行は 1 回だけ出す。多くても MAX_LINES 行

**最小の不整合集合（MUC）**: 契約の不整合は「欠けている・形の違うメンバー」の集合として出す。
同じメンバーに由来するエラーが何十件あっても 1 行に畳む。連鎖したエラーは根に畳まれる。

本モジュールは観測と整形だけを行い、判定（合否）には使わない。
"""
import re

MAX_LINES = 10
HEADER = ("ビルドが通りません。検査系の故障ではなく、実装が interface の契約か C# の文法を満たしていません。"
          "次の行が原因の候補です（コンパイラのメッセージは渡しません）:")
DIAG_RE = re.compile(r"(?P<file>.+?)\((?P<line>\d+),\d+\): error (?P<code>CS\d{4}): (?P<msg>.*?)(?: \[[^\]]*\])?\s*$")
QUOTED_RE = re.compile(r"'([^']+)'")


def _identifiers(unit):
    """{識別子: "型.メンバー" または "型"}。メンバー名だけで引いたときは、宣言した型と組にして返す。"""
    ids = {}
    for t in unit.get("interface", {}).get("types", []):
        ids.setdefault(t["name"], t["name"])
        for m in t.get("members", []):
            full = f"{t['name']}.{m['name']}"
            ids[full] = full
            ids.setdefault(m["name"], full)
        for v in t.get("values", []) if t.get("kind") == "enum" else []:
            ids.setdefault(f"{t['name']}.{v}", f"{t['name']}.{v}")
    return ids


def _norm(path):
    return path.replace("\\", "/")


def project(build_output, unit, impl_dir):
    """ビルド出力を BUILD_DIAG の行の列にする。コンパイルエラーが 1 つも無ければ空の列。"""
    ids = _identifiers(unit)
    impl_dir = _norm(impl_dir).strip("/") + "/"
    contract, impl = [], []
    for raw in (build_output or "").splitlines():
        m = DIAG_RE.search(raw.strip())
        if not m:
            continue
        path = _norm(m.group("file"))
        code = m.group("code")
        if impl_dir in path:
            rel = path[path.index(impl_dir):]
            impl.append(f"BUILD_DIAG kind=impl file={rel} line={m.group('line')} code={code}")
            continue
        # 型.メンバー の形を先に探し、無ければ単独の名前を探す（'GameState' と 'Tick' が別々に引用される）
        quoted = QUOTED_RE.findall(m.group("msg"))
        owner = next((q for q in quoted if q in ids and "." not in ids[q]), None)
        symbols = []
        for q in quoted:
            if q == owner:
                continue
            if f"{owner}.{q}" in ids:
                symbols.append(f"{owner}.{q}")
            elif q in ids and "." in ids[q]:
                symbols.append(ids[q])
        if not symbols and owner:
            symbols.append(owner)
        for s in symbols:
            contract.append(f"BUILD_DIAG kind=contract symbol={s} code={code}")
    lines = list(dict.fromkeys(contract + impl))
    return lines[:MAX_LINES]


def feedback(build_output, unit, impl_dir):
    """実装役に返す文章。射影できる診断が無ければ None（呼び出し側が従来どおり扱う）。"""
    lines = project(build_output, unit, impl_dir)
    if not lines:
        return None
    return HEADER + "\n" + "\n".join(lines)
