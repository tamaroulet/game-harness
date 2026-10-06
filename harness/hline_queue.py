"""H ライン（harness/hline.py）のキュー：受信箱の What を取り込み、依存の順に取り出し、未収束の下流を凍結する。

状態（済み・待ち・未収束・凍結）は <out>/queue.json に置き、起動をまたいで保つ。What の本文は <out>/queue/<名前>.md。
受信箱は投入口にすぎない。取り込んだ What は受信箱から消える（二度取らない）。

What は本文の行で次を宣言できる（書式は総監督の部屋の CLAUDE.md の雛形にある）:
    マイルストーン: B7.1          必須。進捗のグループの ID。設定の milestones に無いもの・無いものは取らない
    タスク: S1-2                  任意。進捗のタスクの ID
    依存: 120-s1-2-x, 130-s1-3-y  任意。受信箱でのファイル名から .md を除いたもの

項目の状態: waiting（待ち）→ processing → done（済み）／unconverged（未収束）。未収束に依存する項目は frozen（凍結）。
"""
import json
import re
from pathlib import Path

from hline_base import RESERVED, title_of, write_json

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
            "infra_halt": st.get("infra_halt")}


def save_state(cfg, st):
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    write_json(state_path(cfg), st)


def by_status(st, status):
    return sorted(n for n, i in st["items"].items() if i["status"] == status)


# ============================================================ 取り込み

def intake(cfg, st, replacements_only=False):
    """受信箱の What をキューに入れ、受信箱から消す。許されないマイルストーンの What・宣言の無い What は受信箱に残す。
    未収束の What と同じ名前の What は、その記録を差し替える（再開）。BLOCKED の間は、その差し替えだけを取る。"""
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
        elif replacements_only and not (old and old["status"] == "unconverged"):
            why = "BLOCKED の間は、未収束の What を直したものだけを取ります"
        else:
            why = None
        if why:
            skipped.append({"file": src.name, "reason": why})
            continue
        what_path(cfg, name).parent.mkdir(parents=True, exist_ok=True)
        what_path(cfg, name).write_text(text, encoding="utf-8")
        st["items"][name] = {"status": "waiting", "title": title_of(text), "replaced": bool(old), **meta}
        save_state(cfg, st)    # 状態に入れてから受信箱を消す（途中で落ちても What は失われず、取り直しは同じ結果になる）
        src.unlink()
        taken.append(name)
    st["skipped"] = skipped
    return taken


# ============================================================ 取り出し・凍結・停止

def refresh(st):
    """未収束に（推移的に）依存する項目を凍結し、上流が直れば凍結を解く。凍結した項目は処理しない。"""
    items = st["items"]
    stuck = {n for n, i in items.items() if i["status"] == "unconverged"}
    grew = True
    while grew:
        grew = False
        for n, i in items.items():
            if i["status"] in ("waiting", "frozen") and n not in stuck and stuck & set(i["deps"]):
                stuck.add(n)
                grew = True
    for n, i in items.items():
        if i["status"] in ("waiting", "frozen"):
            if n in stuck:
                i["status"], i["frozen_by"] = "frozen", sorted(stuck & set(i["deps"]))
            else:
                i["status"] = "waiting"
                i.pop("frozen_by", None)


def next_runnable(st):
    """依存がすべて済んだ待ちの What のうち、名前の順で最初のもの。"""
    for name in sorted(st["items"]):
        i = st["items"][name]
        if i["status"] == "waiting" and all(st["items"].get(d, {}).get("status") == "done" for d in i["deps"]):
            return name
    return None


def blocked(cfg, st):
    """同じマイルストーンで未収束が上限に達したものの {マイルストーン: [未収束の What]}。空でなければ BLOCKED。"""
    by = {}
    for n in by_status(st, "unconverged"):
        by.setdefault(st["items"][n]["milestone"], []).append(n)
    return {m: ns for m, ns in sorted(by.items()) if len(ns) >= cfg["max_unconverged_per_milestone"]}


def recover(cfg, st, integrated):
    """強制終了で残った processing を戻す。統合ブランチに積み終えていたら済み、でなければ待ちに戻す（What は失わない）。"""
    for n in by_status(st, "processing"):
        st["items"][n]["status"] = "done" if integrated(n) else "waiting"
    save_state(cfg, st)
