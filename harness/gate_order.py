"""Gate 1 のテストの実行：タスク個別の検証・影響テストを走らせ、結果を短く刈り込んで返す（H ライン、harness/hline.py の gate が使う）。

**なぜ要るか**: 生ログをそのまま返すと、落ちたテストの名前が長い出力に押し流され、次の試行の入力も膨らむ。
返すのは「見出し・落ちたテストの名前とトレースバック」だけにし、生ログは走行の記録に残す。
"""
import os
import re
from pathlib import Path

import impacted
import proc
from hline_spec import verification_of

MAX_LINES = 30   # 実装役に返す出力の行数の上限（見出しと digest まで。生ログは連結しない）

_FAILURE = re.compile(r"^(?:FAIL|ERROR): [^\n]*|^Traceback \(most recent call last\):.*?(?=^[=-]{20,}[ \t\r]*$|\Z)",
                      re.MULTILINE | re.DOTALL)
_TEST_MODULE = re.compile(r"tests\.(test_\w+)")


def test_modules(paths):
    """変えたパスのうち tests/test_*.py に当たるものを tests.test_xxx の形に直す（重複は除き、現れた順）。"""
    return tuple(dict.fromkeys(filter(None, map(impacted.dotted, paths))))


def present(wt, modules, keep=()):
    """tests.test_xxx の形で、作業ツリーに tests/test_xxx.py が無いものを外す（消したテストを名指しして落ちないように）。
    keep（進捗の検証コマンドの名前）は外さない。tests.test_x.Class.method のような深い名前はファイルの有無で判定できない。"""
    def exists(m):
        t = _TEST_MODULE.fullmatch(m)
        return t is None or (Path(wt) / "tests" / f"{t.group(1)}.py").is_file()
    return tuple(m for m in modules if m in keep or exists(m))


def failure_digest(text):
    """落ちたテストの名前の行（FAIL:・ERROR:）と、トレースバックの塊を、現れた順に改行でつなぐ。"""
    return "\n".join(m.group(0).rstrip() for m in _FAILURE.finditer(text))


def report(command, code, out, err, limit, expected=0):
    """見出しと failure_digest（落ちたテストの名前とトレースバック）だけ。生ログは連結しない。
    digest が空のときに限り、生の出力の末尾を代わりに入れる。全体は MAX_LINES 行・limit 文字以内。"""
    raw = err + out
    digest = failure_digest(raw) or "\n".join(raw.splitlines()[-(MAX_LINES - 1):])   # 見出しの 1 行を残す
    head = f"検証コマンド `{command}` が終了コード {code}（期待 {expected}）"
    return "\n".join(f"{head}\n{digest}".splitlines()[:MAX_LINES])[:limit]


def focused(cfg, wt, paths, task, spec=None, warnings=None, verify=None):
    """影響テスト（変えたパスから引いたテスト）と、タスクがあれば進捗の検証コマンドを走らせる。
    (通ったか, 出力)。走らせるものが無ければ None。
    タスクを宣言しない What は、What の `検証:` のコマンド verify を、タスクの検証コマンドと同じ扱い（期待する終了コード 0）で走らせる。

    検証コマンドが見つからないときは、不合格にせず warnings に積み、影響テストだけで判定する
    （進捗はコードの付随情報で、パッチを捨てる理由にしない）。"""
    v, warn = None, [] if warnings is None else warnings
    if task:
        v = verification_of(wt, task)
        if not v:
            warn.append(f"進捗のタスク {task} の検証コマンドが見つかりません（影響テストだけで判定しました）")
    elif verify:
        v = {"command": verify, "expected_exit_code": 0}
        if (gone := [m for m in impacted.command_modules(verify) if m not in present(wt, [m])]):
            return False, f"検証コマンド `{verify}` が名指しするテストのモジュールが作業ツリーにありません: {', '.join(gone)}"
    changed = test_modules(paths)
    keep = impacted.command_modules(v.get("command")) if isinstance(v, dict) else ()
    modules = present(wt, impacted.test_modules(wt, spec, v, changed, paths), keep)
    always = present(wt, cfg.get("gate_always_modules") or ())   # リポジトリ全体を走査する試験は毎回同じ命令に足す
    modules = tuple(dict.fromkeys((*modules, *always)))
    stage = impacted.stage_command(cfg["gate_command"], modules, v)
    if stage is None:
        return None
    text, cmd, expected = stage
    label = f"検証 {task}" if task else "検証 What" if verify else "Gate 1 個別"
    code, out, err = proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], label, env=dict(os.environ, PYTHONUTF8="1"))
    return code == expected, report(text, code, out, err, cfg["gate_tail_chars"], expected)
