"""H ライン（harness/hline.py）の統合 PR の自動マージ：承認済みで CI が緑なら H ラインがマージする。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。

見るのは gh pr view の今の head のチェック（ワークフローの名前で見分ける）とラベルだけ。ラベルは付けも外しもしない。
結果は st["auto_merge"] に残す（走行のたびに run_line が消す）。report の人間作業欄が読む。
"""
import json
import re
from pathlib import Path

from hline_base import Infra

RECORDS = Path(__file__).resolve().parent.parent   # bench_regress の --records の既定（リポジトリの根）
SHA = re.compile(r"[0-9a-f]{40}")

LABEL = "ms4:approved"
TESTS, APPROVAL = "harness-tests", "approval"
PASSED = {"SUCCESS"}


def _runs(checks, workflow):
    """そのワークフローの走行のうち、startedAt が最も新しい 1 件（同じなら completedAt が新しいもの）。無ければ空。"""
    found = [c for c in checks if isinstance(c, dict) and c.get("workflowName") == workflow]
    if not found:
        return []
    return [max(found, key=lambda c: (str(c.get("startedAt") or ""), str(c.get("completedAt") or "")))]


def _done(run):
    return run.get("status") == "COMPLETED"


def verdict(info):
    """'merge'（承認済みで両方緑）・'ci_failed'（harness-tests が失敗）・'wait'（それ以外）。"""
    checks = info.get("statusCheckRollup") or []
    tests, approval = _runs(checks, TESTS), _runs(checks, APPROVAL)
    if any(_done(r) and r.get("conclusion") not in PASSED | {"SKIPPED", "NEUTRAL"} for r in tests):
        return "ci_failed"
    green = lambda runs: bool(runs) and all(_done(r) and r.get("conclusion") in PASSED for r in runs)   # noqa: E731
    labels = {x.get("name") for x in info.get("labels") or []}
    return "merge" if LABEL in labels and green(tests) and green(approval) else "wait"


def regress_check(root, head):
    """外側の統合 PR の非劣性の記録（bench/records/regress/<head>.json）を読む。(合格か, 理由, 記録のパス)。"""
    path = Path(root) / "bench" / "records" / "regress" / f"{head}.json"
    if not SHA.fullmatch(head or ""):
        return False, f"head の SHA を読めない（{head!r}）", path
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "非劣性の記録が無い", path
    except (OSError, ValueError):
        return False, "非劣性の記録を読めない", path
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), bool):
        return False, "非劣性の記録の passed が真偽でない", path
    if rec.get("rev") != head:
        return False, f"非劣性の記録の rev が head と違う（記録 {rec.get('rev')!r}）", path
    if not rec["passed"]:
        return False, "非劣性の検査が不合格", path
    return True, "非劣性の検査に合格", path


def _note(st, text):
    st["outer"] = {**(st.get("outer") or {}), "merge_note": text}


def settle(host, cfg, st, url, records=None):
    """開いている統合 PR を確かめる。'merged'・'ci_failed'・'wait' を返す。gh の読み取りの失敗は待ち（次の走行でもう一度確かめる）。
    records を渡す（外側）と、今の head の非劣性の記録が合格のときだけマージする。内側は records=None で今まで通り。"""
    head = None
    try:
        info = host.pr_checks(cfg, url)
        what = verdict(info)
        if records is not None and what == "merge":
            head = info.get("headRefOid")
            ok, why, path = regress_check(records, head)
            if not ok:
                _note(st, f"マージしない: {why}（{path}）")
                return "wait"
            _note(st, f"{why}、マージ待ち（{path}）")
    except Exception:   # noqa: BLE001  読めないときはマージしない
        return "wait"
    if what == "ci_failed":
        st["auto_merge"] = {"kind": "CI_FAILED", "url": url}
        return "ci_failed"
    if what == "merge":
        try:
            if records is None:
                host.merge_pr(cfg, url)
            else:
                host.merge_pr(cfg, url, head)   # head が進んでいたら gh が拒む
        except Exception as e:   # noqa: BLE001
            if records is None and not isinstance(e, Infra):
                raise
            if records is not None:
                _note(st, f"マージしなかった: {' '.join(str(e).split())}")
            st["auto_merge"] = {"kind": "MERGE_FAILED", "url": url, "reason": " ".join(str(e).split())}
            return "wait"
        return "merged"
    return "wait"
