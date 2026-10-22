"""失敗の分類：不合格の試行を、どの検査で落ちたかで分け（describe）、queue.json から日ごとの件数の表を出す。

    python -m harness.failure_classes --queue <queue.json のパス>

表はキューだけを読む（いまの時刻に依らない・キューを書き換えない）。分類の無い古い記録は「不明」に数える。
"""
import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):   # 直接実行でも harness/ の兄弟モジュールを引けるように
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import fastsuite  # noqa: E402
import hline_abort  # noqa: E402
import northstar  # noqa: E402

CLASSES = ("abandon", "repeat", "protected", "timeout", "full_suite", "test", "turns", "cutoff", "other")
TURNS_MARK = "ターン数の上限"   # hline_budget.turn_cutoff の理由の書き出し
INFRA = "infra"
UNKNOWN = "不明"
COLUMNS = CLASSES + (INFRA, UNKNOWN)
PROTECTED_HEAD = "変えてはならないパスを変えています"
TIMEOUT_MARKS = ("TTL超過", "時間切れ")
SYNTAX_HEAD = "構文の誤りがあります"   # hline_gate.SYNTAX_HEAD と同じ（hline_gate は host を引くので import しない）
TEST_HEAD ="検証コマンド `"
_MODULES = re.compile(r"^落ちたモジュール: (.*?)[ \t\r]*$", re.MULTILINE)


def failed_modules(text):
    """fastsuite の本文の「落ちたモジュール:」の行から、モジュール名を現れた順・重複なしで（「なし」は除く）。"""
    names = [n.strip() for m in _MODULES.finditer(text or "") for n in m.group(1).split(",")]
    return tuple(dict.fromkeys(n for n in names if n and n != "なし"))


def classify(rec, text):
    """試行の記録 rec と Gate 1（と全件）の出力 text から分類。上から先に当たったもの。
    実装役がターン数の上限で打ち切られて test に落ちた試行は turns に分ける（abort・timeout・protected・full_suite は先のまま）。"""
    cls = _classify(rec, text)
    if cls == "test" and TURNS_MARK in str(rec.get("cutoff") or ""):
        return "turns"
    return cls


def _classify(rec, text):
    abort = rec.get("abort")
    if abort:
        return "repeat" if str(abort).startswith(hline_abort.REPEAT) else "abandon"
    if rec.get("timeout"):
        return "timeout"
    text = text or ""
    if text.startswith(SYNTAX_HEAD):
        return "test"
    if text.startswith(PROTECTED_HEAD):
        return "protected"
    if any(m in text for m in TIMEOUT_MARKS):
        return "timeout"
    if "suite" in rec:
        return "full_suite"
    if TEST_HEAD in text or failed_modules(text) or fastsuite.failed_tests(text):
        return "test"
    return "cutoff" if rec.get("cutoff") else "other"


def describe(rec, text):
    """試行の記録に残す failure：分類と、落ちたモジュール・落ちたテストの名前。"""
    return {"class": classify(rec, text), "modules": list(failed_modules(text)), "tests": list(fastsuite.failed_tests(text))}


def last_class(item):
    """項目の最後の試行の分類。試行が無い・分類が無ければ other。"""
    tries = item.get("tries") or []
    return ((tries[-1].get("failure") or {}).get("class") if tries else None) or "other"


def _count(table, day, name):
    table.setdefault(day, {}).setdefault(name, 0)
    table[day][name] += 1


def counts(items):
    """(未収束の日ごとの分類別の件数, 不合格の試行の日ごとの分類別の件数)。日付の無い項目は数えない。"""
    stuck, tries = {}, {}
    for item in items.values():
        if item.get("status") not in ("done", "unconverged"):
            continue
        day = northstar.date_of(item)
        if day is None:
            continue
        if item["status"] == "unconverged":
            _count(stuck, day, item.get("failure_class") or UNKNOWN)
        for t in item.get("tries") or []:
            if not t.get("gate"):
                _count(tries, day, (t.get("failure") or {}).get("class") or UNKNOWN)
    return stuck, tries


def _table(title, table):
    lines = [title, "", "| 日付 | " + " | ".join(COLUMNS) + " |", "|:--|" + "--:|" * len(COLUMNS)]
    for day in sorted(table):
        lines.append(f"| {day.isoformat()} | " + " | ".join(str(table[day].get(c, 0)) for c in COLUMNS) + " |")
    return lines


def render(items):
    stuck, tries = counts(items)
    return "\n".join(_table("未収束の項目（最後の試行の分類）", stuck) + [""] + _table("不合格の試行", tries)) + "\n"


def section(items):
    return "### 失敗の分類\n\n" + render(items)


def safe_section(items):
    """report.md 用。計器が例外で止まっても、節を 1 行の理由に替えて、書き出しを止めない。"""
    try:
        return section(items)
    except Exception as e:   # noqa: BLE001
        reason = " ".join(f"{type(e).__name__}: {e}".split())
        return f"### 失敗の分類\n- 失敗の分類を出せませんでした: {reason}\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.failure_classes")
    ap.add_argument("--queue", required=True, help="queue.json のパス")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.stdout.write(render(northstar.load_items(args.queue)))
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
