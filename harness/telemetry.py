"""テレメトリ（runs.jsonl に載せる実測値）の読み取りと計算。

判定には使わない。記録だけ。ここで例外を出して門やマージを止めない。

**取れない値は 0 にしない。** 値が None のときは、同じ階層に `<key>_null_reason` を必ず置く。
0 と「不明」を混ぜると、MS3 の欠陥 1（`0 == 0` で静かに緑）と同じ型の事故になる。

指標の定義は docs/design/telemetry.md にある。
"""
import hashlib
import json
from pathlib import Path

import fileops

SCHEMA = 1

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
              "total_tokens", "cost_usd", "num_turns")

# CLI ごとの `--output-format json` の形（2026-09-17 に 1 回ずつ実測した）。
# None はその CLI が報告しない値。SUM は、報告された 4 つの和として組み立てる。
SUM = "sum"
USAGE_FORMATS = {
    # {"response", "num_turns", "usage": {input_tokens, output_tokens, thinking_tokens,
    #                                     cache_read_tokens, total_tokens}}  コストは無い
    "agy": {
        "input_tokens": ("usage", "input_tokens"),
        "output_tokens": ("usage", "output_tokens"),
        "cache_read_tokens": ("usage", "cache_read_tokens"),
        "cache_creation_tokens": None,
        "total_tokens": ("usage", "total_tokens"),
        "cost_usd": None,
        "num_turns": ("num_turns",),
    },
    # {"result", "num_turns", "total_cost_usd", "usage": {input_tokens, output_tokens,
    #   cache_creation_input_tokens, cache_read_input_tokens, ...}}
    "claude": {
        "input_tokens": ("usage", "input_tokens"),
        "output_tokens": ("usage", "output_tokens"),
        "cache_read_tokens": ("usage", "cache_read_input_tokens"),
        "cache_creation_tokens": ("usage", "cache_creation_input_tokens"),
        "total_tokens": SUM,
        "cost_usd": ("total_cost_usd",),
        "num_turns": ("num_turns",),
    },
}
SUM_OF = ("input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens")


# ============================================================ 基本

def put(d, key, value, reason):
    """d[key] = value。None なら理由も置く。"""
    d[key] = value
    if value is None:
        d[key + "_null_reason"] = reason
    return d


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path, data):
    """一時ファイルに書いてから置き換える。途中で死んでも壊れた JSON を残さない。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    fileops.replace(tmp, path)


def read(path):
    """(データ, None) か (None, 理由)。"""
    path = Path(path)
    if not path.exists():
        return None, "テレメトリのファイルが無い（この道具は書かないか、書く前に終了した）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"テレメトリを読めない: {e}"[:200]
    if not isinstance(data, dict):
        return None, "テレメトリがオブジェクトではない"
    return data, None


def _number(v, allow_float):
    ok = isinstance(v, int) or (allow_float and isinstance(v, float))
    return v if ok and not isinstance(v, bool) else None


def _dig(doc, path):
    cur = doc
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


# ============================================================ CLI の利用量

def usage_unknown(reason, fmt=None):
    """利用量がまったく分からないときの記録。すべて None ＋同じ理由。"""
    out = {"format": fmt}
    for k in USAGE_KEYS:
        put(out, k, None, reason)
    return out


def cli_usage(stdout, fmt):
    """CLI の JSON 出力から利用量を取り出す。取れない値は None ＋理由。"""
    out = {"format": fmt}

    def all_null(reason):
        return usage_unknown(reason, fmt)

    spec = USAGE_FORMATS.get(fmt)
    if spec is None:
        return all_null(f"利用量の形式 {fmt!r} を知らない")
    try:
        doc = json.loads(stdout or "")
    except ValueError:
        return all_null("CLI の出力が JSON ではない")
    if not isinstance(doc, dict):
        return all_null("CLI の出力が JSON オブジェクトではない")

    for k in USAGE_KEYS:
        path = spec[k]
        if path is None:
            put(out, k, None, f"{fmt} はこの値を報告しない")
        elif path != SUM:
            raw = _dig(doc, path)
            put(out, k, _number(raw, allow_float=(k == "cost_usd")),
                f"{'.'.join(path)} が無いか数値ではない: {raw!r}"[:160])
    if spec["total_tokens"] == SUM:
        parts = [out[k] for k in SUM_OF]
        missing = [k for k, v in zip(SUM_OF, parts) if v is None]
        put(out, "total_tokens", None if missing else sum(parts),
            "和の要素が不明: " + ", ".join(missing))
    return out


def response_text(stdout, key):
    """CLI の JSON 出力から本文を取り出す。(本文, None) か (None, 理由)。"""
    try:
        doc = json.loads(stdout or "")
    except ValueError:
        return None, "CLI の出力が JSON ではない"
    v = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(v, str):
        return None, f"CLI の出力に文字列の {key} が無い"
    return v, None


# ============================================================ 試行の指標

def added_lines(diff_text):
    """diff の追加行の集合。前後の空白を除き、空行とファイル見出し（+++）は捨てる。"""
    lines = set()
    for line in (diff_text or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            s = line[1:].strip()
            if s:
                lines.add(s)
    return lines


def retry_entropy(line_sets):
    """連続する 2 試行の追加行の集合の非類似度（1 − Jaccard）の平均。(値, 理由)。

    名前は entropy だが Shannon エントロピーではない（docs/design/telemetry.md）。
    0 = 同じ差分の繰り返し（堂々巡り）、1 = 全面的に別の差分。
    """
    if len(line_sets) < 2:
        return None, "差分のある試行が 2 回未満"
    dist = []
    for a, b in zip(line_sets, line_sets[1:]):
        union = a | b
        dist.append(0.0 if not union else 1 - len(a & b) / len(union))
    return round(sum(dist) / len(dist), 4), None


def same_failure_repeats(attempts):
    """連続する不合格の試行で、落ちた門と理由のハッシュが同じだった回数。"""
    fails = [a for a in attempts if a.get("verdict") != "SUCCESS"]
    return sum(1 for a, b in zip(fails, fails[1:])
               if (a.get("stage"), a.get("reason_sha256")) == (b.get("stage"), b.get("reason_sha256")))


def p2p_violation_rate(attempts):
    """受入判定まで到達した試行のうち、P2P 破壊が 1 件以上あった試行の割合（%）。(値, 判定数, 理由)。"""
    judged = [a for a in attempts if a.get("p2p_broken") is not None]
    if not judged:
        return None, 0, "受入判定まで到達した試行が無い"
    bad = sum(1 for a in judged if a["p2p_broken"] > 0)
    return round(100.0 * bad / len(judged), 2), len(judged), None


def summarize_attempts(tel, line_sets):
    """tel["attempts"] から試行の指標を計算して tel に入れる。"""
    attempts = tel.get("attempts", [])
    rate, judged, why = p2p_violation_rate(attempts)
    put(tel, "p2p_violation_rate", rate, why)
    tel["attempts_judged"] = judged
    ent, why = retry_entropy(line_sets)
    put(tel, "retry_entropy", ent, why)
    tel["retry_same_failure_repeats"] = same_failure_repeats(attempts)


# ============================================================ Issue 単位の指標

def tokens_total(records):
    """Issue の全記録（不合格の回を含む）の、分解役と実装役の total_tokens の和。(値, 理由)。

    1 つでも不明があれば全体を不明にする（少なく数えない）。監査役は数えない（定義の範囲外）。
    """
    total, calls = 0, 0
    for rec in records:
        for step in rec.get("steps", []):
            if step.get("name") not in ("decompose", "pipeline"):
                continue
            tel = step.get("telemetry")
            if tel is None:
                return None, f"{step.get('name')} のテレメトリが無い: {step.get('telemetry_null_reason')}"
            if step["name"] == "decompose":
                usages = [tel.get("usage")]
            else:
                usages = [(a.get("implementer") or {}).get("usage") for a in tel.get("attempts", [])]
            for u in usages:
                v = (u or {}).get("total_tokens")
                if v is None:
                    return None, (f"{step['name']} の total_tokens が不明: "
                                  f"{(u or {}).get('total_tokens_null_reason', '利用量の記録が無い')}")
                total += v
                calls += 1
    if calls == 0:
        return None, "AI の呼び出しの記録が無い"
    return total, None


def token_to_accepted_loc(tokens, tokens_reason, loc, loc_reason):
    if tokens is None:
        return None, tokens_reason
    if loc is None:
        return None, loc_reason
    if loc == 0:
        return None, "マージされた実装の追加行が 0"
    return round(tokens / loc, 2), None
