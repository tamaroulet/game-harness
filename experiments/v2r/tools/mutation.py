"""性質テストのオラクルの妥当性：受入に通った実装への変異の死滅率（docs/design/v2r_protocol.md §8、§13 の U2）。

    python experiments/v2r/tools/mutation.py run --wt <作業ツリー> --test-project <テストの csproj> --task T1 \\
        --mutate Game/Assets/Core/GameState.cs Game/Assets/Core/Board.cs --out <出力先> [--seeds 1,2,3]
    python experiments/v2r/tools/mutation.py summarize <T1 の報告.json> <T2 の報告.json> ... [--threshold 0.8]

- run：作業ツリー（タスクの終わりの実装）で Stryker.NET（`dotnet stryker`）を動かし、JSON の報告を出力先に置く。変異を入れる
  のは書き換えてよいファイルだけ（--mutate）。非公開シードは環境変数（propgen.HIDDEN_ENV）で渡す
- summarize：報告から、タスクごとの死滅率と状態ごとの数、変異の種類ごとの数を出す。死滅率が閾値（既定 0.8）に届かないタスクを
  「オラクル不合格」として印を付け、1 つでもあれば終了コード 1

**死滅率の定義**（Stryker の mutation score と同じ）：検出（Killed・Timeout）÷（検出＋未検出（Survived・NoCoverage））。
CompileError・RuntimeError・Ignored・Pending は数えない（有効な変異ではない）。

**防壁①**：報告にはコードの断片（source・位置）が入る。この道具は状態と変異の種類の名前だけを数え、コードは出さない。

**U2（未検証）**：falling-blocks のテストの構成で Stryker.NET が動くことと、その版は、まだ確かめていない（乾式の走行で確かめる）。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DETECTED = ("Killed", "Timeout")
UNDETECTED = ("Survived", "NoCoverage")
EXCLUDED = ("CompileError", "RuntimeError", "Ignored", "Pending")
THRESHOLD = 0.8
HIDDEN_ENV = "HARNESS_PROPERTY_SEEDS"  # propgen.HIDDEN_ENV と同じ（tests/test_v2r_tools.py で一致を確かめる）


def stryker_args(test_project, mutate, out_dir, project=None):
    """`dotnet stryker` の引数。報告は JSON だけ、変異を入れるのは mutate のファイルだけ。"""
    args = ["dotnet", "stryker", "--test-project", str(test_project), "--reporter", "json", "--output", str(out_dir)]
    if project:
        args += ["--project", str(project)]
    for m in mutate:
        args += ["--mutate", f"**/{Path(m).name}"]
    return args


def run(wt, test_project, mutate, out_dir, seeds=None, runner=subprocess.run, ttl=7200, project=None):
    """Stryker を動かし、JSON の報告のパスを返す。見つからなければ None。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **({HIDDEN_ENV: ",".join(str(s) for s in seeds)} if seeds else {}))
    r = runner(stryker_args(test_project, mutate, out_dir, project=project), cwd=str(wt), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=ttl, env=env)
    (out_dir / "stryker.rc").write_text(str(r.returncode), encoding="utf-8")
    reports = sorted(out_dir.rglob("mutation-report.json"))
    return reports[-1] if reports else None


def counts(report):
    """報告（mutation-testing-report-schema の JSON）→ ({状態: 数}, {変異の種類: 数})。コードは読まない。"""
    by_status, by_mutator = {}, {}
    for f in (report.get("files") or {}).values():
        for m in f.get("mutants") or []:
            s = m.get("status")
            by_status[s] = by_status.get(s, 0) + 1
            name = m.get("mutatorName") or "?"
            by_mutator[name] = by_mutator.get(name, 0) + 1
    return dict(sorted(by_status.items())), dict(sorted(by_mutator.items()))


def score(by_status):
    """死滅率（0〜1）。有効な変異が 0 なら None（測れていない）。"""
    detected = sum(by_status.get(s, 0) for s in DETECTED)
    valid = detected + sum(by_status.get(s, 0) for s in UNDETECTED)
    return round(detected / valid, 4) if valid else None


def summarize(reports, threshold=THRESHOLD):
    """[{"task", "score", "status", "mutators", "oracle_ok"}]。reports は {タスク: 報告の表}。

    死滅率が閾値に届かない・測れていない（有効な変異が 0）タスクは oracle_ok = False（オラクル不合格）。
    """
    out = []
    for task, report in sorted(reports.items(), key=lambda kv: int(kv[0].lstrip("T")) if kv[0].lstrip("T").isdigit() else 0):
        by_status, by_mutator = counts(report)
        s = score(by_status)
        unknown = sorted(set(by_status) - set(DETECTED + UNDETECTED + EXCLUDED))
        out.append({"task": task, "score": s, "status": by_status, "mutators": by_mutator,
                    "oracle_ok": s is not None and s >= threshold and not unknown, "unknown_status": unknown})
    return out


def table(rows, threshold=THRESHOLD):
    lines = [f"| タスク | 死滅率 | 検出 | 未検出 | 数えない | 判定（閾値 {threshold:.0%}） |", "|:--|:--|:--|:--|:--|:--|"]
    for r in rows:
        st = r["status"]
        det = sum(st.get(s, 0) for s in DETECTED)
        und = sum(st.get(s, 0) for s in UNDETECTED)
        exc = sum(st.get(s, 0) for s in EXCLUDED)
        verdict = "合格" if r["oracle_ok"] else "**オラクル不合格**"
        shown = "—" if r["score"] is None else f"{r['score']:.1%}"
        lines.append(f"| {r['task']} | {shown} | {det} | {und} | {exc} | {verdict} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="変異の死滅率（Stryker.NET）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--wt", required=True)
    r.add_argument("--test-project", required=True)
    r.add_argument("--project", help="変異対象の csproj（例 Core.csproj）")
    r.add_argument("--task", required=True)
    r.add_argument("--mutate", nargs="+", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--seeds")
    s = sub.add_parser("summarize")
    s.add_argument("reports", nargs="+", help="<タスク>=<報告.json> の形（例 T1=out/T1/mutation-report.json）")
    s.add_argument("--threshold", type=float, default=THRESHOLD)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else None
        path = run(args.wt, args.test_project, args.mutate, Path(args.out) / args.task, seeds, project=args.project)
        print(f"報告: {path}" if path else "NG: Stryker の報告が見つかりません（U2：Stryker.NET が動くかを確かめる）")
        return 0 if path else 1
    reports = {}
    for item in args.reports:
        task, _, path = item.partition("=")
        reports[task] = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = summarize(reports, args.threshold)
    print(table(rows, args.threshold))
    return 0 if all(x["oracle_ok"] for x in rows) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
