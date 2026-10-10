"""蛹玲･ｵ譏溯ｨ亥勣・唏 繝ｩ繧､繝ｳ縺ｮ繧ｭ繝･繝ｼ・・ueue.json・峨°繧峨∝ｷ･蟒縺ｮ譌･縺斐→縺ｮ謌千ｸｾ・域ｸ医∩ 1 莉ｶ縺ゅ◆繧翫・繝医・繧ｯ繝ｳ・峨ｒ蜃ｺ縺吶・
    python -m harness.northstar --queue <queue.json 縺ｮ繝代せ>

繧ｭ繝･繝ｼ縺縺代ｒ隱ｭ繧・医＞縺ｾ縺ｮ譎ょ綾縺ｫ萓昴ｉ縺ｪ縺・・繧ｭ繝･繝ｼ繧呈嶌縺肴鋤縺医↑縺・ｼ峨り｡ｨ縺ｯ縲√ョ繝ｼ繧ｿ縺ｮ荳ｭ縺ｮ譛蠕後・譌･莉倥°繧蛾■縺｣縺ｦ 7 譌･蛻・・"""
import argparse
import datetime
import json
import sys
from pathlib import Path

if __package__ in (None, ""):   # 逶ｴ謗･螳溯｡後〒繧・harness/ 縺ｮ蜈・ｼ溘Δ繧ｸ繝･繝ｼ繝ｫ繧貞ｼ輔￠繧九ｈ縺・↓
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode, hline_usage  # noqa: E402

DAYS = 7
NONE = "窶・


def date_of(item):
    """鬆・岼縺ｮ譌･莉假ｼ壽ｸ医∩縺ｯ done_at縲∵悴蜿取據縺ｯ at 縺ｮ蜈磯ｭ 10 譁・ｭ暦ｼ域嶌縺九ｌ縺滓律莉倥・縺ｾ縺ｾ・峨りｪｭ繧√↑縺代ｌ縺ｰ None縲・""
    key = {"done": "done_at", "unconverged": "at"}.get(item.get("status"))
    text = item.get(key) if key else None
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def rows(items):
    """(譌･縺斐→縺ｮ陦後・荳隕ｧ, 譌･莉倥・辟｡縺・・岼縺ｮ莉ｶ謨ｰ, 蛻ｩ逕ｨ驥上・辟｡縺・ｩｦ陦後・莉ｶ謨ｰ)縲り｡後・蜿､縺・律縺九ｉ縲・""
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
    lines = ["| 譌･莉・| 貂医∩ | 譛ｪ蜿取據 | 繝医・繧ｯ繝ｳ | 貂医∩ 1 莉ｶ縺ゅ◆繧翫・繝医・繧ｯ繝ｳ | 1 蝗槭〒貂医ｓ縺蜑ｲ蜷・|", "|:--|--:|--:|--:|--:|--:|"]
    for r in table:
        per = NONE if r["per_done"] is None else r["per_done"]
        once = NONE if r["once"] is None else f"{r['once']}%"
        lines.append(f"| {r['day']} | {r['done']} | {r['unconverged']} | {r['tokens']} | {per} | {once} |")
    lines += ["", f"- 譌･莉倥・辟｡縺・・岼: {undated} 莉ｶ・郁｡ｨ縺ｫ蜈･繧後※縺・↑縺・ｼ・, f"- 蛻ｩ逕ｨ驥上・辟｡縺・ｩｦ陦・ {missed} 莉ｶ・医ヨ繝ｼ繧ｯ繝ｳ縺ｫ謨ｰ縺医※縺・↑縺・ｼ・]
    return "\n".join(lines) + "\n"


def load_items(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("items", {})


def section(items):
    return "## 險亥勣\n" + render(items)


def safe_section(items):
    """report.md 逕ｨ縲りｨ亥勣縺御ｾ句､悶〒豁｢縺ｾ縺｣縺ｦ繧ゅ∫ｯ繧・1 陦後・逅・罰縺ｫ譖ｿ縺医※縲∵嶌縺榊・縺励ｒ豁｢繧√↑縺・・""
    try:
        return section(items)
    except Exception as e:   # noqa: BLE001
        reason = " ".join(f"{type(e).__name__}: {e}".split())
        return f"## 險亥勣\n- 險亥勣繧貞・縺帙∪縺帙ｓ縺ｧ縺励◆: {reason}\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.northstar")
    ap.add_argument("--queue", required=True, help="queue.json 縺ｮ繝代せ")
    args = ap.parse_args(argv)
    sys.stdout.write(render(load_items(args.queue)))
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
