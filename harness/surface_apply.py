"""提案の「## 差分」を、最適化の面の差分のコミットにする（v3 §7-7 の 2、L7）。モデルを呼ばず、作業ツリーに触らない。

    python -m harness.surface_apply --repo <clone> --rev <rev> --proposal <提案の .md> --branch <枝>

提案役は読むだけの道具で動くので、ファイルを変えられない。そこで、提案の中に直しの本文を書かせて、機械がコミットにする：
- python のブロック：直した関数の全文（harness/hline_prompt.py の build_prompt か ledger_section。名前と引数は surface check が見る）。
- json のブロック：変えるつまみの名と新しい値だけ（config/surface.json）。範囲は surface.KNOBS。ほかの鍵と順は保つ。
どちらも無い・読めない・面の関数の外を含むときは誤りの文を返し、コミットを作らない。中身の判じ（面の外・関数の単位・漏れ）は surface check が行う。
"""
import argparse
import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import surface  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

HEADING = "## 差分"
BLOCK = re.compile(r"^```(?P<lang>[A-Za-z]*)[ \t]*\n(?P<body>.*?)^```[ \t]*$", re.M | re.S)
AUTHOR = {"GIT_AUTHOR_NAME": "harness-cycle", "GIT_AUTHOR_EMAIL": "harness-cycle@localhost",
          "GIT_COMMITTER_NAME": "harness-cycle", "GIT_COMMITTER_EMAIL": "harness-cycle@localhost"}


class Change:
    """読み取った直し。functions は {関数の名: 全文}、knobs は {つまみの名: 値}。"""

    def __init__(self, functions=None, knobs=None):
        self.functions, self.knobs = functions or {}, knobs or {}


def section(text):
    """提案の全文の「## 差分」の節の本文（次の見出しまで）。無ければ None。"""
    lines, found, body = text.splitlines(), False, []
    for line in lines:
        if line.rstrip() == HEADING:
            found = True
        elif found and line.startswith("## "):
            break
        elif found:
            body.append(line)
    return "\n".join(body) + "\n" if found else None


def read_functions(source):
    """python のブロックの (全文の辞書, 誤りの列)。頂の文は面の関数の def だけで、同じ名は 1 回だけ。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return {}, [f"python のブロックを読めません（{type(e).__name__}）"]
    errs, got = [], {}
    lines = source.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in surface.FACES:
            errs.append(f"python のブロックに、面の関数（{'・'.join(surface.FACES)}）の外の頂の文があります（{node.lineno} 行）")
        elif node.name in got:
            errs.append(f"python のブロックに {node.name} が 2 回あります")
        else:
            got[node.name] = "\n".join(lines[node.lineno - 1:node.end_lineno])
    return got, errs


def read_knobs(source):
    """json のブロックの (つまみの辞書, 誤りの列)。名と範囲は surface.values_of が照らす。"""
    try:
        values = surface.values_of(source, "json のブロック")
    except BenchError as e:
        return {}, [str(e)]
    return values, [] if values else ["json のブロックにつまみがありません"]


def parse(text):
    """(Change, 誤りの文の列)。text は提案の全文。ブロックは python と json を 1 つずつまで。"""
    body = section(text)
    if body is None:
        return Change(), [f"見出し {HEADING} がありません"]
    blocks = list(BLOCK.finditer(body))
    langs = [m["lang"].lower() for m in blocks]
    errs = [f"{HEADING} に python か json のブロックがありません"] if not blocks else []
    errs += [f"{HEADING} のブロックの言語が python でも json でもありません: {lang or '（無し）'}" for lang in langs if lang not in ("python", "json")]
    errs += [f"{HEADING} に {lang} のブロックが 2 つあります" for lang in ("python", "json") if langs.count(lang) > 1]
    if errs:
        return Change(), errs
    change, errs = Change(), []
    for m in blocks:
        if m["lang"].lower() == "python":
            change.functions, more = read_functions(m["body"])
        else:
            change.knobs, more = read_knobs(m["body"])
        errs += more
    return change, errs


def replace_functions(source, functions):
    """hline_prompt.py の本文の、頂の関数の行の範囲を置き換える。無い関数は BenchError。ほかの行は 1 字も変えない。"""
    nodes = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    if (missing := [name for name in functions if name not in nodes]):
        raise BenchError(f"{surface.PROMPT_FILE} に関数がありません: {', '.join(missing)}")
    lines = source.split("\n")
    for name in sorted(functions, key=lambda x: nodes[x].lineno, reverse=True):
        lines[nodes[name].lineno - 1:nodes[name].end_lineno] = functions[name].split("\n")
    return "\n".join(lines)


def replace_knobs(source, knobs):
    """面の設定の本文の値を替える。ほかの鍵（_comment など）と順は保ち、2 字下げで書き直す。"""
    doc = json.loads(source)
    doc.update(knobs)
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def blob_text(repo, rev, path):
    data = surface.blob(repo, rev, path)
    if data is None:
        raise BenchError(f"{rev} に {path} がありません")
    return data.decode("utf-8")


def new_files(repo, rev, change):
    """{パス: 新しいバイト列}。中身が変わらないものは入れない。"""
    files = {}
    if change.functions:
        old = blob_text(repo, rev, surface.PROMPT_FILE)
        new = replace_functions(old, change.functions)
        if new != old:
            files[surface.PROMPT_FILE] = new.encode("utf-8")
    if change.knobs:
        old = blob_text(repo, rev, surface.SETTING)
        if json.loads(old) != {**json.loads(old), **change.knobs}:
            files[surface.SETTING] = replace_knobs(old, change.knobs).encode("utf-8")
    return files


def commit(repo, rev, files, message):
    """rev の木に files を足した、rev を親とするコミットの SHA（オブジェクトだけ。作業ツリーと枝は変えない）。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, **AUTHOR, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, ["read-tree", rev], env=env)
        for path, data in sorted(files.items()):
            sha = git(repo, ["hash-object", "-w", "--stdin"], data=data).decode().strip()
            git(repo, ["update-index", "--add", "--cacheinfo", f"100644,{sha},{path}"], env=env)
        tree = git(repo, ["write-tree"], env=env).decode().strip()
        return git(repo, ["commit-tree", tree, "-p", rev, "-m", message], env=env).decode().strip()


def apply(repo, rev, text, message):
    """提案の全文をコミットにして SHA を返す。読めない・変わらないときは BenchError（何も作らない）。"""
    change, errs = parse(text)
    if errs:
        raise BenchError("；".join(errs))
    files = new_files(repo, rev, change)
    if not files:
        raise BenchError(f"{HEADING} を当てても何も変わりません")
    return commit(repo, rev, files, message)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.surface_apply", description=__doc__.splitlines()[0])
    for flag in ("--repo", "--rev", "--proposal", "--branch"):
        ap.add_argument(flag, required=True)
    a = ap.parse_args(argv)
    try:
        sha = apply(a.repo, a.rev, Path(a.proposal).read_text(encoding="utf-8"), f"improve(surface): {a.branch} の提案の差分")
        git(a.repo, ["update-ref", f"refs/heads/{a.branch}", sha])
    except BenchError as e:
        print(f"止めた: {e}", file=sys.stderr)
        return 2
    print(sha)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
