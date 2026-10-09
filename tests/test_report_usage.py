"""report.md のキューに出す、What ごとの試行の回数と利用量の検査。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline  # noqa: E402
import hline_report as hr  # noqa: E402
import hline_usage  # noqa: E402

CFG = hline.load_config()


def usage(total, out):
    return {"format": "claude", "total_tokens": total, "output_tokens": out}


def attempt(n, u, turns):
    return {"run": 0, "attempt": n, "cli_exit": 0, "models": ["m"], "gate": True, "usage": u, "turns": turns}


def item(status, tries, **extra):
    return {"status": status, "title": "題", "milestone": "B7.4", "task": None, "deps": [], "tid": "t1", "tries": tries, **extra}


def section(items):
    return hr.h_section(CFG, {"items": items, "awaiting_pr": None, "skipped": []})


class Totals(unittest.TestCase):
    def test_sums_the_measured_tries_and_counts_the_rest(self):
        tries = [attempt(1, usage(100, 10), 3), attempt(2, usage(200, 20), 4), attempt(3, usage(None, 5), 2),
                 attempt(4, None, 1), attempt(5, usage("x", 1), 1), {"run": 0, "attempt": 6}]
        self.assertEqual(hline_usage.totals({"tries": tries}), (6, 7, 300, 30, 4))

    def test_no_tries_gives_no_note(self):
        self.assertEqual((hline_usage.note({}), hline_usage.note({"tries": []})), ("", ""))


class Report(unittest.TestCase):
    def test_done_and_unconverged_items_show_the_usage(self):
        tries = [attempt(1, usage(100, 10), 3), attempt(2, None, None)]
        text = section({"010-a": item("done", tries), "020-b": item("unconverged", tries, reason="打ち切り")})
        for name in ("010-a", "020-b"):
            line = next(x for x in text.splitlines() if name in x)
            for s in ("試行 2 回", "ターン 3", "トークン 100", "うち出力 10", "利用量なしの試行 1 件"):
                self.assertIn(s, line)
        self.assertIn("020-b（打ち切り）", text)

    def test_an_item_without_tries_has_no_usage_note_and_warnings_still_show(self):
        text = section({"010-a": item("done", []), "020-b": item("done", [dict(attempt(1, usage(1, 1), 1), warnings=["w"])])})
        self.assertNotIn("010-a（", text)
        self.assertIn("020-b（警告 1 件）（試行 1 回", text)


if __name__ == "__main__":
    unittest.main()
