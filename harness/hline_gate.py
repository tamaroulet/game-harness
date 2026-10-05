"""H ライン（harness/hline.py）の門：Gate 1 と、実装役を呼ぶ前の base 検査。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import os
from pathlib import Path


def gate(host, cfg, wt, paths, spec=None, task=None):
    """H ラインの Gate 1。(通ったか, 実装役に返す出力)。TaskSpec があれば編集境界も、タスクがあれば進捗の検証コマンドも見る。"""
    if not paths:
        return False, "作業ツリーに変更がありません。TaskSpec を満たす変更を加えてください。"
    if bad := host.hline_protect.violations(paths):
        return False, host.hline_protect.reason(bad)
    problems = host.boundary_problems(spec, paths, host.diff_counts(cfg, wt)) if spec else []
    problems += host.size_limits.scan(wt, paths, host.size_limits.limits(), host.size_limits.head_source(cfg, wt))
    if problems:
        return False, "\n".join(problems)
    first = host.gate_order.focused(cfg, wt, paths, task)   # 個別の検証が先。落ちたら全件テストは走らせない
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
