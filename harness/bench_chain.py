"""累積の筋書き（鎖）の走らせ役（v3 の L3）：段 0 の木から、段ごとに決まった型の What 1 件で工廠を回し、段ごとに採点する。

    python -m harness.bench_chain stage  --tag <t> --set <dev|held|reserve> --bases B07,B08,B09 [--stages <K>] [--chains <dir>] [--staged <dir>]
    python -m harness.bench_chain run    --tag <t> --repo <clone> --base <rev> --staged <dir> [--out <dir>] [--implementer <json>]
    python -m harness.bench_chain finish --tag <t> --repo <clone> [--chains <dir>] [--out <dir>]

stage（操縦士のアカウント）は、非公開の鎖の置き場 <chains>/<土台>/ から公開の材料だけ（段 0 の木の <package>/ の .py と
tests/test_<package>_base.py、段 1〜K の spec.md）を、<staged>/<tag>-<当てられない 16 桁>/ に写し、chain.json を書いて、
その場所を出す（作業ツリーにも記録にも書かない。run に渡す）。写すものに canary があれば何も書かずに 1。
run（bench のアカウント。<clone> は走らせ役自身のクローン）は、土台ごとに段 0 の先端（<rev> の木から凍結の課題の <package>/ と
試験を除き、段 0 の木を足した、親の無いコミット）を作り、段 k = 1..K を、段 k−1 の先端の木に docs/bench/<土台>/chain/s<k>/spec.md
と段 k の台帳を足した開始のコミットから、What 1 件の工廠（計画役も監修役も無い v0）で走らせる。試験が落ちても巻き戻さない。
走りが無効か異常なら、その段を RETRIES 回まで走り直し、それでも駄目ならその土台の鎖を止める。記録は <out>/chains/<tag>/<土台>.json
（済んだ土台は飛ばす）。What ごとの生ログ（実装役の stream-json を含む）は、走りの記録の items/ に写す。
finish（操縦士のアカウント）は、段ごとに漏れの走査（bench_guard）・道の検め（作業の場所の外の ${local} の下のパスか、ネットを
見る道具）・境界の検め（段の開始と先端の差分）・採点（段 0〜k の隠しテストから、段 1〜k の replaced.json の名前を除いた組）をする。
孤立（段 k の新しい試験）・前の試験（段 0〜k−1 の必須）・保ち（段 k−1 の先端で通っていた前の必須のうち、段 k でも通る）を分け、
試験ごとの結果と索引を、開発の組は H ラインの out の chains/<tag>/<土台>/（提案役が読める）、ほかの組は oracles の
chains/runs/<tag>/<土台>/ に置き、集計の数だけを <clone>/bench/records/chains/<tag>/<土台>.json に書く。
"""
import argparse
import datetime
import re
import secrets
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402
import bench_start  # noqa: E402
import chain_score  # noqa: E402
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
import progress  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

CONFIG = bench_run.ROOT / "config" / "hline.json"
STAGE_SECONDS = 3600          # 段ごとの上限（枠切れの待ちを除く）。agy には手番の上限が渡らないので、時間とトークンで止める
STAGE_TOKENS = 3_000_000
RETRIES = 2
WHAT = "P-01"
SETS = ("dev", "held", "reserve")
TAG = re.compile(r"[A-Za-z0-9._-]+")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")




# ============================================================ stage（操縦士のアカウント）

def public_files(chains, base, stages=None):
    """(package, 段の数, [(元, 写す先の相対パス)])。段 0 の木のパッケージは 1 つ、段の仕様は s1 から欠けずにそろうこと。"""
    src, tree = Path(chains) / base, Path(chains) / base / "s0" / "tree"
    pkgs = sorted(p.name for p in tree.iterdir() if p.is_dir() and p.name not in ("tests", "__pycache__")) if tree.is_dir() else []
    if len(pkgs) != 1:
        raise BenchError(f"{tree} のパッケージが 1 つではありません: {pkgs}")
    pkg = pkgs[0]
    have = sorted(int(m.group(1)) for p in src.iterdir()
                  if (m := re.fullmatch(r"s([1-9][0-9]*)", p.name)) and (p / "spec.md").is_file())
    count = len(have) if stages is None else stages
    if not count or have[:count] != list(range(1, count + 1)):
        raise BenchError(f"{src} の段の仕様が s1〜s{count} でそろいません: {have}")
    files = [(p, f"s0/tree/{p.relative_to(tree).as_posix()}") for p in sorted((tree / pkg).rglob("*.py"))]
    files.append((tree / "tests" / f"test_{pkg}_base.py", f"s0/tree/tests/test_{pkg}_base.py"))
    files += [(src / f"s{k}" / "spec.md", f"s{k}/spec.md") for k in range(1, count + 1)]
    if (missing := [str(s) for s, _ in files if not s.is_file()]):
        raise BenchError(f"ありません: {', '.join(missing)}")
    return pkg, count, files


def stage(a):
    cfg = machine.read_json(CONFIG)
    chains = Path(a.chains) if a.chains else Path(cfg["oracles"]) / "chains"
    root = Path(a.staged) if a.staged else machine.path("local") / "bench" / "chains"
    try:
        plan = [(b, *public_files(chains, b, a.stages)) for b in a.bases.split(",") if b]
    except (BenchError, OSError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    marks = bench_guard.canaries(chains.parent)
    bad = [str(s) for _, _, _, files in plan for s, _ in files
           if any(m in s.read_text(encoding="utf-8", errors="ignore") for m in marks)]
    if bad:
        print(f"canary があるので写しません: {', '.join(bad)}", file=sys.stderr)
        return 1
    dest = root / f"{a.tag}-{secrets.token_hex(8)}"
    doc = {"tag": a.tag, "set": a.set, "staged_at": now(), "bases": []}
    for base, pkg, count, files in plan:
        for src, rel in files:
            (dest / base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / base / rel)
        doc["bases"].append({"base": base, "package": pkg, "stages": count, "spec_sha256": {
            f"s{k}": bench_run.sha256_file(dest / base / f"s{k}" / "spec.md") for k in range(1, count + 1)}})
    hline_base.write_json(dest / "chain.json", doc)
    print(dest)
    return 0


# ============================================================ 開始のコミット・What・台帳

def tree_lines(repo, rev, keep):
    """rev の木の、keep に当たるパスの ls-tree の行（"<mode> <sha>\\t<path>\\0"）。"""
    lines = []
    for entry in filter(None, git(repo, ["ls-tree", "-r", "-z", "--full-tree", rev]).split(b"\0")):
        meta, _, raw = entry.partition(b"\t")
        path = raw.decode("utf-8", "surrogateescape")
        if keep(path):
            mode, _kind, sha = meta.decode().split()
            lines.append(f"{mode} {sha}\t{path}\0")
    return lines


def with_files(repo, rev, keep, files, message):
    """rev の木のうち keep に当たるものに、files（相対パス → バイト列）を足した（同じパスは替えた）、親の無いコミット。"""
    lines = tree_lines(repo, rev, lambda p: keep(p) and p not in files)
    for path, data in sorted(files.items()):
        sha = git(repo, ["hash-object", "-w", "--stdin"], data=data).decode().strip()
        lines.append(f"100644 {sha}\t{path}\0")
    return bench_start.commit_lines(repo, lines, message)


def s0_commit(repo, rev, base, pkg, tree):
    files = {p.relative_to(tree).as_posix(): p.read_bytes() for p in sorted(Path(tree).rglob("*.py"))}
    return with_files(repo, rev, lambda p: not bench_start.excluded(p, pkg), files, f"bench chain s0: {base}")


def section(text, name):
    m = re.search(rf"^## {re.escape(name)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def spec_info(text):
    """仕様の題（見出しの「：」の後）・頼みの種類（その節の 1 行目）・段の新しい要件 [(REQ-xx, 本文)]。
    要件の本文は、「- REQ-xx：」の箇条と、その下の字下げの行をつないだもの。"""
    head = re.sub(r"^#+\s*", "", (text.strip().splitlines() or [""])[0])
    kind = (section(text, "頼みの種類").strip().splitlines() or [""])[0].strip()
    reqs = []
    for line in section(text, "要件").splitlines():
        if (m := re.match(r"- (REQ-\d+)[：:]", line)):
            reqs.append([m.group(1), line[2:].strip()])
        elif reqs and line[:1] in (" ", "\t") and line.strip():
            reqs[-1][1] += " " + line.strip()
    return {"title": head.split("：", 1)[-1].strip(), "kind": kind, "reqs": [tuple(r) for r in reqs]}


def changeable(repo, tip, pkg, k):
    """変えるファイル：先端の木の <package>/ と tests/test_<package>* の .py の全部と、段の新しい試験（v0 の境界）。"""
    own = re.compile(rf"(?:{re.escape(pkg)}/.*|tests/test_{re.escape(pkg)}[^/]*)\.py")
    have = [line.split("\t", 1)[1][:-1] for line in tree_lines(repo, tip, lambda p: own.fullmatch(p) is not None)]
    new = f"tests/test_{pkg}_s{k}.py"
    return have + ([new] if new not in have else [])


def what_text(base, pkg, k, info, files):
    reqs = ", ".join(r for r, _ in info["reqs"])
    before = f"`docs/bench/{base}/spec.md`" + (f" と、`docs/bench/{base}/chain/` の下の段 1〜{k - 1} の `spec.md`" if k > 1 else "")
    return "\n".join([
        f"# {info['title']}", "マイルストーン: bench", f"タスク: {WHAT}", f"要件: {reqs}", "", "## What",
        f"- `docs/bench/{base}/chain/s{k}/spec.md` の頼み（{info['kind']}）に応える。その仕様の要件（{reqs}）を満たすように "
        f"`{pkg}` を変え、仕様の「置き換える要件」と「保つ要件」に従う",
        f"- 前の段までの仕様は {before}", "", "## 合格条件",
        "- 変えるファイルは " + "・".join(f"`{p}`" for p in files),
        f"- 新しい試験 `tests/test_{pkg}_s{k}.py` は、この段の要件のそれぞれを確かめる", ""])


def ledger_rows(pkg, k, info, files):
    test = f"tests/test_{pkg}_s{k}"
    return [{"id": WHAT, "title": info["title"], "depends_on": [], "acceptance": [t for _, t in info["reqs"]] or [info["title"]],
             "files": files, "oracle": [{"path": f"{test}.py", "from": "self"}],
             "verification": {"command": f"python -m unittest {test.replace('/', '.')}", "expected_exit_code": 0}}]


def write_plan(d, base, pkg, k, info, files):
    """固定の計画の形（P-01.md・ledger.yaml・planner.json）。planner.json の loop は 0 回（計画役を呼んでいない）。"""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WHAT}.md").write_text(what_text(base, pkg, k, info, files), encoding="utf-8", newline="\n")
    (d / "ledger.yaml").write_text(yaml.safe_dump(ledger_rows(pkg, k, info, files), allow_unicode=True, sort_keys=False),
                                   encoding="utf-8", newline="\n")
    hline_base.write_json(d / "planner.json", {"files": {p.name: bench_run.sha256_file(p) for p in bench_run.plan_files(d)},
                                               "problems": [], "plan_check": [], "generated": "bench_chain v0",
                                               "loop": {"rounds": 0, "confirmed": True, "failed": []}})
    return d


def start_commit(repo, tip, base, pkg, k, spec, rows):
    state = bench_start.ledger_state(rows, pkg)
    if (problems := progress.validate(state)):
        raise BenchError(f"{base} 段 {k} の台帳が progress.validate に落ちます: {problems}")
    files = {f"docs/bench/{base}/chain/s{k}/spec.md": spec,
             bench_start.LEDGER_PATH: yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode("utf-8")}
    return with_files(repo, tip, lambda p: p != progress.ARCHIVE_REL_PATH, files, f"bench chain start: {base} s{k}")


# ============================================================ run（bench のアカウント）

class ChainStage(bench_run.Factory):
    """鎖の段の 1 回の走り。開始のコミットと仕様を外から受け取り、HEAD を確かめず、段の上限で止め、What ごとの生ログを写す。"""

    def __init__(self, a, start, spec):
        super().__init__(a)
        self.start_sha, self.spec = start, Path(spec)

    def check_head(self):
        self.run["base"] = git(self.repo, ["rev-parse", "--verify", "-q", f"{self.rev}^{{commit}}"]).decode().strip()
        if (self.rundir / "factory" / "run.json").exists():
            raise BenchError(f"走行のディレクトリがすでにあります: {self.rundir}")

    def keep_plan(self, plan_inbox, spec):
        super().keep_plan(plan_inbox, self.spec)

    def start(self, plan_inbox):
        self.run["start"] = self.run["head"] = self.start_sha
        return True

    def stop_reason(self):
        if self.tokens()["total"] >= STAGE_TOKENS:
            return "tokens"
        return "time" if self.timer.elapsed() >= STAGE_SECONDS else None

    def finalize(self):
        super().finalize()
        if getattr(self, "line_cfg", None):
            keep_items(Path(self.line_cfg["out"]), self.rundir / "items")


def keep_items(out, dest):
    """H ラインの out の What ごとのディレクトリ（what.md のあるもの。延長で増えた分も）の全部のファイルを dest/<名前>/ に写す。
    試行ごとの差分は残らない（last.patch は上書き）が、編集の道具の引数として stream-json に入っている。"""
    for d in sorted(p for p in out.iterdir() if p.is_dir() and (p / "what.md").is_file()) if out.is_dir() else []:
        (dest / d.name).mkdir(parents=True, exist_ok=True)
        for p in sorted(x for x in d.iterdir() if x.is_file()):
            shutil.copy2(p, dest / d.name / p.name)


def run_stage(f):
    """bench_run.factory の本体と同じ並び（bench_run.py は行の上限を超えていて増やせないので、ここに写す）。書けば 0、異常は 2。"""
    try:
        f.check_head()
        f.check_plan()
        f.load_implementer()
        cfg = f.cfg()
        f.before = bench_run.sessions_now(f.sessions)
        f.rundir.mkdir(parents=True, exist_ok=True)
        if f.guard(cfg):
            plan_inbox = f.plan(cfg)
            if plan_inbox is not None and f.start(plan_inbox):
                f.setup_clone(plan_inbox, cfg)
                f.loop()
        f.finalize()
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


def fetch_tip(repo, rundir, ref):
    git(repo, ["fetch", "-q", str(Path(rundir) / chain_score.BUNDLE), f"+refs/heads/tip:{ref}"])
    return git(repo, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"]).decode().strip()


def run_step(a, repo, out, staged, entry, tip, k, rec):
    """段 k を走らせる。無効・異常なら RETRIES 回まで走り直す。試すたびに rec に足し、済んだ走りの記録を返す（駄目なら None）。"""
    base, pkg = entry["base"], entry["package"]
    spec = staged / base / f"s{k}" / "spec.md"
    if bench_run.sha256_file(spec) != entry["spec_sha256"][f"s{k}"]:
        raise BenchError(f"{spec} の指紋が chain.json と違います")
    info = spec_info(spec.read_text(encoding="utf-8"))
    files = changeable(repo, tip, pkg, k)
    start = start_commit(repo, tip, base, pkg, k, spec.read_bytes(), ledger_rows(pkg, k, info, files))
    for attempt in range(RETRIES + 1):
        run_id = f"{a.tag}-s{k}" + (f"-r{attempt}" if attempt else "")
        plan = write_plan(out / "chains" / a.tag / base / run_id, base, pkg, k, info, files)
        ns = argparse.Namespace(task=base, run_id=run_id, repo=str(repo), base=tip, out=str(out), sessions=a.sessions,
                                plan=str(plan), implementer=a.implementer)
        code = run_stage(ChainStage(ns, start, spec))
        done = bench_run.read_json(bench_run.run_dir(out, base, run_id) / "factory" / "run.json", {})
        st = {"k": k, "attempt": attempt, "run_id": run_id, "code": code, "base": tip, "start": start, "head": done.get("head"),
              "invalid": done.get("invalid") or (None if code == 0 and done else "ABORT"), "reason": done.get("reason"),
              "stop": done.get("stop"), "tries": done.get("tries"), "tokens": (done.get("tokens") or {}).get("total")}
        rec["stages"].append(st)
        if not st["invalid"] and st["head"]:
            return st
    return None


def run_chain(a, repo, out, staged, rev, entry, kind):
    base, pkg = entry["base"], entry["package"]
    tip = s0_commit(repo, rev, base, pkg, staged / base / "s0" / "tree")
    rec = {"tag": a.tag, "set": kind, "base": base, "package": pkg, "rev": rev, "s0": tip, "stages": [], "stopped": None}
    for k in range(1, int(entry["stages"]) + 1):
        st = run_step(a, repo, out, staged, entry, tip, k, rec)
        if st is None:
            rec["stopped"] = f"s{k}"
            break
        tip = fetch_tip(repo, bench_run.run_dir(out, base, st["run_id"]), f"refs/chains/{a.tag}/{base}/s{k}")
    return rec


def run(a):
    repo, staged = Path(a.repo), Path(a.staged)
    out = Path(a.out) if a.out else repo / ".bench"
    doc = bench_run.read_json(staged / "chain.json", {})
    if doc.get("tag") != a.tag or not doc.get("bases"):
        print(f"{staged} の chain.json が無いか、tag が {a.tag} ではありません", file=sys.stderr)
        return 2
    if (why := bench_run.version_problem(repo, a.base)):
        print(f"ABORT: {why}", file=sys.stderr)
        return 2
    try:
        rev = git(repo, ["rev-parse", "--verify", "-q", f"{a.base}^{{commit}}"]).decode().strip()
        with bench_run.KeepAwake():
            for entry in doc["bases"]:
                path = out / "chains" / a.tag / f"{entry['base']}.json"
                if not path.exists():
                    rec = run_chain(a, repo, out, staged, rev, entry, doc.get("set"))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    hline_base.write_json(path, rec)
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0




def tag(value):
    if not TAG.fullmatch(value):
        raise argparse.ArgumentTypeError(f"tag は英数字と . _ - だけ: {value!r}")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_chain", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("stage", "run", "finish"):
        sub.add_parser(name).add_argument("--tag", required=True, type=tag)
    sub.choices["stage"].add_argument("--set", required=True, choices=SETS)
    sub.choices["stage"].add_argument("--bases", required=True)
    sub.choices["stage"].add_argument("--stages", type=int)
    sub.choices["stage"].add_argument("--staged")
    for name in ("stage", "finish"):
        sub.choices[name].add_argument("--chains")
    for name in ("run", "finish"):
        sub.choices[name].add_argument("--repo", required=True)
        sub.choices[name].add_argument("--out")
    sub.choices["run"].add_argument("--base", required=True)
    sub.choices["run"].add_argument("--staged", required=True)
    sub.choices["run"].add_argument("--implementer")
    sub.choices["run"].add_argument("--sessions")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"stage": stage, "run": run, "finish": chain_score.finish}[a.command](a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
