"""H ライン（harness/hline.py）の報告：report.md・TODO.md・統合 PR の本文。

report.md は `harness.progress report` の出力（進捗ツリーは統合ブランチの進捗を映す）に、H ラインの節を足す。
TODO.md は未収束の What の記録で、キューの状態から書く（再開で差し替えられた記録は消える）。
"""
import datetime
import re
import sys
from pathlib import Path

import yaml

from hline_base import ROOT, must
from hline_git import remote_ref
from hline_queue import blocked, by_status

import proc  # noqa: E402
import progress  # noqa: E402

MARK = "<!-- hline-queue:"
BODY_MAX = 60000   # GitHub の PR 本文の上限は 65536 文字


def line_status(cfg, st):
    return "BLOCKED" if blocked(cfg, st) else "統合 PR 待ち" if st["awaiting_pr"] else "走行中"


def progress_report(cfg):
    """進捗の report。統合ブランチの docs/progress.yaml を映す（統合ブランチが無ければ base）。"""
    t = cfg["ttl_seconds"]["git"]
    for ref in (remote_ref(cfg), cfg["base"]):
        code, out, _ = proc.run(["git", "show", f"{ref}:{progress.REL_PATH}"], ROOT, t, "git show")
        if code == 0:
            return progress.report(yaml.safe_load(out))
    return must([sys.executable, "-m", "harness.progress", "report"], ROOT, t, "report")


def h_section(cfg, st):
    items = st["items"]

    def row(label, status, note=lambda i: ""):
        names = by_status(st, status)
        return f"- {label}（{len(names)} 件）: " + (", ".join(f"{n}{note(items[n])}" for n in names) or "なし")

    waiting = lambda i: f"（依存先: {', '.join(i['deps'])}）" if i["deps"] else ""   # noqa: E731
    frozen = lambda i: f"（上流の未収束: {', '.join(i.get('frozen_by', []))}）"   # noqa: E731
    lines = ["## H ライン", f"- 状態: {line_status(cfg, st)}", f"- 統合ブランチ: {cfg['integration_branch']}",
             f"- 統合 PR: {st['awaiting_pr'] or 'なし'}", "", "## キュー",
             row("済み", "done"), row("待ち", "waiting", waiting), row("処理中", "processing"),
             row("未収束", "unconverged", lambda i: f"（{i.get('reason', '')}）"), row("凍結", "frozen", frozen)]
    if st["skipped"]:
        lines += ["", "## 受信箱に残した What"] + [f"- {s['file']}: {s['reason']}" for s in st["skipped"]]
    return "\n".join(lines) + "\n"


def human_line(cfg, st):
    """report の人間作業欄。BLOCKED と統合 PR のレビューは人間の作業。それ以外は None（進捗の記録のまま）。"""
    blk = blocked(cfg, st)
    if blk:
        unconverged = [n for ns in blk.values() for n in ns]
        return (f"- 人間作業: BLOCKED 同じマイルストーンで未収束が {cfg['max_unconverged_per_milestone']} 件に達しました"
                f"（未収束: {', '.join(unconverged)}／凍結: {', '.join(by_status(st, 'frozen')) or 'なし'}）。"
                "直した What を同じ名前で受信箱に置くと再開します")
    if st["awaiting_pr"]:
        return f"- 人間作業: REVIEW_REQUIRED {st['awaiting_pr']}"
    return None


def write_report(cfg, st):
    text, human = progress_report(cfg), human_line(cfg, st)
    if human:
        text = re.sub(r"^- 人間作業:.*$", lambda _: human, text, flags=re.M)
    (Path(cfg["inbox"]) / "report.md").write_text(text + "\n" + h_section(cfg, st), encoding="utf-8")
    write_todo(cfg, st)


def write_todo(cfg, st):
    p = Path(cfg["inbox"]) / "TODO.md"
    keep = [x for x in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []) if MARK not in x]
    rows = [f"- {i.get('at', '')} {n}「{i['title']}」（{i['milestone']}）：{i.get('reason', '')} {MARK}{n} -->"
            for n, i in sorted(st["items"].items()) if i["status"] == "unconverged"]
    p.write_text("\n".join(keep + rows) + ("\n" if keep or rows else ""), encoding="utf-8")


def today():
    return datetime.date.today().isoformat()


# ============================================================ 統合 PR の本文

def item_section(cfg, name, item, with_spec):
    tries = "\n".join(f"| {t['run']} | {t['attempt']} | {t['cli_exit']} | {', '.join(t['models'])} | "
                      f"{'通過' if t['gate'] else '不合格'} |" for t in item.get("tries", []))
    dec = item.get("decompose", {}).get("attempts", [])
    models = sorted({m for a in dec for m in a["models"]} | {m for t in item.get("tries", []) for m in t["models"]})
    spec_file = Path(cfg["out"]) / item.get("tid", "-") / "taskspec.json"
    spec = "（走行の記録に taskspec.json がありません）"
    if not with_spec:
        spec = "（本文の上限のため省略。走行の記録の taskspec.json にある）"
    elif spec_file.exists():
        spec = spec_file.read_text(encoding="utf-8").strip()
    return (f"### {item['title']}（{name}）\n\n- 進捗のタスク: {item.get('task') or 'なし'}／マイルストーン: {item['milestone']}\n"
            f"- 分解役: {len(dec)} 回の試行（Gate A）／使われたモデル: {', '.join(models)}\n\n"
            "| 作業ツリー | 試行 | CLI の終了コード | 使われたモデル | Gate 1 |\n|:--|:--|:--|:--|:--|\n"
            f"{tries}\n\n<details><summary>TaskSpec</summary>\n\n```json\n{spec}\n```\n\n</details>\n")


def pr_body(cfg, st):
    def build(with_spec):
        done = [n for n in by_status(st, "done") if not st["items"][n].get("merged")]
        out = [f"## 積んだタスク（{len(done)} 件）\n"] + [item_section(cfg, n, st["items"][n], with_spec) for n in done]
        out += [f"## 未収束（{len(by_status(st, 'unconverged'))} 件）\n"]
        out += [f"- {n}「{st['items'][n]['title']}」：{st['items'][n].get('reason', '')}" for n in by_status(st, "unconverged")]
        out += ["", f"## 凍結（{len(by_status(st, 'frozen'))} 件）\n"]
        out += [f"- {n}「{st['items'][n]['title']}」：上流の未収束 {', '.join(st['items'][n].get('frozen_by', []))}"
                for n in by_status(st, "frozen")]
        out += ["", "- Gate 1：`python -m unittest discover -s tests` の終了コード 0、TaskSpec の編集境界、進捗の検証コマンド",
                "- この PR は H ライン（`harness/hline.py`）が作った統合 PR。タスクごとの PR は作っていない",
                "", "🤖 Generated with [Claude Code](https://claude.com/claude-code)", ""]
        return "\n".join(out)
    body = build(True)
    return body if len(body) <= BODY_MAX else build(False)


def pr_title(cfg, st):
    done = [n for n in by_status(st, "done") if not st["items"][n].get("merged")]
    ms = sorted({st["items"][n]["milestone"] for n in done})
    return f"feat(hline): {', '.join(ms)} の統合（{len(done)} 件）"
