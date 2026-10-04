"""Gate 1 の順序：タスク個別の検証を先に走らせ、落ちたら全件テストを走らせずに返す（H ライン、harness/hline.py の gate が使う）。

**なぜ要るか**: 全件テストは長く、出力の末尾だけを返すと、落ちたテストの名前が長い出力に押し流される。個別の検証で早く落とし、
返す出力は「見出し・落ちたテストの名前とトレースバック・生の末尾」の順に、見出しと要約を削らずに組む。
"""
import os
import re
import shlex

import base_whitelist
import proc
from hline_spec import verification_of

_TEST_FILE = re.compile(r"tests/(test_\w+)\.py")
_FAILURE = re.compile(r"^(?:FAIL|ERROR): [^\n]*|^Traceback \(most recent call last\):.*?(?=^[=-]{20,}[ \t\r]*$|\Z)",
                      re.MULTILINE | re.DOTALL)


def test_modules(paths):
    """変えたパスのうち tests/test_*.py に当たるものを tests.test_xxx の形に直す（重複は除き、現れた順）。"""
    found = (_TEST_FILE.fullmatch(p.replace("\\", "/").removeprefix("./")) for p in paths)
    return tuple(dict.fromkeys(f"tests.{m.group(1)}" for m in found if m))


def failure_digest(text):
    """落ちたテストの名前の行（FAIL:・ERROR:）と、トレースバックの塊を、現れた順に改行でつなぐ。"""
    return "\n".join(m.group(0).rstrip() for m in _FAILURE.finditer(text))


def report(command, code, out, err, limit, expected=0):
    """見出し・failure_digest・生の出力の末尾。limit 文字以内で、見出しと digest は生の出力の長さによらず削らない。"""
    raw, digest = err + out, failure_digest(err + out)
    head = f"検証コマンド `{command}` が終了コード {code}（期待 {expected}）\n" + (digest + "\n" if digest else "")
    room = limit - len(head)
    return head[:limit] if room <= 0 else head + raw[-room:]


def focused(cfg, wt, paths, task):
    """タスク個別の検証（task があれば進捗の検証コマンド、無ければ変えた tests/ のテスト）を走らせる。
    (通ったか, 出力)。走らせるものが無ければ None。"""
    if task:
        v = verification_of(wt, task)
        if not v:
            return False, f"進捗のタスク {task} の検証コマンドが見つかりません"
        text, expected, label = v["command"], v["expected_exit_code"], f"検証 {task}"
        cmd = shlex.split(text)
    else:
        cmd, expected, label = base_whitelist.unittest_command(cfg["gate_command"], test_modules(paths)), 0, "Gate 1 個別"
        if cmd is None:
            return None
        text = shlex.join(cmd)
    code, out, err = proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], label, env=dict(os.environ, PYTHONUTF8="1"))
    return code == expected, report(text, code, out, err, cfg["gate_tail_chars"], expected)
