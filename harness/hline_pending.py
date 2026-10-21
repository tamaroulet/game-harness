"""H ライン（harness/hline.py）の保留の積み：内側の統合 PR が開いて待っている間に Gate 1 を通った変更（W-91）。

**なぜ要るか**: CI と承認を待つ間もラインを止めない。ただし開いている PR の head（統合ブランチの先端）は変えない。
待っている間の内側の What は、保留のブランチ（統合ブランチ名 + "-pending"。出発点は PR の head）に積み、項目に pending を立てる。
PR が閉じたら rebuild が、保留のコミットだけを新しい先端（マージ後は main、マージされず閉じたときは統合ブランチ）の上に積み直す（W-111）。
積み直せない（衝突した）What だけ差分を捨てて待ちに戻し（試行に数えない）、依存する保留の What も待ちに戻す。
harness.hline を import しない（循環を作らない）。依存は第 1 引数 host（harness.hline 自身）の属性として読むので、テストの差し替えが効く。
"""
import hline_git
import hline_outer


def held(st):
    """保留の積みの What の名前（名前の順）。"""
    return sorted(n for n, i in st["items"].items() if i["status"] == "done" and i.get("pending"))


def stack_cfg(cfg, st, milestone):
    """What を積む設定。内側の統合 PR を待っている走行（st["pr_open"]）の内側の What は保留のブランチ、それ以外は今まで通り。"""
    plain = hline_outer.branch_cfg(cfg, milestone)
    if st.get("pr_open") and plain["integration_branch"] == cfg["integration_branch"]:
        return {**cfg, "integration_branch": hline_git.pending_branch(cfg), "branch_from": cfg["integration_branch"]}
    return plain


def pending_cfg(cfg):
    return {**cfg, "integration_branch": hline_git.pending_branch(cfg), "branch_from": cfg["integration_branch"]}


def landed(host, cfg, st, name):
    """強制終了からの復旧：この What を積み終えているか。保留のブランチだけにあれば、保留の印も立てる。"""
    if host.integrated(hline_outer.branch_cfg(cfg, st["items"][name].get("milestone")), name):
        return True
    if host.integrated({**cfg, "integration_branch": hline_git.pending_branch(cfg)}, name):
        st["items"][name]["pending"] = True
        return True
    return False


def regenerate(item):
    """差分を捨て、待ちに戻す。試行の回数には数えず、新しい先端の上で実装役を呼び直す。"""
    for k in ("pending", "pending_self", "pr", "merged", "done_at", "infra_retries", "reason"):
        item.pop(k, None)
    item.update(status="waiting", tries=[])


def depends(item, bad):
    return bool(set(item["deps"]) & bad)


def rebuild(host, cfg, st):
    """統合 PR が閉じた後、保留の積みを新しい先端の上に積み直す。何度呼んでも同じ結果になる（途中で落ちても次の走行が続ける）。"""
    names = held(st)
    if not names:
        return
    items = st["items"]
    # マージ後は統合ブランチが無く、作業ツリーの出発点は main の新しい先端になる。保留のブランチには開いていた PR の
    # コミットも見えるが、保留の印のある What（names）だけを積むので二重にはならない
    commits = host.pending_commits(cfg)
    done = {n for n in names if host.integrated(cfg, n)}   # 前の積み直しで積み終えている
    shas = {n: s for s, n in commits}
    bad = {x for n in names if n not in done and n not in shas for x in (n, items[n].get("task"))} - {None}
    wt, _ = host.new_worktree(cfg, "pending")
    for sha, n in commits:
        if n not in names or n in done or items[n]["status"] != "done":
            continue
        if depends(items[n], bad) or not host.cherry_pick(cfg, wt, sha):
            bad |= {n, items[n].get("task")} - {None}
        else:
            done.add(n)
    if any(n in done and not host.integrated(cfg, n) for n in names):
        host.push_tip(cfg, wt)
    for n in names:
        if n in done:
            for k in ("pending", "pending_self"):
                items[n].pop(k, None)
        else:
            regenerate(items[n])
            print(f"[{n}] 保留の積みを新しい先端に積み直せないので、待ちに戻して実装し直します（試行に数えません）")
    host.drop_merged_branch(pending_cfg(cfg))
    host.save_state(cfg, st)


def self_held(st):
    """保留の積みの中に、ライン自身を変えた What があるか（その変更は統合 PR が閉じるまで読み込めない）。"""
    return any(st["items"][n].get("pending_self") for n in held(st))


def line(st):
    """report.md の「## H ライン」の行：保留の積みの件数と名前（無ければ行なし）。"""
    names = held(st)
    return [f"- 保留の積み（{len(names)} 件）: {', '.join(names)}"] if names else []
