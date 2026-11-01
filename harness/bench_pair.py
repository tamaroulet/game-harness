"""対の走り（外側の周期の比べ）：基準の走りの段 k−1 の先端から段 k だけを、旧版と新版で交互に走らせ、採点し、比べる。

    python -m harness.bench_pair run     --tag <t> --repo <clone> --base <rev> --staged <dir> --from <基準の tag> --stages <土台:段,...> [--out <dir>]
    python -m harness.bench_pair pair    --tag <p> --old <clone> --old-rev <rev> --new <clone> --new-rev <rev> --staged <dir> --from <基準の tag> --stages <土台:段,...> --out <dir> [--allow-same]
    python -m harness.bench_pair finish  --tag <t> --repo <clone> [--chains <dir>] [--out <dir>]
    python -m harness.bench_pair compare --tag <p> --repo <clone>

run と pair は bench のアカウント、finish は操縦士のアカウント。版ごとのクローンで、その版のコードを子のプロセスとして起こす
（走らせ役と --base の harness/ の木が同じでなければ走りは止まるので、1 つのプロセスで 2 つの版は走らせられない）。
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import chain_score  # noqa: E402
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

FIELDS = ("new", "old", "kept", "new_at_start", "invalid", "reason", "tokens", "tries")
SUMMARY = ("tag", "set", "base", "rev", "stopped")   # dev でない組の公開の集計に残す欄（ほかは合計だけ）


# ============================================================ run（bench のアカウント）

def parse_stages(text, doc):
    """「土台:段,...」を [(土台, 段)] に。土台が chain.json に無い・段が 1〜段の数の外・重なりは BenchError。"""
    counts = {e["base"]: int(e["stages"]) for e in doc.get("bases") or []}
    pairs = []
    for item in (x.strip() for x in text.split(",")):
        base, _, k = item.partition(":")
        if base not in counts:
            raise BenchError(f"--stages の土台 {base!r} が chain.json にありません")
        if not k.isdigit() or not 1 <= int(k) <= counts[base]:
            raise BenchError(f"--stages の {item!r} の段が 1〜{counts[base]} ではありません")
        if (base, int(k)) in pairs:
            raise BenchError(f"--stages の {item!r} が重なっています")
        pairs.append((base, int(k)))
    return pairs


def plan(staged, ref, stages):
    """(chain.json, [(土台, 段)])。chain.json の tag が基準の tag でない・--stages の形が違えば BenchError。"""
    doc = bench_run.read_json(Path(staged) / "chain.json", {})
    if doc.get("tag") != ref or not doc.get("bases"):
        raise BenchError(f"{staged} の chain.json が無いか、tag が基準の {ref} ではありません")
    return doc, parse_stages(stages, doc)


def ref_attempt(ref, k):
    """基準の記録の段 k の試しのうち、最後の「無効でなく head のある」もの。無ければ None。"""
    ok = [st for st in ref.get("stages") or [] if st.get("k") == k and not st.get("invalid") and st.get("head")]
    return ok[-1] if ok else None


def start_tip(a, repo, out, staged, entry, ref, k):
    """段 k の起点 (コミット, None)。基準の段 k−1 の先端、段 1 は基準の版（記録の rev）の木からの段 0 の先端。
    基準の記録が無い・段 k−1 に使える試しが無ければ (None, 理由)。"""
    base = entry["base"]
    if not ref:
        return None, f"基準の記録がありません: {Path(out) / 'chains' / a.ref / f'{base}.json'}"
    if k == 1:
        return bench_chain.s0_commit(repo, ref["rev"], base, entry["package"], staged / base / "s0" / "tree"), None
    if (st := ref_attempt(ref, k - 1)) is None:
        return None, f"基準の段 {k - 1} に使える試し（無効でなく head のあるもの）がありません"
    return bench_chain.fetch_tip(repo, bench_run.run_dir(out, base, st["run_id"]), f"refs/pairs/{a.tag}/{base}/s{k - 1}"), None


def load_rec(path, a, entry, ref, rev, ks):
    """前の起動の記録（あれば）か、bench_chain run と同じ形の新しい記録に、from・set・pairs を足したもの。"""
    rec = bench_run.read_json(path, {}) or {"tag": a.tag, "base": entry["base"], "package": entry["package"], "rev": rev,
                                            "stages": [], "skipped": [], "stopped": None}
    if rec.get("from", a.ref) != a.ref:
        raise BenchError(f"{path} は基準 {rec.get('from')} の記録です（--from は {a.ref}）")
    rec.update({"from": a.ref, "set": ref.get("set"), "pairs": ks})
    return rec


def run_base(a, repo, out, staged, rev, entry, ks):
    """土台 1 つの頼まれた段を、どれも基準の先端から走らせ、段が終わるたびに記録を書く。
    記録に試しのある段は走らせ直さない（中断の後の残りは run_step が扱う）。飛ばした段は、次の起動で試し直す。"""
    path = out / "chains" / a.tag / f"{entry['base']}.json"
    ref = bench_run.read_json(out / "chains" / a.ref / f"{entry['base']}.json", {})
    rec = load_rec(path, a, entry, ref, rev, ks)
    done = {st["k"] for st in rec["stages"]}
    for k in (k for k in ks if k not in done):
        rec["skipped"] = [s for s in rec["skipped"] if s["k"] != k]
        tip, why = start_tip(a, repo, out, staged, entry, ref, k)
        if tip is None:
            rec["skipped"].append({"k": k, "reason": why})
            print(f"{entry['base']} 段 {k} を飛ばします: {why}", file=sys.stderr)
        else:
            bench_chain.run_step(a, repo, out, staged, entry, tip, k, rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        hline_base.write_json(path, rec)


def run(a):
    repo, staged = Path(a.repo), Path(a.staged)
    out = Path(a.out) if a.out else repo / ".bench"
    try:
        doc, pairs = plan(staged, a.ref, a.stages)
        if (why := bench_run.version_problem(repo, a.base)):
            raise BenchError(why)
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    try:
        rev = git(repo, ["rev-parse", "--verify", "-q", f"{a.base}^{{commit}}"]).decode().strip()
        entries = {e["base"]: e for e in doc["bases"]}
        with bench_run.KeepAwake():
            for base in dict.fromkeys(b for b, _ in pairs):
                run_base(a, repo, out, staged, rev, entries[base], [k for b, k in pairs if b == base])
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


# ============================================================ pair（bench のアカウント）

def spawn(cmd, cwd):
    """子のプロセスを起こし、終了コードを返す（試験が差し替える）。時間の上限は掛けない（段ごとの上限は走らせ役の側にある）。
    timeout=None は、上限を掛けないことを明示するため（TTL の無い呼び出しを禁じる test_scheduler の検めが、書き忘れと区別する）。"""
    return subprocess.run(cmd, cwd=cwd, env=bench_run.child_env(), timeout=None).returncode


def child_cmd(a, side, base, k):
    """(作業ディレクトリ, 命令)。その版のクローンで、その版のコードの run を 1 段だけ起こす。out は両方の版で同じ。"""
    clone, rev = (a.old, a.old_rev) if side == "old" else (a.new, a.new_rev)
    cmd = [sys.executable, "-m", "harness.bench_pair", "run", "--tag", f"{a.tag}-{side}", "--repo", str(Path(clone).resolve()),
           "--base", rev, "--staged", str(Path(a.staged).resolve()), "--from", a.ref, "--stages", f"{base}:{k}",
           "--out", str(Path(a.out).resolve())]
    cmd += [x for n in ("implementer", "sessions") if getattr(a, n, None) is not None for x in (f"--{n}", getattr(a, n))]
    return Path(clone), cmd


def pair(a):
    path = Path(a.out) / "pairs" / f"{a.tag}.json"
    try:
        _, pairs = plan(a.staged, a.ref, a.stages)
        same = git(Path(a.old), ["rev-parse", f"{a.old_rev}:harness"]) == git(Path(a.new), ["rev-parse", f"{a.new_rev}:harness"])
    except BenchError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    if same and not a.allow_same:
        print("ABORT: 旧版と新版の harness/ の木が同じです（揺れの測りなら --allow-same）", file=sys.stderr)
        return 2
    log = {"tag": a.tag, "from": a.ref, "old": {"repo": a.old, "rev": a.old_rev}, "new": {"repo": a.new, "rev": a.new_rev},
           "same": same, "pairs": [f"{b}:{k}" for b, k in pairs], "runs": []}
    for i, (base, k) in enumerate(pairs):
        for side in ("old", "new") if i % 2 == 0 else ("new", "old"):
            cwd, cmd = child_cmd(a, side, base, k)
            row = {"pair": i, "base": base, "k": k, "side": side, "started": bench_chain.now()}
            row.update(code=spawn(cmd, cwd), ended=bench_chain.now())
            log["runs"].append(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            hline_base.write_json(path, log)
    return 0 if all(r["code"] == 0 for r in log["runs"]) else 1


# ============================================================ finish（操縦士のアカウント）

def start_prev(rec, st, out, chains):
    """保ちの起点：段の開始のコミットの木を、基準の finish が段 k−1 で使った組（段 0〜k−1 の隠しテストから、
    段 1〜k−1 の replaced.json の名前を除く。段 1 なら段 0 の組）で採点した結果。"""
    base, k = rec["base"], st["k"]
    with tempfile.TemporaryDirectory(prefix="bench-pair-") as tmp:
        tree = chain_score.tip_tree(bench_run.run_dir(out, base, st["run_id"]), tmp, st["start"], "start")
        return chain_score.grade(tree, chain_score.hidden_files(chains, base, k - 1), chain_score.replaced(chains, base, k - 1))


def finish_pairs(rec, out, chains, cfg, priv):
    """from のある記録 1 本の公開の集計。段はどれも基準の先端から走ったので、段ごとに独りで採点する（前の段が無効でも汚さない）。"""
    priv.mkdir(parents=True, exist_ok=True)
    public = {"tag": rec["tag"], "set": rec.get("set"), "base": rec["base"], "rev": rec["rev"], "from": rec["from"],
              "stopped": rec.get("stopped"), "skipped": rec.get("skipped") or [], "stages": []}
    last = {st["k"]: st for st in rec.get("stages") or []}   # 段ごとに最後の試し（finish_chain と同じ）
    for st in (last[k] for k in sorted(last)):
        row = {"k": st["k"], "run_id": st["run_id"], "attempts": st["attempt"] + 1, "invalid": st.get("invalid") or False,
               "reason": st.get("reason"), "stop": st.get("stop"), "tries": st.get("tries"), "tokens": st.get("tokens")}
        public["stages"].append(row)
        if not row["invalid"] and st.get("head"):
            chain_score.finish_stage(rec, st, row, out, chains, cfg, priv, start_prev(rec, st, out, chains))
    return public


def totals(rows):
    """段ごとの行の合計：採点に掛けた段（行）の数・通った段の数・無効の段の数・トークンの合計（数でない値は足さない）。"""
    tokens = [r.get("tokens") for r in rows]
    return {"stages": len(rows), "passed": sum(map(passed, rows)), "invalid": sum(1 for r in rows if r.get("invalid")),
            "tokens": sum(t for t in tokens if isinstance(t, int) and not isinstance(t, bool))}


def publish(dest, public, priv, keys=SUMMARY):
    """公開の集計を dest に書く。set が dev でない組（保留・予備・不明）は、段ごとの点で提案役が合わせ込めないように合計だけにし、
    段ごとの行を含む今までの形は、非公開の置き場 priv（oracles の下）の public_full.json に書く。dev は今までの形のまま。"""
    if public.get("set") != "dev":
        hline_base.write_json(Path(priv) / "public_full.json", public)
        public = {**{k: public.get(k) for k in keys}, "totals": totals(public.get("stages") or [])}
    hline_base.write_json(dest, public)


def finish(a):
    repo = Path(a.repo)
    out = Path(a.out) if a.out else repo / ".bench"
    try:
        cfg = machine.read_json(repo / "config" / "hline.json")
        chains = Path(a.chains) if a.chains else Path(cfg["oracles"]) / "chains"
        recs = [r for r in (bench_run.read_json(p, {}) for p in sorted((out / "chains" / a.tag).glob("*.json"))) if r.get("from")]
        if not recs:
            print(f"from のある記録がありません: {out / 'chains' / a.tag}", file=sys.stderr)
            return 1
        for rec in recs:
            priv = (Path(cfg["out"]) / "chains" if rec.get("set") == "dev" else chains / "runs") / a.tag / rec["base"]
            dest = repo / "bench" / "records" / "chains" / a.tag / f"{rec['base']}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            publish(dest, finish_pairs(rec, out, chains, cfg, priv), priv, (*SUMMARY[:4], "from", "stopped"))
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


# ============================================================ compare

def stage_rows(repo, tag):
    """公開の集計の ({(土台, 段): 行}, 記録にある組の集合（飛ばした段を含む）)。"""
    rows, keys = {}, set()
    for p in sorted((Path(repo) / "bench" / "records" / "chains" / tag).glob("*.json")):
        doc = bench_run.read_json(p, {})
        rows.update({(doc.get("base"), r.get("k")): r for r in doc.get("stages") or []})
        keys |= {(doc.get("base"), s.get("k")) for s in doc.get("skipped") or []}
    return rows, keys | set(rows)


def full(rate):
    return isinstance(rate, list) and len(rate) == 2 and rate[0] == rate[1]


def passed(row):
    """版の 1 つが組を通ったか：無効でなく、new が全部通り、kept が全部通る（kept の無い行は通らない）。
    この決まりはここだけに置き、compare と、dev でない組の公開の合計（totals。chain_score.finish からも使う）の両方で使う。"""
    return not row.get("invalid") and full(row.get("new")) and full(row.get("kept"))


def verdict(old, new):
    if old is None or new is None:
        return "欠け"
    return {(False, True): "直した", (True, False): "壊した"}.get((passed(old), passed(new)), "同じ")


def brief(row):
    return "記録無し" if row is None else (f"new {row.get('new')}・kept {row.get('kept')}・無効 {row.get('invalid') or '無し'}"
                                         f"・{'通った' if passed(row) else '通らない'}")


def compare(a):
    repo = Path(a.repo)
    (old, ko), (new, kn) = stage_rows(repo, f"{a.tag}-old"), stage_rows(repo, f"{a.tag}-new")
    if not ko | kn:
        print(f"{a.tag}-old・{a.tag}-new の公開の集計がありません", file=sys.stderr)
        return 1
    count, pairs = dict.fromkeys(("直した", "壊した", "欠け"), 0), []
    for base, k in sorted(ko | kn, key=lambda x: (str(x[0]), x[1] if isinstance(x[1], int) else -1)):
        o, n = old.get((base, k)), new.get((base, k))
        v = verdict(o, n)
        count[v] = count.get(v, 0) + 1
        pairs.append({"base": base, "k": k, "verdict": v,
                      **{side: None if r is None else {f: r.get(f) for f in FIELDS} for side, r in (("old", o), ("new", n))}})
        print(f"{base}:{k}  旧 {brief(o)}  ／  新 {brief(n)}  → {v}")
    dest = repo / "bench" / "records" / "pairs" / f"{a.tag}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    hline_base.write_json(dest, {"tag": a.tag, "fixed": count["直した"], "broke": count["壊した"], "missing": count["欠け"],
                                 "pairs": pairs})
    print(f"直した {count['直した']}・壊した {count['壊した']}・欠け {count['欠け']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_pair", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "pair", "finish", "compare"):
        sub.add_parser(name).add_argument("--tag", required=True, type=bench_chain.tag)
    for name in ("run", "finish", "compare"):
        sub.choices[name].add_argument("--repo", required=True)
    for name in ("run", "pair"):
        p = sub.choices[name]
        p.add_argument("--staged", required=True)
        p.add_argument("--from", dest="ref", required=True, type=bench_chain.tag)
        p.add_argument("--stages", required=True)
        p.add_argument("--implementer")
        p.add_argument("--sessions")
    sub.choices["run"].add_argument("--base", required=True)
    sub.choices["run"].add_argument("--out")
    for flag in ("--old", "--old-rev", "--new", "--new-rev", "--out"):
        sub.choices["pair"].add_argument(flag, required=True)
    sub.choices["pair"].add_argument("--allow-same", action="store_true")
    sub.choices["finish"].add_argument("--chains")
    sub.choices["finish"].add_argument("--out")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"run": run, "pair": pair, "finish": finish, "compare": compare}[a.command](a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
