"""使い方：python experiments/v2/tools/percall.py <run-id> [<条件><タスク><回>（節ごとの字数を出す。例 BT21）]

呼び出しごとの表：条件・タスク・回・プロンプトの字数・最初の手番の思考と秒・思考の合計・出力・入力・キャッシュ読み・費用の内訳。

本文は出さない。プロンプトは字数と、節の見出しごとの字数だけ。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, "harness")
import agy_stream  # noqa: E402

P_IN, P_OUT, P_CACHE = 0.75e-6, 3.75e-6, 0.075e-6
run = sys.argv[1]
root = Path("C:/src/.local/out/ab") / run
rows = []
for cond in "AB":
    d = root / cond
    files = sorted(d.glob("T*_a*.implementer.log")) if cond == "A" else sorted(d.rglob("implementer_attempt_*.log"))
    for f in files:
        t = f.read_text(encoding="utf-8")
        if cond == "A":
            o, prompt = t[t.index("# stdout\n") + 9:t.index("\n\n# stderr")], t[len("# prompt\n"):t.index("\n\n# stdout")]
            task, call = f.name.split("_a")[0], f.name.split("_a")[1].split(".")[0]
        else:
            o, prompt = t[t.index("=== stdout ===\n") + 15:t.index("\n\n=== stderr ===")], t[t.index("=== プロンプト ===\n") + 15:t.index("\n\n=== stdout ===")]
            task, call = f.parent.name, f.stem.replace("implementer_attempt_", "")
        p = agy_stream.parse(o)
        u = p["usage"]
        steps = [s for s in p["steps"] if s.get("usage")]
        first = steps[0] if steps else {}
        fu = first.get("usage") or {}
        cost_in, cost_out, cost_c = (u["input_tokens"] or 0) * P_IN, (u["output_tokens"] or 0) * P_OUT, (u["cache_read_tokens"] or 0) * P_CACHE
        heads = {}
        cur = "(head)"
        for line in prompt.splitlines():
            if line.startswith("## ") or line.startswith("# "):
                cur = line[:30]
            heads[cur] = heads.get(cur, 0) + len(line) + 1
        rows.append((cond, task, call, len(prompt), fu.get("thinking_tokens"), first.get("seconds"), u["thinking_tokens"],
                     u["output_tokens"], u["input_tokens"], u["cache_read_tokens"], round(cost_in, 4), round(cost_out, 4),
                     round(cost_c, 4), len(steps), heads))
print("cond task call prompt_chars first_think first_s think out in cache $in $out $cache model_steps")
for r in rows:
    print(*r[:14])
for cond in "AB":
    rs = [r for r in rows if r[0] == cond]
    print(cond, "calls", len(rs), "$in", round(sum(r[10] for r in rs), 4), "$out", round(sum(r[11] for r in rs), 4),
          "$cache", round(sum(r[12] for r in rs), 4), "think", sum(r[6] or 0 for r in rs), "out", sum(r[7] or 0 for r in rs),
          "cache", sum(r[9] or 0 for r in rs), "in", sum(r[8] or 0 for r in rs))
if len(sys.argv) > 2:
    for r in rows:
        if r[0] + r[1] + r[2] == sys.argv[2]:
            for k, v in r[14].items():
                print("  section", repr(k), v)
