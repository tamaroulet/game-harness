"""公式のテストの項目を、パッチの結果を見ずに「根拠：文／根拠：参照／隠し：名／隠し：文言」に分ける（実験と計画 §5-2）。

使うのは、課題の文・隠しのパッチ（データの patch。実装を消すパッチ）・公式のテストのファイル（データの test_patch から戻す）と、
課題の始まりの状態のソースに名と文言が出てくるか（箱の中の harness/fb_gate_probe.py の refs で調べる）だけ。
A のパッチと評価の結果は読まない。
"""
import ast
import re

DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)|^\s*class\s+([A-Za-z_]\w*)")
TOP_ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)")
WORD_RE = re.compile(r"[A-Za-z_]\w*")
SETUP_METHODS = {"setup_method", "setup_class", "setup", "setUp"}
MATCH_CALLS = {"raises", "warns", "deprecated_call", "assert_produces_warning"}
MSG_CHARS = set("<>:%{}[]=")
MIN_LEN = 12
LABELS = ("隠し：文言", "隠し：名", "根拠：参照", "根拠：文")


def is_test_path(path):
    parts = path.split("/")
    name = parts[-1]
    return (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or any(p in ("tests", "test") for p in parts[:-1]))


def _file_sections(patch):
    """unified diff を、ファイルごとの (パス, 行の並び) に分ける。パスは b/ 側（消したファイルは a/ 側）。"""
    cur, lines = None, []
    for line in (patch or "").splitlines():
        if line.startswith("diff --git "):
            if cur is not None:
                yield cur, lines
            m = re.match(r"diff --git a/(\S+) b/(\S+)", line)
            cur, lines = (m.group(2) if m else line), []
        elif cur is not None:
            lines.append(line)
    if cur is not None:
        yield cur, lines


def removed_names(mask_patch):
    """隠しのパッチが消した行（テストのファイルを除く）の def・class・字下げの無い代入の名（前後が __ の名は除く）。"""
    names = set()
    for path, lines in _file_sections(mask_patch):
        if is_test_path(path):
            continue
        for line in lines:
            if not line.startswith("-") or line.startswith("---"):
                continue
            body = line[1:]
            m = DEF_RE.match(body)
            name = (m.group(1) or m.group(2)) if m else None
            if not name:
                m2 = TOP_ASSIGN_RE.match(body)
                name = m2.group(1) if m2 else None
            if name and not (name.startswith("__") and name.endswith("__")):
                names.add(name)
    return names


def mask_files(mask_patch):
    """隠しのパッチが変えたファイル（テストのファイルを除く）。"""
    return {p for p, _ in _file_sections(mask_patch) if not is_test_path(p)}


def original_tests(test_patch):
    """test_patch（公式のテストのファイルを消すパッチ）から、元のファイルの中身を戻す。{パス: 中身}。"""
    out = {}
    for path, lines in _file_sections(test_patch):
        body = [ln[1:] for ln in lines if ln.startswith("-") and not ln.startswith("---")]
        if body:
            out[path] = "\n".join(body) + "\n"
    return out


def statement_words(statement):
    return set(WORD_RE.findall(statement or ""))


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def unregex(s):
    s = s.strip()
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("$") and not s.endswith("\\$"):
        s = s[:-1]
    return re.sub(r"\\(.)", r"\1", s)


def _is_fixture(fn):
    for d in fn.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name == "fixture":
            return True
    return False


def _fixtures(body):
    return {n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_fixture(n)}


def test_scopes(src):
    """テストの関数ごとの範囲（§5-2 の規則 2）。{"Class::name" か "name": [ノードの並び]}。"""
    tree = ast.parse(src)
    fixtures = _fixtures(tree.body)
    helpers = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def scope_of(fn, cls=None):
        nodes = [fn]
        cls_fixtures = {}
        if cls is not None:
            nodes += [m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in SETUP_METHODS]
            cls_fixtures = _fixtures(cls.body)
        for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs:
            fx = cls_fixtures.get(a.arg) or fixtures.get(a.arg)
            if fx is not None and fx is not fn:
                nodes.append(fx)
        called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for name in sorted(called):
            h = helpers.get(name)
            if h is not None and h is not fn and h not in nodes and not name.startswith("test"):
                nodes.append(h)
        return nodes

    scopes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            scopes[node.name] = scope_of(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test"):
                    scopes[f"{node.name}::{m.name}"] = scope_of(m, node)
    return scopes


def scope_names(nodes):
    out = set()
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Name):
                out.add(n.id)
            elif isinstance(n, ast.Attribute):
                out.add(n.attr)
    return out


def _str_consts(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))]
    return []


def message_like(s):
    return len(s) >= MIN_LEN or any(c.isspace() for c in s) or any(c in MSG_CHARS for c in s)


def scope_literals(nodes):
    """範囲の中の文言（§5-2 の規則 4）。[(種類, 文字列)]。種類は match か assert。"""
    out = []
    for root in nodes:
        for n in ast.walk(root):
            if isinstance(n, ast.Call):
                f = n.func
                fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if fname in MATCH_CALLS:
                    for kw in n.keywords:
                        if kw.arg == "match":
                            out += [("match", s) for s in _str_consts(kw.value) if s]
                if fname.startswith("assert"):
                    for a in list(n.args) + [kw.value for kw in n.keywords]:
                        out += [("assert", s) for s in _str_consts(a) if s and message_like(s)]
            elif isinstance(n, ast.Assert) and isinstance(n.test, ast.Compare):
                if all(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in n.test.ops):
                    for side in [n.test.left] + list(n.test.comparators):
                        out += [("assert", s) for s in _str_consts(side) if s and message_like(s)]
    return out


def literal_key(kind, s):
    return unregex(s) if kind == "match" else s


def classify(statement, removed, src_names, src_literals, nodes):
    """1 つのテストの関数を分ける（§5-2 の規則 5）。(分け, 理由の dict)。

    src_names：始まりの状態のソースに名として出てくる、消された名の集まり。
    src_literals：始まりの状態のソースに出てくる文言（literal_key の後の文字列）の集まり。
    """
    stmt_n = norm(statement)
    words = statement_words(statement)
    lits = []
    for kind, s in scope_literals(nodes):
        key = literal_key(kind, s)
        if norm(key) and norm(key) not in stmt_n and key not in src_literals:
            lits.append(key)
    used = scope_names(nodes) & set(removed)
    hidden = sorted(n for n in used if n not in words and n not in src_names)
    referred = sorted(n for n in used if n not in words and n in src_names)
    why = {"literals": lits[:5], "hidden_names": hidden[:10], "referred_names": referred[:10]}
    if lits:
        return "隠し：文言", why
    if hidden:
        return "隠し：名", why
    if referred:
        return "根拠：参照", why
    return "根拠：文", why


def function_key(node_id):
    """公式の項目の ID（path::Class::test[param]）→ テストの関数の鍵（Class::test か test）。"""
    parts = node_id.split("::")[1:]
    if not parts:
        return None
    parts[-1] = parts[-1].split("[", 1)[0]
    return "::".join(parts)


def literals_to_check(scopes):
    """refs で、始まりの状態のソースに出てくるかを調べる文言（重なりなし）。"""
    out = set()
    for nodes in scopes.values():
        for kind, s in scope_literals(nodes):
            key = literal_key(kind, s)
            if norm(key):
                out.add(key)
    return sorted(out)
