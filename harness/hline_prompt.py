"""H ライン（harness/hline.py）の実装役：入力の組み立てと 1 回の呼び出し。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。"""
import json
from pathlib import Path

import infra_retry
from hline_base import Infra
from hline_budget import capped_args, outcome, turn_cap


def build_prompt(spec, feedback=None, symbol_map=None):
    """実装役に渡す入力。TaskSpec（JSON）だけで、What の本文（自然言語の背景）は渡さない。"""
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
         "次の TaskSpec（JSON）を満たす変更を、作業ディレクトリの中だけで行ってください。",
         "- edit_boundary の allowed_files の外と forbidden_files は変えない。差分は max_diff_lines 行以内",
         "- contracts と test_oracle を満たすことを、unittest のテストを tests/ に書いて示す（既存の書き方に合わせる）",
         "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）。CLAUDE.md の進捗（anchor・report）と報告の規約は総監督向けで、あなたには適用しない",
         "- タスク個別の検証（test_oracle の verification_command。無ければ自分が書いた tests/ のテスト）を手元で走らせて通す。"
         "全件テストはハーネスが走らせるので、自分では走らせない。既存のテストを壊さない",
         "- 全件テストはモジュールごとに別のプロセスで並列に走る。tests/ に書くテストは、固定の一時ディレクトリ・固定のポート・走る順番に依らせない",
         *(["", symbol_map] if symbol_map else []),   # 目次は最初の区切りの前に置く（区切りの後は TaskSpec の JSON だけ）
         "", "---", json.dumps(spec, ensure_ascii=False, indent=2)]
    if feedback:
        p += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(p) + "\n"


def implement(host, cfg, wt, spec, feedback, log):
    """実装役を 1 回呼ぶ。使ったモデルを照合し記録する（model_pin）。TTL 超過・起動の失敗は Infra。ターン数の上限の打ち切りは例外にせず、照合しない（hline_budget.outcome）。"""
    agent = cfg["implementer"]
    code, out, err = host.proc.run(capped_args(agent, host.proc.resolve_cli(agent["cli"]), cap := turn_cap(agent)), wt,
                                   cfg["ttl_seconds"]["implementer"], "実装役",
                                   input=host.build_prompt(spec, feedback, host.symbolmap.prompt_text(cfg, wt, host.symbolmap.spec_modules(spec))))
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    if code in infra_retry.INFRA_EXIT_CODES:
        raise Infra(f"実装役の CLI が終了コード {code}: {infra_retry.classify_exit(code, err)}")
    return (code, *outcome(agent, out, cap))
