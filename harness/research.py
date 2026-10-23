"""外部の調べもの（ADR-004 の K-15、W-123）：問い 1 つを調べ役の CLI に渡し、主張ごとの出典を照合役で確かめて、記録に残す。

    python -m harness.research ask <問い.md> [--backend agy|claude|claude-deep] [--out <記録の置き場>]
    python -m harness.research queue [--backend …]     積んだ問いを名前の順に回す（利用枠が切れたらそこで止まる）
    python -m harness.research index                   記録から索引（知識の台帳）を作る
    python -m harness.research search <語>… [--stale]  索引を引く（--stale は見直しの期限を過ぎた記録）

調べ方の規則は docs/design/research_protocol.md。問いは段（map・compare・refute・verify）と決めたい判断を持つ形で書き、
形が違えば調べ役を呼ぶ前に拒む（利用枠を使わない）。調べ役は段ごとの既定（config の stages）か --backend で選ぶ。

**なぜ要るか**: 調べものを会話に貼ると、文脈の要約で薄れ、出どころも失われ、確かめないまま判断の根拠になる。
ここでは調べ役の出力を形（config/research_schema.json）で受け、出典の抜粋を LLM なしで照合し（harness/research_verify.py）、
主張の ID（R-<日付>-<問いの名前>#C<n>）で引ける記録にする。勧めは書かない。判断は ADR と前提の台帳（docs/design/premises.yaml）の側でする。

調べ役の CLI は config/research.json の backends から選ぶ。モデルは model_flag と正確な ID で固定し、実行ごとに報告と照合する（model_pin）。
終了コード：0 = 記録した、1 = 出力の形が崩れた（記録しない）、2 = 環境の異常、3 = 利用枠が切れたので問いを積んで待つ。
"""
import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_pinned  # noqa: E402
import agy_stream  # noqa: E402
import exitcode  # noqa: E402
import hline_quota  # noqa: E402
import machine  # noqa: E402
import model_pin  # noqa: E402
import proc  # noqa: E402
import research_verify  # noqa: E402
from hline_base import implementer_args  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "research.json"
RECORDED, REJECTED, ABORT, QUEUED = 0, 1, 2, 3
CLAIM_KEYS = ("claim", "source_url", "source_title", "published", "quote", "kind", "confidence", "source_tier", "sector",
              "approach")
KINDS, CONFIDENCE = ("fact", "opinion", "vendor_claim"), ("high", "medium", "low")
TIERS = ("primary", "secondary", "marketing")
SECTORS = ("frontier_labs", "practitioners_at_scale", "academia", "standards_tools", "practitioners")
STAGES = ("map", "compare", "refute", "verify")
WHERE = "調べ役"
# 問いのファイルの節（docs/design/research_protocol.md §4）
STAGE, DECISION, CHANGERS, SECTOR_HEAD, CANDIDATES, BODY = "段", "決めたい判断", "判断を変えうるもの", "分野", "候補", "問い"


class Abort(Exception):
    """環境の異常（CLI の失敗・モデルの照合の失敗・導入先の照合の失敗）。終了コード 2。"""


def load_config(path=CONFIG):
    return machine.read_json(path)


def read_question(path):
    """(問いの名前, 題, 本文)。名前はファイル名から、題は先頭の # 行から取る。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
    return re.sub(r"[^a-z0-9-]+", "-", path.stem.lower()).strip("-") or "question", title, text


def sections(text):
    """問いの「## 名前」の節 {名前: 本文（前後の空白を除く）}。"""
    out, name = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            out[name] = []
        elif name is not None:
            out[name].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def question_problems(sec):
    """問いの形の誤り（docs/design/research_protocol.md §4）。空なら合格。"""
    out = [f"「## {k}」が無いか空" for k in (STAGE, DECISION, CHANGERS, BODY) if not sec.get(k)]
    stage = sec.get(STAGE, "").split()[0] if sec.get(STAGE) else ""
    if sec.get(STAGE) and stage not in STAGES:
        out.append(f"段 {stage!r} が {STAGES} のどれでもない")
    if stage == "map":
        named = sec.get(SECTOR_HEAD, "").split()
        if not named:
            out.append("map には「## 分野」が要る")
        out += [f"分野 {s!r} が {SECTORS} のどれでもない" for s in named if s not in SECTORS]
        if sec.get(CANDIDATES):
            out.append("map には「## 候補」を書かない（自分の案を俯瞰に入れない）")
    if stage in ("compare", "refute") and not sec.get(CANDIDATES):
        out.append(f"{stage} には「## 候補」が要る")
    return out


def build_prompt(cfg, question):
    return (ROOT / cfg["instruction"]).read_text(encoding="utf-8").rstrip() + "\n\n---\n\n" + question


def call(cfg, name, prompt, workdir, run=proc.run, resolve=proc.resolve_cli):
    """調べ役を 1 回呼ぶ。{"code", "out", "text", "models", "usage", "quota"}。quota があれば text は読まない。"""
    b = cfg["backends"][name]
    ttl = b.get("ttl_seconds", cfg["ttl_seconds"])
    try:
        if b["cli"] == "agy":
            agy_pinned.require()
            model_pin.require_implementer(b, f"{WHERE}（{name}）")
            args = agy_stream.args(b, resolve(b["cli"]), prompt, ttl) + ["--json-schema", str(ROOT / cfg["schema"])]
            code, out, err = run(args, workdir, ttl, WHERE, input=agy_stream.stdin_for(b, prompt))
            if quota := hline_quota.message(out, err, agy=True):
                return {"code": code, "out": out, "quota": quota}
            parsed = agy_stream.parse(out, workdir)
            _require_ok(code, err)
            models = [model_pin.check_reported(b["model_name"], parsed["model"], f"{WHERE}（{name}）")]
            return {"code": code, "out": out, "text": parsed["response"], "models": models, "usage": parsed["usage"], "quota": None}
        model_pin.require(b, f"{WHERE}（{name}）")
        code, out, err = run(implementer_args(b, resolve(b["cli"])), workdir, ttl, WHERE, input=prompt)
        if quota := hline_quota.message(out, err):
            return {"code": code, "out": out, "quota": quota}
        _require_ok(code, err)
        used, why = model_pin.claude_models(out)
        models = model_pin.check_claude(b, used, why, f"{WHERE}（{name}）")
        doc = json.loads(out)
        return {"code": code, "out": out, "text": str(doc.get("result") or ""), "models": models, "usage": doc.get("usage"), "quota": None}
    except (model_pin.ModelPinError, agy_pinned.AgyDigestError) as e:
        raise Abort(str(e)) from e


def _require_ok(code, err):
    if code != 0:
        raise Abort(f"調べ役の CLI が終了コード {code}: {' '.join((err or '').split())[:200]}")


def extract_json(text):
    """応答の中の JSON の表（前後の文や ``` の囲みは捨てる）。読めなければ None。"""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        doc = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def shape_problems(doc):
    """config/research_schema.json の形に合わない点の列（空なら合格）。"""
    if not isinstance(doc, dict):
        return ["応答に JSON の表がない"]
    out = [f"未知のキー {k}" for k in doc if k not in ("claims", "queries", "unknowns")]
    claims = doc.get("claims")
    if not isinstance(claims, list):
        out.append("claims が列でない")
        claims = []
    for key in ("queries", "unknowns"):
        if not isinstance(doc.get(key), list) or not all(isinstance(u, str) for u in doc[key]):
            out.append(f"{key} が文字列の列でない")
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict):
            out.append(f"claims[{i}] が表でない")
            continue
        out += [f"claims[{i}] に {k} が無いか文字列でない" for k in CLAIM_KEYS if not isinstance(c.get(k), str)]
        out += [f"claims[{i}] に未知のキー {k}" for k in c if k not in CLAIM_KEYS]
        if c.get("kind") not in KINDS:
            out.append(f"claims[{i}] の kind が {KINDS} のどれでもない")
        if c.get("confidence") not in CONFIDENCE:
            out.append(f"claims[{i}] の confidence が {CONFIDENCE} のどれでもない")
        if c.get("source_tier") not in TIERS:
            out.append(f"claims[{i}] の source_tier が {TIERS} のどれでもない")
        if c.get("sector") not in SECTORS:
            out.append(f"claims[{i}] の sector が {SECTORS} のどれでもない")
        if isinstance(c.get("quote"), str) and len(c["quote"]) > 300:
            out.append(f"claims[{i}] の quote が 300 字を超える")
    return out


def _cell(text):
    return " ".join(str(text or "").split()).replace("|", "／")


def render(rec):
    """記録の Markdown。勧めは書かない（事実と照合の印だけ）。"""
    counts = "・".join(f"{s} {n}" for s, n in rec["counts"].items())
    lines = [f"# {rec['title']}", "", f"- 記録：{rec['id']}", f"- 段：{rec['stage']}", f"- 決めたい判断：{_cell(rec['decision'])}",
             f"- 調べた日時：{rec['asked_at']}", f"- 調べ役：{rec['backend']}（{', '.join(rec['models'])}）", f"- 照合：{counts}",
             "- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い",
             "", "## 主張", "", "| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |",
             "|:--|:--|:--|:--|:--|:--|:--|:--|:--|"]
    lines += [f"| {c['id']} | {_cell(c['claim'])} | {_cell(c['approach'])} | {c['sector']} | {c['source_tier']} | {c['kind']} | "
              f"{_cell(c['published'])} | {c['verify']['status']} | [{_cell(c['source_title'])}]({c['source_url']}) |"
              for c in rec["claims"]]
    lines += ["", "## 抜粋", ""] + [f"- {c['id']}：「{_cell(c['quote'])}」" for c in rec["claims"]]
    lines += ["", "## 確かめられなかったこと", ""] + ([f"- {_cell(u)}" for u in rec["unknowns"]] or ["- なし"])
    lines += ["", "## 検索語", ""] + ([f"- {_cell(q)}" for q in rec["queries"]] or ["- なし"])
    lines += ["", "## 問い", "", rec["question"].rstrip()]
    return "\n".join(lines) + "\n"


def record(slug, title, question, backend, result, doc, verdicts, now, sec=None):
    sec = sec if sec is not None else sections(question)
    claims = [{"id": f"C{i}", **{k: c[k] for k in CLAIM_KEYS}, "verify": v} for i, (c, v) in enumerate(zip(doc["claims"], verdicts), 1)]
    counts = {s: sum(1 for c in claims if c["verify"]["status"] == s) for s in research_verify.STATUSES}
    return {"id": f"R-{now:%Y%m%d}-{slug}", "title": title, "stage": sec.get(STAGE, "").split()[0] if sec.get(STAGE) else "",
            "decision": sec.get(DECISION, ""), "sectors": sec.get(SECTOR_HEAD, "").split(),
            "asked_at": now.isoformat(timespec="seconds"), "backend": backend,
            "models": result["models"], "usage": result.get("usage"), "claims": claims, "queries": doc["queries"],
            "unknowns": doc["unknowns"], "counts": counts, "question": question}


def ask(cfg, question_path, backend=None, out_dir=None, fetcher=None, run=proc.run, resolve=proc.resolve_cli, now=None):
    """問い 1 つを調べて記録する。終了コード（RECORDED・REJECTED・QUEUED）。環境の異常は Abort。"""
    now = now or datetime.datetime.now().astimezone()
    slug, title, question = read_question(question_path)
    sec = sections(question)
    if bad := question_problems(sec):
        print(f"問いの形が規則（docs/design/research_protocol.md §4）と違います（調べ役を呼びません）: {question_path}\n- "
              + "\n- ".join(bad))
        return REJECTED
    name = backend or (cfg.get("stages") or {}).get(sec[STAGE].split()[0]) or cfg["default"]
    work = Path(cfg["work"]) / f"{now:%Y%m%d-%H%M%S}-{slug}"
    work.mkdir(parents=True, exist_ok=True)
    result = call(cfg, name, build_prompt(cfg, question), work, run, resolve)
    (work / "raw.txt").write_text(result["out"] or "", encoding="utf-8")
    if result["quota"]:
        enqueue(cfg, question_path)
        print(f"調べ役（{name}）の利用枠が切れています。問いを積みました: {result['quota']}")
        return QUEUED
    doc = extract_json(result["text"])
    if bad := shape_problems(doc):
        print(f"調べ役の出力の形が崩れています（記録しません。生の出力は {work / 'raw.txt'}）:\n- " + "\n- ".join(bad[:10]))
        return REJECTED
    verdicts = research_verify.verify_all(doc["claims"], fetcher or (lambda u: research_verify.fetch(u, cfg["fetch_ttl_seconds"])), now)
    rec = record(slug, title, question, name, result, doc, verdicts, now, sec)
    dest = Path(out_dir or cfg["records"])
    dest.mkdir(parents=True, exist_ok=True)
    # 記録は docs/research/ に写すので、.gitattributes と同じ LF で書く（write_text は Windows で CRLF にする）
    (dest / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                                            newline="\n")
    (dest / f"{rec['id']}.md").write_text(render(rec), encoding="utf-8", newline="\n")
    print(f"記録しました: {dest / (rec['id'] + '.md')}（{'・'.join(f'{s} {n}' for s, n in rec['counts'].items())}）")
    return RECORDED


def enqueue(cfg, question_path):
    queue = Path(cfg["queue"])
    queue.mkdir(parents=True, exist_ok=True)
    target = queue / Path(question_path).name
    if Path(question_path).resolve() != target.resolve():
        shutil.copyfile(question_path, target)
    return target


def run_queue(cfg, backend=None, out_dir=None, **kw):
    """積んだ問いを名前の順に回す。記録した問いは積みから消す。利用枠が切れたら、そこで止まって QUEUED。"""
    for q in sorted(Path(cfg["queue"]).glob("*.md")) if Path(cfg["queue"]).is_dir() else []:
        rc = ask(cfg, q, backend, out_dir, **kw)
        if rc == QUEUED:
            return QUEUED
        if rc == RECORDED:
            q.unlink()
    return RECORDED


def record_files(cfg):
    """記録の JSON（Git の外の置き場と docs/research/）。同じ ID は Git の外を先に取る。"""
    seen, out = set(), []
    for d in (Path(cfg["records"]), ROOT / cfg.get("public_records", "docs/research")):
        for p in sorted(d.glob("R-*.json")) if d.is_dir() else []:
            if p.stem not in seen:
                seen.add(p.stem)
                out.append(p)
    return out


def domain(url):
    m = re.match(r"^[a-z]+://(?:www\.)?([^/:?#]+)", str(url or "").lower())
    return m.group(1) if m else ""


def build_index(cfg, files=None):
    """記録の主張ごとの索引の行。古い形の記録（格・分野・系統の無いもの）は空で入れる。"""
    rows = []
    for p in files if files is not None else record_files(cfg):
        rec = json.loads(p.read_text(encoding="utf-8"))
        asked = datetime.datetime.fromisoformat(rec["asked_at"])
        due = (asked + datetime.timedelta(days=cfg.get("recheck_days", 30))).date().isoformat()
        for c in rec.get("claims") or []:
            rows.append({"ref": f"{rec['id']}#{c['id']}", "record": rec["id"], "title": rec.get("title", ""),
                         "stage": rec.get("stage", ""), "decision": rec.get("decision", ""), "claim": c.get("claim", ""),
                         "approach": c.get("approach", ""), "sector": c.get("sector", ""), "tier": c.get("source_tier", ""),
                         "kind": c.get("kind", ""), "status": (c.get("verify") or {}).get("status", ""),
                         "url": c.get("source_url", ""), "domain": domain(c.get("source_url")),
                         "published": c.get("published", ""), "asked_at": rec["asked_at"], "recheck_by": due})
    return rows


def render_index(rows):
    lines = ["# 調べものの索引（python -m harness.research index が作った。手で編集しない）", "",
             "| 主張 | 系統 | 分野 | 格 | 印 | 公開日 | 見直し | 内容 |", "|:--|:--|:--|:--|:--|:--|:--|:--|"]
    lines += [f"| {r['ref']} | {_cell(r['approach'])} | {r['sector']} | {r['tier']} | {r['status']} | {_cell(r['published'])} | "
              f"{r['recheck_by']} | {_cell(r['claim'])} |" for r in rows]
    return "\n".join(lines) + "\n"


def write_index(cfg, rows):
    path = Path(cfg["index"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    path.with_name("INDEX.md").write_text(render_index(rows), encoding="utf-8", newline="\n")
    return path


def search(rows, words, stale_on=None):
    """語をすべて含む行（主張・系統・題・決めたい判断・出典を、大文字と小文字を区別せずに見る）。stale_on は見直しの期限の日付。"""
    words = [w.lower() for w in words]
    hits = [r for r in rows if all(w in " ".join(str(r[k]) for k in ("claim", "approach", "title", "decision", "url")).lower()
                                   for w in words)]
    return [r for r in hits if r["recheck_by"] < stale_on] if stale_on else hits


def main(argv=None):
    ap = argparse.ArgumentParser(description="外部の調べもの（ADR-004 の K-15、docs/design/research_protocol.md）")
    ap.add_argument("action", choices=["ask", "queue", "index", "search"])
    ap.add_argument("question", nargs="*", help="問いのファイル（ask のとき）か、探す語（search のとき）")
    ap.add_argument("--stale", action="store_true", help="search で、見直しの期限を過ぎた行だけを出す")
    ap.add_argument("--backend", help="config/research.json の backends の名前（省くと default）")
    ap.add_argument("--out", help="記録の置き場（省くと config の records）")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.backend and args.backend not in cfg["backends"]:
        ap.error(f"--backend は {sorted(cfg['backends'])} のどれか")
    if args.action == "index":
        rows = build_index(cfg)
        print(f"索引を書きました: {write_index(cfg, rows)}（{len(rows)} 件の主張）")
        return 0
    if args.action == "search":
        path = Path(cfg["index"])
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else build_index(cfg)
        hits = search(rows, args.question, datetime.date.today().isoformat() if args.stale else None)
        for r in hits:
            print(f"{r['ref']}  [{r['approach'] or '-'}／{r['sector'] or '-'}／{r['tier'] or '-'}／{r['status']}]  "
                  f"{r['claim']}  {r['url']}")
        print(f"{len(hits)} 件")
        return 0
    try:
        if args.action == "ask":
            if len(args.question) != 1:
                ap.error("ask には問いのファイルが 1 つ要ります")
            return ask(cfg, args.question[0], args.backend, args.out)
        return run_queue(cfg, args.backend, args.out)
    except Abort as e:
        print(f"ABORT（環境の異常）: {e}")
        return ABORT


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
