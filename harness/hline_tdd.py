"""H ライン（S3-3）の TDD 境界：受入テストを公開分と非公開分に分け、実装役には公開分だけを見せ、実装の後に両方を走らせる。
公開分の本文は入力に載せるので、実装役はそれを満たす実装を書ける。非公開分は作業ツリーの外に置いたまま入力に漏らさず、実装の後に注入して
走らせる（写した実装を落とす）。公開分を書き換えて通す・skip で飛ばす不正は tamper_problems が見る。外のプロセス・LLM・git・gh・ネットワークは
自分では呼ばず、動かすのは gate_tdd に渡された run だけ。副作用は split（outdir の下）と gate_tdd（tests/ への注入と片付け）だけ。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_red  # noqa: E402  python -m harness.x でも読めるように

VISIBLE_KEYS = ("target_symbols", "signatures", "edit_boundary")
SKIP_MARK = re.compile(r"\b(?:skip|skipIf|skipUnless|expectedFailure|SkipTest)\b")
TAIL = 800


def _is_test(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def _render(tree: ast.Module, keep: set[int]) -> str:
    out, index, emptied = copy.deepcopy(tree), 0, set()
    for cls in (n for n in out.body if isinstance(n, ast.ClassDef)):
        body = []
        for node in cls.body:
            if _is_test(node):
                index += 1
                if index - 1 not in keep:
                    continue
            body.append(node)
        if not any(_is_test(n) for n in body) and any(_is_test(n) for n in cls.body):
            emptied.add(cls.name)
        cls.body = body or [ast.Pass()]
    bases = {b.id for n in out.body if isinstance(n, ast.ClassDef) for b in n.bases if isinstance(b, ast.Name)}
    out.body = [n for n in out.body if not (isinstance(n, ast.ClassDef) and n.name in emptied - bases)]
    return ast.unparse(out) + "\n"


def split(accept: str, outdir: str) -> tuple[str, str]:
    """検査（test で始まるメソッド）を 1 つおきに公開分・非公開分へ。検査が 1 件しか無いときだけ、両方に同じ検査を入れる。"""
    tree = ast.parse(Path(accept).read_text(encoding="utf-8"), accept)
    total = sum(1 for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body if _is_test(m))
    if total == 0:
        raise ValueError("受入テストに検査（TestCase の test メソッド）がありません")
    shares = (set(range(0, total, 2)), set(range(1, total, 2)) or {0})
    stem, dest = Path(accept).stem, os.path.join(os.path.abspath(outdir), "split")
    os.makedirs(dest, exist_ok=True)
    paths = []
    for tag, keep in zip(("public", "hidden"), shares):
        path = os.path.join(dest, f"{stem}_{tag}.py")
        Path(path).write_text(_render(tree, keep), encoding="utf-8")
        paths.append(path)
    if (problems := [p for path in paths for p in hline_red.compile_problems(path)]):
        raise ValueError("; ".join(problems))
    return paths[0], paths[1]


def visible_spec(spec: dict) -> dict:
    return {k: copy.deepcopy(spec[k]) for k in VISIBLE_KEYS}


def build_prompt(spec: dict, public: str, feedback: str | None = None, symbol_map: str | None = None) -> str:
    """public は公開分のパス（読めないときは本文そのもの）。contracts と test_oracle・非公開分は入力に入れない。"""
    path = Path(public)
    text = path.read_text(encoding="utf-8") if path.is_file() else public
    lines = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
             "次の仕様（JSON）と公開テストを満たす変更を、作業ディレクトリの中だけで行ってください。",
             "- edit_boundary の allowed_files の外と forbidden_files は変えない。差分は max_diff_lines 行以内",
             f"- 公開テストは実装の後に tests/{path.name} へ置き直して走らせる。書き換えず、skip もしない。公開テストのほかに、見せない検査も走る",
             "- 自分でも unittest のテストを tests/ に書いて示す（既存の書き方に合わせる）。全件テストはハーネスが走らせるので、自分では走らせない",
             "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）",
             "- 仕様のとおりには実装できないと判断したときは、回り道をせず、最終の返答に「H-LINE-ABANDON: 理由」を 1 行で書いて止まる（行頭から書く）",
             *(["", symbol_map] if symbol_map else []),
             "", "---", json.dumps(visible_spec(spec), ensure_ascii=False, indent=2),
             "", "---", "公開テスト:", text]
    if feedback:
        lines += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(lines) + "\n"


def _skips(text: str) -> int:
    return len(SKIP_MARK.findall(text))


def tamper_problems(wt: str, public: str) -> list[str]:
    name = os.path.basename(public)
    placed = Path(wt, "tests", name)
    if not placed.is_file():
        return [f"公開テスト tests/{name} が作業ツリーにありません（消されました）"]
    want, got = Path(public).read_bytes(), placed.read_bytes()
    if want == got:
        return []
    out = [f"公開テスト tests/{name} の中身が元と違います（書き換えられました）"]
    if _skips(got.decode("utf-8", errors="replace")) > _skips(want.decode("utf-8", errors="replace")):
        out.append(f"公開テスト tests/{name} に skip の指定が増えています（検査が飛ばされます）")
    return out


def _result(label: str, name: str, code: int, out: str, show: bool) -> list[str]:
    if code == 0:
        return []
    head = f"{label}（tests/{name}）が落ちました（終了コード {code}）"
    return [f"{head}: {out[-TAIL:]}" if show else f"{head}。非公開の検査なので出力は伏せます"]


def gate_tdd(wt: str, public: str, hidden: str, run: object) -> list[str]:
    placed, problems = ["tests/" + os.path.basename(p) for p in (public, hidden)], []
    try:
        for path in (public, hidden):
            hline_red.inject(path, wt)
        for label, path, show in (("公開分", public, True), ("非公開分", hidden, False)):
            name = os.path.basename(path)
            code, out = run([sys.executable, "-m", "unittest", "tests." + Path(path).stem], wt)
            problems += _result(label, name, code, out, show)
    finally:
        hline_red.discard(wt, placed)
    return problems
