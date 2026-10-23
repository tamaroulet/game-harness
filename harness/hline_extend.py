"""H ライン（harness/hline.py）の自動の延長：試験が最後まで走って通った数が増えている未収束の What を、1 回だけ待ちに戻す。

**なぜ要るか**: 前進の無い試行（構文の誤り・収集の失敗・時間切れ・途中の落ち）に続けても同じ失敗を繰り返すだけ。
通った数が増えている What は、残した差分（hline_resume）を載せて続ければ収束に近づく。
harness.hline・hline_task を import しない（循環を作らない）。
"""
import re

import failure_classes
import hline_resume

RAN = re.compile(r"^Ran (\d+) tests? ", re.MULTILINE)
SUMMARY = re.compile(r"^(?:OK|FAILED) \((.*)\)[ \t\r]*$", re.MULTILINE)
COUNT = re.compile(r"(failures|errors|skipped)=(\d+)")
BROKEN = ("Failed to import test module", "_FailedTest", "SyntaxError", "IndentationError", "TabError")   # 収集・import・構文の誤り
EXTENDABLE = ("test", "turns", "full_suite")   # turns は、ターン切れで分ける前なら test だった落ち


def test_counts(rec, text):
    """試行の記録 rec と Gate 1 の出力 text から、検証コマンドの試験の結果。
    {"tests_complete": 最後まで走ったか, "tests_passed": 通った数, "tests_failed": 落ちた数}。最後まで走っていなければ数は None。"""
    text = text or ""
    ran = RAN.findall(text)
    if (rec.get("timeout") or rec.get("abort") or not ran or text.startswith(failure_classes.SYNTAX_HEAD)
            or any(m in text for m in failure_classes.TIMEOUT_MARKS) or any(m in text for m in BROKEN)):
        return {"tests_complete": False, "tests_passed": None, "tests_failed": None}
    counts = {k: 0 for k in ("failures", "errors", "skipped")}
    for m in SUMMARY.finditer(text):
        for key, n in COUNT.findall(m.group(1)):
            counts[key] = int(n)   # 最後の集計の行が効く
    failed = counts["failures"] + counts["errors"]
    return {"tests_complete": True, "tests_passed": max(int(ran[-1]) - failed - counts["skipped"], 0), "tests_failed": failed}


def decide(cfg, name, item):
    """未収束になった item を延長するなら {"passed": 最後の通った数, "failed": 落ちた数, "previous_passed": 前の通った数}、しないなら None。"""
    tries = [t for t in item.get("tries") or [] if isinstance(t, dict)]
    if item.get("extended") or len(tries) < 2:
        return None
    last, prev = tries[-1], tries[-2]
    if not (hline_resume.material(cfg, name)["kept"] and last.get("tests_complete") and prev.get("tests_complete")):
        return None
    if (last["tests_passed"] <= prev["tests_passed"] or not last["tests_failed"]
            or (last.get("failure") or {}).get("class") not in EXTENDABLE):
        return None
    return {"passed": last["tests_passed"], "failed": last["tests_failed"], "previous_passed": prev["tests_passed"]}


def extend(item, got):
    """item を待ちに戻す（置き直したのと同じ：次の処理が残した差分を載せる）。延長の記録を item と最後の試行に残す。"""
    item["extended"] = got
    item["tries"][-1]["extended"] = got
    item.update(status="waiting", replaced=True)
    item.pop("reason", None)
    item.pop("failure_class", None)
    item.pop("at", None)


def note(item):
    """report.md のキューの行の注記。延長していなければ空。"""
    got = item.get("extended")
    if not got:
        return ""
    return f"（自動で 1 回延長: 通った {got['passed']}・落ちた {got['failed']}）"
