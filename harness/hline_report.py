"""H ライン（harness/hline.py）の報告：report.md・TODO.md・統合 PR の本文。

report.md は `harness.progress report` の出力（進捗ツリーは統合ブランチの進捗を映す）に、H ラインの節を足す。
TODO.md は未収束の What の記録で、キューの状態から書く（再開で差し替えられた記録は消える）。
"""
import datetime
import re
import sys
from pathlib import Path

import yaml

import hline_usage
from hline_base import ROOT, must
from hline_git import remote_ref
from hline_queue import by_status, save_state

import proc  # noqa: E402
import progress  # noqa: E402

MARK = "<!-- hline-queue:"
BODY_MAX = 60000   # GitHub の PR 本文の上限は 65536 文字


SELF_CONFIGS = ("config/hline.json", "config/taskspec.schema.json")


def line_modules():
    """読み込み済みのモジュールのうち、このリポジトリの harness の下にあるものの、根からの相対パス（/ 区切り）の集合。"""
    harness, root, found = (ROOT / "harness").resolve(), ROOT.resolve(), set()
    for mod in list(sys.modules.values()):
        try:
            file = Path(getattr(mod, "__file__", None) or "").resolve()
            if file.is_file() and harness in file.parents:
                found.add(file.relative_to(root).as_posix())
        except (OSError, ValueError, TypeError):
            continue
    return frozenset(found)


def self_change(paths, modules=None):
    """paths のうち、走行中のライン自身（読み込み済みのモジュール・2 つの設定ファイル）に当たるもの。正規化して並べ替える。"""
    mods = line_modules() if modules is None else modules
    return sorted({n for n in (p.replace("\\", "/") for p in paths) if n in mods or n in SELF_CONFIGS})


def line_status(cfg, st):
    if st.get("infra_halt"):
        return "インフラ例外で停止"
    return "統合 PR 待ち" if st["awaiting_pr"] else "走行中"


def progress_report(cfg):
    """進捗の report。統合ブランチの docs/progress.yaml を映す（統合ブランチが無ければ base）。"""
    t = cfg["ttl_seconds"]["git"]
    for ref in (remote_ref(cfg), cfg["base"]):
        code, out, _ = proc.run(["git", "show", f"{ref}:{progress.REL_PATH}"], ROOT, t, "git show")
        if code == 0:
            return progress.report(yaml.safe_load(out))
    return must([sys.executable, "-m", "harness.progress", "report"], ROOT, t, "report")


def stage_note(item):
    """処理中の項目の注記。古い queue.json・recover で戻った項目には段階も開始の時刻も無い。"""
    return f"（段階: {item.get('stage') or '不明'}／開始: {item.get('started_at') or '不明'}）"


def flaky_tests(item):
    """項目の試行の記録に残った、並列で落ちて単独で通ったテストの名前（現れた順・重複なし）。古い記録には無い。"""
    return list(dict.fromkeys(n for t in item.get("tries", []) for n in t.get("flaky") or []))


def warning_count(item):
    """項目の試行の記録に残った警告（Gate 1 が不合格にしなかった指摘）の件数。古い記録には無い。"""
    return sum(len(t.get("warnings") or []) for t in item.get("tries", []))


def h_section(cfg, st):
    items = st["items"]

    def row(label, status, note=lambda i: ""):
        names = by_status(st, status)
        return f"- {label}（{len(names)} 件）: " + (", ".join(f"{n}{note(items[n])}" for n in names) or "なし")

    done = lambda i: (f"（警告 {warning_count(i)} 件）" if warning_count(i) else "") + hline_usage.note(i)   # noqa: E731
    waiting = lambda i: f"（依存先: {', '.join(i['deps'])}）" if i["deps"] else ""   # noqa: E731
    frozen = lambda i: f"（上流の未収束: {', '.join(i.get('frozen_by', []))}）"   # noqa: E731
    lines = ["## H ライン", f"- 状態: {line_status(cfg, st)}", f"- 統合ブランチ: {cfg['integration_branch']}",
             f"- 統合 PR: {st['awaiting_pr'] or 'なし'}", "", "## キュー",
             row("済み", "done", done), row("待ち", "waiting", waiting), row("処理中", "processing", stage_note),
             row("未収束", "unconverged", lambda i: f"（{i.get('reason', '')}）" + hline_usage.note(i)), row("凍結", "frozen", frozen)]
    flaky = [f"- {n}: {', '.join(flaky_tests(i))}" for n, i in sorted(items.items()) if flaky_tests(i)]
    if flaky:
        lines += ["", "## 揺れたテスト（並列で落ち、単独で通った）"] + flaky
    if st.get("self_change"):
        lines += ["", f"- ライン自身の変更を積んだので走行を区切った（積んだ What: {st['self_change']['name']}）"]
    if st["skipped"]:
        lines += ["", "## 受信箱に残した What"] + [f"- {s['file']}: {s['reason']}" for s in st["skipped"]]
    return "\n".join(lines) + "\n"


def human_line(cfg, st):
    """report の人間作業欄。インフラ例外での停止・統合 PR のレビューは人間の作業。それ以外は None（進捗の記録のまま）。"""
    halt = st.get("infra_halt")
    if halt:
        return (f"- 人間作業: INFRA_HALTED {halt['name']}: {' '.join(str(halt['reason']).split())}（{halt['attempts']} 回の試行）。"
                "環境を直すと次の走行が取り直します")
    if hyg := st.get("hygiene_halt"):
        return f"- 人間作業: UNCLEAN_DIFF {'／'.join(hyg['files'])}"
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
    seen, keep = set(), []
    for x in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        if MARK in x or (x.strip() and x in seen):
            continue
        seen.add(x)
        keep.append(x)
    rows = [f"- {i.get('at', '')} {n}「{i['title']}」（{i['milestone']}）：{i.get('reason', '')} {MARK}{n} -->"
            for n, i in sorted(st["items"].items()) if i["status"] == "unconverged"]
    p.write_text("\n".join(keep + rows) + ("\n" if keep or rows else ""), encoding="utf-8")


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def mark(cfg, st, name=None, stage=None, *, save=None, write=None):
    """状態を書き、report.md・TODO.md を今の状態で書き直す。name があれば、その項目の段階を置く（stage 省略なら消す）。
    save・write は呼び手の名前で差し替えるためのもの（既定はこのモジュールの save_state・write_report）。"""
    if name is not None:
        item = st["items"][name]
        if stage is None:
            item.pop("stage", None)
            item.pop("started_at", None)
        else:
            item["stage"] = stage
            item.setdefault("started_at", now())
    (save or save_state)(cfg, st)
    (write or write_report)(cfg, st)


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
    flaky = flaky_tests(item)
    flaky_line = f"- 揺れたテスト（並列で落ち、単独で通った）: {', '.join(flaky)}\n" if flaky else ""
    added = list(dict.fromkeys(f for t in item.get("tries", []) for f in t.get("boundary_added", [])))
    added_line = f"- 編集境界にハーネスが足したテスト（変えたモジュールを確かめる既存のテスト）: {', '.join(added)}\n" if added else ""
    return (f"### {item['title']}（{name}）\n\n- 進捗のタスク: {item.get('task') or 'なし'}／マイルストーン: {item['milestone']}\n"
            f"- 分解役: {len(dec)} 回の試行（Gate A）／使われたモデル: {', '.join(models)}\n{flaky_line}{added_line}\n"
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
        out += ["", "- Gate 1：影響テスト（変えたパスから引いたテスト）と進捗の検証コマンドの終了コード 0。全件テストは内側のループでは"
                "走らせない（この PR の CI が見る）。差分の量・編集境界・規模の指摘は不合格にせず警告として記録する",
                "- この PR は H ライン（`harness/hline.py`）が作った統合 PR。タスクごとの PR は作っていない",
                "", "🤖 Generated with [Claude Code](https://claude.com/claude-code)", ""]
        return "\n".join(out)
    body = build(True)
    return body if len(body) <= BODY_MAX else build(False)


def pr_title(cfg, st):
    done = [n for n in by_status(st, "done") if not st["items"][n].get("merged")]
    ms = sorted({st["items"][n]["milestone"] for n in done})
    return f"feat(hline): {', '.join(ms)} の統合（{len(done)} 件）"
