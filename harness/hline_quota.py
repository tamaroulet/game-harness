"""H ライン（harness/hline.py）の利用枠切れ（429）：実装役の出力からの検出と、再開してよい時刻の記録（queue.json の quota_wait）。

利用枠が尽きた試行は試行に数えず、What を待ちに戻して走行を終える。プロセスの中では眠らない（定期起動が次の走行を呼ぶ）。
次の走行は、再開の時刻の前なら何もせずに終わり、過ぎていれば記録を消して通常どおり走る。
"""
import datetime
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FALLBACK = datetime.timedelta(minutes=60)   # リセットの時刻が読めないときの待ち
HIT = re.compile(r"hit your (?:\w+ )?limit", re.I)
RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:\s*\(([^)]+)\))?", re.I)


def message(out, err):
    """実装役の出力が利用枠切れなら、その文（JSON の result、無ければ標準エラーの行）。違えば None。終了コードは見ない。"""
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        doc = None
    if isinstance(doc, dict):
        text = str(doc.get("result") or "").strip()
        if doc.get("api_error_status") == 429 or HIT.search(text):
            return text or "api_error_status 429"
    return next((line.strip() for line in (err or "").splitlines() if HIT.search(line)), None)


def _zone(name):
    try:
        return ZoneInfo(name.strip()) if name else None
    except (ZoneInfoNotFoundError, ValueError):   # tzdata の無い Windows。手元の時刻帯で読む
        return None


def resume_at(text, now):
    """now（時刻帯つき）より後で、text に書かれたリセットの時刻（例: "resets 10:30pm (Asia/Tokyo)"）。読めなければ now の 60 分後。"""
    m = RESET.search(text or "")
    if not m or not 1 <= int(m[1]) <= 12 or int(m[2] or 0) > 59:
        return now + FALLBACK
    zone = _zone(m[4])
    local = now.astimezone(zone) if zone else now
    at = local.replace(hour=int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0), minute=int(m[2] or 0), second=0, microsecond=0)
    return at if at > local else at + datetime.timedelta(days=1)


def now():
    return datetime.datetime.now().astimezone()


def record(st, name, text, at=None):
    """利用枠切れを状態に残す（再開してよい時刻を含む）。"""
    t = at or now()
    st["quota_wait"] = {"name": name, "reason": text, "at": t.isoformat(timespec="minutes"),
                        "until": resume_at(text, t).isoformat(timespec="minutes")}


def until(st):
    """再開してよい時刻（時刻帯つき）。記録が無い・読めないときは None。"""
    try:
        return datetime.datetime.fromisoformat(st["quota_wait"]["until"])
    except (KeyError, TypeError, ValueError):
        return None


def waiting(st, at=None):
    """再開の時刻の前なら True。過ぎた記録・読めない記録は消して False。"""
    if not st.get("quota_wait"):
        return False
    u = until(st)
    if u is not None and (at or now()) < u:
        return True
    st["quota_wait"] = None
    return False


def shown(st):
    """report に出す再開の時刻（手元の時刻帯）。"""
    u = until(st)
    return u.astimezone().strftime("%Y-%m-%d %H:%M") if u else "不明"
