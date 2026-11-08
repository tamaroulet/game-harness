"""素のエージェント A の走らせ役（v3 §8 の E5）：bench_chain の stage が写した鎖を、段ごとに実装役（agy）を新しく 1 回起動して積む。

    python -m harness.bench_solo run --tag <t> --repo <clone> --base <rev> --staged <dir> --implementer <json> [--out <dir>]

stage と finish は bench_chain のまま（stage の --box が要る。A は箱でだけ走らせる）。
B（bench_chain run）と揃えるもの：段 0 と段の開始のコミット（s0_commit・start_commit）、What の文（what_text）と検証コマンド、
箱と実装役の設定、呼び出しの経路（hline_agy.implement：生ログの形・時間切れ・利用枠・モデルの照合・受け取りの決まり）、
試行の後のコミット（B の H ラインと同じく、作業の木の全部を add する）、走りのディレクトリと鎖の記録の形（finish がそのまま採点する）。
違えるもの（ハーネスの構造）：門・落ちた出力を渡すやり直し・受け取らなかった知らせ・頼みの組み方（hline_prompt）。
A は段ごとに 1 回だけ呼ぶ。呼び出しの時間は B の段の上限（bench_chain.STAGE_SECONDS）と同じで、トークンは呼び出しの途中では
見ない（B も poll の間でしか見ない）。頼みは docs/bench/prompts/solo.md の前置きに、What の文と検証コマンドを足したもの。
走り直しは環境の破れ（箱の確かめ・Infra）だけ、bench_chain.RETRIES 回まで。利用枠が尽きたら、その段を無効と書いて止める
（終了コード 2。回復の後に同じ命令で続きから。書き終えた鎖の記録と走りのディレクトリは使う）。
"""
import argparse
import datetime
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import box  # noqa: E402
import chain_score  # noqa: E402
import exitcode  # noqa: E402
import hline_agy  # noqa: E402
import hline_base  # noqa: E402
from bench_start import BenchError, git  # noqa: E402
from hline_base import Infra, Quota  # noqa: E402

ARM = "solo"
PROMPT = bench_run.ROOT / "docs" / "bench" / "prompts" / "solo.md"
IDENT = {"GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@localhost",
         "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@localhost"}   # bench_start.commit_lines と同じ名
LEFT = "前の起動の残り（run.json が無い。中断）"


class QuotaStop(Exception):
    """実装役の利用枠が尽きた。走りを止める（回復の後に続きから）。"""


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ============================================================ 頼み

def prompt_text(what, rows):
    """A の頼み：固定の前置き、B の What と同じ文、B の台帳の節と同じ検証コマンド。門・やり直し・中断の手順・目次は入れない。"""
    command = rows[0]["verification"]["command"]
    return "\n".join([PROMPT.read_text(encoding="utf-8").strip(), "", "---", what.strip(), f"- 検証コマンド: {command}", ""])


# ============================================================ 1 回の走り

def clone_at(repo, work, start):
    """start を取り出した作業の木（B の Factory.setup_clone と同じく、start を押してから取り出す）。"""
    clone = Path(work) / "clone"
    clone.mkdir(parents=True, exist_ok=True)
    git(clone, ["init", "-q"])
    git(repo, ["push", "-q", "-f", str(clone), f"{start}:refs/heads/start"])
    git(clone, ["checkout", "-q", "-f", "--detach", start])
    return clone


def commit_result(clone, start, message):
    """受け取った木を start の子のコミットにする（変わっていなければ start のまま）。先端が start から伸びていないと、finish が境界の違反にする。"""
    env = {**os.environ, **IDENT}
    git(clone, ["add", "-A"], env=env)
    tree = git(clone, ["write-tree"], env=env).decode().strip()
    if tree == git(clone, ["rev-parse", f"{start}^{{tree}}"]).decode().strip():
        return start
    return git(clone, ["commit-tree", tree, "-p", start, "-m", message], env=env).decode().strip()


def keep_tip(clone, head, rundir):
    """先端を走りの記録の bundle にする（finish がここから採点する。B の Factory.finalize と同じ形）。"""
    (rundir / chain_score.BUNDLE).parent.mkdir(parents=True, exist_ok=True)
    git(clone, ["update-ref", "refs/heads/tip", head])
    git(clone, ["bundle", "create", "-q", str(rundir / chain_score.BUNDLE), "refs/heads/tip"])


def box_problems(run):
    """箱の確かめ（B の ChainStage.guard と同じ）。像の ID を run に書き、外れたところの列を返す。"""
    settings = box.settings()
    run["box_image"] = box.require(settings)
    return box.check(settings)


def call(ctx, run, rundir, clone, prompt, log):
    """実装役を 1 回呼び（B の実装役と同じ hline_agy.implement）、受け取った木をコミットして先端にする。"""
    host = types.SimpleNamespace(box_dropped=None)
    cfg = {"implementer": ctx.agent, "box": {"package": ctx.pkg}, "ttl_seconds": {"implementer": bench_chain.STAGE_SECONDS}}
    code, models, usage, _, turns = hline_agy.implement(host, cfg, clone, prompt, log)
    run.update(code=code, models=models, usage=usage, turns=turns, dropped=len(host.box_dropped or []))
    run["head"] = commit_result(clone, run["start"], f"bench solo: {ctx.base} s{ctx.k}")
    keep_tip(clone, run["head"], rundir)
    if code == hline_agy.TIMEOUT_EXIT:
        run["stop"] = "time"   # 時間切れでも、そこまでに受け取った木を採点する（B の段の上限と同じ扱い）


def attempt(ctx, run, rundir, work, prompt, log):
    """箱の確かめと呼び出し。環境の破れは run に無効と書く。利用枠が尽きたときだけ、その文を返す。"""
    try:
        if (problems := box_problems(run)):
            run["invalid"], run["reason"] = "REQ-10", f"箱の確かめ: {' / '.join(problems)[:400]}"
            return None
        call(ctx, run, rundir, clone_at(ctx.repo, work, run["start"]), prompt, log)
    except Quota as e:
        run["invalid"], run["reason"] = "quota", str(e)[:400]
        return str(e)
    except (Infra, box.BoxError, BenchError, OSError) as e:
        run["invalid"], run["reason"] = "REQ-22", str(e)[:400]
    return None


def env_record(run):
    """走りの環境の記録。欄を B と同じにするため、B の Factory.env をそのまま使う（A は計画役を呼ばないので cli_version は null、
    利用枠の回復待ちはしないので 0）。"""
    me = types.SimpleNamespace(task=run["task"], run_id=run["run_id"], run=run, started_at=run["started_at"], timer=bench_run.Timer(),
                               cli_version=lambda role="planner": box.agy_version() if role == "implementer" else None)
    return {**bench_run.Factory.env(me), "arm": ARM}


def write_records(ctx, rundir, run):
    """B の走りのディレクトリと同じ置き場に書く。factory/config.json が無いと、finish のモデルの照合（bench_run.model_problem）が
    hline.json の既定の実装役のモデルと照らし、全部の段を REQ-02 で無効にする。"""
    for d in ("inputs", "calls", "factory"):
        (rundir / d).mkdir(parents=True, exist_ok=True)
    (rundir / chain_score.BUNDLE).parent.mkdir(parents=True, exist_ok=True)
    (rundir / "inputs" / PROMPT.name).write_bytes(PROMPT.read_bytes())
    (rundir / "inputs" / "spec.md").write_bytes(ctx.spec.read_bytes())
    hline_base.write_json(rundir / "factory" / "config.json", {"implementer": ctx.agent})
    hline_base.write_json(rundir / "env.json", env_record(run))
    hline_base.write_json(rundir / "factory" / "run.json", run)


def solo_stage(ctx, run_id, start, prompt):
    """段の 1 回の走り。走りのディレクトリに B と同じ形の記録（factory/run.json・calls/・items/・git/・env.json・inputs/・factory/config.json）
    を書いて run を返す。利用枠が尽きたら、無効と書いてから QuotaStop。"""
    rundir, work = bench_run.run_dir(ctx.out, ctx.base, run_id), bench_run.work_dir(ctx.out, ctx.base, run_id)
    log = rundir / "items" / bench_chain.WHAT / "implementer-1.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    (log.parent / "what.md").write_text(ctx.what, encoding="utf-8")
    run = {"task": ctx.base, "run_id": run_id, "arm": ARM, "base": ctx.tip, "start": start, "head": None, "started_at": now(),
           "invalid": None, "reason": None, "stop": None, "tries": 0, "tokens": {}, "prompt_sha256": bench_run.sha256_file(PROMPT)}
    quota = attempt(ctx, run, rundir, work, prompt, log)
    calls = bench_run.log_calls(rundir / "items", set())
    bench_run.write_calls(rundir, calls)
    run.update(tries=int(log.is_file()), tokens=bench_run.tokens_of(calls))
    if run["head"] and not run["stop"]:
        run["stop"] = "tokens" if run["tokens"]["total"] >= bench_chain.STAGE_TOKENS else "done"
    write_records(ctx, rundir, run)
    if quota:
        raise QuotaStop(quota)
    return run


# ============================================================ 鎖

def stage_inputs(ctx, k):
    """段 k の開始のコミットと頼み（bench_chain.run_step と同じ作り）。"""
    spec = ctx.staged / ctx.base / f"s{k}" / "spec.md"
    if bench_run.sha256_file(spec) != ctx.entry["spec_sha256"][f"s{k}"]:
        raise BenchError(f"{spec} の指紋が chain.json と違います")
    info = bench_chain.spec_info(spec.read_text(encoding="utf-8"))
    files = bench_chain.changeable(ctx.repo, ctx.tip, ctx.pkg, k)
    rows = bench_chain.ledger_rows(ctx.pkg, k, info, files)
    ctx.k, ctx.spec, ctx.what = k, spec, bench_chain.what_text(ctx.base, ctx.pkg, k, info, files)
    return bench_chain.start_commit(ctx.repo, ctx.tip, ctx.base, ctx.pkg, k, spec.read_bytes(), rows), prompt_text(ctx.what, rows)


def stage_row(k, attempt_no, run_id, done, left, tip, start):
    """鎖の記録の段の行（bench_chain.run_step と同じ欄）。done は書き終えた run（無ければ空）。"""
    invalid = done.get("invalid") or (None if done else "ABORT")
    reason = done.get("reason") or (LEFT if left and not done else None)
    return {"k": k, "attempt": attempt_no, "run_id": run_id, "code": 0 if done else 2, "base": done.get("base") or tip,
            "start": done.get("start") or start, "head": done.get("head"), "invalid": invalid, "reason": reason,
            "stop": done.get("stop"), "tries": done.get("tries"), "tokens": (done.get("tokens") or {}).get("total"), "resumed": left}


def run_step(ctx, k):
    """段 k を走らせる。前の起動の走りのディレクトリは使い（run.json の無い残りは中断した試しに数える）、無効なら RETRIES 回まで
    走り直す。済んだ行を返す（駄目なら None）。"""
    start, prompt = stage_inputs(ctx, k)
    for attempt_no in range(bench_chain.RETRIES + 1):
        run_id = f"{ctx.tag}-s{k}" + (f"-r{attempt_no}" if attempt_no else "")
        left = bench_run.run_dir(ctx.out, ctx.base, run_id).exists()
        done = bench_run.read_json(bench_run.run_dir(ctx.out, ctx.base, run_id) / "factory" / "run.json", {}) if left \
            else solo_stage(ctx, run_id, start, prompt)
        st = stage_row(k, attempt_no, run_id, done, left, ctx.tip, start)
        ctx.rec["stages"].append(st)
        if not st["invalid"] and st["head"]:
            return st
    return None


def run_chain(ctx, rev, kind):
    """土台 1 本の鎖。記録は bench_chain.run_chain と同じ形に、箱の印と腕の名を足したもの。"""
    ctx.tip = bench_chain.s0_commit(ctx.repo, rev, ctx.base, ctx.pkg, ctx.staged / ctx.base / "s0" / "tree")
    ctx.rec = {"tag": ctx.tag, "set": kind, "base": ctx.base, "package": ctx.pkg, "rev": rev, "s0": ctx.tip, "stages": [],
               "stopped": None, "box": True, "arm": ARM}
    for k in range(1, int(ctx.entry["stages"]) + 1):
        st = run_step(ctx, k)
        if st is None:
            ctx.rec["stopped"] = f"s{k}"
            break
        ctx.tip = bench_chain.fetch_tip(ctx.repo, bench_run.run_dir(ctx.out, ctx.base, st["run_id"]), f"refs/chains/{ctx.tag}/{ctx.base}/s{k}")
    return ctx.rec


def load_agent(path):
    doc = bench_run.read_json(Path(path), None)
    if not isinstance(doc, dict):
        raise BenchError(f"--implementer の中身がオブジェクトではありません: {path}")
    return doc


def problem(a, doc):
    """走らせる前に止める理由（無ければ None）。"""
    if doc.get("tag") != a.tag or not doc.get("bases"):
        return f"{a.staged} の chain.json が無いか、tag が {a.tag} ではありません"
    if not doc.get("box"):
        return "A は箱でだけ走らせます（stage に --box を付けて写し直す）"
    return bench_run.version_problem(Path(a.repo), a.base)


def run(a):
    repo, staged = Path(a.repo), Path(a.staged)
    out = Path(a.out) if a.out else repo / ".bench"
    doc = bench_run.read_json(staged / "chain.json", {})
    if (why := problem(a, doc)):
        print(f"ABORT: {why}", file=sys.stderr)
        return 2
    try:
        agent = load_agent(a.implementer)
        rev = git(repo, ["rev-parse", "--verify", "-q", f"{a.base}^{{commit}}"]).decode().strip()
        with bench_run.KeepAwake(), box.Keep(box.settings()):
            for entry in doc["bases"]:
                path = out / "chains" / a.tag / f"{entry['base']}.json"
                if path.exists():
                    continue
                ctx = types.SimpleNamespace(tag=a.tag, repo=repo, out=out, staged=staged, entry=entry, base=entry["base"],
                                            pkg=entry["package"], agent=agent)
                rec = run_chain(ctx, rev, doc.get("set"))
                path.parent.mkdir(parents=True, exist_ok=True)
                hline_base.write_json(path, rec)
    except QuotaStop as e:
        print(f"STOP: 実装役の利用枠が尽きました。回復の後に同じ命令で続きから走ります: {e}", file=sys.stderr)
        return 2
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--tag", required=True, type=bench_chain.tag)
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--staged", required=True)
    p.add_argument("--implementer", required=True)
    p.add_argument("--out")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
