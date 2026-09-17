"""網羅検査器。GDD と構造化仕様（spec.md・questions.md）を決定論的に突き合わせる（LLM を使わない）。

    python harness/gdd_check.py --project falling-blocks --gdd docs/gdd/source.md \\
        --spec docs/spec/spec.md --questions docs/spec/questions.md \\
        [--previous-spec <main の spec.md>] [--summary <要約の JSON>]

終了コード: 0 = 合格（spec に不備なし。マージできるかは要約の mergeable）
            1 = 不合格（spec の不備。構造化役に理由を渡して作り直させる）
            2 = 環境異常（ファイルが無い・設定が壊れている）

規則の出所は docs/design/spec_pipeline.md §4〜§6。

**合否と mergeable を分ける理由**: 「仮」の値・質問・錨の欠落は、spec の不備ではなく GDD の不足を表す。
構造化役に作り直させても直らない（直すのは GDD を書く人間）。これらは合格のまま mergeable を偽にし、
スケジューラが ms4:questions を付けて止める（§7）。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import project

SECTIONS = [
    ("## 1. ループと終了条件", "LP", ["ID", "内容", "根拠"]),
    ("## 2. 状態遷移", "ST", ["ID", "状態", "契機", "遷移先", "根拠"]),
    ("## 3. 規則と計算式", "RL", ["ID", "規則・式", "境界値", "参照パラメーター", "根拠"]),
    ("## 4. 公開インターフェース", "IF", ["ID", "公開する状態・操作", "型・範囲", "根拠"]),
    ("## 5. 外部パラメーター", "PR", ["ID", "名前", "値", "区分", "根拠"]),
    ("## 6. 人間確認・演出", "HC", ["ID", "内容", "根拠"]),
]
QUESTION_COLUMNS = ["ID", "質問", "根拠", "推測で決めない理由"]
NONE_ROW = "（該当なし）"
SPEC_META = [re.compile(r"<!-- project: ([a-z0-9][a-z0-9-]*) -->"),
             re.compile(r"<!-- gdd-version: ([1-9][0-9]*) -->"),
             re.compile(r"<!-- gdd-sha256: ([0-9a-f]{64}) -->")]
GDD_META = [re.compile(r"<!-- project: ([a-z0-9][a-z0-9-]*) -->"),
            re.compile(r"<!-- version: ([1-9][0-9]*) -->")]
REF_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")
ID_TOKEN_RE = re.compile(r"\b(LP|ST|RL|IF|PR|HC)-(\d{2,})\b|\bQ-(\d{2,})\b")
COORD_RE = re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")
PIVOT_RE = re.compile(r"^\(\s*(-?\d+(?:\.5)?)\s*,\s*(-?\d+(?:\.5)?)\s*\)$")


class ConfigError(Exception):
    pass


# ============================================================ 読み込み

def load_config():
    cfg = project.config("gdd_check")
    try:
        rules = [(r["name"], re.compile(r["regex"])) for r in cfg["exclude_rules"]]
        re.compile(cfg["hc_forbidden_regex"])
        re.compile(cfg["anchors"]["if_forbidden_time_regex"])
        re.compile(cfg["anchors"]["forbidden_rng_regex"])
        re.compile(cfg["precheck"]["coordinate_regex"])
        int(cfg["precheck"]["min_coordinate_pairs"])
        list(cfg["precheck"]["words"])
        for rx in cfg["candidate_regexes"]:
            re.compile(rx)
    except (KeyError, TypeError, re.error) as e:
        raise ConfigError(f"config/gdd_check.json が不正です: {e}")
    if not any(name == "code_fence" for name, _ in rules):
        raise ConfigError("config/gdd_check.json に code_fence の規則がありません（フェンスの扱いに必要）")
    return cfg, rules


def load_terms(project_id):
    """projects/<id>/spec_terms.json の禁止語。ファイルが無いゲームは禁止語なし。"""
    path = project.ROOT / "projects" / project_id / "spec_terms.json"
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        terms = [(g["feature"], t) for g in d["forbidden_terms"] for t in g["terms"]]
    except (ValueError, KeyError, TypeError) as e:
        raise ConfigError(f"{path} が不正です: {e}")
    if any(not isinstance(t, str) or not t.strip() for _, t in terms):
        raise ConfigError(f"{path} に空の語があります")
    return terms


# ============================================================ GDD

def gdd_lines(text):
    """(行の一覧, 対象行の番号の集合, 規則ごとに外した行数)。行番号は 1 始まり。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    cfg, rules = CFG
    fence = next(rx for name, rx in rules if name == "code_fence")
    excluded = {name: 0 for name, _ in rules}
    targets, in_fence = set(), False
    for n, line in enumerate(lines, 1):
        if fence.match(line):
            excluded["code_fence"] += 1
            in_fence = not in_fence
            continue
        if in_fence:
            targets.add(n)   # フェンスの中身は外さない
            continue
        hit = next((name for name, rx in rules if name != "code_fence" and rx.match(line)), None)
        if hit:
            excluded[hit] += 1
        else:
            targets.add(n)
    return lines, targets, excluded


def headings_of(lines, n):
    """行 n が属する見出しの連なり（例: ['# 仕様', '## 3. 操作']）。"""
    chain = []
    for line in lines[:n]:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            chain = [h for h in chain if len(h.split(" ", 1)[0]) < level] + [line.strip()]
    return chain


def precheck(gdd_text):
    """LLM を呼ぶ前の錨の事前検査（B-2d）。GDD の本文だけを見る。足りないものの一覧（空なら構造化に進む）。

    構造化役に作らせても、錨が GDD に無ければ必ず仮・質問・錨の欠落になり、マージできない（§5・§7）。
    そうと分かっている GDD で LLM を呼ばない（クレジットを使わず、すぐ人間に返す）。
    ここで見るのは「語があるか」「座標の組が足りているか」だけで、構造化後の検査（check）の代わりにはならない。
    """
    cfg, _ = CFG
    pc = cfg["precheck"]
    missing = [f"「{w}」の記述が GDD にありません" for w in pc["words"] if w not in gdd_text]
    pairs = len(re.findall(pc["coordinate_regex"], gdd_text))
    if pairs < pc["min_coordinate_pairs"]:
        missing.append(f"形状の座標 (x, y) が {pairs} 組しかありません（7 種 × 4 方向 × 4 ブロック = "
                       f"{pc['min_coordinate_pairs']} 組が要ります）")
    return missing


# ============================================================ 表

def split_row(line):
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")) or len(s) < 2:
        return None
    cells = re.split(r"(?<!\\)\|", s[1:-1])
    return [c.strip().replace("\\|", "|") for c in cells]


def parse_table(block, columns, where, problems):
    """block: 見出しの次から次の見出しまでの行 [(行番号, 本文)]。行の一覧 [{列: 値, "_line": n}]"""
    body = [(n, l) for n, l in block if l.strip()]
    if len(body) < 3:
        problems.append(f"{where}: 表（見出し行・区切り行・1 行以上）がありません")
        return []
    (hn, header), (sn, sep), rows = body[0], body[1], body[2:]
    if split_row(header) != columns:
        problems.append(f"{where}（{hn} 行目）: 表の列が {columns} ではありません: {split_row(header)}")
        return []
    sep_cells = split_row(sep)
    if not sep_cells or len(sep_cells) != len(columns) or not all(re.fullmatch(r":?-+:?", c) for c in sep_cells):
        problems.append(f"{where}（{sn} 行目）: 表の区切り行が不正です")
        return []
    out = []
    for n, line in rows:
        cells = split_row(line)
        if cells is None:
            problems.append(f"{where}（{n} 行目）: 表の外に本文があります: {line.strip()[:60]}")
            continue
        if len(cells) != len(columns):
            problems.append(f"{where}（{n} 行目）: 列の数が {len(columns)} ではありません（{len(cells)}）")
            continue
        out.append(dict(zip(columns, cells), _line=n))
    if any(r["ID"] == NONE_ROW for r in out) and len(out) != 1:
        problems.append(f"{where}: {NONE_ROW} の行は、ほかの行と一緒に置けません")
    return out


def parse_doc(text, meta_res, sections, name, problems):
    """(メタデータの値, {節の見出し: 行の一覧})。sections: [(見出し, 列)]"""
    lines = text.split("\n")
    meta = []
    for i, rx in enumerate(meta_res):
        m = rx.fullmatch(lines[i]) if i < len(lines) else None
        if not m:
            problems.append(f"{name}: {i + 1} 行目がメタデータ（{rx.pattern}）ではありません")
            return None, {}
        meta.append(m.group(1))
    heads = [(n, l.strip()) for n, l in enumerate(lines, 1) if l.startswith("## ")]
    expected = [h for h, _ in sections]
    if [h for _, h in heads] != expected:
        problems.append(f"{name}: 節の見出しと順序が {expected} ではありません: {[h for _, h in heads]}")
        return meta, {}
    first = heads[0][0] if heads else len(lines) + 1
    for n, l in enumerate(lines[len(meta_res):first - 1], len(meta_res) + 1):
        if l.strip() and not l.startswith("# "):
            problems.append(f"{name}（{n} 行目）: 最初の節より前に本文があります: {l.strip()[:60]}")
    tables = {}
    bounds = [n for n, _ in heads] + [len(lines) + 1]
    for (n, head), (_, columns), end in zip(heads, sections, bounds[1:]):
        block = list(enumerate(lines[n:end - 1], n + 1))
        tables[head] = parse_table(block, columns, f"{name} {head}", problems)
    return meta, tables


def parse_refs(value, nlines, targets, where, problems):
    """根拠の列 → 行番号の集合。形・範囲・対象行を含むかを検査する。"""
    cited = set()
    if not value or value == "-":
        problems.append(f"{where}: 根拠がありません")
        return cited
    for part in value.split(", "):
        m = REF_RE.fullmatch(part)
        if not m:
            problems.append(f"{where}: 根拠の形が L<行> / L<行>-L<行> ではありません: 「{part}」")
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if not (1 <= a <= b <= nlines):
            problems.append(f"{where}: 根拠 {part} が GDD の範囲（L1-L{nlines}）の外、または逆順です")
            continue
        span = set(range(a, b + 1))
        if not span & targets:
            problems.append(f"{where}: 根拠 {part} は分母から外した行（見出し・空行など）だけを指しています")
        cited |= span
    return cited


# ============================================================ 語

def term_regex(term):
    t = re.escape(term)
    if re.fullmatch(r"[\x00-\x7f]+", term):
        return re.compile(rf"(?<![A-Za-z0-9]){t}(?![A-Za-z0-9])", re.I)
    return re.compile(t, re.I)


# ============================================================ 検査

def check(gdd_text, spec_text, questions_text, terms, previous_spec_text=None):
    """{"problems": [...], "summary": {...}}。problems が空なら合格。"""
    cfg, _ = CFG
    problems = []
    lines, targets, excluded = gdd_lines(gdd_text)
    nlines = len(lines)
    gdd_meta = [rx.fullmatch(lines[i]) if i < nlines else None for i, rx in enumerate(GDD_META)]
    if not all(gdd_meta):
        problems.append("GDD: 先頭 2 行がメタデータ（project / version）ではありません")
    gdd_sha = hashlib.sha256(gdd_text.encode("utf-8")).hexdigest()

    spec_meta, spec = parse_doc(spec_text, SPEC_META, [(h, c) for h, _, c in SECTIONS], "spec.md", problems)
    q_meta, qdoc = parse_doc(questions_text, SPEC_META, [("## 質問", QUESTION_COLUMNS)], "questions.md", problems)
    for name, meta in (("spec.md", spec_meta), ("questions.md", q_meta)):
        if meta and all(gdd_meta):
            if meta[0] != gdd_meta[0].group(1) or meta[1] != gdd_meta[1].group(1):
                problems.append(f"{name}: メタデータの project / gdd-version が GDD と違います")
            if meta[2] != gdd_sha:
                problems.append(f"{name}: gdd-sha256 が GDD（{gdd_sha[:12]}…）と一致しません")

    # 行を 1 本にまとめる（ID・種別・根拠の行番号・本文）
    rows, ids = [], {}
    for head, prefix, columns in SECTIONS:
        for r in spec.get(head, []):
            rows.append(("spec", prefix, head, r))
    for r in qdoc.get("## 質問", []):
        rows.append(("questions", "Q", "## 質問", r))
    cited_all = set()
    for doc, prefix, head, r in rows:
        where = f"{'spec.md' if doc == 'spec' else 'questions.md'} {r['ID']}（{r['_line']} 行目）"
        if r["ID"] == NONE_ROW:
            r["_cited"] = set()
            continue
        if not re.fullmatch(rf"{prefix}-\d{{2,}}", r["ID"]):
            problems.append(f"{where}: ID が {prefix}-nn の形ではありません")
        if r["ID"] in ids:
            problems.append(f"{where}: ID が重複しています（{ids[r['ID']]} 行目）")
        ids[r["ID"]] = r["_line"]
        r["_cited"] = parse_refs(r["根拠"], nlines, targets, where, problems)
        cited_all |= r["_cited"]

    real_rows = [x for x in rows if x[3]["ID"] != NONE_ROW]

    # 網羅（§6.3）
    missing = sorted(targets - cited_all)
    for n in missing:
        problems.append(f"網羅: GDD の L{n} がどの行の根拠にもありません: {lines[n - 1].strip()[:80]}")

    # 参照（§6.5）
    for doc, prefix, head, r in real_rows:
        for col, value in r.items():
            if col in ("ID", "根拠", "_line", "_cited"):
                continue
            for m in ID_TOKEN_RE.finditer(value):
                if m.group(0) not in ids:
                    problems.append(f"{r['ID']}: 列「{col}」が実在しない ID {m.group(0)} を参照しています")
        if prefix == "RL":
            ref = r["参照パラメーター"]
            if ref != "-" and not re.fullmatch(r"PR-\d{2,}(, PR-\d{2,})*", ref):
                problems.append(f"{r['ID']}: 参照パラメーターは PR-nn の並びか - です: 「{ref}」")
        if prefix == "PR" and r["区分"] not in ("確定", "仮"):
            problems.append(f"{r['ID']}: 区分は 確定 か 仮 です: 「{r['区分']}」")

    # 禁止語（§6.6）
    compiled = [(feature, term, term_regex(term)) for feature, term in terms]
    for doc, prefix, head, r in real_rows:
        text = " ".join(v for k, v in r.items() if k not in ("根拠", "_line", "_cited"))
        cited_text = "\n".join(lines[n - 1] for n in sorted(r["_cited"]))
        for feature, term, rx in compiled:
            if rx.search(text) and not rx.search(cited_text):
                problems.append(f"{r['ID']}: 実験用の機能（{feature}）の語「{term}」があります。"
                                "GDD の根拠行に無い語で機能を補ってはいけません")

    # HC の近似（§6.8）
    hc_rx = re.compile(cfg["hc_forbidden_regex"])
    for doc, prefix, head, r in real_rows:
        if prefix == "HC":
            m = hc_rx.search(r["内容"])
            if m:
                problems.append(f"{r['ID']}: 人間確認にロジックらしい記述（「{m.group(0)}」）があります。"
                                "論理・得点・判定は RL / IF に置き、テストで判定します")

    # 錨（§5）: 欠落は GDD の不足（mergeable を偽に）、書き方の誤りは NG
    a = cfg["anchors"]
    by = lambda p: [x[3] for x in real_rows if x[1] == p]
    joined = lambda rs: [" ".join(v for k, v in r.items() if not k.startswith("_")) for r in rs]
    anchors_missing = []
    if not any(a["tick_word"] in t for t in joined(by("RL"))) or not any(a["tick_word"] in t for t in joined(by("IF"))):
        anchors_missing.append("A1: RL と IF に「ティック」の行が要る（時間は整数ティック）")
    for r in by("IF"):
        m = re.search(a["if_forbidden_time_regex"], r["公開する状態・操作"] + " " + r["型・範囲"])
        if m:
            problems.append(f"{r['ID']}: 公開インターフェースに秒・浮動小数の時間（「{m.group(0)}」）があります。"
                            "時間の注入は整数ティックだけです（§5 A1）")
    shapes = {}
    for r in by("PR"):
        m = re.fullmatch(r"形状 (\S+) (向き (\S+)|回転原点)", r["名前"])
        if not m:
            continue
        kind, rot = m.group(1), m.group(3)
        if kind not in a["shapes"] or (rot is not None and rot not in a["rotations"]):
            problems.append(f"{r['ID']}: 形状の名前が不正です: 「{r['名前']}」")
            continue
        if rot is None:
            if not PIVOT_RE.fullmatch(r["値"]):
                problems.append(f"{r['ID']}: 回転原点は (x, y) の 1 組です（x, y は整数か .5）: 「{r['値']}」")
            shapes.setdefault(kind, set()).add("pivot")
            continue
        coords = COORD_RE.findall(r["値"])
        rest = COORD_RE.sub("", r["値"]).replace(" ", "")
        if len(coords) != 4 or rest or len(set(coords)) != 4:
            problems.append(f"{r['ID']}: 形状の座標は異なる 4 組の (x, y) です: 「{r['値']}」")
        shapes.setdefault(kind, set()).add(rot)
    lacking = [k for k in a["shapes"] if shapes.get(k, set()) != set(a["rotations"]) | {"pivot"}]
    if lacking:
        anchors_missing.append("A2: 形状ごとの座標（向き " + "・".join(a["rotations"]) + "）と回転原点が揃っていない: "
                               + ", ".join(lacking))
    if not any(a["seed_word"] in t for t in joined(by("IF"))) or not any(a["rng_word"] in t for t in joined(by("RL"))):
        anchors_missing.append("A3: IF にシードの注入、RL に XorShift32 の行が要る")
    for doc, prefix, head, r in real_rows:
        m = re.search(a["forbidden_rng_regex"], " ".join(v for k, v in r.items() if not k.startswith("_")))
        if m:
            problems.append(f"{r['ID']}: {m.group(0)} を使う記述があります（乱数は XorShift32 だけ。§5 A3）")
    if not any(r["規則・式"].startswith(a["invariant_prefix"]) for r in by("RL")):
        anchors_missing.append(f"A4: RL に「{a['invariant_prefix']}」で始まる不変条件の行が要る")

    # ID の継続性（§6.9）
    id_changes = None
    if previous_spec_text is not None:
        prev_problems = []
        _, prev = parse_doc(previous_spec_text, SPEC_META, [(h, c) for h, _, c in SECTIONS], "前の版の spec.md",
                            prev_problems)
        if prev_problems:
            raise ConfigError("前の版の spec.md を読めません: " + "; ".join(prev_problems[:3]))
        body = lambda r: tuple((k, v) for k, v in r.items() if k not in ("根拠", "_line", "_cited"))
        prev_rows = {r["ID"]: body(r) for rs in prev.values() for r in rs if r["ID"] != NONE_ROW}
        cur_rows = {r["ID"]: body(r) for _, _, _, r in real_rows if not r["ID"].startswith("Q-")}
        id_changes = {"kept": sorted(i for i in cur_rows if i in prev_rows and cur_rows[i] == prev_rows[i]),
                      "changed": sorted(i for i in cur_rows if i in prev_rows and cur_rows[i] != prev_rows[i]),
                      "removed": sorted(i for i in prev_rows if i not in cur_rows),
                      "added": sorted(i for i in cur_rows if i not in prev_rows)}
        top = {}
        for i in prev_rows:
            p, num = i.split("-")
            top[p] = max(top.get(p, 0), int(num))
        for i in id_changes["added"]:
            p, num = i.split("-")
            if int(num) <= top.get(p, 0):
                problems.append(f"{i}: 前の版の最大番号（{p}-{top[p]:02d}）以下の番号を新しい行に使っています"
                                "（消えた ID の再利用になりうる。新しい行は最大番号より大きくする）")

    # 要約（§6.7 と承認コメント）
    provisional = [{"id": r["ID"], "name": r["名前"], "value": r["値"], "refs": r["根拠"],
                    "headings": headings_of(lines, min(r["_cited"])) if r["_cited"] else []}
                   for r in by("PR") if r["区分"] == "仮"]
    questions = [{"id": r["ID"], "question": r["質問"], "refs": r["根拠"], "why": r["推測で決めない理由"],
                  "headings": headings_of(lines, min(r["_cited"])) if r["_cited"] else []}
                 for r in by("Q")]
    human = [{"id": r["ID"], "content": r["内容"], "refs": r["根拠"]} for r in by("HC")]
    gdd_lower = gdd_text.lower()
    candidates = set()
    for doc, prefix, head, r in real_rows:
        text = " ".join(v for k, v in r.items() if k not in ("ID", "根拠", "_line", "_cited"))
        text = ID_TOKEN_RE.sub(" ", text)
        for rx in cfg["candidate_regexes"]:
            candidates |= {w for w in re.findall(rx, text) if w.lower() not in gdd_lower}
    summary = {
        "gdd": {"project": gdd_meta[0].group(1) if gdd_meta[0] else None,
                "version": int(gdd_meta[1].group(1)) if gdd_meta[1] else None,
                "sha256": gdd_sha, "lines": nlines, "target_lines": len(targets), "excluded_by_rule": excluded},
        "counts": {p: len(by(p)) for p in ["LP", "ST", "RL", "IF", "PR", "HC", "Q"]},
        "provisional": provisional,
        "questions": questions,
        "human_checks": human,
        "anchors_missing": anchors_missing,
        "term_candidates": sorted(candidates),
        "id_changes": id_changes,
        "passed": not problems,
        "mergeable": not problems and not provisional and not questions and not anchors_missing,
    }
    return {"problems": problems, "summary": summary}


CFG = None


def main(argv=None):
    global CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--gdd", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--previous-spec")
    ap.add_argument("--summary", help="要約（JSON）の書き出し先")
    a = ap.parse_args(argv)
    try:
        project.load(a.project)
        CFG = load_config()
        terms = load_terms(a.project)
        read = lambda p: Path(p).read_bytes().decode("utf-8")
        texts = [read(p) for p in (a.gdd, a.spec, a.questions)]
        prev = read(a.previous_spec) if a.previous_spec else None
        result = check(*texts, terms, prev)
    except (ConfigError, project.ProjectError, OSError, UnicodeDecodeError) as e:
        print(f"ABORT: {e}")
        return 2
    s = result["summary"]
    if a.summary:
        Path(a.summary).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"GDD: {s['gdd']['project']} v{s['gdd']['version']} / {s['gdd']['lines']} 行（対象 {s['gdd']['target_lines']}）")
    print("件数: " + ", ".join(f"{k} {v}" for k, v in s["counts"].items()))
    for p in result["problems"]:
        print("  NG " + p)
    print(f"仮 {len(s['provisional'])} / 質問 {len(s['questions'])} / 錨の欠落 {len(s['anchors_missing'])}")
    print("RESULT: " + ("合格" if s["passed"] else "不合格") + (" / マージ可" if s["mergeable"] else " / マージ不可"))
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
