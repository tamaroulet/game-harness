"""比べの集計（harness/chain_compare.py）が、A の行の stop の tokens を止まりと読まないこと（v3 §15 の 11-12）。
bench_solo は B の段の予算を超えた A の段に tokens と書くが、A の 1 回の呼び出しはトークンでは切らない。A が止まるのは時間切れだけで、
予算を超えて自分で終えた段は「終えたと言った」に数え、落ちの種類は新しい要件か前の要件にする。B の tokens は今までどおり上限の止まり。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import chain_compare  # noqa: E402


def row(stop, gate, new=(2, 2), old=(5, 5)):
    return {"k": 1, "invalid": None, "violations": 0, "stop": stop, "gate": gate, "new": list(new), "old": list(old),
            "cost": {"non_cache": 1000}}


A_OVER = row("tokens", None, old=(3, 5))                # A：予算を超えて自分で終え、前の要件が欠けた
A_TIME = row("time", None, new=(0, 2))                  # A：時間切れ
B_OVER = row("tokens", {"passed": False, "first": False}, new=(1, 2))   # B：予算で止まった
B_OLD = row("done", None)                               # 門の欄が無い古い B の行は solo でない
B_OLD.pop("gate")


class StopLabel(unittest.TestCase):
    def test_a_tokens_stop_is_not_a_stop(self):
        self.assertTrue(chain_compare.solo(A_OVER))
        self.assertFalse(chain_compare.stopped(A_OVER))
        self.assertTrue(chain_compare.claimed(A_OVER))
        self.assertEqual(chain_compare.failure(A_OVER), "old")

    def test_a_time_stop_and_b_tokens_stop_are_stops(self):
        self.assertTrue(chain_compare.stopped(A_TIME))
        self.assertFalse(chain_compare.claimed(A_TIME))
        self.assertEqual(chain_compare.failure(A_TIME), "limit")
        self.assertFalse(chain_compare.solo(B_OVER))
        self.assertTrue(chain_compare.stopped(B_OVER))
        self.assertEqual(chain_compare.failure(B_OVER), "limit")

    def test_old_rows_without_a_gate_are_not_solo(self):
        self.assertFalse(chain_compare.solo(B_OLD))
        self.assertIsNone(chain_compare.claimed(B_OLD))
        self.assertFalse(chain_compare.solo(None))

    def test_health_counts_only_real_stops_and_the_claims(self):
        runs = [{"bases": {"B07": {"rows": {1: A_OVER, 2: A_TIME, 3: B_OVER}}}}]
        doc = chain_compare.health(runs, [("B07", 1), ("B07", 2), ("B07", 3)])
        self.assertEqual(doc["limit"], 2)
        self.assertEqual((doc["claimed"], doc["claimed_broken"]), (1, 1))
        self.assertEqual(doc["failures"]["limit"], 2)
        self.assertEqual(doc["failures"]["old"], 1)


if __name__ == "__main__":
    unittest.main()
