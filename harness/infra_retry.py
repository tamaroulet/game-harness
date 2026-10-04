"""インフラの例外（CLI の起動の失敗・タイムアウト・git や gh・認証の一時的な失敗）の呼び直し。標準ライブラリだけを使う。"""
import re
import time

SLEEP = time.sleep
INFRA_EXIT_CODES = {124: "TTL 超過（タイムアウト）", 127: "CLI の起動の失敗（コマンドが見つかりません）"}
TRANSIENT = [
    ("ネットワークや git の一時的な失敗",
     r"could not resolve host|connection reset|connection timed out|the remote end hung up|early eof|index\.lock"),
    ("サーバーの一時的な失敗", r"http 5xx|\bhttp(?:/\d(?:\.\d)?)?\s*:?\s*5\d\d\b|returned error: 5\d\d|service unavailable"),
    ("認証・レート制限", r"authentication failed|bad credentials|gh auth login|rate limit"),
]


class InfraExhausted(Exception):
    def __init__(self, records):
        self.records = records
        super().__init__(f"インフラの例外が {len(records)} 回続きました（最後の理由: {records[-1]['reason']}）")


def classify_exit(code, err):
    """インフラの例外ならその理由（err の末尾を添える）。そうでなければ None。"""
    tail = f": {err[-200:]}" if err else ""
    if code in INFRA_EXIT_CODES:
        return INFRA_EXIT_CODES[code] + tail
    for label, pattern in TRANSIENT if code != 0 else []:
        if re.search(pattern, (err or "").lower()):
            return label + tail
    return None


def wait_for(waits, k):
    return waits[min(max(k, 1), len(waits)) - 1] if waits else 0   # 1 始まりの k 回目。尽きたら最後の値、空なら 0


def retry(attempt, fault, limit, waits, sleep=None, log=print):
    """attempt(1) を呼び、fault が出たら待って attempt(2)… と limit 回まで呼び直す。(戻り値, 失敗の記録の列)。"""
    records = []
    for n in range(1, limit + 2):
        try:
            return attempt(n), records
        except fault as e:
            records.append(rec := {"attempt": n, "reason": str(e), "wait": None})
            if n > limit:
                log(f"インフラの例外（{n}/{limit + 1} 回目）: {rec['reason']}。上限に達しました")
                break
            rec["wait"] = wait_for(waits, n)
            log(f"インフラの例外（呼び直し {n}/{limit}）: {rec['reason']}。{rec['wait']} 秒待ちます")
            if rec["wait"]:
                (SLEEP if sleep is None else sleep)(rec["wait"])
    raise InfraExhausted(records)
