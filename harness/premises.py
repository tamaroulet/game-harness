"""前提の台帳（ADR-004 の K-15、W-123）：いまの方向が依っている前提を、根拠の種類・確かめた日・期限とともに持つ。

    python -m harness.premises check [--strict] [--today YYYY-MM-DD]

**なぜ要るか**: 方向を決めた前提がどこにも書かれていなければ、点検もされない。S2-3（Gate 2 の廃止）の根拠は出典の無い
「外部知見」で、代わりの非同期の GC は作られず、誰もそれを確かめ直さなかった。ここで前提を 1 件ずつ持ち、期限で確かめ直させる。

根拠の種類：research（調べものの記録の主張の ID、R-YYYYMMDD-<名前>#C<n>）・measured（実測の記録のパス）・decision（決めた者と日付）・
assumption（根拠なし）。終了コード：0 = 形が正しい（期限切れは表示だけ）、1 = 形の誤り、または --strict で期限切れがある。
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "docs" / "design" / "premises.yaml"
KINDS = ("research", "measured", "decision", "assumption")
PREMISE_ID = re.compile(r"^P-\d{3}$")
CLAIM_REF = re.compile(r"^R-\d{8}-[a-z0-9-]+#C\d+$")


def load(path=PATH):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _date(value):
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


def problems(state):
    """形の誤りの列（空なら合格）。"""
    items = state.get("premises")
    if not isinstance(items, list) or not items:
        return ["premises が空でない列でない"]
    out, seen = [], set()
    for p in items:
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or not PREMISE_ID.match(pid):
            out.append(f"前提の id {pid!r} が P-nnn の形でない")
            continue
        if pid in seen:
            out.append(f"{pid} が重複している")
        seen.add(pid)
        if not isinstance(p.get("text"), str) or not p["text"].strip():
            out.append(f"{pid} に text が無い")
        kind, basis = p.get("kind"), p.get("basis")
        if kind not in KINDS:
            out.append(f"{pid} の kind {kind!r} が {KINDS} のどれでもない")
        if not isinstance(basis, list) or not all(isinstance(b, str) and b.strip() for b in basis):
            out.append(f"{pid} の basis が文字列の列でない")
        elif kind == "research" and not basis:
            out.append(f"{pid} は research なのに basis が空")
        elif kind == "research" and (bad := [b for b in basis if not CLAIM_REF.match(b)]):
            out.append(f"{pid} の basis {bad} が主張の ID（R-YYYYMMDD-名前#Cn）でない")
        checked, due = _date(p.get("checked_at")), _date(p.get("recheck_by"))
        if checked is None or due is None:
            out.append(f"{pid} の checked_at・recheck_by が日付でない")
        elif due < checked:
            out.append(f"{pid} の recheck_by が checked_at より前")
        if not isinstance(p.get("used_by"), list) or not p["used_by"]:
            out.append(f"{pid} の used_by（依っている ADR の行・タスク）が空")
    return out


def expired(state, today):
    """期限（recheck_by）を過ぎた前提の id の列。"""
    return [p["id"] for p in state.get("premises") or []
            if isinstance(p, dict) and (due := _date(p.get("recheck_by"))) is not None and due < today]


def main(argv=None):
    ap = argparse.ArgumentParser(description="前提の台帳の検査")
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--strict", action="store_true", help="期限切れがあれば終了コード 1")
    ap.add_argument("--today", help="今日の日付（試験用）")
    ap.add_argument("--path", default=str(PATH))
    args = ap.parse_args(argv)
    state = load(args.path)
    today = _date(args.today) if args.today else datetime.date.today()
    bad, old = problems(state), expired(state, today)
    for b in bad:
        print(f"NG: {b}")
    for pid in old:
        print(f"期限切れ: {pid}（確かめ直す）")
    print("合格" if not bad else f"不合格（{len(bad)} 件）")
    return 1 if bad or (args.strict and old) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
