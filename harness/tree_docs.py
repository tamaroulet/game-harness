"""ノードの説明（docs/tree/<ノードの id>.md。W-149）。

台帳（docs/progress.yaml）は木の形と状態だけを持ち、ノードの目的・判断を求める点・人の承認・使った量は、
1 ノード 1 ファイルの説明に置く。進捗ツリーは、ここから承認の有無と説明の古さを読んで印を付ける。

ファイルの形:

    # <ノードの id> <題>

    更新: YYYY-MM-DD

    ## 目的 / ## 中身 / ## 前提 / ## 判断を求める点 / ## 承認 / ## 使った量 / ## 見送り

- 承認：表（| 日付 | 決めた者 | 決めたこと |）で 1 行 1 件。無ければ本文は「なし」
- 使った量：表（| 日付 | トークン | 時間 | 出典 |）でセクターが終わるたびに 1 行。無ければ「なし」
- 更新：説明を書き直した日。ノードの下のタスクの完了（台帳の completed_at）より前なら「説明が古い」
"""
import re
from pathlib import Path

DIR = "docs/tree"
HEADINGS = ("目的", "中身", "前提", "判断を求める点", "承認", "使った量", "見送り")
TABLES = {"承認": ("日付", "決めた者", "決めたこと"), "使った量": ("日付", "トークン", "時間", "出典")}
NONE = "なし"
UPDATED = re.compile(r"^更新: (\d{4}-\d{2}-\d{2})$", re.M)
SECTION = re.compile(r"^## (.+)$", re.M)


def path(root, node_id):
    return Path(root) / DIR / f"{node_id}.md"


def read_dir(root):
    """{ノードの id: 本文}。置き場が無ければ空。"""
    d = Path(root) / DIR
    if not d.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8").replace("\r\n", "\n") for p in sorted(d.glob("*.md"))}


def read_git(git, ref):
    """{ノードの id: 本文}（ref の版）。git は引数の一覧を受けて出力の文字列か None を返す。読めなければ None。"""
    names = git(["ls-tree", "-r", "--name-only", ref, DIR])
    if names is None:
        return None
    docs = {}
    for name in (n.strip() for n in names.splitlines()):
        if name.endswith(".md"):
            text = git(["show", f"{ref}:{name}"])
            if text is None:
                return None
            docs[Path(name).stem] = text.replace("\r\n", "\n")
    return docs


def sections(text):
    """{見出し: 本文}（## の見出しごと）。"""
    parts = SECTION.split(text)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def rows(body):
    """表の本文の行（見出しと区切りを除く）。各行はセルの一覧。本文が「なし」なら空。"""
    if body.strip() == NONE:
        return []
    out = [[c.strip() for c in line.strip().strip("|").split("|")]
           for line in body.splitlines() if line.strip().startswith("|")]
    return [r for r in out[2:]]


def problems(node_id, text):
    """形の問題の一覧（空なら合格）。"""
    found = []
    s = sections(text)
    heads = [h.strip() for h in SECTION.findall(text)]
    if heads != list(HEADINGS):
        found.append(f"{node_id}: 見出しは {'・'.join(HEADINGS)} をこの順に 1 つずつです（今は {'・'.join(heads) or 'なし'}）")
    if not text.startswith(f"# {node_id} "):
        found.append(f"{node_id}: 1 行目は「# {node_id} <題>」です")
    if not UPDATED.search(text):
        found.append(f"{node_id}: 「更新: YYYY-MM-DD」の行がありません")
    for h, cols in TABLES.items():
        body = (s.get(h) or "").strip()
        if h not in s or body == NONE:
            continue
        lines = body.splitlines()
        head = [c.strip() for c in lines[0].strip().strip("|").split("|")] if lines else []
        if tuple(head) != cols or not all(l.strip().startswith("|") for l in lines) or not rows(body):
            found.append(f"{node_id}: {h} は欄が {'・'.join(cols)} の表で 1 行以上か、「{NONE}」です")
            continue
        for r in rows(body):
            if len(r) != len(cols) or not all(r):
                found.append(f"{node_id}: {h} の行 {'|'.join(r)} は {len(cols)} つの欄をすべて埋めます")
    return found


def approved(text):
    return bool(rows(sections(text).get("承認", NONE)))


def updated(text):
    m = UPDATED.search(text)
    return m.group(1) if m else None


def stale(text, completions):
    """説明の更新の日が、ノードの下の最後の完了（completed_at、ISO の日時）の日より前か。完了の時刻が無ければ古くない。"""
    last = max((c[:10] for c in completions if c), default=None)
    day = updated(text)
    return bool(last) and (day is None or day < last)


def marks(text, completions):
    """進捗ツリーのノードの行に足す印。説明が無ければ「説明なし」。"""
    if text is None:
        return "〔説明なし〕"
    out = ["承認あり" if approved(text) else "承認なし"]
    if stale(text, completions):
        out.append("説明が古い")
    return f"〔{'・'.join(out)}〕"


def skeleton(node_id, title, day, purpose=None, body=None):
    """見出しだけの雛形（中身は W-153 で総監督が書く）。"""
    todo = "（W-153 で書く）"
    parts = [f"# {node_id} {title}", "", f"更新: {day}", ""]
    for h in HEADINGS:
        text = {"目的": purpose or todo, "中身": body or todo}.get(h, NONE if h in TABLES or h == "見送り" else todo)
        parts += [f"## {h}", "", text, ""]
    return "\n".join(parts)
