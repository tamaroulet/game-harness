"""H ライン（harness/hline.py）の門：Gate 1 と、実装役を呼ぶ前の base 検査。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import os
from pathlib import Path

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
    first = host.gate_order.focused(cfg, wt, paths, task, spec)   # 影響テストが先。落ちたら全件テストは走らせない
    if first and not first[0]:
        return first
    env = dict(os.environ, PYTHONUTF8="1")
    code, out, err = host.proc.run(cfg["gate_command"], wt, cfg["ttl_seconds"]["gate"], "Gate 1", env=env)
    return code == 0, host.gate_order.report(" ".join(cfg["gate_command"]), code, out, err, cfg["gate_tail_chars"])


def base_check(host, cfg, wt):
    """実装役を呼ぶ前の検査。合格済みのタスクのテストだけを走らせる。(通ったか, 出力の末尾)。走らせるものが無ければ通ったとする。"""
    path = Path(wt) / "docs" / "progress.yaml"
    modules = host.base_whitelist.completed_modules(host.progress.load(path), "game-harness") if path.is_file() else ()
    cmd = host.base_whitelist.unittest_command(cfg["gate_command"], modules)
    if cmd is None:
        return True, ""
    code, out, err = host.proc.run(cmd, wt, cfg["ttl_seconds"]["gate"], "Base 検査", env=dict(os.environ, PYTHONUTF8="1"))
    return code == 0, (err + out)[-cfg["gate_tail_chars"]:]
