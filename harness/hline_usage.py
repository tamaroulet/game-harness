"""What ごとの実装役の消費（試行の回数・ターン数・トークン）の集計。report.md のキューの注記に使う。"""


def _num(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _measured(try_):
    """試行 1 回の (ターン数, トークンの合計, 出力トークン)。どれかが無い・数値でなければ None。古い記録には usage が無い。"""
    u = try_.get("usage")
    if not isinstance(u, dict):
        return None
    turns = _num(try_.get("turns"))
    turns = _num(u.get("num_turns")) if turns is None else turns
    got = (turns, _num(u.get("total_tokens")), _num(u.get("output_tokens")))
    return None if None in got else got


def totals(item):
    """項目の試行の記録の集計。(試行の回数, ターン数の合計, トークンの合計, 出力トークンの合計, 集計に含めなかった試行の件数)。"""
    tries = [t for t in item.get("tries") or [] if isinstance(t, dict)]
    rows = [m for m in map(_measured, tries) if m]
    return (len(tries), *(sum(r[i] for r in rows) for i in range(3)), len(tries) - len(rows))


def note(item):
    """項目の利用量の注記。試行の記録が 1 件も無ければ空。"""
    n, turns, tokens, out, missed = totals(item)
    if not n:
        return ""
    return f"（試行 {n} 回／ターン {turns}／トークン {tokens}（うち出力 {out}）／利用量なしの試行 {missed} 件）"
