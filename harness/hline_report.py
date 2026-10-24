"""H ライン（harness/hline.py）の報告：report.md・TODO.md・統合 PR の本文。

report.md は `harness.progress report` の出力（進捗ツリーは統合ブランチの進捗を映す）に、H ラインの節を足す。
TODO.md は未収束の What の記録で、キューの状態から書く（再開で差し替えられた記録は消える）。
"""
import datetime
import re
import sys
import time
from pathlib import Path

import yaml

import director_launch
import hline_checks
import hline_extend
import hline_outer
import hline_pending
import hline_probe
import hline_quota
import hline_resume
import hline_usage
from hline_base import ROOT, must
from hline_git import remote_ref
from hline_queue import by_status, downstream_impact, missing_deps, save_state

import proc  # noqa: E402
import main_mirror  # noqa: E402
import progress  # noqa: E402

MARK = "<!-- hline-queue:"
BODY_MAX = 60000   # GitHub の PR 本文の上限は 65536 文字


SELF_CONFIGS = ("config/hline.json",)


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
    """paths のうち、走行中のライン自身（読み込み済みのモジュール・設定ファイル）に当たるもの。正規化して並べ替える。"""
    mods = line_modules() if modules is None else modules
    return sorted({n for n in (p.replace("\\", "/") for p in paths) if n in mods or n in SELF_CONFIGS})


def line_status(cfg, st):
    if st.get("infra_halt"):
        return "インフラ例外で停止"
    if st.get("quota_wait"):
        return f"利用枠の回復待ち（再開: {hline_quota.shown(st)} 以降）"
    return "統合 PR 待ち" if st["awaiting_pr"] else "走行中"


def git_text(args, ttl):
    """git の出力。失敗なら None（ノードの説明を読めないときは、ツリーに印を付けない）。"""
    code, out, _ = proc.run(["git", *args], ROOT, ttl, f"git {args[0]}")
    return out if code == 0 else None


def progress_report(cfg):
    """進捗の report。統合ブランチの docs/progress.yaml を映す（統合ブランチが無ければ base）。"""
    t = cfg["ttl_seconds"]["git"]

    def git(args):
        return git_text(args, t)
    for ref in (remote_ref(cfg), cfg["base"]):
        st = progress.read_ref(lambda p: git(["show", f"{ref}:{p}"]))
        if st is not None:
            return progress.report(st)
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


def material_note(item):
    """未収束の行の、再開の材料（差分と失敗の出力）の注記。古い記録には材料の記録が無い。"""
    m = item.get("material")
    if not isinstance(m, dict):
        return "（再開の材料: 記録なし）"
    return f"（再開の材料: あり・差分 {m.get('lines', 0)} 行）" if m.get("kept") else "（再開の材料: なし）"


DONE_SHOWN = 10      # report.md に出す済みの件数（完了の時刻の新しい順）
POINTS_MAX = 5       # 未収束の What ごとに出す、落ちた試験の件数
EXC_MAX = 200        # 例外の行を切る字数
NO_FAILURE = "失敗の出力なし"
_HEAD = re.compile(r"^(?:FAIL|ERROR): (\S.*)$")
_FILE = re.compile(r'^\s*File "(.*)", line (\d+)')


def recent_done(items, names):
    """済みの名前のうち、完了の時刻のある新しい順の直近 DONE_SHOWN 件（時刻の無い項目は候補に入れない）。"""
    import northstar
    timed = []
    for n in names:
        at = northstar.ended_at(items[n])
        if at is not None:
            timed.append((at, n))
    timed.sort(key=lambda p: p[0], reverse=True)
    return [n for _, n in timed[:DONE_SHOWN]]


def failure_points(text):
    """Gate 1 の出力（unittest の FAIL:・ERROR: とトレースバック）から「試験の名前: ファイル:行: 例外の行」の一覧を、現れた順に出す。"""
    blocks, cur = [], None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            cur = [m.group(1).strip(), []]
            blocks.append(cur)
        elif cur is not None:
            if line.startswith("=====") or (line.startswith("-----") and cur[1]):
                cur = None
            elif not line.startswith("-----"):
                cur[1].append(line)
    out = []
    for name, body in blocks:
        last = max((i for i, l in enumerate(body) if _FILE.match(l)), default=None)
        where, exc = "不明", ""
        if last is not None:
            f = _FILE.match(body[last])
            where = f"{f.group(1)}:{f.group(2)}"
            exc = next((l for l in body[last + 1:] if l.strip() and not l[:1].isspace()), "")
        if not exc:
            exc = next((l for l in reversed(body) if l.strip()), "")
        out.append(f"{name}: {where}: {' '.join(exc.split())[:EXC_MAX]}")
    return out


def failure_lines(cfg, name):
    """未収束の What の再開の置き場の last-failure.txt から、落ちた試験の要点の行（字下げ 4）。読めなければ「失敗の出力なし」の 1 行。"""
    try:
        text = (hline_resume.resume_dir(cfg, name) / hline_resume.FAILURE).read_text(encoding="utf-8", errors="replace")
        points = failure_points(text)
    except Exception:   # noqa: BLE001
        return [f"    - {NO_FAILURE}"]
    if not points:
        first = [l.strip() for l in text.splitlines() if l.strip()]
        if first:
            return [f"    - {first[0][:EXC_MAX]}"]
        return ["    - 落ちた試験を読み取れず"]
    return [f"    - {p}" for p in points[:POINTS_MAX]]


def goal_section(cfg, checks=None):
    """「## 目的までの距離」の節（report.md の先頭）。読み込みが例外で止まっても、節を 1 行の理由に替えて、書き出しを止めない。
    checks は照合の本文を返す関数（H ラインが止まっている間は、照合を走らせない hline_checks.cached_section）。"""
    try:
        import goal_distance
        return goal_distance.section(cfg, checks)
    except Exception as e:   # noqa: BLE001
        return f"## 目的までの距離\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"


def flow_section(cfg):
    """「## 関門ごとの流れ」の節。読み込みが例外で止まっても、節を 1 行の理由に替えて、書き出しを止めない。"""
    try:
        import gate_flow
        return gate_flow.section(cfg)
    except Exception as e:   # noqa: BLE001
        return f"## 関門ごとの流れ\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"


def northstar_section(items):
    """「## 計器」の節。計器の読み込み・計算が例外で止まっても、節を 1 行の理由に替えて、report.md の書き出しを止めない。"""
    try:
        import northstar
        return northstar.safe_section(items)
    except Exception as e:   # noqa: BLE001
        return f"## 計器\n- 計器を出せませんでした: {' '.join(f'{type(e).__name__}: {e}'.split())}\n"


def failure_section(items):
    """計器の節に続く失敗の分類の表。読み込み・計算が例外で止まっても、表を 1 行の理由に替えて、report.md の書き出しを止めない。"""
    try:
        import failure_classes
        return failure_classes.safe_section(items)
    except Exception as e:   # noqa: BLE001
        return f"### 失敗の分類\n- 失敗の分類を出せませんでした: {' '.join(f'{type(e).__name__}: {e}'.split())}\n"


def pr_wait_line(times, at=None):
    """「直近の統合 PR の待ち」の行（記録が無ければ行なし）。出した時刻 → 閉じた時刻とその長さ。開いている間は経過の長さ。"""
    try:
        opened, closed = float(times["opened_at"]), times.get("closed_at")
        if closed is None:
            took = (time.time() if at is None else at) - opened
            return [f"- 直近の統合 PR の待ち: {hline_probe.stamp(opened)} → 開いている（経過 {hline_probe.span(took)}）"]
        return [f"- 直近の統合 PR の待ち: {hline_probe.stamp(opened)} → {hline_probe.stamp(float(closed))}"
                f"（{hline_probe.span(float(closed) - opened)}）"]
    except (TypeError, KeyError, ValueError):
        return []


def h_section(cfg, st, stopped_at=None):
    """「## H ライン」から後の節。stopped_at（H ラインが最後に走った時刻）があれば、止まっている間の形にする：
    走っている間の状態の語を出さず、統合 PR の待ちの行を外し、キューと計器に最後に走った時刻を添える（W-144）。"""
    items = st["items"]

    def row(label, status, note=lambda i: "", shown=None):
        names = by_status(st, status)
        shown_names = names if shown is None else shown(names)
        return f"- {label}（{len(names)} 件）: " + (", ".join(f"{n}{note(items[n])}" for n in shown_names) or "なし")

    def unconverged_rows():
        names = by_status(st, "unconverged")
        note = lambda n, i: (f"（{i.get('reason', '')}）{material_note(i)}" + triage(n) + after_note(i)   # noqa: E731
                             + hline_usage.note(i) + hline_extend.note(i))
        if not names:
            return ["- 未収束（0 件）: なし"]
        out = [f"- 未収束（{len(names)} 件）:"]
        for n in names:
            out.append(f"  - {n}{note(n, items[n])}")
            out += failure_lines(cfg, n)
        return out

    after_note = lambda i: (f"（警告: after の上流 {', '.join(i['after_unconverged'])} が未収束のまま処理）" if i.get("after_unconverged") else "")   # noqa: E731

    def triage(n):
        hit, mainline = downstream_impact(st, n)
        return f"（凍結した下流 {len(hit)} 件・本線への影響 {'あり' if mainline else 'なし'}）"

    done = lambda i: (f"（警告 {warning_count(i)} 件）" if warning_count(i) else "") + after_note(i) + hline_usage.note(i) + hline_extend.note(i)   # noqa: E731
    waiting = lambda i: (f"（依存先: {', '.join(i['deps'])}）" if i["deps"] else "") + (f"（台帳を読めず待ち: {i['ledger_error']}）" if i.get("ledger_error") else "") + (f"（依存先なし: {', '.join(missing_deps(st, i))}）" if missing_deps(st, i) else "") + ("（台帳の項目の main への取り込み待ち）" if i.get("entry_wait") else "") + hline_extend.note(i)   # noqa: E731
    frozen = lambda i: f"（上流の未収束: {', '.join(i.get('frozen_by', []))}）"   # noqa: E731
    if stopped_at is None:
        state = [f"- 状態: {line_status(cfg, st)}", f"- 統合ブランチ: {cfg['integration_branch']}",
                 f"- 統合 PR: {st['awaiting_pr'] or 'なし'}"]
        waits, queue_note = pr_wait_line(st.get("pr_times")), []
    else:
        state = [f"- 状態: 止まっている（最後に走った時刻: {stopped_at}）", f"- 統合ブランチ: {cfg['integration_branch']}",
                 f"- 最後の走行の時点で開いていた統合 PR: {st['awaiting_pr'] or 'なし'}"]
        waits, queue_note = [], [f"- 最後に走った時刻（{stopped_at}）の状態"]
    lines = ["## H ライン", *state] + hline_outer.section(cfg, st) + hline_pending.line(st) + ([f"- コード: {st['code']}"] if st.get("code") else []) + waits + ([d] if (d := director_launch.latest_line(cfg)) else []) + ["", "## キュー", *queue_note,
             row("済み", "done", done, lambda ns: recent_done(items, ns)), row("待ち", "waiting", waiting),
             row("処理中", "processing", stage_note), *unconverged_rows(), row("凍結", "frozen", frozen)]
    meters = northstar_section(items).rstrip("\n")
    if stopped_at is not None:
        meters = meters.replace("## 計器", f"## 計器（最後に走った時刻 {stopped_at} の状態）", 1)
    lines += ["", meters, "", failure_section(items).rstrip("\n")]
    flaky = [f"- {n}: {', '.join(flaky_tests(i))}" for n, i in sorted(items.items()) if flaky_tests(i)]
    if flaky:
        lines += ["", "## 揺れたテスト（並列で落ち、単独で通った）"] + flaky
    if st.get("self_change"):
        lines += ["", f"- ライン自身の変更を積んだので走行を区切った（積んだ What: {st['self_change']['name']}）"]
    if st["skipped"]:
        lines += ["", "## 受信箱に残した What"] + [f"- {s['file']}: {s['reason']}" for s in st["skipped"]]
    return "\n".join(lines) + "\n"


def human_line(cfg, st):
    """report の人間作業欄。インフラ例外での停止・統合 PR のレビューは人間の作業。利用枠の回復待ちは人間の作業ではなく、待ちの表示。
    それ以外は None（進捗の記録のまま）。"""
    if stale := st.get("stale_code"):
        return f"- 人間作業: STALE_CODE 走っている {stale['running']} ／ 先端 {stale['tip']}"
    halt = st.get("infra_halt")
    if halt:
        return (f"- 人間作業: INFRA_HALTED {halt['name']}: {' '.join(str(halt['reason']).split())}（{halt['attempts']} 回の試行）。"
                "環境を直すと次の走行が取り直します")
    if hyg := st.get("hygiene_halt"):
        return f"- 人間作業: UNCLEAN_DIFF {'／'.join(hyg['files'])}"
    if st.get("quota_wait"):   # 人間の作業ではない。待ちの表示で、定期起動が再開の時刻の後に続きを取る
        return f"- 人間作業: NONE（利用枠の回復待ち（再開 {hline_quota.shown(st)} 以降））"
    if am := st.get("auto_merge"):
        return f"- 人間作業: {am['kind']} {am['url']}" + (f"：{am['reason']}" if am.get("reason") else "")
    if st["awaiting_pr"]:
        return f"- 人間作業: REVIEW_REQUIRED {st['awaiting_pr']}"
    return None


def write_report(cfg, st):
    text, human = progress_report(cfg), human_line(cfg, st)
    if human:
        text = re.sub(r"^- 人間作業:.*$", lambda _: human, text, flags=re.M)
    probe = hline_probe.safe_section(cfg, pr=st.get("awaiting_pr"), waits=True)
    body = "".join(s.rstrip("\n") + "\n\n" for s in [main_mirror.section(cfg)] if s) + goal_section(cfg).rstrip("\n") + "\n\n" + flow_section(cfg).rstrip("\n") + "\n\n" + text + "\n" + h_section(cfg, st) + "\n" + probe + "\n" + hline_checks.safe_section(cfg)
    (Path(cfg["inbox"]) / "report.md").write_text(body, encoding="utf-8")
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

def item_section(cfg, name, item):
    """統合 PR の本文の 1 項目。古い queue.json にある分解役の記録（decompose）は読まない。"""
    tries = "\n".join(f"| {t['run']} | {t['attempt']} | {t['cli_exit']} | {', '.join(t['models'])} | "
                      f"{'通過' if t['gate'] else '不合格'} |" for t in item.get("tries", []))
    models = sorted({m for t in item.get("tries", []) for m in t["models"]})
    flaky = flaky_tests(item)
    flaky_line = f"- 揺れたテスト（並列で落ち、単独で通った）: {', '.join(flaky)}\n" if flaky else ""
    added = list(dict.fromkeys(f for t in item.get("tries", []) for f in t.get("boundary_added", [])))
    added_line = f"- 編集境界にハーネスが足したテスト（変えたモジュールを確かめる既存のテスト）: {', '.join(added)}\n" if added else ""
    return (f"### {item['title']}（{name}）\n\n- 進捗のタスク: {item.get('task') or 'なし'}／マイルストーン: {item['milestone']}\n"
            f"- 使われたモデル: {', '.join(models)}\n{flaky_line}{added_line}\n"
            "| 作業ツリー | 試行 | CLI の終了コード | 使われたモデル | Gate 1 |\n|:--|:--|:--|:--|:--|\n"
            f"{tries}\n")


def pr_body(cfg, st):
    done = [n for n in by_status(st, "done") if not st["items"][n].get("merged")]
    out = [f"## 積んだタスク（{len(done)} 件）\n"] + [item_section(cfg, n, st["items"][n]) for n in done]
    out += [f"## 未収束（{len(by_status(st, 'unconverged'))} 件）\n"]
    out += [f"- {n}「{st['items'][n]['title']}」：{st['items'][n].get('reason', '')}" for n in by_status(st, "unconverged")]
    out += ["", f"## 凍結（{len(by_status(st, 'frozen'))} 件）\n"]
    out += [f"- {n}「{st['items'][n]['title']}」：上流の未収束 {', '.join(st['items'][n].get('frozen_by', []))}"
            for n in by_status(st, "frozen")]
    out += ["", "- Gate 1：影響テスト（変えたパスから引いたテスト）と進捗の検証コマンドの終了コード 0。全件テストは内側のループでは"
            "走らせない（この PR の CI が見る）。差分の量・編集境界・規模の指摘は不合格にせず警告として記録する",
            "- この PR は H ライン（`harness/hline.py`）が作った統合 PR。タスクごとの PR は作っていない",
            "", "🤖 Generated with [Claude Code](https://claude.com/claude-code)", ""]
    return "\n".join(out)[:BODY_MAX]


def pr_title(cfg, st):
    done = [n for n in by_status(st, "done") if not st["items"][n].get("merged")]
    ms = sorted({st["items"][n]["milestone"] for n in done})
    return f"feat(hline): {', '.join(ms)} の統合（{len(done)} 件）"
