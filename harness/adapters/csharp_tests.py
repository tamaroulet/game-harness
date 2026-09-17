"""受入テストの一覧（テスト名・期待値・メッセージ）を C# から機械的に抜き出す。

    python harness/test_summary.py path/to/XxxTests.cs [...]

人間の承認は、実装コードではなくこの一覧に対して行う（10 秒で読める量）。

**なぜ LLM に要約させないか**: 要約役を挟むと、要約が誤っていても人間には見えない。
新しい信用点を増やすだけになる。ここは正規表現と括弧の対応だけで決定論的に作る。

抜き出すもの:
  [Test] / [TestCase(...)] / [TestCaseSource(...)] が付いたメソッド名と TestCase の引数
  そのメソッド本体にある Assert.* の文（複数行にまたがってもよい）
  Assert が 1 つも無いメソッドには「アサーション無し」と明示する（見落とさせない）
"""
import re
import sys
from pathlib import Path

TEST_ATTR = re.compile(r"^\s*(Test|TestCase|TestCaseSource|Theory)\b")
METHOD = re.compile(r"\s*(?:(?:public|private|internal|protected|static|async)\s+)*"
                    r"(?:void|Task)\s+(\w+)\s*\(")
STRING_LITERAL = re.compile(r'(?:\$@|@\$|\$|@)?"(?:[^"\\]|\\.|"")*"', re.S)


def strip_comments(src):
    """コメントを同じ長さの空白に置き換える（文字列リテラルは残す）。位置を保つため。"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
        elif c == '"' or (c == "@" and nxt == '"') or (c == "$" and nxt == '"'):
            j = skip_string(src, i)
            out.append(src[i:j])
            i = j
        elif c == "'":
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def skip_string(src, i):
    """src[i] から始まる文字列リテラルの直後の位置を返す。"""
    verbatim = src[i] == "@"
    if src[i] in "@$":
        i += 1
    i += 1  # 開きの "
    n = len(src)
    while i < n:
        if verbatim:
            if src[i] == '"':
                if i + 1 < n and src[i + 1] == '"':
                    i += 2
                    continue
                return i + 1
        else:
            if src[i] == "\\":
                i += 2
                continue
            if src[i] == '"':
                return i + 1
        i += 1
    return n


def scan_until(src, i, closers):
    """括弧の深さ 0 で closers のどれかに当たる位置を返す。文字列の中は見ない。"""
    depth = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"' or (c in "@$" and i + 1 < n and src[i + 1] == '"'):
            i = skip_string(src, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0 and c in closers:
                return i
            depth -= 1
        elif depth == 0 and c in closers:
            return i
        i += 1
    return n


def split_args(s):
    """トップレベルのカンマで分ける。"""
    args, depth, start, i = [], 0, 0, 0
    while i < len(s):
        c = s[i]
        if c == '"' or (c in "@$" and i + 1 < len(s) and s[i + 1] == '"'):
            i = skip_string(s, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(s[start:i].strip())
            start = i + 1
        i += 1
    tail = s[start:].strip()
    if tail:
        args.append(tail)
    return args


def one_line(s, limit=80):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def parse_assert(stmt):
    """'Assert.AreEqual(a, b, "msg")' → {kind, args, message}"""
    m = re.match(r"Assert\.(\w+)(<[^>(]*>)?\s*\(", stmt)
    if not m:
        return None
    body = stmt[m.end():]
    close = scan_until(body, 0, ")")
    args = split_args(body[:close])
    message = None
    if len(args) >= 2 and STRING_LITERAL.fullmatch(args[-1]):
        lit = args.pop()
        message = lit[1:-1] if lit.startswith('"') else lit
    return {"kind": m.group(1) + (m.group(2) or ""), "args": args, "message": message}


def summarize_source(src):
    """[{name, cases:[str], asserts:[{kind,args,message}]}]"""
    code = strip_comments(src)
    tests = []
    i = 0
    n = len(code)
    while i < n:
        j = code.find("[", i)
        if j < 0:
            break
        close = scan_until(code, j + 1, "]")
        inner = code[j + 1:close]
        attrs = split_args(inner)
        if not any(TEST_ATTR.match(a) for a in attrs):
            i = j + 1
            continue

        # 連続する属性をまとめて読む
        cases = []
        is_test = False
        k = j
        while True:
            close = scan_until(code, k + 1, "]")
            for a in split_args(code[k + 1:close]):
                am = TEST_ATTR.match(a)
                if am:
                    is_test = True
                    if am.group(1) == "TestCase":
                        p = a.find("(")
                        if p >= 0:
                            cases.append(one_line(a[p + 1:a.rfind(")")]))
            k = close + 1
            nxt = re.match(r"\s*\[", code[k:])
            if not nxt:
                break
            k += nxt.end() - 1

        mm = METHOD.match(code, k)
        if not (is_test and mm):
            i = k
            continue
        name = mm.group(1)
        brace = code.find("{", mm.end())
        arrow = code.find("=>", mm.end())
        if brace < 0 or (0 <= arrow < brace and code[mm.end():arrow].count(")") >= 1
                         and ";" not in code[mm.end():arrow]):
            end = scan_until(code, arrow, ";") if arrow >= 0 else n
            body = code[arrow + 2:end]
        else:
            end = scan_until(code, brace + 1, "}")
            body = code[brace + 1:end]

        asserts = []
        for am in re.finditer(r"\bAssert\.\w+", body):
            stop = scan_until(body, am.start(), ";")
            parsed = parse_assert(body[am.start():stop])
            if parsed:
                asserts.append(parsed)
        tests.append({"name": name, "cases": cases, "asserts": asserts})
        i = end + 1
    return tests


def format_assert(a):
    kind, args, msg = a["kind"], a["args"], a["message"]
    if kind in ("AreEqual", "AreNotEqual", "AreSame") and len(args) >= 2:
        text = f"{kind}: 期待 `{one_line(args[0], 40)}` / 実測 `{one_line(args[1], 40)}`"
        if len(args) >= 3:
            text += f"（許容 `{one_line(args[2], 20)}`）"
    elif kind == "That" and len(args) >= 2:
        text = f"That: `{one_line(args[0], 40)}` は `{one_line(args[1], 50)}`"
    else:
        text = f"{kind}(`{one_line(', '.join(args), 70)}`)"
    if msg:
        text += f" —「{one_line(msg, 60)}」"
    return text


def to_markdown(path_label, tests):
    lines = [f"#### {path_label}（{len(tests)} 件）", ""]
    for t in tests:
        lines.append(f"- `{t['name']}`")
        for c in t["cases"]:
            lines.append(f"  - TestCase({c})")
        if not t["asserts"]:
            lines.append("  - **アサーション無し**（何も検査していない可能性）")
        for a in t["asserts"]:
            lines.append("  - " + format_assert(a))
    return "\n".join(lines)


def summarize_files(paths, root=None):
    blocks, total = [], 0
    for p in paths:
        full = Path(root) / p if root else Path(p)
        tests = summarize_source(full.read_text(encoding="utf-8-sig"))
        total += len(tests)
        blocks.append(to_markdown(Path(p).name, tests))
    return total, "\n\n".join(blocks)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    total, md = summarize_files(sys.argv[1:])
    print(md)
    print(f"\n合計 {total} 件")
