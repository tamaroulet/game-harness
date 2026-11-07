"""H ラインの呼び出し口と箱のあいだ（v3 §8 の E4）：門・積む前の全件・進捗の記録の検証を、門の箱で走らせる。

実装役が書いたコード（試験を含む）を箱の外で走らせないための口。実装役の呼び出しは hline_agy.in_box、採点は chain_score.run_grade。
箱そのものの失敗は Infra にする（H ラインが環境の異常として呼び直す。実装役の失敗に数えない）。
"""
import box
from hline_base import Infra

SUITE_TTL = 1800     # 呼び手が時間を渡さないときの上限（config/hline.json の ttl_seconds.gate と同じ）


def gate(cmd, wt, ttl):
    """門の命令を箱で走らせる。(終了コード, 標準出力, 標準エラー)。試験の落ちは普通の結果。"""
    try:
        return box.tests(box.settings(), cmd, wt, ttl)
    except box.BoxError as e:
        raise Infra(str(e)) from e


def suite(modules, wt, timeout=None):
    """変えたパッケージの全件を、既存の fastsuite で箱の中で走らせる（再試行と報告の形が手元の走りと同じになる）。(終了コード, 報告の文)。"""
    code, out, err = gate([box.PYTHON, "-m", "harness.fastsuite", *modules], wt, timeout or SUITE_TTL)
    return code, (out + ("\n" + err if err.strip() else "")).strip()


def verifier(wt, ttl):
    """progress.complete の verify：検証の命令を箱で走らせる（命令 → (終了コード, 出力)）。命令はシェルの文なので、手元の shell=True と同じくシェルに渡す。"""
    def run(command):
        code, out, err = gate(["bash", "-c", command], wt, ttl)
        return code, out + err
    return run
