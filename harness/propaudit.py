"""性質の宣言の監査：前提（given）が「構成できる」か（docs/design/v2r_instrument_redesign.md の原則 P1）。

    python -m harness.propaudit experiments/v2r/properties.json [--strict]

**なぜ要るか**: 性質テストの前提が、実装の結果（`after`）を条件にしていると、実装がその出来事を起こさない限り前提が
成り立たず、性質は空虚になる（v2r-dry-01・02 の T6 で、ライン消去の性質 3 件が成立回数 0）。前提が成り立たないのが
「実装の欠陥」なのか「系列の運」なのかを、測定器が見分けられない。そのたびに、前提を狙う探索（v2.1e）・抽出（v2.1f）・
穴の行（gap_rows）を足して確率を上げてきたが、どれも確率を上げるだけで、成り立つことを保証しない。

原則 P1（Given は構成する。観測しない）：前提は、テストが実装を動かす前に作れる状態と入力だけで書く。実装の結果は then に書く。

前提の論理積の項を 3 つに分ける：
- `start`：開始状態だけで評価できる（propgen._pre_ok）。前提の探索（directed）が実装に依らず確かめる
- `constructible`：before と input だけを使うが、開始状態では決まらない（盤面・得点・消去数など）。系列の途中で運よく
  起きるのを待つのではなく、証拠の状態（witness）を注入して起こす必要がある
- `outcome`：after か occupied_after を使う。実装の結果を前提にしている。**P1 に反する**

`--strict` では、outcome の項が 1 つでもあれば終了コード 1。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import propgen  # noqa: E402
import witness  # noqa: E402

def classify(c):
    """前提の論理積の 1 項 → "start" | "constructible" | "outcome"。"""
    if witness.uses_after(c):
        return "outcome"
    if propgen._pre_ok(c):
        return "start"
    return "constructible"


def audit(props):
    """[{"id", "task", "start", "constructible", "outcome"}]。各キーは項の数。"""
    rows = []
    for p in props:
        counts = {"start": 0, "constructible": 0, "outcome": 0}
        for c in propgen._conjuncts(propgen.parse(str(p["given"]), f"{p['id']}.given")):
            counts[classify(c)] += 1
        rows.append({"id": p["id"], "task": p["task"], **counts})
    return rows


def render(rows):
    bad = [r for r in rows if r["outcome"]]
    need = [r for r in rows if not r["outcome"] and r["constructible"]]
    lines = ["| 性質 | タスク | start | constructible | outcome |", "|:--|:--|:--|:--|:--|"]
    lines += [f"| {r['id']} | {r['task']} | {r['start']} | {r['constructible']} | {r['outcome']} |" for r in rows]
    lines += ["", f"- 性質：{len(rows)} 件",
              f"- outcome の項を持つ（P1 に反する）：{len(bad)} 件",
              f"- outcome は無いが constructible の項を持つ（証拠の状態の注入が要る）：{len(need)} 件",
              f"- start の項だけ（前提の探索で足りる）：{len(rows) - len(bad) - len(need)} 件"]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="性質の前提が構成できるかの監査（P1）")
    ap.add_argument("decl")
    ap.add_argument("--strict", action="store_true", help="outcome の項が 1 つでもあれば終了コード 1")
    args = ap.parse_args(argv)
    props = json.loads(Path(args.decl).read_text(encoding="utf-8"))["properties"]
    rows = audit(props)
    print(render(rows))
    return 1 if args.strict and any(r["outcome"] for r in rows) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
