"""改善ループの段階（v3 §7-7・§7-9、L7）：propose → diff → pair → dev → held → pr → records。1 つの関数が 1 つの段階で、進んだら True、待つなら False を返す。"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_job  # noqa: E402
import cycle_judge as cj  # noqa: E402
import cycle_pr as cp  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402
import proposer_launch as pl  # noqa: E402
import proposer_stage as ps  # noqa: E402
import surface  # noqa: E402
import surface_apply as sa  # noqa: E402
from bench_start import BenchError, git  # noqa: E402
from cycle_base import (DONE, FAILED, OK, WAIT, Reject, Stop, active, bench_place, branch, chains_dir, home, job_done, job_step,  # noqa: E402, F401
                        notify, now_text, py, runner, save, set_active, settings, sh)

PROPOSE_TRIES, RETRY_AFTER = 2, 3600
GIT_TTL, FINISH_TTL, SUITE_TTL, ARGON_TTL = 300, 7200, 900, 1800
IMPLEMENTER = "config/bench_implementer_agy.json"
PINNED = "python -m harness.agy_pinned"


# ============================================================ propose：提案役

def collect_once(dest):
    """提案役の記録を取り込む。前の見回りが取り込んだ後に落ちていれば（コピーが無い）、取り込み済みの記録を返す。"""
    if Path(dest).exists():
        return ps.collect(hline_base.CONFIG, dest)
    store = pl.store_dir(hline_base.load_config(hline_base.CONFIG))
    recs = (json.loads(p.read_text(encoding="utf-8")) for p in store.glob("*.json"))
    return [r for r in recs if (r.get("staged") or {}).get("dest") == str(dest)]


def step_propose(st):
    p = st["propose"]
    if time.time() < p.get("not_before", 0):
        return False
    if not p.get("dest"):
        p["dest"] = str(ps.prepare(hline_base.CONFIG, st["baseline"]["dev"], root=runner()))
        save(st)
    dest = Path(p["dest"])
    result = job_step(st, f"propose{p['attempt']}", dest, [f"call {dest / ps.BATCH}"])
    if result == WAIT:
        return False
    recs = collect_once(dest) if result == OK else []
    if result == FAILED and dest.exists():
        ps.discard(dest)   # やり直しは必ずコピーを作り直す（前の未完の記録が混ざらないように）
    good = [r for r in recs if pl.ok(r)]
    if good:
        st["proposal"], st["phase"] = good[-1]["proposal"], "diff"
        return True
    p["last"] = [pl.line_of(r) for r in recs]
    if p["attempt"] >= PROPOSE_TRIES:
        raise Stop(f"提案役が {PROPOSE_TRIES} 回とも提案を残しませんでした")
    p.update(attempt=p["attempt"] + 1, dest=None, not_before=time.time() + RETRY_AFTER)
    return False


# ============================================================ diff：差分と検査と対象の選び

def named_stages(text):
    body = next((b for h, b in pl.sections(text) if h == pl.STAGE_HEAD), [])
    return {(m["base"], int(m["k"])) for line in body if (m := pl.STAGE.match(line.strip()))}


def all_bases():
    sets = settings()["sets"]
    return [b for names in sets.values() for b in names]


def run_suite(head):
    """新しいコミットの一時の作業ツリーで全件の試験を走らせる（CI で落ちる候補にトークンを使わない）。終了コード 0 でなければ Reject。"""
    wt = home() / "wt"
    shutil.rmtree(wt, ignore_errors=True)   # 前の見回りが途中で落ちた残り
    git(runner(), ["worktree", "prune"])
    git(runner(), ["worktree", "add", "-q", "--detach", str(wt), head])
    try:
        code, out, err = proc.run(py("harness.fastsuite", "discover", "-s", "tests"), wt, SUITE_TTL, "試験")
    finally:
        git(runner(), ["worktree", "remove", "--force", str(wt)])
    if code:
        raise Reject("試験", (out or err).strip()[-800:])


def step_diff(st):
    text = Path(st["proposal"]).read_text(encoding="utf-8")
    try:
        head = sa.apply(runner(), st["rev"], text, f"improve(surface): 改善ループ {st['id']} の提案の差分")
    except BenchError as e:
        raise Reject("差分", str(e)) from e
    git(runner(), ["update-ref", f"refs/heads/{branch(st)}", head])
    st["head"] = head
    code, out, err = proc.run(py("harness.surface", "check", "--repo", str(runner()), "--old", st["rev"], "--new", head,
                                 "--chains", str(chains_dir()), "--bases", ",".join(all_bases())), runner(), GIT_TTL, "面の検査")
    if code == 1:
        raise Reject("面の検査", out.strip()[-800:])
    if code:
        raise Stop(f"面の検査が走りませんでした: {(err or out).strip()[-300:]}")
    run_suite(head)
    first, second = (cj.public_rows(runner(), t) for t in st["baseline"]["dev"])
    picked = cj.targets(named_stages(text), cj.classify(first, second))
    if not picked:
        raise Stop("提案の出た段に、基準の 2 回で結果がそろったステージがありません（v3 §7-7 の 3：そのときに決める）")
    st["pair"] = {"stages": [f"{b}:{k}" for b, k in picked]}
    st["phase"] = "pair"
    return True


# ============================================================ pair：対の実行

def clones(st):
    """bench の置き場に旧版と新版のクローンを作る（作るときだけ git を動かす）。面の設定は作業ツリーから読まれるので、作業ツリーをその版に合わせる。"""
    place = bench_place(st["id"])
    place.mkdir(parents=True, exist_ok=True)
    for side, rev in (("old", st["rev"]), ("new", st["head"])):
        dest = place / side
        if dest.exists():
            continue
        tmp = place / f"{side}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        sh(["git", "clone", "-q", "--no-hardlinks", str(runner()), str(tmp)], GIT_TTL, "クローン")
        git(tmp, ["checkout", "-q", "--detach", rev])
        tmp.rename(dest)
    return place / "old", place / "new"


def staged_of(tag):
    """基準のタグの材料の置き場（公開の材料だけ。名は記録に書かない）。1 つでなければ Stop。"""
    found = [p for p in (machine.path("local") / "bench" / "chains").glob(f"{tag}-*")
             if (json.loads((p / "chain.json").read_text(encoding="utf-8")).get("tag") == tag)]
    if len(found) != 1:
        raise Stop(f"基準 {tag} の材料の置き場が 1 つではありません（{len(found)} 個）")
    return found[0]


def pair_runs(st):
    """(対のタグ, 基準のタグ) の列。対象が 1 つだけなら基準の 2 回のそれぞれの先端から 2 回、ほかは基準の 1 回目から 1 回（§7-7 の 3）。"""
    dev = st["baseline"]["dev"]
    refs = dev if len(st["pair"]["stages"]) == 1 else dev[:1]
    return [(f"{st['id']}-p{i}", ref) for i, ref in enumerate(refs, 1)]


def step_pair(st):
    old, new = clones(st)
    out = Path(st["baseline"]["out"])
    stages, runs = ",".join(st["pair"]["stages"]), pair_runs(st)
    cmds = [PINNED, *(f"python -m harness.bench_pair pair --tag {tag} --old {old.as_posix()} --old-rev {st['rev']} --new {new.as_posix()} --new-rev {st['head']} "
                      f"--staged {staged_of(ref)} --from {ref} --stages {stages} --out {out.as_posix()} --implementer {IMPLEMENTER}" for tag, ref in runs)]
    tags = [f"{tag}-{side}" for tag, _ in runs for side in ("old", "new")]
    watch = {"out": str(out), "tags": tags, "line": len(tags) * len(st["pair"]["stages"]) * settings()["guard"]["pair_run_tokens"]}
    if not job_done(st, "pair", new, cmds, watch):
        return False
    for tag in tags:
        sh(py("harness.bench_pair", "finish", "--tag", tag, "--repo", runner(), "--out", out), FINISH_TTL, f"対の採点 {tag}")
    for tag, _ in runs:
        sh(py("harness.bench_pair", "compare", "--tag", tag, "--repo", runner()), GIT_TTL, f"対の比べ {tag}")
    first, second = (cj.public_rows(runner(), t) for t in st["baseline"]["dev"])
    picked = [(s.split(":")[0], int(s.split(":")[1])) for s in st["pair"]["stages"]]
    counts = cj.pair_counts(picked, cj.classify(first, second),
                            [(cj.public_rows(runner(), f"{t}-old"), cj.public_rows(runner(), f"{t}-new")) for t, _ in runs])
    st["pair"].update(counts)
    if not counts["ok"]:
        raise Reject("対の実行", f"直した {counts['fixed']}・壊した {counts['broke']}")
    st["phase"] = "dev"
    return True


# ============================================================ dev・held：回帰の守り

def rows_of(kind, tag):
    return cj.private_rows(chains_dir() / "runs", tag) if kind == "held" else cj.public_rows(runner(), tag)


def base_totals(st, kind):
    return [cj.totals(rows_of(kind, t)) for t in st["baseline"][kind]]


def step_guard(st, kind):
    info, tag = st.setdefault(kind, {}), f"{st['id']}-{kind}"
    if not info.get("staged"):
        out = sh(py("harness.bench_chain", "stage", "--tag", tag, "--set", kind, "--bases", ",".join(settings()["sets"][kind])), GIT_TTL, "材料の写し")
        info["staged"] = out.strip().splitlines()[-1]
        save(st)
    new = clones(st)[1]
    out = new / ".bench"
    guard = settings()["guard"]
    watch = {"out": str(out), "tags": [tag], "line": cj.cost_line(base_totals(st, kind), guard["cost_ratio"])}
    cmds = [PINNED, f"python -m harness.bench_chain run --tag {tag} --repo {new.as_posix()} --base {st['head']} --staged {info['staged']} --implementer {IMPLEMENTER}"]
    if not job_done(st, kind, new, cmds, watch):
        return False
    sh(py("harness.bench_chain", "finish", "--tag", tag, "--repo", runner(), "--out", out), FINISH_TTL, f"採点 {tag}")
    info["totals"] = cj.totals(rows_of(kind, tag))
    judged = (cj.guard_held if kind == "held" else cj.guard_dev)(info["totals"], base_totals(st, kind), guard)
    info["why"] = judged["why"]
    drop_staged(st)
    if not judged["ok"]:
        raise Reject("回帰の守り：held-out" if kind == "held" else "回帰の守り：開発用", "；".join(judged["why"]))
    st["phase"] = "held" if kind == "dev" else "pr"
    return True


def drop_staged(st):
    """held-out の材料は、走った後に bench の置き場に残さない。"""
    staged = st.get("held", {}).get("staged")
    if staged and Path(staged).exists():
        shutil.rmtree(staged)


# ============================================================ pr・records：公開に出す

def check_public(name, text):
    """公開に出す文を見る。当たれば push せずに Stop（中身は出さない）。"""
    bases = all_bases()
    hits = cp.public_problems(name, text, surface.test_names_of(chains_dir(), bases), surface.packages_of(chains_dir(), bases))
    if hits:
        raise Stop("公開に出す文に当たりがあります: " + "、".join(hits[:5]))


def pr_number(head):
    """その枝の PR の番号。無ければ None。"""
    rows = json.loads(sh(["gh", "pr", "list", "--head", head, "--state", "all", "--json", "number"], GIT_TTL, "PR の一覧"))
    return rows[0]["number"] if rows else None


def create_pr(st, head, title, body, *flags):
    """PR を作る（前の見回りが作っていれば、それを使う）。番号を返す。"""
    number = pr_number(head)
    if number is not None:
        return number
    path = home() / st["id"] / "body.md"
    path.write_text(body, encoding="utf-8")
    url = sh(["gh", "pr", "create", "--base", "main", "--head", head, "--title", title, "--body-file", path, *flags], GIT_TTL, "PR の作成")
    return int(re.search(r"/pull/(\d+)", url).group(1))


def record(st):
    """ひな形に渡す、数だけの記録。"""
    pair = st["pair"]
    return {"id": st["id"], "rev": st["rev"], "head": st["head"], "dev": st["dev"]["totals"], "dev_base": base_totals(st, "dev"),
            "held": st["held"]["totals"], "held_base": base_totals(st, "held"),
            "pair": {"stages": pair["stages"], "fixed": pair["fixed"], "broke": pair["broke"], "same": pair["same"], "skipped": len(pair["skipped"])}}


def argon_verdict(number):
    """Argon の記録の、その PR の最後の (判定, 判断点)。無ければ None。"""
    path = Path(machine.read_json(hline_base.CONFIG)["argon"]) / "evals.jsonl"
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        if row.get("kind") == "post" and row.get("target") == str(number):
            return row.get("decision"), row.get("rationale")
    return None


def step_pr(st):
    pr, rec = st.setdefault("pr", {}), record(st)
    check_public("PR の本文", cp.surface_body(rec))
    sh(["git", "push", "-q", "origin", branch(st)], GIT_TTL, "push")
    pr["number"] = create_pr(st, branch(st), f"improve(surface): 改善ループ {st['id']} の差分", cp.surface_body(rec), "--draft")
    save(st)
    if not pr.get("argon"):
        focus = home() / st["id"] / "argon_focus.md"
        focus.write_text(cp.argon_focus(rec), encoding="utf-8")
        sh(py("harness.main_mirror", "sync"), GIT_TTL, "写しの同期")
        sh(py("harness.argon_eval", "post", pr["number"], "--tools", "--with", focus), ARGON_TTL, "Argon の事後の評価")
        pr["argon"] = argon_verdict(pr["number"]) or ["HIGH", "Argon の記録を読めませんでした"]
        save(st)
    body = cp.surface_body(rec, pr["argon"])
    check_public("PR の本文", body)
    path = home() / st["id"] / "body.md"
    path.write_text(body, encoding="utf-8")
    sh(["gh", "pr", "edit", pr["number"], "--body-file", path], GIT_TTL, "PR の本文の更新")
    if pr["argon"][0] != "HIGH":
        sh(["gh", "pr", "ready", pr["number"]], GIT_TTL, "PR を ready に")
    st["outcome"], st["phase"] = "取り込みの候補", "records"
    return True


def summary(st):
    """公開の 1 周の記録（数と決まった語だけ）。"""
    pair = st.get("pair") or {}
    return {"id": st["id"], "rev": st["rev"], "head": st.get("head"), "outcome": st.get("outcome"), "ended": now_text(),
            "pair": {k: pair.get(k) for k in ("stages", "fixed", "broke", "same")} if pair else None,
            "dev": (st.get("dev") or {}).get("totals"), "held": (st.get("held") or {}).get("totals")}


def records_files(st):
    """{パス: バイト列}：この周の公開の集計と、1 周の記録。公開の文の当たりを見る。"""
    root, cid = runner() / "bench" / "records", st["id"]
    files = {p.relative_to(runner()).as_posix(): p.read_bytes()
             for p in [*sorted(root.glob(f"chains/{cid}-*/*.json")), *sorted(root.glob(f"pairs/{cid}-*.json"))]}
    files[f"bench/records/cycles/{cid}.json"] = (json.dumps(summary(st), ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    for path, data in files.items():
        check_public(path, data.decode("utf-8"))
    return files


def step_records(st):
    head = f"{branch(st)}-records"
    if not st.get("records_head"):
        st["records_head"] = sa.commit(runner(), st["rev"], records_files(st), f"docs(records): 改善ループ {st['id']} の集計")
        git(runner(), ["update-ref", f"refs/heads/{head}", st["records_head"]])
        save(st)
    body = cp.records_body({"id": st["id"], "rev": st["rev"]}, st.get("outcome"))
    check_public("記録の PR の本文", body)
    sh(["git", "push", "-q", "origin", head], GIT_TTL, "push")
    st["records_pr"] = create_pr(st, head, f"docs(records): 改善ループ {st['id']} の集計", body)
    st["phase"], st["status"], st["ended"] = "done", DONE, now_text()
    set_active(None)
    notify(f"改善ループ {st['id']} が終わりました（{st.get('outcome')}）")
    return False


STEPS = {"propose": step_propose, "diff": step_diff, "pair": step_pair, "dev": lambda st: step_guard(st, "dev"),
         "held": lambda st: step_guard(st, "held"), "pr": step_pr, "records": step_records}
KNOWN = (Stop, BenchError, bench_job.JobError, ps.StageError, cp.TemplateError, OSError, ValueError, KeyError)
