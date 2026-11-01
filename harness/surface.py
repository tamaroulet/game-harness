"""最適化の面（v3 §7-8）の外枠・つまみの当て方・差分の検査。外枠（FRAME と KNOBS）はこのコードの定数で、周期からは変えられない。

    python -m harness.surface check --repo <clone> --old <rev> --new <rev> [--chains <dir> --bases <B,...>]

面は 2 つ：実装役への指示を組む 2 つの関数（harness/hline_prompt.py の build_prompt と ledger_section だけ。同じファイルの
implement() と import などほかの頂の文は面の外）と、面の設定のファイル 1 つ（config/surface.json）。
面の設定は、高リスクの設定（config/hline.json）のつまみの値を、範囲つきで写したもの。鎖の段の走り（bench_chain.ChainStage）でだけ当てる。
check は、<old>..<new> の差分の、面の外のパス（改名は元と先の両方・面の設定を消すのも当たり）・<new> の面の設定の誤り・
hline_prompt.py の関数の単位の違反（2 つの関数の外の頂の文の違い・2 つの関数の欠けと名と引数と decorator・関数の中の import と
書き込みや外への呼び出しと属性への代入）・隠しテストの関数の名やクラスの名・package の名・土台の名の漏れ（足した行に出るもの）を見る。
漏れは文字列を出さず、ファイルと行だけを出す。関数の単位の当たりは、規則と関数の名（か「頂の文」）と行だけを出す。
終了コード：当たりが無ければ 0、あれば 1、引数や git の誤りは 2。
"""
import argparse
import ast
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

SETTING = "config/surface.json"
FRAME = ("harness/hline_prompt.py", SETTING)   # 面のパス。このモジュール自身は面の外
KNOBS = {   # つまみの名前 → (config/hline.json の中の場所, 型, 最小, 最大)
    "implementer_max_turns": (("implementer", "budget", "max_turns"), int, 5, 40),
    "implementer_resume_max_turns": (("implementer", "budget", "resume_max_turns"), int, 3, 30),
    "max_attempts": (("max_attempts",), int, 1, 4),
}
PROMPT_FILE = FRAME[0]
FACES = ("build_prompt", "ledger_section")   # PROMPT_FILE のうち面の中の関数。ほかの頂の文（implement()・import など）は面の外
BANNED_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)
BANNED_CALLS = frozenset(("exec", "eval", "compile", "open", "__import__", "setattr", "delattr", "getattr", "globals",
                          "locals", "vars", "breakpoint"))
BANNED_ATTRS = frozenset(("write_text", "write_bytes", "open", "unlink", "rename", "mkdir", "rmdir", "touch", "chmod",
                          "system", "popen", "run", "call", "Popen", "check_output", "sleep"))
MIN_CLASS = 6  # これより短いクラスの名（T など）は、ふつうの語と重なるので集めない
WORD = "A-Za-z0-9_"
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# ============================================================ 面の設定とつまみ

def values_of(text, where=SETTING):
    """面の設定の本文を照らし、{つまみの名前: 値} を返す。`_` で始まるキーは読み飛ばす。合わなければ BenchError。"""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise BenchError(f"{where} が JSON として読めません: {e}") from e
    if not isinstance(doc, dict):
        raise BenchError(f"{where} がオブジェクトではありません")
    out = {}
    for name, value in doc.items():
        if name.startswith("_"):
            continue
        if name not in KNOBS:
            raise BenchError(f"{where} に知らないつまみがあります: {name}")
        _, kind, low, high = KNOBS[name]
        if isinstance(value, bool) or not isinstance(value, kind):   # True は int に当たるので、先に除く
            raise BenchError(f"{where} の {name} が {kind.__name__} ではありません: {value!r}")
        if not low <= value <= high:
            raise BenchError(f"{where} の {name} が範囲 {low}〜{high} の外です: {value}")
        out[name] = value
    return out


def load(path):
    """(つまみの値, ファイルの sha256)。ファイルが無い・読めない・照らしに合わなければ BenchError。"""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise BenchError(f"面の設定を読めません: {path}: {e}") from e
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BenchError(f"{path} が UTF-8 ではありません: {e}") from e
    return values_of(text, str(path)), hashlib.sha256(data).hexdigest()


def put(cfg, values):
    """cfg の写しに values を当てて返す（元の dict は変えない）。"""
    out = copy.deepcopy(cfg)
    for name, value in values.items():
        keys = KNOBS[name][0]
        node = out
        for key in keys[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise BenchError(f"設定の {'.'.join(keys[:-1])} がオブジェクトではありません")
        node[keys[-1]] = value
    return out


def apply(cfg, path):
    """cfg（hline の設定の dict）の写しに、面の設定 path の値を当てて返す。面の設定に誤りがあれば BenchError。"""
    return put(cfg, load(path)[0])


# ============================================================ check：面の外・面の設定

def name_status(repo, old, new):
    """[(状態, [パス, ...])]。改名と写しは元と先の 2 つ。"""
    raw = git(repo, ["-c", "core.quotepath=false", "diff", "--name-status", "-M", "-z", old, new]).decode("utf-8", "surrogateescape")
    fields, out, i = raw.split("\0"), [], 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        n = 2 if status[:1] in ("R", "C") else 1
        out.append((status, fields[i + 1:i + 1 + n]))
        i += 1 + n
    return out


def outside(entries):
    """面の外に触れた差分と、面の設定を消した差分の行。"""
    hits = []
    for status, paths in entries:
        bad = [p for p in paths if p not in FRAME]
        if bad:
            hits.append(f"面の外: {status} {' -> '.join(paths)}")
        elif status == "D" and SETTING in paths:
            hits.append(f"面の設定を消しています: {SETTING}")
    return hits


def setting(repo, new):
    """<new> の面の設定を照らした当たり（無ければ空）。"""
    try:
        data = git(repo, ["cat-file", "blob", f"{new}:{SETTING}"])
    except BenchError:
        return [f"面の設定がありません: {SETTING}"]
    try:
        values_of(data.decode("utf-8"), f"{new}:{SETTING}")
    except (BenchError, UnicodeDecodeError) as e:
        return [f"面の設定: {e}"]
    return []


# ============================================================ check：hline_prompt.py の関数の単位

def unit_hit(rule, where, line=None):
    """関数の単位の当たりの文。規則と、関数の名か「頂の文」と、行の番号だけを出す（中身は出さない）。"""
    return f"関数の単位: {rule}: {where}" + ("" if line is None else f" {line} 行")


def blob(repo, rev, path):
    """<rev> の path の中身（bytes）。無ければ None。"""
    try:
        return git(repo, ["cat-file", "blob", f"{rev}:{path}"])
    except BenchError:
        return None


def parse_blob(data):
    """ast の木。無い・読めなければ None。"""
    try:
        return None if data is None else ast.parse(data.decode("utf-8"))
    except (SyntaxError, ValueError):   # UnicodeDecodeError と NUL の混じりは ValueError
        return None


def split_top(tree):
    """(面の関数の {名: ノード}, 面の外の頂の文の列)。同じ名の 2 つ目の def と async def は面の外に数える。"""
    faces, rest = {}, []
    for node in tree.body if tree is not None else ():
        if isinstance(node, ast.FunctionDef) and node.name in FACES and node.name not in faces:
            faces[node.name] = node
        else:
            rest.append(node)
    return faces, rest


def top_hits(old, new):
    """面の外の頂の文が、並びも含めて 1 つでも違えば、最初に違う所の当たり 1 つ（行の番号は比べない）。"""
    a, b = [ast.dump(n) for n in old], [ast.dump(n) for n in new]
    if a == b:
        return []
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    line = new[i].lineno if i < len(new) else (new[-1].end_lineno if new else None)
    return [unit_hit("面の外の頂の文が違います", "頂の文", line)]


def signature_hits(old, new):
    """2 つの関数の欠け・名か引数の違い・decorator。"""
    hits = []
    for name in FACES:
        o, n = old.get(name), new.get(name)
        if n is None:
            hits.append(unit_hit("関数がありません", name))
            continue
        if o is None or ast.dump(o.args) != ast.dump(n.args):
            hits.append(unit_hit("名か引数が違います", name, n.lineno))
        if n.decorator_list:
            hits.append(unit_hit("decorator が付いています", name, n.lineno))
    return hits


def body_reason(node):
    """面の関数の中のノード 1 つが当たる規則の文。当たらなければ None。"""
    if isinstance(node, BANNED_NODES):
        return f"{type(node).__name__} があります"
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
            return "禁じた名の呼び出しがあります"
        if isinstance(f, ast.Attribute) and f.attr in BANNED_ATTRS:
            return "禁じた属性の呼び出しがあります"
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return "属性への代入か del があります"
    return None


def body_hits(faces):
    """面の関数の中（引数の既定値・注釈を含む）の当たり。"""
    return [unit_hit(why, name, getattr(node, "lineno", fn.lineno))
            for name, fn in faces.items() for node in ast.walk(fn) if (why := body_reason(node))]


def unit_check(repo, old, new):
    """<old>..<new> で PROMPT_FILE が変わっていれば、関数の単位で照らした当たり（変わっていなければ空）。"""
    before, after = blob(repo, old, PROMPT_FILE), blob(repo, new, PROMPT_FILE)
    if before == after:
        return []
    tree = parse_blob(after)
    if tree is None:
        return [unit_hit(f"<new> の {PROMPT_FILE} が無いか、ast で読めません", "頂の文")]
    (of, orest), (nf, nrest) = split_top(parse_blob(before)), split_top(tree)
    return top_hits(orest, nrest) + signature_hits(of, nf) + body_hits(nf)


# ============================================================ check：漏れ

def package_of(chains, base):
    """段 0 の木のパッケージの名（bench_chain.public_files と同じ読み方）。1 つでなければ BenchError。"""
    tree = Path(chains) / base / "s0" / "tree"
    pkgs = sorted(p.name for p in tree.iterdir() if p.is_dir() and p.name not in ("tests", "__pycache__")) if tree.is_dir() else []
    if len(pkgs) != 1:
        raise BenchError(f"{base} の段 0 の木のパッケージが 1 つではありません（{len(pkgs)} 個）")
    return pkgs[0]


def hidden_names(path):
    """隠しテストのファイルの、test_ で始まる関数の名と、MIN_CLASS 字以上のクラスの名。"""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        raise BenchError(f"隠しテストを読めません: {path}（{type(e).__name__}）") from e
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef) and len(node.name) >= MIN_CLASS:
            names.add(node.name)
    return names


def secrets_of(chains, bases):
    """漏れを見る文字列の組：隠しテストの関数とクラスの名・package の名・土台の名。"""
    words = set()
    for base in bases:
        if not (Path(chains) / base).is_dir():
            raise BenchError(f"鎖の置き場に {base} がありません")
        words |= {base, package_of(chains, base)}
        for path in sorted(Path(chains, base).glob("s*/hidden/*.py")):
            words |= hidden_names(path)
    return words


def added_lines(repo, old, new):
    """差分の足した行の [(パス, 新しい側の行の番号, 本文)]。"""
    raw = git(repo, ["-c", "core.quotepath=false", "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", old, new])
    out, path, line = [], None, 0
    for text in raw.decode("utf-8", "surrogateescape").splitlines():
        if text.startswith("+++ "):
            path = None if text[4:] == "/dev/null" else text[4:].removeprefix("b/")
        elif (m := HUNK.match(text)):
            line = int(m.group(1))
        elif text.startswith("+") and path is not None:
            out.append((path, line, text[1:]))
            line += 1
    return out


def leaks(lines, words):
    """足した行のうち、words のどれかが語の境で出てくるものの "漏れ: <パス>:<行>"（文字列は出さない）。"""
    if not words:
        return []
    pattern = re.compile(rf"(?<![{WORD}])(?:{'|'.join(map(re.escape, sorted(words, key=len, reverse=True)))})(?![{WORD}])")
    return [f"漏れ: {path}:{n}" for path, n, text in lines if pattern.search(text)]


# ============================================================ 命令

def check(a):
    repo = Path(a.repo)
    if bool(a.chains) != bool(a.bases):
        print("--chains と --bases は一緒に渡してください", file=sys.stderr)
        return 2
    try:
        old, new = (git(repo, ["rev-parse", "--verify", "-q", f"{r}^{{commit}}"]).decode().strip() for r in (a.old, a.new))
        hits = outside(name_status(repo, old, new)) + setting(repo, new) + unit_check(repo, old, new)
        if a.chains:
            hits += leaks(added_lines(repo, old, new), secrets_of(a.chains, [b for b in a.bases.split(",") if b]))
        else:
            print("--chains が無いので、漏れの検めを飛ばします")
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    print(f"当たり {len(hits)} 件")
    return 1 if hits else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.surface", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    for name in ("--repo", "--old", "--new"):
        c.add_argument(name, required=True)
    c.add_argument("--chains")
    c.add_argument("--bases")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return check(a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
