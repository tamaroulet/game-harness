"""H ライン（harness/hline.py）の統合 PR の自動マージ：承認済みで CI が緑なら H ラインがマージする。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。

見るのは gh pr view の今の head のチェック（ワークフローの名前で見分ける）とラベルだけ。ラベルは付けも外しもしない。
結果は st["auto_merge"] に残す（走行のたびに run_line が消す）。report の人間作業欄が読む。
"""
from hline_base import Infra

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


def settle(host, cfg, st, url):
    """開いている統合 PR を確かめる。'merged'・'ci_failed'・'wait' を返す。gh の読み取りの失敗は待ち（次の走行でもう一度確かめる）。"""
    try:
        what = verdict(host.pr_checks(cfg, url))
    except Exception:   # noqa: BLE001  読めないときはマージしない
        return "wait"
    if what == "ci_failed":
        st["auto_merge"] = {"kind": "CI_FAILED", "url": url}
        return "ci_failed"
    if what == "merge":
        try:
            host.merge_pr(cfg, url)
        except Infra as e:
            st["auto_merge"] = {"kind": "MERGE_FAILED", "url": url, "reason": " ".join(str(e).split())}
            return "wait"
        return "merged"
    return "wait"
