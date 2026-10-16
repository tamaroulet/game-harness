"""H ライン Gate 1 の「変えてはならないパス」の検査。一覧は config/protected_paths.json（データだけ）。

**なぜ要るか**: 実装役が自分の作業ツリーで一覧や守りのテストを弱めても、判定が変わってはならない。
一覧は、検査される作業ツリーではなく、走っているハーネスの根のファイルから読む。
"""
import json
from pathlib import Path

from hline_base import ROOT, Infra  # isort: skip（harness/ を import の道に足す）
from hline_spec import matches  # noqa: E402

PROTECTED_FILE = ROOT / "config" / "protected_paths.json"
REQUIRED = ("config/protected_paths.json", "docs/progress.yaml", ".claude/",
            "tests/test_size_limits.py", "tests/test_no_inline_review.py", "tests/test_protected_paths.py")


class ProtectError(Infra):
    """一覧が読めない・壊れている・必須の項目が欠けている（環境の異常）。"""


def load(path=None):
    """設定ファイルの "protected_paths" を現れた順の tuple で返す。異常は ProtectError。"""
    path = Path(path) if path is not None else PROTECTED_FILE
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProtectError(f"保護パスの一覧を読めません: {path}: {e}") from e
    items = table.get("protected_paths") if isinstance(table, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
        raise ProtectError(f"{path} の protected_paths が、空でない文字列の配列ではありません")
    missing = [r for r in REQUIRED if r not in items]
    if missing:
        raise ProtectError(f"{path} の protected_paths に必須の項目がありません: {', '.join(missing)}")
    return tuple(items)


def violations(paths, patterns=None):
    """patterns のいずれかに当たる paths の要素を、paths の順で重複なく返す。"""
    patterns = load() if patterns is None else patterns
    return list(dict.fromkeys(p for p in paths if any(matches(p, q) for q in patterns)))


def reason(bad):
    return f"変えてはならないパスを変えています: {', '.join(bad)}。元に戻してください。"
