"""H ライン Gate B：TaskSpec の中身の矛盾（content_problems）と、実装の後のコードが宣言どおりか（declared_problems）。
標準ライブラリと size_limits・hline_spec.matches だけに頼る。LLM・子プロセス・ネットワークは呼ばない。"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hline_spec  # noqa: E402
import size_limits  # noqa: E402

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def module_path(module):
    """'harness/x.py' でも 'harness.x' でも、リポジトリの根からの 'harness/x.py' にそろえる。"""
    m = module.replace("\\", "/").removeprefix("./")
    return m if m.endswith(".py") else m.replace(".", "/") + ".py"


def _hit(path, patterns):
    return any(hline_spec.matches(path, q) for q in patterns)


def _annotation_problems(signatures):
    out = []
    for i, s in enumerate(signatures):
        pairs = [(f"$.signatures[{i}].params[{n}].type", p["type"]) for n, p in enumerate(s["params"])]
        for where, text in pairs + [(f"$.signatures[{i}].returns", s["returns"])]:
            try:
                ast.parse(text.strip(), mode="eval")
            except (SyntaxError, ValueError):
                out.append(f"{where}（{s['symbol']}）: 型の注釈として読めません（{text!r}）")
    return out


def content_problems(spec: dict, root: str | pathlib.Path | None = None) -> list[str]:
    """TaskSpec の項目どうしの矛盾の一覧（空なら矛盾なし）。スキーマに適合済みの spec を受け取る。"""
    box, out = spec["edit_boundary"], []
    allowed, forbidden = box["allowed_files"], box["forbidden_files"]
    for i, t in enumerate(spec["target_symbols"]):
        path, at = module_path(t["module"]), f"$.target_symbols[{i}]（{t['name']}）"
        if not _hit(path, allowed):
            out.append(f"{at}: module {t['module']}（{path}）が edit_boundary.allowed_files（{', '.join(allowed)}）のどれにも一致しません")
        if _hit(path, forbidden):
            out.append(f"{at}: module {t['module']}（{path}）が edit_boundary.forbidden_files（{', '.join(forbidden)}）に一致します")
    for i, a in enumerate(allowed):
        if clash := [f for f in forbidden if hline_spec.matches(a, f)]:
            out.append(f"$.edit_boundary.allowed_files[{i}]: {a} が forbidden_files の {', '.join(clash)} と重なります")
    out += _annotation_problems(spec["signatures"])
    cap = size_limits.limits(root)["max_added_lines"]
    if box["max_diff_lines"] > cap:
        out.append(f"$.edit_boundary.max_diff_lines: {box['max_diff_lines']} が追加行の上限 {cap} を超えます")
    return out


def _find(body, parts, kind):
    if (kind == "function" and len(parts) != 1) or (kind == "method" and len(parts) < 2):
        return None
    node = None
    for n, part in enumerate(parts):
        want = _FUNCS if n == len(parts) - 1 and kind != "class" else (ast.ClassDef,)
        node = next((x for x in body if isinstance(x, want) and x.name == part), None)
        if node is None:
            return None
        body = node.body
    return node


def _squash(text):
    return "".join(text.split())


def _ann(node):
    return _squash(ast.unparse(node)) if node is not None else "Any"


def _params(fn, method):
    a = fn.args
    args = a.posonlyargs + a.args + ([a.vararg] if a.vararg else []) + a.kwonlyargs + ([a.kwarg] if a.kwarg else [])
    have = [(x.arg, _ann(x.annotation)) for x in args]
    return have[1:] if method and have and have[0][0] in ("self", "cls") else have


def _show(params, returns):
    return "(" + ", ".join(f"{n}: {t}" for n, t in params) + f") -> {returns}"


def _signature_problem(name, fn, sig):
    want = [(p["name"], _squash(p["type"])) for p in sig["params"]]
    have = _params(fn, "." in name and not (want and want[0][0] in ("self", "cls")))
    want_ret, have_ret = _squash(sig["returns"]), _ann(fn.returns)
    if have == want and have_ret == want_ret:
        return None
    return f"{name}: 型シグネチャが宣言と違います（宣言: {_show(want, want_ret)} / 実装: {_show(have, have_ret)}）"


def _symbol_problem(root, t, sig):
    path = module_path(t["module"])
    where = f"{t['kind']} {t['name']}（{path}）"
    try:
        tree = ast.parse((pathlib.Path(root) / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as e:
        return f"{where}: {path} を読めません（{type(e).__name__}）"
    if t["kind"] == "module":
        node = tree if t["name"] in (pathlib.PurePosixPath(path).stem, path[:-3].replace("/", ".")) else None
    else:
        node = _find(tree.body, t["name"].split("."), t["kind"])
    if node is None:
        return f"{where}: 宣言した {t['kind']} {t['name']} が {path} にありません（無い・別の場所・種類違い）"
    if sig and isinstance(node, _FUNCS):
        return _signature_problem(t["name"], node, sig)
    return None


def declared_problems(spec: dict, root: str | pathlib.Path) -> list[str]:
    """実装の後のコード（root の下）が、対象シンボルと型シグネチャの宣言どおりか。食い違い 1 件につき 1 文字列。"""
    sigs = {s["symbol"]: s for s in spec.get("signatures", [])}
    return [why for t in spec.get("target_symbols", []) if (why := _symbol_problem(root, t, sigs.get(t["name"])))]
