"""Gate 1 の順序：タスク個別の検証を先に走らせ、落ちたら全件テストを走らせずに返す（H ライン、harness/hline.py の gate が使う）。

**なぜ要るか**: 全件テストは長く、出力の末尾だけを返すと、落ちたテストの名前が長い出力に押し流される。個別の検証で早く落とし、
返す出力は「見出し・落ちたテストの名前とトレースバック・生の末尾」の順に、見出しと要約を削らずに組む。
"""
import os
import re

import impacted
import proc
from hline_spec import verification_of

_FAILURE = re.compile(r"^(?:FAIL|ERROR): [^\n]*|^Traceback \(most recent call last\):.*?(?=^[=-]{20,}[ \t\r]*$|\Z)",
                      re.MULTILINE | re.DOTALL)


def test_modules(paths):
    """変えたパスのうち tests/test_*.py に当たるものを tests.test_xxx の形に直す（重複は除き、現れた順）。"""
    return tuple(dict.fromkeys(filter(None, map(impacted.dotted, paths))))


def failure_digest(text):
    """落ちたテストの名前の行（FAIL:・ERROR:）と、トレースバックの塊を、現れた順に改行でつなぐ。"""
    return "\n".join(m.group(0).rstrip() for m in _FAILURE.finditer(text))


def report(command, code, out, err, limit, expected=0):
    """見出し・failure_digest・生の出力の末尾。limit 文字以内で、見出しと digest は生の出力の長さによらず削らない。"""
    raw, digest = err + out, failure_digest(err + out)
    head = f"検証コマンド `{command}` が終了コード {code}（期待 {expected}）\n" + (digest + "\n" if digest else "")
    room = limit - len(head)
    return head[:limit] if room <= 0 else head + raw[-room:]


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
