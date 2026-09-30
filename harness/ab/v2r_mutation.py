"""走行のタスクごとの終わりの実装に、変異の死滅率（Stryker.NET）を一括で当てる（docs/design/v2r_protocol.md §8、
docs/design/v2r_dry03_ceiling.md §6）。v2r-dry-03 の天井（A0 の欠陥 0 件）が「系列が易しい」のか「オラクルが弱い」のかを
見分けるための診断。

    python -m harness.ab.v2r_mutation run --run-id v2r-dry-03 [--condition A0] [--scope all|task] [--tasks T1,T2] [--force]
    python -m harness.ab.v2r_mutation summarize --run-id v2r-dry-03 [--condition A0] [--scope all|task] [--threshold 0.8]

- 範囲（--scope）：all（既定）は impl_dir の .cs すべて、task はそのタスクで足した・変えた .cs だけ。出力は
  `<出力>/mutation/`（all）と `<出力>/mutation-task/`（task）に分ける

- run：metrics.jsonl の各行の `resume.end_commit`（タスクの終わりのコミット）から一時の worktree を作り、
  `experiments/v2r/tools/mutation.py` の run で Stryker を動かす。変異を入れるのは、そのコミットの impl_dir の下の .cs
  （実装役が書き換えてよいファイル）だけ。非公開シードは走行の測定と同じ（common.hidden_seeds）。報告が既にあるタスクは
  飛ばす（--force で測り直す）。終わったら summarize と同じ集計を書く
- summarize：`<出力>/mutation/<タスク>/` の報告から、タスクごとの死滅率の表を `mutation/summary.md` と `summary.json` に
  書いて表示する。閾値（既定 0.8）に届かないタスクが 1 つでもあれば終了コード 1

**防壁①**：Stryker の標準出力・報告の本文（コードの断片が入る）は表示しない。数えるのは状態と変異の種類の名前だけ。
"""
import argparse
import json
import sys
import time
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))
_TOOLS = _HARNESS.parent / "experiments" / "v2r" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import envcheck  # noqa: E402
import exitcode  # noqa: E402
import mutation  # noqa: E402
import project  # noqa: E402
import provenance  # noqa: E402
from ab import common  # noqa: E402

PROJECT = "falling-blocks"
MANIFEST = common.ROOT / "experiments" / "v2r" / "tasks.json"
CORE_PROJECT = "Core.csproj"   # falling-blocks#20 で実装をテストから分けた csproj（U2）
SCOPES = ("all", "task")


def end_commits(out):
    """metrics.jsonl → [(タスク, タスクの終わりのコミット)]（行の順）。再開の情報が無い行は止める。"""
    path = Path(out) / "metrics.jsonl"
    if not path.exists():
        raise common.ABError(f"metrics.jsonl がありません: {path}")
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        commit = (r.get("resume") or {}).get("end_commit")
        if not commit:
            raise common.ABError(f"{r.get('task')} の行にタスクの終わりのコミットがありません（古い走行）")
        pairs.append((r["task"], commit))
    return pairs


def impl_files(repo, commit, impl_dir, git=common.git, since=None):
    """そのコミットの impl_dir の下の .cs（パスだけ。本文は読まない）。since を与えると、since からそのコミットまでに
    足した・変えたものだけ（範囲 task）。"""
    if since:
        out = git(["diff", "--name-only", "--diff-filter=AM", since, commit, "--", impl_dir], repo, "git diff --name-only")
    else:
        out = git(["ls-tree", "-r", "--name-only", commit, "--", impl_dir], repo, "git ls-tree")
    return [p for p in out.splitlines() if p.endswith(".cs")]


def env_require(out_path, measure=envcheck.measure):
    """実行環境の照合を、変異の診断が使うもの（OS・Python・dotnet・dotnet-stryker・テストの NuGet パッケージ）に絞る。

    実装役の CLI（agy・claude）は使わないので照合しない（agy の自動更新で診断まで止めない。版のずれは走行の照合で止まる）。
    """
    pinned = envcheck.load_pinned()
    pinned["cli"] = {k: v for k, v in (pinned.get("cli") or {}).items() if k == "dotnet"}
    measured = measure(clis=("dotnet",), tools=tuple(pinned.get("tools") or ()),
                       csproj=envcheck.game_csproj(PROJECT) if "test_packages" in pinned else None)
    return envcheck.require(out_path, measured=measured, pinned=pinned)


def mutation_dir(out, scope="all"):
    return Path(out) / ("mutation" if scope == "all" else f"mutation-{scope}")


def run_all(run_id, condition, manifest=MANIFEST, tasks=None, force=False, wt_root=common.WT_ROOT,
            out_root=common.OUT_ROOT, stryker=mutation.run, git=common.git, scope="all"):
    """タスクごとに Stryker を動かす。{タスク: 報告のパス | None}。

    scope：all はそのコミットの impl_dir の .cs すべて（まだ性質の無い後のタスクの規則の変異も未検出に入るので、途中の
    タスクでは死滅率が低く出る。T10 の値が実装全体のオラクルの強さ）。task はそのタスクで足した・変えた .cs だけ
    （前のタスクの終わり、T1 は base_commit から）。ファイルの単位なので、同じファイルの触っていない部分も入る。
    """
    if scope not in SCOPES:
        raise common.ABError(f"範囲は {SCOPES} のどれかです: {scope!r}")
    m = common.load_manifest(manifest)
    proj = project.load(PROJECT)
    repo = proj["repo_dir"]
    out = common.paths(run_id, condition, wt_root, out_root)["out"]
    pairs = end_commits(out)
    starts = dict(zip([t for t, _ in pairs], [m.get("base_commit")] + [c for _, c in pairs[:-1]]))
    if tasks:
        unknown = sorted(set(tasks) - {t for t, _ in pairs})
        if unknown:
            raise common.ABError(f"metrics.jsonl に行の無いタスク: {', '.join(unknown)}")
        pairs = [(t, c) for t, c in pairs if t in tasks]
    wt = Path(wt_root) / f"{run_id}-{condition}-mutation"
    done = {}
    for task, commit in pairs:
        dest = mutation_dir(out, scope) / task
        if not force and sorted(dest.rglob("mutation-report.json")):
            print(f"[{task}] 報告があるので飛ばします（測り直すなら --force）")
            done[task] = sorted(dest.rglob("mutation-report.json"))[-1]
            continue
        if wt.exists():
            git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove（変異）", check=False)
        git(["worktree", "add", "-q", "--detach", str(wt), commit], repo, "git worktree add（変異）")
        try:
            files = impl_files(repo, commit, proj["impl_dir"], git, since=starts[task] if scope == "task" else None)
            if not files and scope == "task":
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "run.json").write_text(json.dumps({"task": task, "commit": commit, "scope": scope, "files": 0,
                                                           "skipped": "タスクで impl_dir の .cs を足し・変えていない"},
                                                          ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"[{task}] {commit[:7]}：タスクで変えた .cs が無いので測らない")
                done[task] = None
                continue
            if not files:
                raise common.ABError(f"{task}：{proj['impl_dir']} の下に .cs がありません（{commit[:7]}）")
            seeds = common.hidden_seeds(run_id, task, m["measure_hidden_seeds"])
            t0 = time.monotonic()
            path = stryker(wt, proj["fast_test_project"], files, dest, seeds, project=CORE_PROJECT)
            (dest / "run.json").write_text(json.dumps(
                {"task": task, "commit": commit, "scope": scope, "files": len(files), "seeds": len(seeds),
                 "stryker_args": mutation.stryker_args(proj["fast_test_project"], files, dest, project=CORE_PROJECT),
                 "seconds": round(time.monotonic() - t0, 1), "report": str(path) if path else None},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[{task}] {commit[:7]}：変異の対象 {len(files)} ファイル、"
                  + ("報告あり" if path else "NG: 報告なし（stryker.rc を見る）"))
            done[task] = path
        finally:
            git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove（変異）", check=False)
    return done


def summarize(out, threshold=mutation.THRESHOLD, scope="all"):
    """報告のあるタスクの表を summary.md・summary.json に書く。(rows, 報告の無いタスク)。

    範囲 task で、タスクが .cs を変えていないので測らなかったもの（run.json の skipped）は、報告の無いタスクに数えない。
    """
    reports, missing, skipped = {}, [], []
    base = mutation_dir(out, scope)
    for d in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        found = sorted(d.rglob("mutation-report.json"))
        meta = d / "run.json"
        if found:
            reports[d.name] = json.loads(found[-1].read_text(encoding="utf-8"))
        elif meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("skipped"):
            skipped.append(d.name)
        else:
            missing.append(d.name)
    rows = mutation.summarize(reports, threshold)
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps({"scope": scope, "threshold": threshold, "rows": rows,
                                                   "missing": missing, "skipped": skipped},
                                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = f"範囲：{scope}\n\n" + mutation.table(rows, threshold)
    if skipped:
        text += f"\n\n- 測らなかったタスク（.cs を変えていない）：{', '.join(skipped)}"
    if missing:
        text += f"\n\n- NG: 報告の無いタスク：{', '.join(missing)}"
    (base / "summary.md").write_text(text + "\n", encoding="utf-8")
    return rows, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="走行のタスクごとの終わりの実装に、変異の死滅率を一括で当てる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "summarize"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True)
        p.add_argument("--condition", default="A0")
        p.add_argument("--out-root", default=str(common.OUT_ROOT))
        p.add_argument("--threshold", type=float, default=mutation.THRESHOLD)
        p.add_argument("--scope", choices=SCOPES, default="all",
                       help="all＝impl_dir の .cs すべて（既定）、task＝そのタスクで足した・変えた .cs だけ")
    r = sub.choices["run"]
    r.add_argument("--manifest", default=str(MANIFEST))
    r.add_argument("--tasks", help="このタスクだけ（例 T1,T2）")
    r.add_argument("--force", action="store_true", help="報告があっても測り直す")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "run":
            # 走行と同じく、実行環境（dotnet-stryker の版を含む）と来歴を照合して残す。違えば・汚れていれば回さない
            base = mutation_dir(common.paths(args.run_id, args.condition, out_root=args.out_root)["out"], args.scope)
            try:
                env_require(base / "env.json")
                provenance.require(base / "provenance.json", manifest=args.manifest)
            except (envcheck.EnvError, provenance.ProvenanceError) as e:
                raise common.ABError(str(e))
            run_all(args.run_id, args.condition, args.manifest,
                    tasks=args.tasks.split(",") if args.tasks else None, force=args.force, out_root=args.out_root,
                    scope=args.scope)
        out = common.paths(args.run_id, args.condition, out_root=args.out_root)["out"]
        rows, missing = summarize(out, args.threshold, args.scope)
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT
    print((mutation_dir(out, args.scope) / "summary.md").read_text(encoding="utf-8").rstrip())
    print(f"集計: {mutation_dir(out, args.scope) / 'summary.md'}")
    return 0 if rows and not missing and all(x["oracle_ok"] for x in rows) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
