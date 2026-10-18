"""H ライン Gate 1 の基盤のパスの検査。一覧は config/foundation_paths.json（paths と milestones）。

**なぜ要るか**: 課題・製品を解く What（工廠の走行のマイルストーン bench など）が、判定に使うハーネスや指示を書き換えてはならない。
基盤の変更は、milestones に挙げた外側のループの What に限る（D-057・D-058）。
一覧は、保護パスと同じく、検査される作業ツリーではなく、走っているハーネスの根のファイルから読む。
"""
import json
from pathlib import Path

from hline_base import ROOT, Infra  # isort: skip（harness/ を import の道に足す）
from hline_spec import matches  # noqa: E402

FOUNDATION_FILE = ROOT / "config" / "foundation_paths.json"


class FoundationError(Infra):
    """一覧が読めない・壊れている・欄が欠けている・形が違う（環境の異常）。"""


def _strings(table, key, path):
    items = table.get(key) if isinstance(table, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
        raise FoundationError(f"{path} の {key} が、空でない文字列の空でない配列ではありません")
    return tuple(items)


def load(path=None):
    """(paths, milestones) を現れた順の tuple で返す。異常は FoundationError。"""
    path = Path(path) if path is not None else FOUNDATION_FILE
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FoundationError(f"基盤のパスの一覧を読めません: {path}: {e}") from e
    return _strings(table, "paths", path), _strings(table, "milestones", path)


def violations(paths, milestone, table=None):
    """基盤のパスに当たる paths の要素を、paths の順で重複なく返す。milestone が None か、許す milestones にあれば空。"""
    if milestone is None:
        return []
    patterns, allowed = load() if table is None else table
    if milestone in allowed:
        return []
    return list(dict.fromkeys(p for p in paths if any(matches(p, q) for q in patterns)))


def reason(bad, milestone):
    return (f"基盤のパスを変えています: {', '.join(bad)}。マイルストーン {milestone} の What は基盤を変えられません"
            "（変えてよいのは外側のループの What だけです）。元に戻してください。")
