"""規模の制約（Gate 1）：追加行・モジュールの行数・関数の循環的複雑度の上限（config/size_limits.json）。標準ライブラリだけ。"""
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_CACHE = {}
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BRANCH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert, ast.match_case)


def limits(root=None):
    root = Path(root or ROOT).resolve()
    if root not in _CACHE:
        _CACHE[root] = json.loads((root / "config" / "size_limits.json").read_text(encoding="utf-8"))
    return _CACHE[root]


def _branches(fn):
    """関数の分岐の数。入れ子の関数・クラスの中は数えない（別の名前で数える）。"""
    total, stack = 0, list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if not isinstance(node, _DEFS):
            total += (len(node.values) - 1 if isinstance(node, ast.BoolOp) else
                      len(node.ifs) if isinstance(node, ast.comprehension) else int(isinstance(node, _BRANCH)))
            stack.extend(ast.iter_child_nodes(node))
    return total


def _functions(source):
    """名前（入れ子は「外.内」）→ (循環的複雑度, 中身の ast.dump)。構文の誤りは None。"""
    def walk(node, prefix):
        found = {}
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFS):
                if not isinstance(child, ast.ClassDef):
                    found[prefix + child.name] = (1 + _branches(child), ast.dump(child))
                found.update(walk(child, f"{prefix}{child.name}."))
            else:
                found.update(walk(child, prefix))
        return found

    try:
        return walk(ast.parse(source), "")
    except (SyntaxError, ValueError):
        return None


def complexities(source):
    return {name: score for name, (score, _) in (_functions(source) or {}).items()}


def complexity_problems(path, before, after, limit):
    """after にある関数のうち、足した・中身を変えたものだけを見て、複雑度が limit を超えるものの理由。"""
    now, old = _functions(after), (_functions(before) if before is not None else None) or {}
    return [f"{path} の関数 {name} の循環的複雑度が {score} で、上限 {limit} を超えています。関数を分けてください。"
            for name, (score, dump) in (now or {}).items() if score > limit and old.get(name, (0, None))[1] != dump]


def module_size_problem(path, before_lines, after_lines, limits):
    """モジュールの行数の理由。新しいモジュールと上限以下のファイルは上限まで。上限を超えた既存のファイルは増やさない。"""
    cap = limits["new_module_max_lines"]
    if after_lines <= cap or (before_lines or 0) > cap and after_lines <= before_lines:
        return None
    was = "新しいモジュール" if before_lines is None else f"元は {before_lines} 行"
    return f"{path} が {after_lines} 行で、上限 {cap} 行を超えています（{was}）。新しい処理は別のモジュールに置いてください。"


def added_over_limit(added, limit):
    return f"差分の追加が {added} 行で、上限 {limit} 行を超えています。{added - limit} 行以上減らしてください。" if added > limit else None


def scan(wt, paths, limits, source_of):
    """paths のうち .py の、モジュールの行数と複雑度の違反の理由（paths の順）。消えた・読めないファイルは飛ばす。git は呼ばない。"""
    out = []
    for path in (p for p in paths if p.endswith(".py")):
        try:
            after = (Path(wt) / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        before = source_of(path)
        size = module_size_problem(path, None if before is None else len(before.splitlines()), len(after.splitlines()), limits)
        out += ([size] if size else []) + complexity_problems(path, before, after, limits["max_complexity"])
    return out


def oversized_files(wt, lim, dirs=("harness", "tests")):
    """wt の下の dirs の .py のうち、行数が new_module_max_lines を超えるもの。(相対パス, 行数) を行数の多い順、同数はパスの昇順で。読み取りだけ。"""
    found = {}
    for d in dirs:
        for f in (Path(wt) / d).glob("**/*.py"):
            try:
                n = len(f.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            if n > lim["new_module_max_lines"]:
                found[f.relative_to(wt).as_posix()] = n
    return sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))


def prompt_text(spec, wt, lim=None):
    """実装役に見せる規模の制約の節。上限の数値は spec・lim・作業ツリーの実測から取る。読み取りだけで、"---" だけの行は入れない。"""
    lim = limits() if lim is None else lim
    box = spec.get("edit_boundary") if isinstance(spec, dict) else None
    box = box if isinstance(box, dict) else {}
    gone = box.get("deletable_files")
    gone = [str(p) for p in gone] if isinstance(gone, (list, tuple)) else []
    big = oversized_files(wt, lim)
    cap = lim["new_module_max_lines"]
    return "\n".join([
        "## 規模の制約（ハーネスの門が機械で検査し、超えると落とします）",
        *([f"- 追加してよい行数は {box['max_diff_lines']} 行以内"] if "max_diff_lines" in box else []),
        f"- モジュール（.py）1 つの行数は {cap} 行以内。すでに {cap} 行を超えている既存のファイルは、増やせない",
        f"- 関数 1 つの循環的複雑度は {lim['max_complexity']} 以内。超えそうなら関数を分ける",
        f"- 丸ごと消してよいファイル: {', '.join(gone) or 'なし'}",
        "- すでに上限を超えているファイル（これらには行を足さない。新しい処理は別のモジュールに置く）:" + ("" if big else " なし"),
        *[f"  - {path}（{n} 行）" for path, n in big],
        "- テストを足すときは、既存の大きなテストのファイルに書き足さず、新しいテストのファイルを作って書く",
    ])


def head_source(cfg, wt):
    def source_of(path):
        code, out, _ = proc.run(["git", "show", f"HEAD:{path}"], wt, cfg["ttl_seconds"]["git"], "git show")
        return out if code == 0 else None
    return source_of


def diff_budget_problem(unit, added, deleted):
    """差分バジェット（ADR-003 §3.7）。feature は追加と削除を別枠、refactor は合計。task_kind の無い単位は feature。"""
    if unit.get("task_kind", "feature") == "refactor":
        total, limit = added + deleted, unit["max_impl_lines"]
        return f"差分超過（refactor）: 合計 {total} 行 > {limit}" if total > limit else None
    max_add = unit.get("max_add_lines", unit["max_impl_lines"])
    max_del = unit.get("max_del_lines", unit["max_impl_lines"])
    if added > max_add:
        return f"差分超過（feature）: 追加 {added} 行 > {max_add}"
    if deleted > max_del:
        return (f"差分超過（feature）: 削除 {deleted} 行 > {max_del}。"
                "削除が多いならリファクタリングの単位を先に切り出す")
    return None
