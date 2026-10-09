"""Gate 1 のテストの実行：タスク個別の検証・影響テストを走らせ、結果を短く刈り込んで返す（H ライン、harness/hline.py の gate が使う）。

**なぜ要るか**: 生ログをそのまま返すと、落ちたテストの名前が長い出力に押し流され、次の試行の入力も膨らむ。
返すのは「見出し・落ちたテストの名前とトレースバック」だけにし、生ログは走行の記録に残す。
"""
import os
import re

import impacted
import proc
from hline_spec import verification_of

MAX_LINES = 30   # 実装役に返す出力の行数の上限（見出しと digest まで。生ログは連結しない）

_FAILURE = re.compile(r"^(?:FAIL|ERROR): [^\n]*|^Traceback \(most recent call last\):.*?(?=^[=-]{20,}[ \t\r]*$|\Z)",
                      re.MULTILINE | re.DOTALL)


def test_modules(paths):
    """変えたパスのうち tests/test_*.py に当たるものを tests.test_xxx の形に直す（重複は除き、現れた順）。"""
    return tuple(dict.fromkeys(filter(None, map(impacted.dotted, paths))))


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


def focused(cfg, wt, paths, task, spec=None):
    """タスク個別の検証（task があれば進捗の検証コマンド、無ければ変えた tests/ のテスト）を走らせる。
    spec があれば、影響テスト（impacted.test_modules）だけを第 1 段で走らせる。(通ったか, 出力)。走らせるものが無ければ None。"""
    v = None
    if task:
        v = verification_of(wt, task)
        if not v:
            return False, f"進捗のタスク {task} の検証コマンドが見つかりません"
    changed = test_modules(paths)
    modules = impacted.test_modules(wt, spec, v, changed) if spec else (() if task else changed)
    stage = impacted.stage_command(cfg["gate_command"], modules, v)
    if stage is None:
        return None
    text, cmd, expected = stage
    label = f"検証 {task}" if task else "Gate 1 個別"
    code, out, err = proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], label, env=dict(os.environ, PYTHONUTF8="1"))
    return code == expected, report(text, code, out, err, cfg["gate_tail_chars"], expected)
