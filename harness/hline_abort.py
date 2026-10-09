"""H ライン（harness/hline.py）の内側の遮断：実装役が断念を宣言したとき・前の試行と同じ差分を出したときに、Gate 1 を呼ばずに試行を打ち切る。
harness.hline・hline_task・hline_prompt を import しない（循環を作らない）。"""
import hashlib
import json
from pathlib import Path

from hline_base import must

MARKER = "H-LINE-ABANDON:"
NO_REASON = "理由の記載なし"


def patch_digest(wt, ttl):
    """追跡外の新しいファイルを含む HEAD からの差分の全文の sha256（16 進）。git add -N は意図の登録だけで、内容は staged にならない。"""
    must(["git", "add", "-A", "-N"], wt, ttl, "git add -N")
    return hashlib.sha256(must(["git", "diff", "HEAD"], wt, ttl, "git diff").encode("utf-8")).hexdigest()


def _lines(text):
    """ログの行。CLI の JSON 出力なら、result の本文の行も続けて見る。"""
    yield from text.splitlines()
    try:
        doc = json.loads(text.split("\n--- stderr ---\n", 1)[0])
    except ValueError:
        return
    if isinstance(doc, dict) and isinstance(doc.get("result"), str):
        yield from doc["result"].splitlines()


def abandon_reason(log):
    """実装役のログの中の、MARKER で始まる最初の行の理由（前後の空白なし）。宣言が無い・ログが読めないときは None。"""
    try:
        text = Path(log).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    for line in _lines(text):
        if line.startswith(MARKER):
            return line[len(MARKER):].strip()
    return None


def instructions():
    """実装役への指示に足す、断念の宣言の書式。"""
    return (f"- What の合格条件を満たせないと判断したときは、回り道をせず、最終の返答に「{MARKER} 理由」を 1 行で書いて止まる（行頭から書く）。\n"
            "  宣言すべき場面の例: 保護されたパス（config/protected_paths.json）を変えないと合格条件を満たせない／"
            "消すべきものを検証のテストや保護されたテストが呼んでいる／What の合格条件どうしが矛盾している。\n"
            "  満たせないと分かった時点で、それ以上の作業をせずにすぐ宣言する。ターンの上限を使い切ってから宣言しない。\n"
            "  宣言した試行は Gate 1 を通さずに止められ、理由は TODO.md に残る。総監督が読んで What を直すので、"
            "原因（どのファイルの何が、どの合格条件とぶつかるか）を書く")


def abort_reason(log, digest, prev):
    """試行を打ち切る理由。断念の宣言が先、次に前の試行と同じ差分（prev が None の初回は見ない）。打ち切らないなら None。"""
    why = abandon_reason(log)
    if why is not None:
        return why or NO_REASON
    if prev is not None and digest == prev:
        return "前の試行と同じ差分でした。直す手がかりが無いまま同じ変更を繰り返したので、残りの試行を行わずに打ち切りました"
    return None


def unconverged_reason(tries):
    """最後の試行の abort の理由。無ければ None。"""
    return (tries[-1].get("abort") or None) if tries else None
