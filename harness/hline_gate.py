"""H ライン（harness/hline.py）の門：Gate 1。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import os

import base_whitelist
import hline_boundary
import hline_symbols


def gate(host, cfg, wt, paths, spec=None, task=None, extended=(), warnings=None):
    """H ラインの Gate 1。(通ったか, 実装役に返す出力)。不合格にするのは、変えてはならないパス（hline_protect）と「変更なし」だけ。

    差分の量・編集境界・規模・宣言したシンボルの食い違いは、不合格にせず warnings に積む（実装役には返さない。
    試行の記録に残し、統合 PR を人間が見るときの手がかりにする）。warnings は呼び手が渡す list で、その場で足す。
    extended はハーネスが編集境界に足したテストのファイルで、あれば件数の減少・skip の増加も warnings に入る。"""
    warn = [] if warnings is None else warnings
    if not paths:
        return False, "作業ツリーに変更がありません。TaskSpec を満たす変更を加えてください。"
    if bad := host.hline_protect.violations(paths):
        return False, host.hline_protect.reason(bad)
    warn += host.boundary_problems(spec, paths, host.diff_counts(cfg, wt)) if spec else []
    warn += host.size_limits.scan(wt, paths, host.size_limits.limits(), host.size_limits.head_source(cfg, wt))
    if extended:
        warn += hline_boundary.shrink_problems(wt, extended, host.size_limits.head_source(cfg, wt))
    warn += hline_symbols.declared_problems(spec, wt) if spec else []
    first = host.gate_order.focused(cfg, wt, paths, task, spec)   # 影響テストだけで判定する（全件テストは統合 PR の CI が見る）
    if first is not None:
        return first
    return canary(host, cfg, wt)


def canary(host, cfg, wt):
    """影響テストに走らせるものが無かったときだけ走らせる、設定の canary_modules。(通ったか, 実装役に返す出力)。"""
    cmd = base_whitelist.unittest_command(cfg["gate_command"], cfg["canary_modules"])
    if cmd is None:
        return True, "影響テストもカナリアも走らせるものがありません"
    code, out, err = host.proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], "Gate 1", env=dict(os.environ, PYTHONUTF8="1"))
    return code == 0, host.gate_order.report(" ".join(cmd), code, out, err, cfg["gate_tail_chars"])
