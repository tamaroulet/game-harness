"""H ライン（harness/hline.py）のキュー：受信箱の What を取り込み、依存の順に取り出し、未収束の下流を凍結する。

状態（済み・待ち・未収束・凍結）は <out>/queue.json に置き、起動をまたいで保つ。What の本文は <out>/queue/<名前>.md。
受信箱は投入口にすぎない。取り込んだ What は受信箱から消える（二度取らない）。

What は本文の行で次を宣言できる（書式は総監督の部屋の CLAUDE.md の雛形にある）:
    マイルストーン: B7.1          必須。進捗のグループの ID。設定の milestones に無いもの・無いものは取らない
    タスク: S1-2                  必須。進捗のタスクの ID。base の台帳にある未完了の by: hline のものだけ取る。ファイル名は常にタスクの ID
    依存: 120-s1-2-x, 130-s1-3-y  任意。受信箱でのファイル名から .md を除いたもの
    検証:                         書かない（`検証:` の行のある What は取らない。検証コマンドの正本は台帳）
1 件の What が 1 つのタスクを完了にする（分けるときは台帳のタスクを分ける）。

項目の状態: waiting（待ち）→ processing → done（済み）／unconverged（未収束）。未収束に依存する項目は frozen（凍結）。
"""
import datetime
import json
import re
from pathlib import Path

from hline_base import RESERVED, ROOT, title_of, write_json

import hline_ledger_entry  # noqa: E402
import hline_review  # noqa: E402
import proc  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

HEADER = re.compile(r"^\s*(?:[-*]\s*)?(マイルストーン|タスク|依存|検証)\s*[:：]\s*(.*?)\s*$")


def parse_header(text):
    found = {}
    for line in text.splitlines():
        m = HEADER.match(line)
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip("` ")
    deps = [d.strip("`").removesuffix(".md") for d in re.split(r"[,、，\s]+", found.get("依存", "")) if d.strip("`")]
    return {"milestone": found.get("マイルストーン") or None, "task": found.get("タスク") or None, "deps": deps,
            "verify": found.get("検証") or None}


ITEM = re.compile(r"^(?:\d+\.|-)\s")


def shape_problem(text):
    """本文に `## What` と `## 合格条件` の節があり、合格条件の字下げの無い箇条が 1〜4 であること。満たさなければ理由。"""
    lines = text.splitlines()
    if not any(re.fullmatch(r"##\s+What\s*", l) for l in lines):
        return "本文に `## What` の行がありません"
    starts = [n for n, l in enumerate(lines) if re.fullmatch(r"##\s+合格条件\s*", l)]
    if not starts:
        return "本文に `## 合格条件` の行がありません"
    section = []
    for l in lines[starts[0] + 1:]:
        if l.startswith("## "):
            break
        section.append(l)
    count = sum(1 for l in section if ITEM.match(l))
    if not 1 <= count <= 4:
        return f"`## 合格条件` の箇条が {count} 件です（1〜4 件で書いてください）"
    return None


def candidates(inbox):
    """受信箱の What を名前の順に。H ラインが書くファイルは取らない。"""
    return sorted(p for p in Path(inbox).glob("*.md") if p.is_file() and p.name not in RESERVED)


def pick(inbox):
    found = candidates(inbox)
    return found[0] if found else None


# ============================================================ 状態

def state_path(cfg):
    return Path(cfg["out"]) / "queue.json"


def what_path(cfg, name):
    return Path(cfg["out"]) / "queue" / f"{name}.md"


def load_state(cfg):
    p = state_path(cfg)
    st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {"items": st.get("items", {}), "awaiting_pr": st.get("awaiting_pr"), "skipped": st.get("skipped", []),
            "infra_halt": st.get("infra_halt"), "quota_wait": st.get("quota_wait"), "pr_times": st.get("pr_times")}


def save_state(cfg, st):
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    write_json(state_path(cfg), st)


def by_status(st, status):
    return sorted(n for n, i in st["items"].items() if i["status"] == status)


# ============================================================ 取り込み

def intake(cfg, st):
    """受信箱の What をキューに入れ、受信箱から消す。許されないマイルストーンの What・タスクを宣言しない What・`検証:` の行のある What・
    base の台帳に無いか完了したタスクの What・台帳を読めないときの What は受信箱に残す（次の走行で取り直す）。
    未収束の What と同じ名前の What は、その記録を差し替える（再開）。"""
    skipped, taken, ledger, ledger_error = [], [], None, None
    entry = None   # この What が持つ、規則を満たした台帳の項目（base の台帳に無いタスクのとき）

    def task_problem(task, text):
        nonlocal ledger, ledger_error, entry
        if ledger is None and ledger_error is None:
            try:
                ledger = read_ledger(cfg)
            except Exception as e:   # noqa: BLE001
                ledger_error = str(e)
        if ledger_error:
            return f"base の台帳を読めないため、タスク {task} を確かめられません（次の走行で取り直します）: {ledger_error}"
        if task not in ledger:
            try:
                found, reasons = hline_ledger_entry.check(cfg, text, task)
            except ValueError as e:
                return f"base の台帳を読めないため、タスク {task} の台帳の項目を確かめられません（次の走行で取り直します）: {e}"
            if found is None and not reasons:
                return f"タスク {task} は base の台帳にありません"
            if reasons:
                return reasons
            entry = found
            return None
        if ledger[task].get("by") == "pilot":
            return f"タスク {task} は操縦士の作業（by: pilot）で、H ラインは取りません"
        if ledger[task]["done"]:
            return f"タスク {task} は base の台帳で完了しています"
        return None

    def oracle_problem(task):
        for o in ledger[task].get("oracle") or []:
            src = str(o.get("from") or "")
            if src != "existing" and not ledger.get(src, {}).get("done"):
                continue
            path = str(o.get("path") or "")
            code, _, _ = proc.run(["git", "cat-file", "-e", f"{cfg['base']}:{path}"], ROOT, cfg["ttl_seconds"]["git"], "git cat-file (oracle)")
            if code != 0:
                return f"タスク {task} のオラクル {path}（from: {src}）が base（{cfg['base']}）にありません"
        return None

    for src in candidates(cfg["inbox"]):
        name, text = src.stem, src.read_text(encoding="utf-8")
        meta, old = parse_header(text), st["items"].get(src.stem)
        entry = None
        if not meta["milestone"]:
            why = "マイルストーンの宣言がありません"
        elif meta["milestone"] not in cfg["milestones"]:
            why = f"マイルストーン {meta['milestone']} はこのラインに許されていません（許可: {', '.join(cfg['milestones'])}）"
        elif not meta["task"]:
            why = "`タスク:` の行がありません（すべての What は台帳のタスクを 1 つ宣言します）"
        elif meta["verify"]:
            why = f"`検証:` の行のある What は取りません（タスク {meta['task']} の検証コマンドの正本は台帳です。行を消してください）"
        elif why := task_problem(meta["task"], text):
            pass
        elif entry is None and (why := oracle_problem(meta["task"])):
            pass
        elif why := shape_problem(text):
            pass
        elif old and old["status"] in ("processing", "done"):
            why = f"同名の What が{'処理中' if old['status'] == 'processing' else '済み'}です"
        else:
            why = None
        if not why:   # 機械の監修をすべて通った What だけを、監修役（LLM）に 1 回かける
            seen = {**ledger, meta["task"]: hline_ledger_entry.as_row(entry)} if entry else ledger   # 足す項目をタスクの欄として渡す
            why = hline_review.review(cfg, seen, meta["task"], text)
        if why:
            skipped.extend({"file": src.name, "reason": r} for r in ([why] if isinstance(why, str) else why))
            continue
        if entry:   # 機械と監修役を通った。台帳の項目を統合ブランチに積んでから、What を待ちに入れる（積む前に落ちれば What は受信箱に残る）
            hline_ledger_entry.stack(cfg, entry, name)
        what_path(cfg, name).parent.mkdir(parents=True, exist_ok=True)
        what_path(cfg, name).write_bytes(src.read_bytes())   # 読み直した文字列でなく、元のバイト列を写す（改行を替えない。D-114）
        st["items"][name] = {"status": "waiting", "title": title_of(text), "replaced": bool(old),
                             "header_deps": list(meta["deps"]), **meta, **({"ledger_entry": True} if entry else {})}
        save_state(cfg, st)    # 状態に入れてから受信箱を消す（途中で落ちても What は失われず、取り直しは同じ結果になる）
        src.unlink()
        taken.append(name)
    st["skipped"] = skipped
    apply_ledger(cfg, st)
    return taken


# ============================================================ 台帳の依存

def read_ledger(cfg):
    """base の台帳（docs/progress.yaml）を読み、{タスク ID: {"deps": [...], "done": bool}} を返す。読めなければ ValueError。"""
    code, out, err = proc.run(["git", "show", f"{cfg['base']}:{progress.REL_PATH}"], ROOT, cfg["ttl_seconds"]["git"], "git show (ledger)")
    if code != 0:
        raise ValueError(f"{cfg['base']} の {progress.REL_PATH} を読めません: {' '.join((err or out or '').split())[:200]}")
    try:
        tasks = yaml.safe_load(out)["tasks"]
        return {t["id"]: {"deps": list(t.get("depends_on") or []), "done": t.get("status") == "completed",
                          "by": t.get("by") or "hline", "oracle": list(t.get("oracle") or []), "row": t,
                          "after": list(t.get("after") or []), "rung": t.get("rung")} for t in tasks}
    except Exception as e:   # noqa: BLE001
        raise ValueError(f"{cfg['base']} の {progress.REL_PATH} を解釈できません: {type(e).__name__}") from e


def apply_ledger(cfg, st):
    """タスクを宣言した待ち・凍結の What の依存を、`依存:` と base の台帳の depends_on の和にする。
    台帳で完了している依存先は met に残す（キューに無くても満たされたものとして扱う）。台帳が読めない What は ledger_error を立てて待たせる。
    タスクを宣言した What が無ければ git を呼ばない。"""
    todo = [i for i in st["items"].values() if i["status"] in ("waiting", "frozen") and i.get("task")]
    if not todo:
        return
    try:
        ledger = read_ledger(cfg)
        error = None
    except Exception as e:   # noqa: BLE001
        ledger, error = {}, str(e)
    for i in todo:
        if error:
            i["ledger_error"] = error
            continue
        i.pop("ledger_error", None)
        if i.get("ledger_entry") and i["task"] not in ledger:   # 項目が main に入る（base の台帳に現れる）まで処理しない
            i["entry_wait"] = True
            continue
        i.pop("entry_wait", None)
        own = ledger.get(i["task"], {}).get("deps", [])
        head = i.get("header_deps", i["deps"])
        i["deps"] = list(dict.fromkeys([*head, *own]))
        i["met"] = [d for d in i["deps"] if ledger.get(d, {}).get("done")]
        i["after"] = list(ledger.get(i["task"], {}).get("after", []))   # 弱い依存：待たせるだけで、凍結の計算（deps）には入れない
        i["mainline"] = reaches_rung(ledger, i["task"])


def reaches_rung(ledger, task):
    """task が、台帳の rung を持つタスクか、rung を持つタスクが depends_on で推移的にたどり着くタスクか（本線への影響）。"""
    seen, todo = set(), [t for t, row in ledger.items() if row.get("rung")]
    while todo:
        t = todo.pop()
        if t not in seen:
            seen.add(t)
            todo.extend(ledger.get(t, {}).get("deps", []))
    return task in seen


def _after_states(st, item):
    """item の after の各タスクについて、そのタスクを宣言したキューの What の状態の集合 {タスク: {状態...}}。"""
    return {a: {j["status"] for j in st["items"].values() if j.get("task") == a} for a in item.get("after") or []}


def after_blockers(st, item):
    """item を待たせる after のタスク：宣言した What が待ち・処理中・凍結のもの。"""
    return [a for a, s in _after_states(st, item).items() if s & {"waiting", "processing", "frozen"}]


def after_unconverged(st, item):
    """after のタスクのうち、宣言した What が未収束のもの（待たせないが警告する）。"""
    return [a for a, s in _after_states(st, item).items() if "unconverged" in s and not s & {"waiting", "processing", "frozen"}]


def downstream_impact(st, name):
    """未収束の項目 name に depends_on で（推移的に）依存して凍結した項目の名前の列と、そのうち本線に響くものがあるか。"""
    items = st["items"]
    ids = lambda n: {n, items[n].get("task")} - {None}   # noqa: E731
    reach, hit, grew = set(ids(name)), [], True
    while grew:
        grew = False
        for n, i in sorted(items.items()):
            if i["status"] == "frozen" and n not in hit and reach & set(i["deps"]):
                hit.append(n)
                reach |= ids(n)
                grew = True
    return hit, any(items[n].get("mainline") for n in hit)


def dep_done(st, item, d):
    """依存先 d（What の名前か、タスクの ID）が済んでいるか。"""
    if d in item.get("met", ()) or st["items"].get(d, {}).get("status") == "done":
        return True
    return any(j.get("task") == d and j["status"] == "done" for j in st["items"].values())


# ============================================================ 取り出し・凍結・復旧

def refresh(st):
    """未収束に（推移的に）依存する項目を凍結し、上流が直れば凍結を解く。凍結した項目は処理しない。"""
    items = st["items"]
    stuck = {n for n, i in items.items() if i["status"] == "unconverged"}
    ids = lambda n: {n, items[n].get("task")} - {None}   # noqa: E731  台帳の依存はタスクの ID で書かれる
    grew = True
    while grew:
        grew = False
        for n, i in items.items():
            if i["status"] in ("waiting", "frozen") and n not in stuck and {x for s in stuck for x in ids(s)} & set(i["deps"]):
                stuck.add(n)
                grew = True
    for n, i in items.items():
        if i["status"] in ("waiting", "frozen"):
            if n in stuck:
                held = {x for s in stuck for x in ids(s)}
                i["status"], i["frozen_by"] = "frozen", sorted(held & set(i["deps"]))
            else:
                i["status"] = "waiting"
                i.pop("frozen_by", None)


def next_runnable(st):
    """依存がすべて済んだ待ちの What のうち、名前の順で最初のもの。"""
    for name in sorted(st["items"]):
        i = st["items"][name]
        if (i["status"] == "waiting" and not i.get("ledger_error") and not i.get("entry_wait") and all(dep_done(st, i, d) for d in i["deps"])
                and not after_blockers(st, i)):
            return name
    return None


def missing_deps(st, item):
    """item の依存先のうち、キューのどの What の名前・タスクとも一致せず、base の台帳でも完了していないもの（欠けた依存先）。"""
    known = set(st["items"]) | {j["task"] for j in st["items"].values() if j.get("task")}
    return [d for d in item["deps"] if d not in known and d not in item.get("met", ())]


def only_orphans_wait(st):
    """走行の終わりに、処理中も、処理できる待ちも、台帳を読めずに待つ What も無いか（残る待ちは依存先なしか、その連鎖だけ）。"""
    items = st["items"].values()
    return (not any(i["status"] == "processing" or (i["status"] == "waiting" and i.get("ledger_error")) for i in items)
            and next_runnable(st) is None)


def now():
    return datetime.datetime.now().astimezone()


def mark_done(item):
    """項目を済みにし、その時刻（ISO 8601、秒まで、UTC からの差つき）を done_at に残す。すでにある done_at は書き換えない。"""
    item["status"] = "done"
    item.setdefault("done_at", now().isoformat(timespec="seconds"))


def recover(cfg, st, integrated):
    """強制終了で残った processing を戻す。統合ブランチに積み終えていたら済み、でなければ待ちに戻す（What は失わない）。"""
    for n in by_status(st, "processing"):
        if integrated(n):
            mark_done(st["items"][n])
        else:
            st["items"][n]["status"] = "waiting"
    save_state(cfg, st)
