"""H ライン（harness/hline.py）の再分解：収束しなかった What を、失敗の要約つきで分解役に返して TaskSpec を作り直し、1 回だけ再実行する。
harness.hline を import しない（循環を作らない）。必要な関数は呼び手が渡す。"""
import json
from pathlib import Path

NO_RECORD = "記録なし"


def _tail(path, n):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return NO_RECORD
    return text[-n:] if text.strip() else NO_RECORD


def failure_summary(cfg, outdir, tries, prev=None):
    """最後の試行の Gate 1 の出力（編集境界の違反を含む）・実装役の終了コード・実装役のログの末尾。prev があれば作り直す前の TaskSpec も付ける。"""
    n, last = cfg["gate_tail_chars"], (tries[-1] if tries else None)
    where = f"{last['run']}-{last['attempt']}" if last else None
    p = ["## Gate 1 の出力（最後の試行。編集境界の違反を含む）",
         _tail(Path(outdir) / f"gate-{where}.log", n) if where else NO_RECORD, "",
         "## 実装役の終了コード", str(last["cli_exit"]) if last else NO_RECORD, "",
         *(["## 打ち切り・断念の理由（Gate 1 を呼ばずに止めた）", str(last["abort"]), ""] if last and last.get("abort") else []),
         "## 実装役のログの末尾",
         _tail(Path(outdir) / f"implementer-{where}.log", n) if where else NO_RECORD]
    if prev:
        p += ["", "## 作り直す前の TaskSpec", json.dumps(prev, ensure_ascii=False, indent=2)]
    return "\n".join(p) + "\n"


def second_round(cfg, tid, what, item, outdir, prev, decompose, new_worktree, run_task, on_stage=None):
    """新しい作業ツリーで TaskSpec を作り直し、run=1 で再実行する。Gate A に通らなければ (None, None, None)。item の respec・tries に記録する。"""
    wt, branch = new_worktree(cfg, tid)
    spec, item["respec"] = decompose(cfg, wt, what, item, outdir, failure=failure_summary(cfg, outdir, item["tries"], prev),
                                     tag="respec")
    if spec is None:
        return None, None, None
    wt, branch, tries = run_task(cfg, tid, spec, outdir, (wt, branch), item["task"], on_stage=on_stage, run=1)
    item["tries"] = item["tries"] + tries
    return wt, branch, spec
