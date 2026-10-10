"""北極星計器：H ラインのキュー（queue.json）から、工廠の日ごとの成績（済み 1 件あたりのトークン）を出す。

    python -m harness.northstar --queue <queue.json のパス>

キューだけを読む（いまの時刻に依らない・キューを書き換えない）。表は、データの中の最後の日付から遡って 7 日分。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

if __package__ in (None, ""):   # 直接実行でも harness/ の兄弟モジュールを引けるように
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_usage  # noqa: E402

DAYS = 7
NONE = "—"


def date_of(item):
    """項目の日付：済みは done_at、未収束は at の先頭 10 文字（書かれた日付のまま）。読めなければ None。"""
    key = {"done": "done_at", "unconverged": "at"}.get(item.get("status"))
    text = item.get(key) if key else None
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def rows(items):
    """(日ごとの行の一覧, 日付の無い項目の件数, 利用量の無い試行の件数)。行は古い日から。"""
    days, undated, missed = {}, 0, 0
    for item in items.values():
        if item.get("status") not in ("done", "unconverged"):
            continue
        day = date_of(item)
        if day is None:
            undated += 1
            continue
        days.setdefault(day, []).append(item)
    if not days:
        return [], undated, 0
    last = max(days)
    out = []
    for back in range(DAYS - 1, -1, -1):
        day = last - datetime.timedelta(days=back)
        group = days.get(day, [])
        done = [i for i in group if i["status"] == "done"]
        tokens = sum(hline_usage.totals(i)[2] for i in group)
        missed += sum(hline_usage.totals(i)[4] for i in group)
        once = sum(1 for i in done if hline_usage.totals(i)[0] == 1)
        out.append({"day": day.isoformat(), "done": len(done), "unconverged": len(group) - len(done), "tokens": tokens,
                    "per_done": tokens // len(done) if done else None, "once": once * 100 // len(done) if done else None})
    return out, undated, missed


def render(items):
    table, undated, missed = rows(items)
    lines = ["| 日付 | 済み | 未収束 | トークン | 済み 1 件あたりのトークン | 1 回で済んだ割合 |", "|:--|--:|--:|--:|--:|--:|"]
    for r in table:
        per = NONE if r["per_done"] is None else r["per_done"]
        once = NONE if r["once"] is None else f"{r['once']}%"
        lines.append(f"| {r['day']} | {r['done']} | {r['unconverged']} | {r['tokens']} | {per} | {once} |")
    lines += ["", f"- 日付の無い項目: {undated} 件（表に入れていない）", f"- 利用量の無い試行: {missed} 件（トークンに数えていない）"]
    return "\n".join(lines) + "\n"


def load_items(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("items", {})


def section(items):
    return "## 計器\n" + render(items)


def safe_section(items):
    """report.md 用。計器が例外で止まっても、節を 1 行の理由に替えて、書き出しを止めない。"""
    try:
        return section(items)
    except Exception as e:   # noqa: BLE001
        reason = " ".join(f"{type(e).__name__}: {e}".split())
        return f"## 計器\n- 計器を出せませんでした: {reason}\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.northstar")
    ap.add_argument("--queue", required=True, help="queue.json のパス")
    args = ap.parse_args(argv)
    sys.stdout.write(render(load_items(args.queue)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
