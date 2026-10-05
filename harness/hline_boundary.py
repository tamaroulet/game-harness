"""H ライン Gate 1 の編集境界の自己拡張：変えたモジュールを確かめている既存のテストが落ちたとき、そのテストのファイルを
変えてよいファイルに足す（ハーネスが足す。実装役は足せない）。足せるのは tests/ の test_*.py だけで、変えてはならないファイルと
保護されたパスの判定が常に先。足したファイルは、テストの件数を減らす・skip を増やす変更を shrink_problems が止める。
読み取りだけ（ネットワーク・git の書き込み・モデルは呼ばない）。harness.hline は import しない。"""
import copy
import re
from pathlib import Path

import hline_protect
import symbolmap
from hline_spec import matches

_MODULE = re.compile(r"(?<!\w)tests[./\\](test_\w+)")
_TEST = re.compile(r"^[ \t]*(?:async[ \t]+)?def test_", re.MULTILINE)
_SKIP = re.compile(r"[.@]skip(?:If|Unless|Test)?\b")


def failing_modules(feedback):
    """Gate 1 の出力に現れた、落ちたテストのモジュール名（`tests.test_xxx`。重複なし・現れた順）。"""
    return tuple(dict.fromkeys(f"tests.{m}" for m in _MODULE.findall(feedback or "")))


def _allowed(path, box):
    return not any(matches(path, q) for q in box.get("allowed_files", []) + box.get("forbidden_files", [])) \
        and not hline_protect.violations([path])


def candidates(root, spec, feedback, changed):
    """変えてよいファイルに足すテストのファイル（tests/test_xxx.py。辞書順・重複なし）。落ちた・今回変えた harness/ の .py を
    import している・まだ許可も禁止もされていない・保護されていない、のすべてを満たすものだけ。目次が作れなければ空。"""
    failing = {m.replace(".", "/") + ".py" for m in failing_modules(feedback)}
    touched = [p for p in changed if p.startswith("harness/") and p.endswith(".py")]
    if not failing or not touched:
        return ()
    try:
        modules = symbolmap.build(root)["modules"]
    except Exception:  # noqa: BLE001 - 目次が作れなければ何も足さない
        return ()
    importing = {t for p in touched for t in modules.get(p, {}).get("tests", [])}
    box = spec["edit_boundary"]
    return tuple(sorted(t for t in importing & failing if t not in changed and _allowed(t, box)))


def extend(spec, files):
    """allowed_files に files を（既にあるものを除いて）足した新しい TaskSpec。引数は書き換えない。"""
    out = copy.deepcopy(spec)
    allowed = out["edit_boundary"]["allowed_files"]
    allowed += [f for f in dict.fromkeys(files) if f not in allowed]
    return out


def note(files):
    """足したことを実装役に知らせる 1 文。files が空なら空の文字列。"""
    if not files:
        return ""
    return f"変えてよいファイルに、今回変えたモジュールを確かめている既存のテストを足しました: {', '.join(files)}。これも直してください。"


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def shrink_problems(wt, files, head_source):
    """足したファイルの、テストの件数が HEAD より減った・skip が HEAD より増えた違反の理由。HEAD に無いファイルは違反としない。"""
    out = []
    for f in files:
        head = head_source(f)
        if head is None:
            continue
        now = _read(Path(wt) / f)
        before, after = len(_TEST.findall(head)), len(_TEST.findall(now))
        if after < before:
            out.append(f"{f} のテストの件数が HEAD より減っています（{before} → {after}）。テストを消さずに、元に戻してください。")
        before, after = len(_SKIP.findall(head)), len(_SKIP.findall(now))
        if after > before:
            out.append(f"{f} の skip が HEAD より増えています（{before} → {after}）。skip を足さずに、元に戻してください。")
    return out
