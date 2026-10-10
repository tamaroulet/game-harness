"""H ライン（harness/hline.py）のキュー：受信箱の What を取り込み、依存の順に取り出し、未収束の下流を凍結する。

状態（済み・待ち・未収束・凍結）は <out>/queue.json に置き、起動をまたいで保つ。What の本文は <out>/queue/<名前>.md。
受信箱は投入口にすぎない。取り込んだ What は受信箱から消える（二度取らない）。

What は本文の行で次を宣言できる（書式は総監督の部屋の CLAUDE.md の雛形にある）:
    マイルストーン: B7.1          必須。進捗のグループの ID。設定の milestones に無いもの・無いものは取らない
    タスク: S1-2                  任意。進捗のタスクの ID
    依存: 120-s1-2-x, 130-s1-3-y  任意。受信箱でのファイル名から .md を除いたもの

項目の状態: waiting（待ち）→ processing → done（済み）／unconverged（未収束）。未収束に依存する項目は frozen（凍結）。
"""
import datetime
import json
import re
from pathlib import Path

from hline_base import RESERVED, ROOT, title_of, write_json

import proc  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

HEADER = re.compile(r"^\s*(?:[-*]\s*)?(マイルストーン|タスク|依存)\s*[:：]\s*(.*?)\s*$")


def parse_header(text):
    found = {}
    for line in text.splitlines():
        m = HEADER.match(line)
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip("` ")
    deps = [d.strip("`").removesuffix(".md") for d in re.split(r"[,、，\s]+", found.get("依存", "")) if d.strip("`")]
    return {"milestone": found.get("マイルストーン") or None, "task": found.get("タスク") or None, "deps": deps}


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
            "infra_halt": st.get("infra_halt"), "quota_wait": st.get("quota_wait")}


def save_state(cfg, st):
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    write_json(state_path(cfg), st)


def by_status(st, status):
    return sorted(n for n, i in st["items"].items() if i["status"] == status)


# ============================================================ 取り込み

def intake(cfg, st):
    """受信箱の What をキューに入れ、受信箱から消す。許されないマイルストーンの What・宣言の無い What は受信箱に残す。
    未収束の What と同じ名前の What は、その記録を差し替える（再開）。"""
    skipped, taken = [], []
    for src in candidates(cfg["inbox"]):
        name, text = src.stem, src.read_text(encoding="utf-8")
        meta, old = parse_header(text), st["items"].get(src.stem)
        if not meta["milestone"]:
            why = "マイルストーンの宣言がありません"
        elif meta["milestone"] not in cfg["milestones"]:
            why = f"マイルストーン {meta['milestone']} はこのラインに許されていません（許可: {', '.join(cfg['milestones'])}）"
        elif old and old["status"] in ("processing", "done"):
            why = f"同名の What が{'処理中' if old['status'] == 'processing' else '済み'}です"
        else:
            why = None
        if why:
            skipped.append({"file": src.name, "reason": why})
            continue
        what_path(cfg, name).parent.mkdir(parents=True, exist_ok=True)
        what_path(cfg, name).write_text(text, encoding="utf-8")
        st["items"][name] = {"status": "waiting", "title": title_of(text), "replaced": bool(old),
                             "header_deps": list(meta["deps"]), **meta}
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
        return {t["id"]: {"deps": list(t.get("depends_on") or []), "done": t.get("status") == "completed"} for t in tasks}
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
        own = ledger.get(i["task"], {}).get("deps", [])
        head = i.get("header_deps", i["deps"])
        i["deps"] = list(dict.fromkeys([*head, *own]))
        i["met"] = [d for d in i["deps"] if ledger.get(d, {}).get("done")]


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
        if i["status"] == "waiting" and not i.get("ledger_error") and all(dep_done(st, i, d) for d in i["deps"]):
            return name
    return None


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
