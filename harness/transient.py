"""一時的な失敗（レートリミット・通信の一時的な失敗）の呼び直し（v2r、docs/design/v2r_protocol.md §10）。

**規則**:
- agy の result の status が ERROR で、エラーの先頭の行が `RATE_LIMIT_PATTERNS` のどれかに当たる呼び出しを、一時的な失敗とする
- 一時的な失敗なら、60 秒・120 秒・240 秒の間隔で、同じプロンプトで呼び直す（最大 3 回）。3 回の呼び直しがすべて一時的な
  失敗で終わったら（3 回続けて失敗）、`TransientFailure` を出し、呼び出し側がその繰り返しを止める
- 一時的な失敗の呼び出しの利用量と費用は記録し、費用に数える（払っているので）。**呼び出しの上限（9 回）には数えない**
  （環境の失敗で、実装役の振る舞いではない）
- TTL の打ち切り（rc 124）と、ここに当たらない ERROR は一時的な失敗ではない（通常の再試行の規則で扱う）

**未検証（§13 の U3）**: レートリミットのときに agy が出す文字列は、実例でまだ確かめていない。下の一覧は、Gemini API と
HTTP の一般的な表記（429・RESOURCE_EXHAUSTED・503・UNAVAILABLE）から置いた暫定のもので、U3 を閉じるときに実測で直す。
"""
import re
import time

RATE_LIMIT_PATTERNS = (r"\b429\b", r"RESOURCE_EXHAUSTED", r"rate[ _-]?limit", r"\bquota\b", r"\b503\b",
                       r"\bUNAVAILABLE\b", r"overloaded", r"try again later")
_RATE_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)
WAITS = (60, 120, 240)


class TransientFailure(Exception):
    """一時的な失敗が続いた（最初の呼び出しと、WAITS の回数の呼び直しがすべて一時的な失敗）。retries に全部の記録。"""

    def __init__(self, retries):
        super().__init__(f"一時的な失敗が {len(retries)} 回続きました（最後：{retries[-1]['reason']}）")
        self.retries = retries


def classify(outcome):
    """(一時的な失敗か, 理由)。outcome は agy_stream.outcome の表（無ければ一時的な失敗ではない）。"""
    if not outcome or outcome.get("status") != "ERROR":
        return False, ""
    err = outcome.get("error") or ""
    m = _RATE_LIMIT_RE.search(err)
    return (True, err[:200]) if m else (False, "")


def with_backoff(call, is_transient, waits=WAITS, sleep=None, log=print):
    """call() を呼び、一時的な失敗なら waits の間隔で呼び直す。(結果, 一時的な失敗の記録の列)。

    is_transient(結果) → (一時的な失敗か, 理由)。記録は {"wait": 次の呼び直しまでの秒, "reason", "result"}。
    最後の呼び直しも一時的な失敗なら TransientFailure（記録の最後の wait は None）。
    """
    sleep = sleep or time.sleep
    retries = []
    result = call()
    for wait in waits:
        transient, why = is_transient(result)
        if not transient:
            return result, retries
        retries.append({"wait": wait, "reason": why, "result": result})
        log(f"    一時的な失敗（{why[:80]}）。{wait} 秒待って呼び直します（{len(retries)}/{len(waits)}）")
        sleep(wait)
        result = call()
    transient, why = is_transient(result)
    if transient:
        retries.append({"wait": None, "reason": why, "result": result})
        raise TransientFailure(retries)
    return result, retries
