"""提案役の起こし方（外側の周期の最初の手）：関所・上限・モデルの固定・道の検め・提案の形の検め・記録。

    python -m harness.proposer_launch --tag <tag> [--tag <tag>]... [--root <dir>]... [--config <hline.json>]

提案役は別の起動の Claude Code（config/proposer.json）で、読むだけの道具で、ハーネスの木（ROOT。作業ディレクトリ）と
tag ごとの開発の組の走りの記録（<out>/chains/<tag>/。chain_score の finish が土台ごとに s<k>.json と索引を置く）を読み、
直す失敗の型を 1 つ選んで、決まった見出しの提案を書く。--tag は 2 回以上渡せる（同じ tag の重なりは、何もせずに終了コード 2）。
- 関所：モデルの固定・指示のファイル（docs/prompts/proposer.md。保護パス）・tag ごとの記録の有無・読む根が oracles と重ならないこと
- 上限：--max-turns と ttl_seconds。トークンは走った後に照らして「超過」と残す（director_launch と同じ）
- 道の検め：stream-json の tool_use が allowed_tools の内で、パスが読む根の下にあること。当たりがあれば提案は捨てる
- 提案の形の検め：見出しの並び・中身・出た段の記録の有無（どれかの tag の記録にあれば合う）。合えば <stamp>.proposal.md に書く
記録は <out>/proposer/<stamp>.json に起動ごとに 1 つ。起こす前にも一度書く（CLI が返らなくても起こした時刻が残る）。
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_budget  # noqa: E402
import machine  # noqa: E402
import model_pin  # noqa: E402
import proc  # noqa: E402
from director_launch import end_reason  # noqa: E402
from hline_base import CONFIG, ROOT, Infra, load_config, pinned_models, save_prompt, write_json  # noqa: E402

AGENT = ROOT / "config" / "proposer.json"
PROMPT = ROOT / "docs" / "prompts" / "proposer.md"
WHERE = "config/proposer.json"
HEADINGS = ("## 狙う失敗の型", "## 出た段", "## 直し", "## 予想", "## 補う弱さ", "## 外す条件")
STAGE_HEAD = HEADINGS[1]
STAGE = re.compile(r"^- (?P<base>[^\s/\\]+) s(?P<k>\d+)\s*$")
DRIVE = re.compile(r"^[A-Za-z]:/")
PATH_KEYS = ("file_path", "path")


def store_dir(cfg):
    return Path(cfg["out"]) / "proposer"


def chains_dir(cfg, tag):
    return Path(cfg["out"]) / "chains" / tag


# ============================================================ パスの正規化（OS をまたいで、文字の形で比べる）

def norm(path, base=None):
    """(ドライブか "", 部分, ...) の小文字の組。\\ と / を同じに見て、. と .. を畳む。相対なら base から。"""
    s = str(path).replace("\\", "/")
    if DRIVE.match(s):
        head, s = (s[:2].lower(),), s[2:]
    elif s.startswith("/") or base is None:
        head = ("",)
    else:
        return norm(str(base).replace("\\", "/").rstrip("/") + "/" + s)
    got = []
    for p in s.split("/"):
        if p == "..":
            got = got[:-1]
        elif p not in ("", "."):
            got.append(p.lower())
    return head + tuple(got)


def under(path, root):
    return path[:len(root)] == root


def is_abs(text):
    s = text.replace("\\", "/")
    return s.startswith("/") or bool(DRIVE.match(s))


# ============================================================ 関所と渡す頼み

def overlap(roots, oracles):
    """oracles の下にある、または oracles を中に含む読む根。"""
    o = norm(oracles)
    return [str(r) for r in roots if under(norm(r), o) or under(o, norm(r))]


def as_list(value, kind):
    """kind の値 1 つなら 1 つの並びに、並びならそのまま並びにする（1 つの tag・記録の置き場で呼ぶ今の形も受ける）。"""
    return [value] if isinstance(value, kind) else list(value)


def gate(cfg, tags, roots):
    """起こさない理由。起こしてよければ None。記録は tag ごとに見る（1 つでも無ければ起こさない）。"""
    if not Path(PROMPT).is_file():
        return "指示のファイルがありません"
    for tag in tags:
        if not chains_dir(cfg, tag).is_dir():
            return f"開発の組の記録がありません: {chains_dir(cfg, tag)}"
    bad = overlap(roots, Path(cfg["oracles"]).absolute())
    if bad:
        return "読む根が oracles と重なります（保留の組と隠しテストは読ませません）: " + "、".join(bad)
    return None


def material(tags, roots, chains):
    """指示の全文の後に足す節「## 材料」。索引は、tag の並びの順に、どの tag の記録のものも全部並べる。"""
    index = [f"  - {p}" for c in chains for p in sorted(c.glob("*/*.index.md"))] or ["  - （無い）"]
    return "\n".join(["", "## 材料", "", f"- tag: {', '.join(tags)}", "- 読む根:", *(f"  - {r}" for r in roots),
                      "- 開発の組の記録の中の索引:", *index,
                      f"- 提案の見出し（この並びで各 1 回、中身を空にしない。{STAGE_HEAD} の各行は `- <土台> s<k>`）:",
                      *(f"  - {h}" for h in HEADINGS), ""])


# ============================================================ stream-json の読み取り

def events(out):
    for line in (out or "").splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            yield line, doc


def result_line(out):
    """最後の "type": "result" の行（JSON の文字列）。無ければ None。"""
    return next((line for line, doc in reversed(list(events(out))) if doc.get("type") == "result"), None)


def tool_uses(out):
    for _, doc in events(out):
        msg = doc.get("message") if doc.get("type") == "assistant" else None
        content = msg.get("content") if isinstance(msg, dict) else None
        for c in content if isinstance(content, list) else ():
            if isinstance(c, dict) and c.get("type") == "tool_use":
                yield c


def stray(use, allowed, roots, cwd):
    """道具の呼び出し 1 つの当たりの理由の列。path が無い呼び出しは作業ディレクトリ（cwd）を見たとする。
    pattern をパスとして見るのは Glob だけ（Grep の pattern は検索の正規表現）。絶対の形か .. を含むときに照らす。"""
    name, inp = use.get("name"), use.get("input")
    if name not in allowed:
        return [f"許していない道具: {name}"]
    inp = inp if isinstance(inp, dict) else {}
    targets = [inp[k] for k in PATH_KEYS if isinstance(inp.get(k), str)]
    pat = inp.get("pattern") if name == "Glob" else None
    if isinstance(pat, str) and (is_abs(pat) or ".." in pat.replace("\\", "/").split("/")):
        targets.append(pat)
    rs = [norm(r) for r in roots]
    return [f"読む根の外: {t}" for t in targets if not any(under(norm(t, cwd), r) for r in rs)]


def audit(out, allowed, roots, cwd):
    """道の当たり [{id, name, why}]。"""
    return [{"id": u.get("id"), "name": u.get("name"), "why": why}
            for u in tool_uses(out) for why in stray(u, allowed, roots, cwd)]


# ============================================================ 提案の形

def sections(text):
    """'## ' の行で区切った [(見出し, 中身の行の列)]。最初の見出しより前は捨てる。"""
    got = []
    for line in text.splitlines():
        if line.startswith("## "):
            got.append((line.rstrip(), []))
        elif got:
            got[-1][1].append(line)
    return got


def stage_errors(body, chains):
    rows = [ln.strip() for ln in body if ln.strip().startswith("- ")]
    errs = [] if rows else [f"{STAGE_HEAD} に段の行（- <土台> s<k>）がありません"]
    for row in rows:
        m = STAGE.match(row)
        if not m or m["base"] in (".", ".."):
            errs.append(f"{STAGE_HEAD} の行の形が違います（- <土台> s<k>）: {row}")
        elif not any((c / m["base"] / f"s{m['k']}.json").is_file() for c in chains):
            errs.append(f"{STAGE_HEAD} の段の記録がありません: {m['base']} s{m['k']}")
    return errs


def shape_errors(text, chains):
    """提案の形の誤りの文の列（空なら合う）。chains は記録の置き場 1 つか、その並び。"""
    chains = [Path(c) for c in as_list(chains, (str, Path))]
    secs = sections(text)
    names = [h for h, _ in secs if h in HEADINGS]
    errs = [f"見出し {h} が {names.count(h)} 回あります（各 1 回）" for h in HEADINGS if names.count(h) != 1]
    if not errs and tuple(names) != HEADINGS:
        errs.append("見出しの並びが違います: " + " → ".join(names))
    errs += [f"見出し {h} の中身が空です" for h, body in secs if h in HEADINGS and not "".join(body).strip()]
    stage = [body for h, body in secs if h == STAGE_HEAD]
    return errs + (stage_errors(stage[0], chains) if len(stage) == 1 else [])


def proposal_text(text):
    lines = text.splitlines()
    first = next(i for i, ln in enumerate(lines) if ln.rstrip() in HEADINGS)
    return "\n".join(lines[first:]).rstrip() + "\n"


# ============================================================ 起こす

def check_model(rec, agent, result):
    """result の行の modelUsage を設定と照合する。照合できない・違うときは「CLI の誤り」。"""
    try:
        if result is None:
            raise Infra("stream-json に result の行がありません（モデルを照合できません）")
        rec["models"] = pinned_models(agent, result, "提案役")
    except Infra as e:
        rec["end"], rec["error"] = "CLI の誤り", str(e)[:300]


def judge(rec, result, chains, path):
    """提案の形を検め、合えば（道の当たりも無ければ）見出しから後を <stamp>.proposal.md に書く。"""
    doc = json.loads(result)
    text = doc.get("result") if isinstance(doc.get("result"), str) else ""
    rec["shape_errors"] = shape_errors(text, chains)
    if not rec["path_hits"] and not rec["shape_errors"]:
        dest = path.with_name(path.stem + ".proposal.md")
        dest.write_text(proposal_text(text), encoding="utf-8")
        rec["proposal"] = str(dest)


def run(rec, agent, budget, roots, path):
    chains = roots[1:1 + len(rec["tags"])]   # 読む根の 2 つ目から、tag ごとの記録の置き場
    prompt = Path(PROMPT).read_text(encoding="utf-8") + material(rec["tags"], roots, chains)
    cap = budget["max_turns"]
    args = hline_budget.capped_args(agent, proc.resolve_cli(agent["cli"]), cap)
    args += [x for r in roots[1:] for x in ("--add-dir", str(r))]
    log = path.with_suffix(".log")
    rec["launched"] = True
    write_json(path, rec)   # 起こした時刻は、CLI が返らなくても残す
    code, out, err = proc.run(args, roots[0], budget["ttl_seconds"], "提案役", input=prompt)
    log.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    save_prompt(log, prompt)
    result = result_line(out)
    rec["end"] = end_reason(code, result or "", cap)
    if rec["end"] == "正常":
        check_model(rec, agent, result)
    rec["usage"] = hline_budget.usage(result or "")
    used = (rec["usage"] or {}).get("total_tokens")
    rec["token_excess"] = isinstance(used, int) and not isinstance(used, bool) and used > budget["max_tokens"]
    rec["path_hits"] = audit(out, agent["allowed_tools"], roots, roots[0])
    if rec["end"] == "正常":
        judge(rec, result, chains, path)


def launch(cfg, agent, tags, extra=(), now=None, root=None):
    """関所を通って、必要なら提案役を 1 回起こす。tags は tag 1 つかその並び。記録（dict）を返し、<out>/proposer/ に書く。"""
    budget = agent["proposer_budget"]
    now = now or datetime.datetime.now()
    tags = as_list(tags, str)
    roots = [Path(root or ROOT).absolute(), *(chains_dir(cfg, t).absolute() for t in tags), *(Path(r).absolute() for r in extra)]
    rec = {"at": now.isoformat(timespec="seconds"), "tags": tags, "roots": [str(r) for r in roots],
           "launched": False, "skipped_reason": None, "end": None, "usage": None,
           "token_limit": budget["max_tokens"], "token_excess": False, "path_hits": [], "shape_errors": [], "proposal": None}
    store_dir(cfg).mkdir(parents=True, exist_ok=True)
    path = store_dir(cfg) / f"{now.strftime('%Y%m%d-%H%M%S-%f')}.json"
    try:
        model_pin.require(agent, WHERE)
        model_pin.auxiliary_prefixes(agent, WHERE)
    except model_pin.ModelPinError as e:   # 理由を残してから、設定の誤りとして止める（exitcode の決まりで 2）
        rec["skipped_reason"] = f"モデルの固定の誤り: {e}"
        write_json(path, rec)
        raise
    rec["skipped_reason"] = gate(cfg, tags, roots)
    if rec["skipped_reason"] is None:
        run(rec, agent, budget, roots, path)
    write_json(path, rec)
    return rec


def ok(rec):
    return bool(rec["launched"] and rec["end"] == "正常" and not rec["path_hits"] and not rec["shape_errors"] and rec["proposal"])


def line_of(rec):
    """標準出力に出す 1 行の要約。"""
    if not rec["launched"]:
        return f"- 提案役の起動: 起こさなかった（{rec['skipped_reason']}）"
    used = (rec.get("usage") or {}).get("total_tokens")
    bits = [rec["at"], f"終了: {rec['end']}", f"使ったトークン: {'不明' if used is None else used}"]
    bits += [t for on, t in ((rec["token_excess"], f"トークンの上限 {rec['token_limit']} を超過"),
                             (rec["path_hits"], f"道の当たり {len(rec['path_hits'])} 件（提案は捨てた）"),
                             (rec["shape_errors"], f"提案の形の誤り {len(rec['shape_errors'])} 件"),
                             (rec["proposal"], f"提案: {rec['proposal']}")) if on]
    return "- 提案役の起動: 起こした（" + "、".join(bits) + "）"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.proposer_launch")
    ap.add_argument("--tag", required=True, action="append", help="走りの記録の tag（2 回以上渡せる）")
    ap.add_argument("--root", action="append", default=[], help="読む根を足す（--add-dir で渡す）")
    ap.add_argument("--config", default=str(CONFIG))
    a = ap.parse_args(argv)
    if len(set(a.tag)) != len(a.tag):
        print(f"--tag が重なっています: {', '.join(a.tag)}", file=sys.stderr)
        return 2
    rec = launch(load_config(a.config), machine.read_json(AGENT), a.tag, a.root)
    print(line_of(rec))
    return 0 if ok(rec) else 1


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
