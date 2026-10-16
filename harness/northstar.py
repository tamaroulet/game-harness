"""北極星計器：H ラインのキュー（queue.json）から、工廠の日ごとの成績（済み 1 件あたりのトークン）を出す。

    python -m harness.northstar --queue <queue.json のパス>

キューだけを読む（いまの時刻に依らない・キューを書き換えない）。表は、データの中の最後の日付から遡って 7 日分。
"""
import argparse
import datetime
import json
import statistics
import sys
from pathlib import Path

if __package__ in (None, ""):   # 直接実行でも harness/ の兄弟モジュールを引けるように
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_usage  # noqa: E402

DAYS = 7
RECENT = 20
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
        parts = [hline_usage.breakdown(i) for i in group]
        out.append({"day": day.isoformat(), "done": len(done), "unconverged": len(group) - len(done), "tokens": tokens,
                    "per_done": tokens // len(done) if done else None, "once": once * 100 // len(done) if done else None,
                    "split": tuple(sum(p[0][k] for p in parts) for k in range(4)), "split_tries": sum(p[1] for p in parts),
                    "split_lack": sum(p[2] for p in parts)})
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


LACK = "内訳なし"


def render_split(items):
    """「### トークンの内訳」の表：日ごとの、内訳のある試行の 4 つの和と読みの割合。内訳のある試行が無い日は「内訳なし」。"""
    table = rows(items)[0]
    lines = ["### トークンの内訳", "", "| 日付 | 入力 | 書き | 読み | 出力 | 読みの割合 | 内訳なしの試行 |", "|:--|--:|--:|--:|--:|--:|--:|"]
    for r in table:
        if r["split_tries"]:
            a, b, c, d = r["split"]
            cols = [a, b, c, d, f"{c * 100 / (a + b + c + d):.1f}%" if a + b + c + d else NONE]
        else:
            cols = [LACK] * 5
        lines.append(f"| {r['day']} | " + " | ".join(map(str, cols)) + f" | {r['split_lack']} |")
    return "\n".join(lines) + "\n"


def ended_at(item):
    """項目の終わった時刻（済みは done_at、未収束は at）。タイムゾーンは UTC に揃えて外す。読めなければ None。"""
    key = {"done": "done_at", "unconverged": "at"}.get(item.get("status"))
    text = str(item.get(key) or "")
    try:
        t = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            t = datetime.datetime.combine(datetime.date.fromisoformat(text[:10]), datetime.time())
        except ValueError:
            return None
    return t.astimezone(datetime.timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _fmt(x):
    return str(int(x)) if x == int(x) else f"{x:.1f}"


def render_recent(items, size=RECENT):
    """直近 size 件（済みか未収束の What のうち、終わった時刻の新しい順）の歩留まりと燃費の 1 行。"""
    stamped = [(ended_at(i), n, i) for n, i in items.items() if i.get("status") in ("done", "unconverged")]
    dated = sorted((s for s in stamped if s[0] is not None), key=lambda s: (s[0], s[1]), reverse=True)[:size]
    undated = len(stamped) - len([s for s in stamped if s[0] is not None])
    target = [s[2] for s in dated]
    n = len(target)
    if not n:
        return "- 直近 0 件: 対象なし\n"
    done = [i for i in target if i["status"] == "done"]
    stuck = n - len(done)
    once = sum(1 for i in done if hline_usage.totals(i)[0] == 1)
    whole = [b for b in map(hline_usage.breakdown, done) if b[1] and not b[2]]
    if whole:
        totals = [sum(b[0]) for b in whole]
        med = f"{_fmt(statistics.median(totals))}（入力 {_fmt(statistics.median(b[0][0] for b in whole))}"
        med += "".join(f"・{name} {_fmt(statistics.median(b[0][k] for b in whole))}" for k, name in ((1, '書き'), (2, '読み'), (3, '出力')))
        med += "）"
    else:
        med = "なし"
    return (f"- 直近 {n} 件（済み {len(done)}・未収束 {stuck}）: 未収束の率 {stuck * 100 / n:.1f}%（{stuck}/{n}）"
            f"／1 回で済んだ率 {once * 100 / n:.1f}%（{once}/{n}）／済み 1 件あたりのトークン（中央値）{med}"
            f"／内訳の無い試行を含む済みの What {len(done) - len(whole)} 件を除く"
            + (f"／時刻の無い項目 {undated} 件は対象外" if undated else "") + "\n")


def load_items(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("items", {})


def section(items):
    return "## 計器\n" + render(items) + "\n" + render_recent(items) + "\n" + render_split(items)


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
    sys.exit(exitcode.normalized(main))
