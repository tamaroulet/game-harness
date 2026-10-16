"""H ライン（harness/hline.py）の門：Gate 1。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import os

import base_whitelist
import hline_boundary
import hline_symbols


SYNTAX_HEAD = "構文の誤りがあります"


def syntax_problems(wt, paths):
    """変えた .py（消したものを除く）の構文の誤り。「ファイル:行: 誤りの文」の列。"""
    found = []
    for p in paths:
        full = os.path.join(wt, p)
        if not str(p).endswith(".py") or not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as f:
                compile(f.read(), str(p), "exec", dont_inherit=True)
        except SyntaxError as e:   # IndentationError・TabError を含む
            found.append(f"{str(p).replace(os.sep, '/')}:{e.lineno or 0}: {e.msg}")
        except (ValueError, OSError) as e:   # ヌル文字など
            found.append(f"{str(p).replace(os.sep, '/')}:0: {e}")
    return found


def gate(host,cfg, wt, paths, spec=None, task=None, extended=(), warnings=None, verify=None):
    """H ラインの Gate 1。(通ったか, 実装役に返す出力)。不合格にするのは、変えてはならないパス（hline_protect）と「変更なし」だけ。

    差分の量・編集境界・規模・宣言したシンボルの食い違いは、不合格にせず warnings に積む（実装役には返さない。
    試行の記録に残し、統合 PR を人間が見るときの手がかりにする）。warnings は呼び手が渡す list で、その場で足す。
    extended はハーネスが編集境界に足したテストのファイルで、あれば件数の減少・skip の増加も warnings に入る。"""
    warn = [] if warnings is None else warnings
    if not paths:
        return False, "作業ツリーに変更がありません。TaskSpec を満たす変更を加えてください。"
    if bad := host.hline_protect.violations(paths):
        return False, host.hline_protect.reason(bad)
    if broken := syntax_problems(wt, paths):   # 試験の全件の時間を、構文の誤りに使わない
        return False, SYNTAX_HEAD + "（試験は走らせていません）:\n" + "\n".join(broken)
    warn += host.boundary_problems(spec, paths, host.diff_counts(cfg, wt)) if spec else []
    warn += host.size_limits.scan(wt, paths, host.size_limits.limits(), host.size_limits.head_source(cfg, wt))
    if extended:
        warn += hline_boundary.shrink_problems(wt, extended, host.size_limits.head_source(cfg, wt))
    warn += hline_symbols.declared_problems(spec, wt) if spec else []
    first = host.gate_order.focused(cfg, wt, paths, task, spec, warn, verify)   # 影響テストだけで判定する（全件テストは統合 PR の CI が見る）
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
