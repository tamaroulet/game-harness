"""v2-smoke-05 の門（V2-6.4 の検証。docs/design/v2_2_architecture_self_critique.md §4.2・§4.3）。

    python experiments/v2/tools/smoke_gate.py v2-smoke-05

終了コード 0 のときだけ、本走（V2-RUN）へ進む条件の機械の部分が成り立つ。見るもの：
- H1 健全性：A・B とも、マニフェストの全タスクの行があり、受入に通り、P2P の破壊 0、テストの改ざん 0、不変条件の違反 0
- H2 最初から編集に入る：実装役の全呼び出しで、agy の終わり方が SUCCESS（打ち切り・ERROR が無い）で、error_message の手番が 0
- 記録だけ（判定しない。N=1 では揺れと効果を分けられない）：最初の手番で編集の道具を呼んだ呼び出しの数、最初の手番の思考、
  思考だけの手番の数
本文は出さない（道具の名前・利用量・件数だけ）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "harness"))
import agy_stream  # noqa: E402

OUT_ROOT = Path("C:/src/.local/out/ab")
MANIFEST = ROOT / "experiments" / "v2" / "tasks.json"
EDIT_TOOLS = {"replace_file_content", "write_to_file", "multi_replace_file_content"}


def calls(run_dir, cond):
    """(タスク, 回, stdout) の列。A はドライバの、B は pipeline の実装役のログから。"""
    d = run_dir / cond
    out = []
    if cond == "A":
        for f in sorted(d.glob("T*_a*.implementer.log")):
            t = f.read_text(encoding="utf-8")
            out.append((f.name.split("_a")[0], f.name.split("_a")[1].split(".")[0],
                        t[t.index("# stdout\n") + 9:t.index("\n\n# stderr")]))
    else:
        for f in sorted(d.rglob("implementer_attempt_*.log")):
            t = f.read_text(encoding="utf-8")
            out.append((f.parent.name, f.stem.replace("implementer_attempt_", ""),
                        t[t.index("=== stdout ===\n") + 15:t.index("\n\n=== stderr ===")]))
    return out


def problems(run_id, out_root=OUT_ROOT, manifest=MANIFEST):
    tasks = [t["id"] for t in json.loads(Path(manifest).read_text(encoding="utf-8"))["tasks"]]
    run_dir = Path(out_root) / run_id
    found, notes = [], []
    for cond in ("A", "B"):
        path = run_dir / cond / "metrics.jsonl"
        if not path.exists():
            found.append(f"{cond}: {path} がありません")
            continue
        rows = {r["task"]: r for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}
        for t in tasks:
            r = rows.get(t)
            if r is None:
                found.append(f"{cond}/{t}: 行がありません")
                continue
            if not r.get("accepted"):
                found.append(f"{cond}/{t}: 受入に通っていません")
            if r.get("p2p_broken"):
                found.append(f"{cond}/{t}: P2P の破壊 {r['p2p_broken']}")
            if r.get("tests_tampered"):
                found.append(f"{cond}/{t}: テストの改ざん {len(r['tests_tampered'])}")
            inv = r.get("invariants") or {}
            if inv.get("failures") or not inv.get("build_ok", False):
                found.append(f"{cond}/{t}: 不変条件の違反 {inv.get('failures')}（build_ok={inv.get('build_ok')}）")
        cs = calls(run_dir, cond)
        if not cs:
            found.append(f"{cond}: 実装役の呼び出しのログがありません")
        first_edit = 0
        for task, n, stdout in cs:
            p = agy_stream.parse(stdout)
            o = p["outcome"]
            if o["status"] != "SUCCESS":
                found.append(f"{cond}/{task} 呼び出し {n}: 終わり方 {o['status']}（{o['error']}）")
            if o["error_steps"]:
                found.append(f"{cond}/{task} 呼び出し {n}: error_message の手番 {o['error_steps']}")
            tools = [s.get("tool") for s in p["steps"] if s.get("tool")]
            first_edit += bool(tools) and tools[0] in EDIT_TOOLS
            used = [s for s in p["steps"] if s.get("usage")]
            notes.append(f"{cond}/{task} 呼び出し {n}: 最初の手番の思考 {(used[0]['usage'].get('thinking_tokens') if used else None)}、"
                         f"思考だけの手番 {o['thinking_only_steps']}")
        notes.append(f"{cond}: 最初の道具が編集だった呼び出し {first_edit}/{len(cs)}")
    return found, notes


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    found, notes = problems(argv[0])
    for n in notes:
        print(f"記録: {n}")
    for f in found:
        print(f"NG: {f}")
    print("合格" if not found else f"不合格（{len(found)} 件）")
    return 0 if not found else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
