"""H ライン（harness/hline.py）の外側の統合ブランチ：マイルストーン outer の What を hline/outer に積み、統合 PR を週に 1 本だけ出す（W-56）。

**なぜ要るか**: 基盤の改善（外側）を週次の束にし、課題や道具の What（内側）の積みと分ける。外側の統合 PR が開いていても内側は止めない。
外側の状態は st["outer"]（awaiting_pr = 開いている外側の統合 PR、week = 外側の統合 PR を出した ISO 週）に置き、内側の st["awaiting_pr"] とは別に持つ。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。
hline_git の関数は cfg["integration_branch"] だけを読むので、外側ではその名前だけを替えた設定（branch_cfg）を渡す。
"""
import datetime
import time

import hline_merge

OUTER = "outer"


def is_outer(milestone):
    return milestone == OUTER


def branch_cfg(cfg, milestone):
    """milestone が外側なら、統合ブランチの名前を outer_integration_branch に替えた設定。それ以外（設定に名前が無いときも）は cfg のまま。"""
    if is_outer(milestone) and cfg.get("outer_integration_branch"):
        return {**cfg, "integration_branch": cfg["outer_integration_branch"]}
    return cfg


def outer_state(st):
    return st.get("outer") or {}


def week(day=None):
    """ISO 週の名前（2026-W43）。"""
    y, w, _ = (day or datetime.date.today()).isocalendar()
    return f"{y}-W{w:02d}"


def view(st, outer):
    """st の items を外側だけ（outer=True）か内側だけ（False）に絞った写し。items の各項目は st と同じものを指す。"""
    return {**st, "items": {n: i for n, i in st["items"].items() if is_outer(i.get("milestone")) == outer}}


def blocked_reason(st):
    """外側の統合 PR が開いていて外側の What を取り込めない理由。取り込めるなら None。"""
    if url := outer_state(st).get("awaiting_pr"):
        return f"外側の統合 PR が開いている間は、外側の What（マイルストーン {OUTER}）を取り込みません: {url}"
    return None


def merge_facts(host, ocfg, url):
    """マージの日時（ISO 8601）と、マージした PR の head（40 桁の SHA）。head を読めないときは空（H ラインを止めない）。"""
    try:
        head = host.pr_checks(ocfg, url).get("headRefOid")
    except Exception:   # noqa: BLE001
        return {}
    if not isinstance(head, str) or not hline_merge.SHA.fullmatch(head):
        return {}
    return {"merged_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "merged_head": head}


def settle(host, cfg, st):
    """外側の統合 PR が閉じられていたら（マージを含む）待ちを解く。まだ開いていれば何もしない。内側の待ちとは関係しない。"""
    o = outer_state(st)
    url = o.get("awaiting_pr")
    if not url:
        return
    ocfg = branch_cfg(cfg, OUTER)
    state = host.pr_state(ocfg, url)
    if state == "OPEN":
        # 外側は非劣性の記録（W-58）も要る。CI の失敗も待ちとして扱う（直す What は積み続けられる）
        if hline_merge.settle(host, ocfg, st, url, records=hline_merge.RECORDS) != "merged":
            if outer_state(st).get("merge_note") != o.get("merge_note"):
                host.save_state(cfg, st)
            return
        state = "MERGED"
    if state == "MERGED":
        host.drop_merged_branch(ocfg)
        facts = merge_facts(host, ocfg, url)
        for i in view(st, True)["items"].values():
            if i["status"] == "done" and i.get("pr") == url:
                i["merged"] = True
                i.update(facts)
    st["outer"] = {**outer_state(st), "awaiting_pr": None, "merge_note": None}
    host.save_state(cfg, st)


def open_pr(host, cfg, st):
    """外側の What が尽きたとき、hline/outer に main に無いコミットがあり、外側の統合 PR が開いておらず、今の週にまだ出していなければ 1 本出す。"""
    o, ocfg, mine = outer_state(st), branch_cfg(cfg, OUTER), view(st, True)
    if o.get("awaiting_pr") or o.get("week") == week():
        return
    if not [i for i in mine["items"].values() if i["status"] == "done" and not i.get("pr") and not i.get("merged")] or host.ahead(ocfg) == 0:
        return
    if bad := host.problems(host.diff_files(ocfg), ocfg):
        st["hygiene_halt"] = {"files": bad}
        host.save_state(cfg, st)
        print(f"外側の統合 PR は作りません（差分に不要なファイル: {len(bad)} 件）")
        return
    url = host.open_pr(ocfg) or host.create_pr(ocfg, host.pr_title(cfg, mine), host.pr_body(cfg, mine))
    for i in mine["items"].values():
        if i["status"] == "done" and not i.get("merged"):
            i["pr"] = url
    st["outer"] = {**o, "awaiting_pr": url, "week": week(), "opened_at": time.time()}
    host.save_state(cfg, st)
    print(f"外側の統合 PR: {url}")


def section(cfg, st):
    """report.md の「## H ライン」に足す行。内側と分けて、外側の統合ブランチ・統合 PR・その週に出したか。"""
    o = outer_state(st)
    if not cfg.get("outer_integration_branch"):
        return []
    sent = o.get("week")
    return [f"- 外側の統合ブランチ: {cfg['outer_integration_branch']}", f"- 外側の統合 PR: {o.get('awaiting_pr') or 'なし'}",
            f"- 外側の統合 PR を今週（{week()}）出したか: {'出した' if sent == week() else 'まだ'}"
            + (f"（最後に出した週: {sent}）" if sent and sent != week() else "")] \
        + ([f"- 外側の統合 PR の非劣性: {o['merge_note']}"] if o.get("awaiting_pr") and o.get("merge_note") else [])
