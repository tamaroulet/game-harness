"""H ライン（harness/hline.py）の実装役：入力の組み立てと 1 回の呼び出し。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
from pathlib import Path

import hline_abort
import impacted
import infra_retry
from hline_base import Infra
from hline_budget import capped_args, outcome, turn_cap


def build_prompt(what, feedback=None, symbol_map=None):
    """実装役に渡す入力。What の本文をそのまま渡す（段を 1 つにしたので TaskSpec は作らない）。"""
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
         "次の What を満たす変更を、作業ディレクトリの中だけで行ってください。",
         "- 合格条件を満たすことを、unittest のテストを tests/ に書いて示す（既存の書き方に合わせる）",
         "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）。CLAUDE.md の進捗（anchor・report）と報告の規約は総監督向けで、あなたには適用しない",
         "- テストは走らせない（走らせる権限も無い）。ハーネスが試行の後に影響テストを走らせ、落ちたテストの名前とトレースバックを"
         "次の試行に渡す。既存のテストを壊さない",
         "- テストはモジュールごとに別のプロセスで並列に走る。tests/ に書くテストは、固定の一時ディレクトリ・固定のポート・走る順番に依らせない",
         hline_abort.instructions(),
         *(["", symbol_map] if symbol_map else []),   # 目次は最初の区切りの前に置く（区切りの後は What の本文だけ）
         "", "---", what.strip()]
    if feedback:
        p += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(p) + "\n"


def implement(host, cfg, wt, what, feedback, log):
    """実装役を 1 回呼ぶ。使ったモデルを照合し記録する（model_pin）。TTL 超過・起動の失敗は Infra。ターン数の上限の打ち切りは例外にせず、照合しない（hline_budget.outcome）。"""
    agent = cfg["implementer"]
    shown = "\n\n".join(filter(None, (host.symbolmap.prompt_text(cfg, wt, None, what), impacted.prompt_note())))
    code, out, err = host.proc.run(capped_args(impacted.scoped_agent(agent), host.proc.resolve_cli(agent["cli"]), cap := turn_cap(agent)),
                                   wt, cfg["ttl_seconds"]["implementer"], "実装役", input=host.build_prompt(what, feedback, shown))
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    if code in infra_retry.INFRA_EXIT_CODES:
        raise Infra(f"実装役の CLI が終了コード {code}: {infra_retry.classify_exit(code, err)}")
    return (code, *outcome(agent, out, cap))
