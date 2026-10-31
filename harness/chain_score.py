"""鎖の段の検めと採点（v3 の L3。操縦士のアカウントで、bench_chain finish から呼ぶ）。

段ごとに、漏れの走査（bench_guard）・道の検め（作業の場所の外の ${local} の下のパスか、ネットを見る道具）・境界の検め（段の開始と
先端の差分）・採点（段 0〜k の隠しテストから、段 1〜k の replaced.json の名前を除いた組）をする。孤立（段 k の新しい試験）・
前の試験（段 0〜k−1 の必須）・保ち（段 k−1 の先端で通っていた前の必須のうち、段 k でも通る）を分ける。試験ごとの結果と索引
（抜き書きだけ。言い換えの要約は作らない）は、開発の組は H ラインの out の chains/<tag>/<土台>/（提案役が読める）、ほかの組は
oracles の chains/runs/<tag>/<土台>/ に置き、集計の数だけを <clone>/bench/records/chains/<tag>/<土台>.json に書く。
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

GRADE_TTL = 600
TAIL = 6                      # 索引に抜く、実装役の最後の手番の数
NET_TOOL = re.compile(r"browser|_url|web", re.I)
DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?]*")
REL_UP = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
BUNDLE = Path("git/run.bundle")
MARK = "CHAIN_RESULT "

# 採点：<tree> と <tests> を import の道の先頭に置き、<tests> の試験を走らせ、試験ごとに "ok" か理由の 1 行を出す。
# subTest の 1 つの失敗は親の失敗（件数を水増ししない）。読めた試験で結果の無いもの（読み込みの誤りを含む）は MISSING
RUNNER = '''import json, sys, unittest
tree, tests = sys.argv[1], sys.argv[2]
sys.path[:0] = [tests, tree]
def last(text):
    lines = text.strip().splitlines()
    return lines[-1][:300] if lines else ""
class R(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcome = {}
    def bad(self, test, label, err):
        if not str(self.outcome.get(test.id(), "")).startswith(("FAIL", "ERROR")):
            self.outcome[test.id()] = label + ": " + last(self._exc_info_to_string(err, test))
    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcome.setdefault(test.id(), "ok")
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.bad(test, "FAIL", err)
    def addError(self, test, err):
        super().addError(test, err)
        self.bad(test, "ERROR", err)
    def addSubTest(self, test, sub, err):
        super().addSubTest(test, sub, err)
        if err is not None:
            kind = "FAIL " if issubclass(err[0], test.failureException) else "ERROR "
            self.bad(test, kind + sub._subDescription(), err)
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcome.setdefault(test.id(), "SKIP: " + reason)
def ids(suite):
    for t in suite:
        yield from (ids(t) if isinstance(t, unittest.TestSuite) else [t.id()])
suite = unittest.defaultTestLoader.discover(tests, pattern="test_*.py", top_level_dir=tests)
want = list(ids(suite))
res = unittest.TextTestRunner(resultclass=R, verbosity=0, stream=sys.stderr).run(suite)
for t in want:
    res.outcome.setdefault(t, "MISSING: no result")
sys.stdout.reconfigure(encoding="utf-8")
print("%s" + json.dumps(res.outcome, ensure_ascii=False))
''' % MARK


def read_text(path):
    """生ログを読む。途中で切れた・混じった字があっても止めずに読む（置き換えの字にする）。"""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def norm(p):
    return re.sub(r"[\\/]+", "/", str(p)).rstrip("/").lower()


def under(p, root):
    p, root = norm(p), norm(root)
    return p == root or p.startswith(root + "/")



# ============================================================ 道の検めと索引

def steps_of(log):
    """実装役の生ログ（要約・LOG_SPLIT・stream-json・LOG_SPLIT・標準エラー）から (作業の場所, 手番の列)。
    手番は番号の順に {type, state, text, tool, parameters, output, error}（同じ番号の更新は重ね、文の断片はつなぐ）。"""
    parts = read_text(log).split(bench_run.LOG_SPLIT)
    cwd, steps = None, {}
    for line in (parts[1] if len(parts) > 1 else "").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("init"), dict):
            cwd = e["init"].get("cwd") or cwd
        su = e.get("step_update") if isinstance(e, dict) else None
        if isinstance(su, dict):
            s = steps.setdefault(su.get("step_index"), {"type": su.get("step_type"), "text": ""})
            s["state"], s["text"] = su.get("state"), s["text"] + (su.get("text_delta") or "")
            info = su.get("tool_info")
            if isinstance(info, dict):
                s["tool"] = str(info.get("name") or su.get("tool_name") or "")
                s.update({k: info[k] for k in ("parameters", "output", "error") if info.get(k) is not None})
    return cwd, [steps[i] for i in sorted(steps, key=lambda i: i if isinstance(i, int) else -1)]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (dict, list)):
        for v in (value.values() if isinstance(value, dict) else value):
            yield from strings(v)


def strays(logs, own, local):
    """道の検め：(ログ, 道具, 当たり) の列。ネットを見る道具と、local の下で own（その段の作業の場所）の外を指すパス
    （引数のすべての文字列の、ドライブからのパスと、作業の場所からの .. を含むパス）。"""
    found = []
    for log in logs:
        cwd, steps = steps_of(log)
        for s in (x for x in steps if x.get("tool")):
            if NET_TOOL.search(s["tool"]):
                found.append((log.name, s["tool"], "ネットを見る道具"))
                continue
            for text in strings(s.get("parameters")):
                found += [(log.name, s["tool"], h) for h in paths_in(text, cwd) if under(h, local) and not under(h, own)]
    return found


def paths_in(text, cwd):
    """引数の文字列が指すパス：中のドライブからのパス、/ で始まる文字列（Linux）、作業の場所からの .. を含む相対のパス。"""
    hits = [m.group(0) for m in DRIVE_PATH.finditer(text)] or ([text] if text.startswith("/") else [])
    return hits or ([os.path.normpath(os.path.join(cwd, text))] if cwd and REL_UP.search(text) else [])


def gate_failures(text):
    """門の生ログの unittest の落ち：(見出し, 例外の行) の列。例外の行は traceback の後の、字下げの無い最後の行。"""
    out, tb = [], False
    for line in text.splitlines():
        if (m := re.match(r"(FAIL|ERROR): (.+)", line)):
            out.append([f"{m.group(1)}: {m.group(2)}", ""])
            tb = False
        elif out and line.startswith("Traceback"):
            tb = True
        elif out and tb and line.strip() and not line[:1].isspace():
            if re.match(r"-{5,}|={5,}|Ran \d+ test|FAILED|OK\b", line):
                tb = False
            else:
                out[-1][1] = line.strip()
    return [tuple(x) for x in out]


def step_line(s):
    if s.get("tool"):
        args = "・".join(x[:200] for x in strings(s.get("parameters")))
        res = s.get("error") or s.get("output") or ""
        res = res.get("message", res) if isinstance(res, dict) else res
        return f"道具 {s['tool']}（{args}）→ {str(res)[:300]}"
    return f"{s.get('type')}：{(s.get('text') or '').strip()[:600]}"


def item_lines(d, item):
    lines = ["", f"## What {item.get('task')}（{d.name}）：{item.get('status')}", ""]
    for t in (x for x in item.get("tries") or [] if isinstance(x, dict)):
        fail = t.get("failure") or {}
        lines.append(f"- 試行 {t.get('run')}-{t.get('attempt')}：門 {'通った' if t.get('gate') else '落ちた'}・手番 {t.get('turns')}・"
                     f"打ち切り {t.get('cutoff')}・落ちの種類 {fail.get('class')}・落ちた試験 {', '.join(fail.get('tests') or []) or '無し'}")
    for g in sorted(d.glob("gate-*.log")):
        lines += [f"- {g.name}：{head} — {exc}" for head, exc in gate_failures(read_text(g))]
    if (d / "last-failure.txt").is_file():
        lines.append("- 最後の落ち：" + " ".join(read_text(d / "last-failure.txt").split())[:600])
    for log in sorted(d.glob("implementer-*.log")):   # 文の無い応答（道具を呼ぶだけの手番。煙の試しでは半分）は数えない
        said = [s for s in steps_of(log)[1] if s.get("tool") or (s.get("text") or "").strip()]
        lines += ["", f"### {log.name} の最後の {TAIL} 手番（文の無い応答を除く）", ""] + [f"- {step_line(s)}" for s in said[-TAIL:]]
    return lines


def index_text(base, k, run_id, rundir, results, found):
    """索引（抜き書きだけ。言い換えの要約は作らない）：落ちた隠しテスト・道の検め・What の試行ごとの門と終わり方・最後の手番。"""
    lines = [f"# 索引：{base} 段 {k}（{run_id}）", "", "## 落ちた隠しテスト", ""]
    lines += [f"- 段 {stage_of(t, base)}：`{t.split('.')[-1]}` — {v}" for t, v in sorted(results.items()) if v != "ok"] or ["- 無し"]
    lines += ["", "## 道の検め", ""] + ([f"- {log}：{tool} — {hit}" for log, tool, hit in found] or ["- 無し"])
    items = bench_run.read_json(rundir / "factory" / "queue.json", {}).get("items") or {}
    for d in sorted(p for p in (rundir / "items").glob("*") if p.is_dir()):
        lines += item_lines(d, next((v for v in items.values() if isinstance(v, dict) and v.get("tid") == d.name), {}))
    return "\n".join(lines) + "\n"


# ============================================================ finish（操縦士のアカウント）

def hidden_files(chains, base, k):
    files = [chains / base / "s0" / "hidden" / f"test_hidden_{base}.py"]
    return files + [chains / base / f"s{j}" / "hidden" / f"test_chain_{base}_s{j}.py" for j in range(1, k + 1)]


def replaced(chains, base, k):
    return set().union(*[set(bench_run.read_json(chains / base / f"s{j}" / "replaced.json", [])) for j in range(1, k + 1)])


def stage_of(test_id, base):
    mod = test_id.split(".", 1)[0]
    if mod == f"test_hidden_{base}":
        return 0
    m = re.fullmatch(rf"test_chain_{re.escape(base)}_s(\d+)", mod)
    return int(m.group(1)) if m else None


def grade(tree, files, gone):
    """{試験の id: "ok" か理由の 1 行}（名前が gone にある試験は除く）。"""
    with tempfile.TemporaryDirectory(prefix="bench-chain-") as tmp:
        tests = Path(tmp) / "tests"
        tests.mkdir()
        for f in files:
            shutil.copy2(f, tests / Path(f).name)
        code, out, err = proc.run([bench_run.PY, "-I", "-c", RUNNER, str(tree), str(tests)], tmp, GRADE_TTL, "chain grade",
                                  env=bench_run.child_env())
    line = next((x for x in out.splitlines() if x.startswith(MARK)), None)
    if line is None:
        raise BenchError(f"採点が結果を出しませんでした（終了コード {code}）: {(out + err).strip()[-400:]}")
    return {t: v for t, v in json.loads(line[len(MARK):]).items() if t.split(".")[-1] not in gone}


def rate(results, ids):
    ids = list(ids)
    return [sum(1 for t in ids if results.get(t) == "ok"), len(ids)]


def tip_tree(rundir, tmp):
    tree = Path(tmp) / "tree"
    tree.mkdir()
    git(tree, ["init", "-q"])
    git(tree, ["fetch", "-q", str(Path(rundir) / BUNDLE), "refs/heads/tip:refs/heads/tip"])
    git(tree, ["checkout", "-q", "tip"])
    return tree


def finish_stage(rec, st, row, out, chains, cfg, priv, prev):
    """段 1 つの検めと採点。row を埋め、試験ごとの結果と索引を priv に書き、試験ごとの結果を返す。"""
    base, pkg, k = rec["base"], rec["package"], st["k"]
    rundir = bench_run.run_dir(out, base, st["run_id"])
    hits = bench_guard.leaks(cfg, [rundir])
    (rundir / "guard.txt").write_text("".join(f"漏れの跡: {f}:{n}\n" for f, n in hits), encoding="utf-8")
    found = strays(sorted((rundir / "items").glob("*/implementer-*.log")), bench_run.work_dir(out, base, st["run_id"]),
                   machine.path("local"))
    gone = replaced(chains, base, k)
    with tempfile.TemporaryDirectory(prefix="bench-chain-") as tmp:
        tree = tip_tree(rundir, tmp)
        try:
            names = git(tree, ["diff", "--name-only", "-z", st["start"], "tip"]).decode("utf-8", "replace").split("\0")
        except BenchError:   # 先端が開始のコミットから伸びていない（H ラインの枝なら起きない）。境界の違反として 0 点にする
            names = ["<開始のコミットが走りの記録の先端の祖先にありません>"]
        res = grade(tree, hidden_files(chains, base, k), gone)
    violations = [n for n in names if n and not bench_run.allowed_path(n, pkg)]
    new = [t for t in res if stage_of(t, base) == k]
    old = [t for t in res if t not in new]
    row.update(new=rate(res, new), old=rate(res, old), kept=rate(res, [t for t in old if prev.get(t) == "ok"]),
               violations=len(violations), leaks=len(hits), strays=len(found))
    for key in ("new", "old", "kept") if violations else ():
        row[key][0] = 0   # 今の finish と同じく、境界の外を変えた段は 0 点
    if hits:
        row["invalid"], row["reason"] = "REQ-11", "漏れの跡があります（guard.txt）"
    elif found:
        row["invalid"], row["reason"] = "stray", "実装役が作業の場所の外の置き場かネットに触れました（索引の道の検め）"
    hline_base.write_json(priv / f"s{k}.json", {"base": base, "k": k, "run_id": st["run_id"], "results": res, "replaced": sorted(gone),
                                               "violations": violations, "strays": found, "leaks": hits})
    (priv / f"s{k}.index.md").write_text(index_text(base, k, st["run_id"], rundir, res, found), encoding="utf-8")
    hline_base.write_json(rundir / "manifest.json", bench_run.manifest(rundir))
    row["manifest_sha256"] = bench_run.sha256_file(rundir / "manifest.json")
    return res


def finish_chain(rec, out, chains, cfg, priv):
    """土台 1 本の公開の集計。段 0 の参照の結果を保ちの起点にし、無効の段の後は「前の段が無効」とする。"""
    base = rec["base"]
    priv.mkdir(parents=True, exist_ok=True)
    prev = grade(chains / base / "s0" / "tree", hidden_files(chains, base, 0), set())
    public = {"tag": rec["tag"], "set": rec.get("set"), "base": base, "rev": rec["rev"], "stopped": rec.get("stopped"),
              "s0": rate(prev, prev), "stages": []}
    last, tainted = {}, None
    for st in rec.get("stages") or []:
        last[st["k"]] = st
    for st in (last[k] for k in sorted(last)):
        row = {"k": st["k"], "run_id": st["run_id"], "attempts": st["attempt"] + 1, "invalid": st.get("invalid") or False,
               "reason": st.get("reason"), "stop": st.get("stop"), "tries": st.get("tries"), "tokens": st.get("tokens")}
        public["stages"].append(row)
        if row["invalid"] or not st.get("head"):
            break
        prev = finish_stage(rec, st, row, out, chains, cfg, priv, prev)
        if tainted and not row["invalid"]:
            row["invalid"], row["reason"] = "tainted", f"前の段（{tainted}）が無効"
        tainted = tainted or (f"s{st['k']}" if row["invalid"] else None)
    return public


def finish(a):
    repo = Path(a.repo)
    out = Path(a.out) if a.out else repo / ".bench"
    try:
        cfg = machine.read_json(repo / "config" / "hline.json")
        chains = Path(a.chains) if a.chains else Path(cfg["oracles"]) / "chains"
        recs = sorted((out / "chains" / a.tag).glob("*.json"))
        if not recs:
            print(f"鎖の記録がありません: {out / 'chains' / a.tag}", file=sys.stderr)
            return 1
        for p in recs:
            rec = bench_run.read_json(p, {})
            priv = (Path(cfg["out"]) / "chains" if rec.get("set") == "dev" else chains / "runs") / a.tag / rec["base"]
            dest = repo / "bench" / "records" / "chains" / a.tag / f"{rec['base']}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            hline_base.write_json(dest, finish_chain(rec, out, chains, cfg, priv))
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0
