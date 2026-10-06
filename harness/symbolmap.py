"""リポジトリの目次。分解役・実装役が探索でターンを使い切らないよう、作業ツリーからその場で作って渡す。
読み取りだけ（ast・pathlib）。子プロセス・ネットワーク・モデルは呼ばず、hline.py・hline_spec.py は import しない。"""
import ast
import re
from pathlib import Path

from hline_base import Infra  # isort: skip（harness/ を import の道に足す）

HEAD = "# リポジトリの目次（harness/ のモジュール・クラス・関数。引数・行番号・import しているテストつき）"
DOC_HEAD = "# 関係しそうなモジュールの説明（モジュール先頭の docstring）"
NOTES = ("（引数を省いて縮めました）", "（引数とメソッドを省いて縮めました）")
CUT = "…（長すぎるため以降を切り落としました）"
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _arg(x, d=None):
    return x.arg + (f": {ast.unparse(x.annotation)}" if x.annotation else "") + (f"={ast.unparse(d)}" if d is not None else "")


def _params(a):
    pos = a.posonlyargs + a.args
    out = [_arg(x, d) for x, d in zip(pos, [None] * (len(pos) - len(a.defaults)) + a.defaults)]
    out += ["*" + _arg(a.vararg)] if a.vararg else ["*"] if a.kwonlyargs else []
    out += [_arg(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults)]
    return out + (["**" + _arg(a.kwarg)] if a.kwarg else [])


def _func(n):
    return {"name": n.name, "lineno": n.lineno, "params": _params(n.args), "returns": ast.unparse(n.returns) if n.returns else None}


def _tree(source):
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def module_index(source, path):
    """モジュール 1 つの目次（doc は先頭の docstring か None）。構文の誤りがあるときだけ None。"""
    tree = _tree(source)
    if tree is None:
        return None
    classes = [{"name": c.name, "lineno": c.lineno, "methods": [_func(m) for m in c.body if isinstance(m, DEFS)]}
               for c in tree.body if isinstance(c, ast.ClassDef)]
    return {"path": str(path).replace("\\", "/"), "classes": classes,
            "functions": [_func(f) for f in tree.body if isinstance(f, DEFS)], "tests": [], "doc": ast.get_docstring(tree)}


def _imported(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            yield from [n.module] + [f"{n.module}.{a.name}" for a in n.names]


def test_importers(root):
    """harness のモジュールのパス → それを import している tests/ のファイルのパス（重複なし・ソート済み）。"""
    root, found = Path(root), {}
    known = {p.relative_to(root).as_posix() for p in root.glob("harness/**/*.py")}
    for path in sorted(root.glob("tests/**/*.py")):
        for name in _imported(_tree(path.read_text(encoding="utf-8-sig", errors="replace")) or ast.Module([], [])):
            stem = "harness/" + name.removeprefix("harness.").replace(".", "/")
            for target in (p for p in (stem + ".py", stem + "/__init__.py") if p in known):
                found.setdefault(target, set()).add(path.relative_to(root).as_posix())
    return {k: sorted(v) for k, v in found.items()}


def build(root):
    """{"modules": {パス: 目次}, "skipped": [構文の誤り・読めないので飛ばしたパス]}。harness/ が無ければ空。"""
    root, modules, skipped = Path(root), {}, []
    importers = test_importers(root)
    for file in sorted(root.glob("harness/**/*.py")):
        rel = file.relative_to(root).as_posix()
        index = module_index(file.read_text(encoding="utf-8-sig", errors="replace"), rel)
        if index is None:
            skipped.append(rel)
        else:
            modules[rel] = dict(index, tests=importers.get(rel, []))
    return {"modules": modules, "skipped": sorted(skipped)}


def for_modules(index, modules):
    """modules に挙げたパスだけを残した目次（元の index は変えない。無いパスは無視する）。"""
    want = {str(m).replace("\\", "/") for m in modules}
    return {"modules": {p: m for p, m in index.get("modules", {}).items() if p in want},
            "skipped": [p for p in index.get("skipped", []) if p in want]}


def spec_modules(spec):
    """TaskSpec の target_symbols[*].module（現れた順・重複なし・/ 区切り）。読めない要素は飛ばす。"""
    items = spec.get("target_symbols") if isinstance(spec, dict) else None
    mods = [s["module"].replace("\\", "/") for s in (items if isinstance(items, list) else [])
            if isinstance(s, dict) and isinstance(s.get("module"), str)]
    return tuple(dict.fromkeys(mods))


def mentioned_modules(text, index):
    """text にパス（/ 区切り。\\ 区切りも同じ）かファイル名が現れる index のモジュール（index の順・重複なし）。"""
    text = text.replace("\\", "/")
    def named(s):
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(s) + r"(?![A-Za-z0-9_])", text)
    return tuple(p for p in index.get("modules", {}) if named(p) or named(p.rsplit("/", 1)[-1]))


def imported_modules(root, paths, index):
    """paths のファイルが import している index のモジュール（1 段だけ。index の順・重複なし）。読めない・構文の誤りは飛ばす。"""
    known, found = index.get("modules", {}), set()
    for rel in paths:
        try:
            tree = _tree((Path(root) / rel).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            continue
        for name in _imported(tree or ast.Module([], [])):
            stem = "harness/" + name.removeprefix("harness.").replace(".", "/")
            found.update(p for p in (stem + ".py", stem + "/__init__.py") if p in known)
    return tuple(p for p in known if p in found)


def candidate_modules(text, root, index):
    """text が挙げたモジュールと、それらが import しているモジュール（1 段だけ。index の順・重複なし）。"""
    mentioned = mentioned_modules(text, index)
    want = set(mentioned) | set(imported_modules(root, mentioned, index))
    return tuple(p for p in index.get("modules", {}) if p in want)


def docs_text(index, paths, max_chars):
    """paths のモジュールの docstring の節（max_chars を超えない）。入り切らないモジュールは末尾から落とし、最後の行で知らせる。"""
    mods = index.get("modules", {})
    docs = [(p, mods[p]["doc"]) for p in dict.fromkeys(paths) if p in mods and mods[p].get("doc")]
    if max_chars < 1 or not docs:
        return ""
    parts = [f"## {p}\n{d}" for p, d in docs]
    text = "\n\n".join([DOC_HEAD] + parts)
    if len(text) <= max_chars:
        return text
    while parts and len("\n\n".join([DOC_HEAD] + parts) + "\n" + CUT) > max_chars:
        parts.pop()
    return "\n\n".join([DOC_HEAD] + parts) + "\n" + CUT if len(DOC_HEAD) + 1 + len(CUT) <= max_chars else CUT[:max_chars]


def _line(f, level, pad):
    params = "..." if level else ", ".join(f["params"])
    return f"{pad}def {f['name']}({params})" + (f" -> {f['returns']}" if f["returns"] else "") + f"  L{f['lineno']}"


def _lines(index, level):
    out = [HEAD] + (["飛ばした（構文の誤り・読めない）: " + ", ".join(index["skipped"])] if index.get("skipped") else [])
    for path, m in index.get("modules", {}).items():
        out += ["", f"## {path}", "テスト: " + (", ".join(m["tests"]) or "なし")] + [_line(f, level, "") for f in m["functions"]]
        for c in m["classes"]:
            out += [f"class {c['name']}  L{c['lineno']}"] + ([_line(f, level, "  ") for f in c["methods"]] if level < 2 else [])
    return out


def render(index, max_chars):
    """目次の文字列（max_chars を超えない）。超えるときは引数 → メソッド → 末尾の順に省き、最後の行で知らせる。"""
    for level in (0, 1, 2):
        text = "\n".join(_lines(index, level) + ([NOTES[level - 1]] if level else []))
        if len(text) <= max_chars:
            return text
    room = max_chars - len(CUT) - 1
    return (text[:room].rsplit("\n", 1)[0] + "\n" + CUT) if room > 0 else CUT[:max(max_chars, 0)]


def limit(cfg):
    sm = cfg.get("symbol_map") if isinstance(cfg, dict) else None
    v = sm.get("max_chars") if isinstance(sm, dict) else None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise Infra(f"config/hline.json の symbol_map.max_chars が足りないか不正です（1 以上の整数）: {v!r}")
    return v


def prompt_text(cfg, wt, modules=None, what=None):
    """作業ツリー wt からその場で作った目次の文字列（modules があればそれだけ）。what があれば、それが挙げたモジュール
    （と import 先）の docstring の節（上限の 3 分の 1 まで）を前に添える。作れなければ空文字列で、呼び手は止まらない。"""
    try:
        index, size = build(wt), limit(cfg)
        shown = index if modules is None else for_modules(index, modules)
        docs = docs_text(index, candidate_modules(what, wt, index), size // 3) if what else ""
        return docs + "\n\n" + render(shown, size - len(docs) - 2) if docs else render(shown, size)
    except Infra:
        raise
    except Exception:  # noqa: BLE001 - 目次が作れなくても、分解役・実装役は呼ぶ
        return ""
