"""鎖の段の検めと採点（v3 の L3。操縦士のアカウントで、bench_chain finish から呼ぶ）。

段ごとに、漏れの走査（bench_guard）・道の検め（作業の場所の外の ${local} の下のパスか、ネットを見る道具）・境界の検め（段の開始と
先端の差分）・採点（段 0〜k の隠しテストから、段 1〜k の replaced.json の名前を除いた組）をする。孤立（段 k の新しい試験）・
前の試験（段 0〜k−1 の必須）・保ち（段 k−1 の先端で通っていた前の必須のうち、段 k でも通る）を分ける。段の開始の木での段 k の試験の通過（new_at_start）も測る。
実装役の下請けの記録は What のディレクトリに写して、同じ道の検めを掛ける。試験ごとの結果と索引
（抜き書きだけ。言い換えの要約は作らない）は、開発の組は H ラインの out の chains/<tag>/<土台>/（提案役が読める）、ほかの組は
oracles の chains/runs/<tag>/<土台>/ に置き、集計の数だけを <clone>/bench/records/chains/<tag>/<土台>.json に書く（dev でない組は合計だけ）。
"""
import ast
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402
from bench_start import BenchError, git  # noqa: E402

GRADE_TTL = 600
TAIL = 6                      # 索引に抜く、実装役の最後の手番の数
NET_TOOL = re.compile(r"browser|_url|web|fetch|http|curl|wget", re.I)
# このリポジトリの名（公開。過去のコミットに、段の中身を書いた版がある）。ネットの道具の引数がこれか段の名を含めば、その段を無効にする
REPO_MARKS = ("tamaroulet", "game-harness", "game_harness")
DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?]*")
REL_UP = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
WORD = re.compile(r"[^\s\"'=(]+")   # paths_in の語（空白・引用符・=・( で区切る）
BUNDLE = Path("git/run.bundle")
MARK = "CHAIN_RESULT "
SUB_TOOL = "invoke_subagent"
SUB_MISSING = "下請けの記録が見つからない（検められない）"
FAILED_IMPORT = "unittest.loader._FailedTest."   # 読み込みで落ちたファイルの、unittest の疑似の試験の id の頭

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

def fold(steps, su):
    """step_update 1 つを steps（番号 → 手番）に重ねる。"""
    s = steps.setdefault(su.get("step_index"), {"type": su.get("step_type"), "text": ""})
    s["state"], s["text"] = su.get("state"), s["text"] + (su.get("text_delta") or "")
    info, sub = su.get("tool_info"), su.get("subagent_info")
    if isinstance(info, dict):
        s["tool"] = str(info.get("name") or su.get("tool_name") or "")
        s.update({k: info[k] for k in ("parameters", "output", "error") if info.get(k) is not None})
    if isinstance(sub, dict) and isinstance(sub.get("subagents"), list):
        s["subagents"] = sub["subagents"]


def steps_of(log):
    """実装役の生ログ（要約・LOG_SPLIT・stream-json・LOG_SPLIT・標準エラー）から (作業の場所, 手番の列)。
    手番は番号の順に {type, state, text, tool, parameters, output, error, subagents}（同じ番号の更新は重ね、文の断片はつなぐ）。"""
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
            fold(steps, su)
    return cwd, [steps[i] for i in sorted(steps, key=lambda i: i if isinstance(i, int) else -1)]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (dict, list)):
        for v in (value.values() if isinstance(value, dict) else value):
            yield from strings(v)


def uri_path(uri):
    """file:/// の URI の指すパス。Windows のドライブ文字の形（/C:/x → C:/x）も Linux の形も扱う。file: でなければそのまま。"""
    if not uri.lower().startswith("file:"):
        return uri
    path = unquote(urlparse(uri).path)
    return path[1:] if re.match(r"/[A-Za-z]:", path) else path


def launches(steps):
    """steps_of の手番から下請けの起動 [{id, path（記録のパス。無ければ None）, work（作業の場所。無ければ None）}]（id の重なりは 1 つに）。"""
    found = {}
    for s in steps:
        for sub in (x for x in s.get("subagents") or [] if isinstance(x, dict) and isinstance(x.get("conversation_id"), str)):
            uri = sub.get("log_uri")
            spaces = [w for w in sub.get("workspace_uris") or [] if isinstance(w, str)]
            found[sub["conversation_id"]] = {"id": sub["conversation_id"], "path": uri_path(uri) if isinstance(uri, str) and uri else None,
                                             "work": uri_path(spaces[0]) if spaces else None}
    return list(found.values())


def adopt(log, steps, own):
    """下請けの記録を log と同じ What のディレクトリに subagent-<id>.jsonl として写す（同じ中身が既にあれば写し直さない）。
    (検める記録 [(写し, 作業の場所)], 見つからない記録の当たり [(ログ, 道具, 理由)])。作業の場所（相対パスの起点）は workspace_uris の最初、無ければ own。"""
    subs, missing = [], []
    for sub in launches(steps):
        dest = Path(log).parent / f"subagent-{re.sub(r'[^A-Za-z0-9._-]', '_', sub['id'])}.jsonl"
        try:
            data = Path(sub["path"]).read_bytes() if sub["path"] else None
        except OSError:
            data = None
        if data is not None and not (dest.is_file() and dest.read_bytes() == data):
            dest.write_bytes(data)
        if dest.is_file():
            subs.append((dest, sub["work"] or str(own)))
        else:
            missing.append((Path(log).name, SUB_TOOL, SUB_MISSING))
    return subs, missing


def calls_of(path):
    """下請けの記録の道具の呼び出し [(道具, 引数)]。JSON でない行と、tool_calls の無い行（SYSTEM_MESSAGE など）は飛ばす。"""
    out = []
    for line in read_text(path).splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        calls = e.get("tool_calls") if isinstance(e, dict) else None
        out += [(str(c["name"]), c.get("args")) for c in calls if isinstance(c, dict) and c.get("name")] if isinstance(calls, list) else []
    return out


def tool_calls(logs, own, subs=()):
    """道の検めの対象：(ログ, 作業の場所, cwd, 道具, 引数, 読んでよいパス) の列。実装役の生ログの道具の手番と、写した下請けの記録
    subs [(写し, 作業の場所)] の呼び出し。実装役が同じログで起こした下請けの記録のパス（正規化したもの）は、実装役の呼び出しでは読んでよい。
    下請けの境界も own（下請けの作業の場所は相対パスの起点にだけ使う。own の外を作業の場所にした下請けを素通りさせない）。"""
    for log in logs:
        cwd, steps = steps_of(log)
        mine = {norm(x["path"]) for x in launches(steps) if x["path"]}
        yield from ((log.name, own, cwd, s["tool"], s.get("parameters"), mine) for s in steps if s.get("tool"))
    for path, work in subs:
        yield from ((path.name, own, work, tool, args, frozenset()) for tool, args in calls_of(path))


def outside(texts, cwd, own, local, skip=()):
    """引数の文字列の指すパスのうち、local の下で own の外のもの（skip の正規化したパスを除く）。"""
    return [h for x in texts for h in paths_in(x, cwd) if under(h, local) and not under(h, own) and norm(h) not in skip]


def strays(logs, own, local, subs=()):
    """道の検め：(ログ, 道具, 当たり) の列。ネットを見る道具と、local の下で own（その段の作業の場所）の外を指すパス
    （引数のすべての文字列の、ドライブからのパスと、作業の場所からの .. を含むパス）。subs は写した下請けの記録。"""
    found = []
    for name, work, cwd, tool, params, skip in tool_calls(logs, own, subs):
        if NET_TOOL.search(tool):
            found.append((name, tool, "ネットを見る道具"))
        else:
            found += [(name, tool, h) for h in outside(strings(params), cwd, work, local, skip)]
    return found


def audit(logs, own, local, marks, subs=()):
    """道の検めの全体：(当たり, 調べもの)。当たりは段を無効にする (ログ, 道具, 理由) の列、調べものは無効にしない
    ネットの道具の手番 (ログ, 道具, 引数の文) の列。ネットの道具は、引数がこのリポジトリか段の名（marks）を含むか、
    local の下で own の外を指すときだけ当たりにする（10-31 の L4：26 回の検索が、どれも MuJoCo の使い方の調べものだった。
    調べものを要る段ほど記録から抜けると、測りが偏る）。"""
    names = [m.lower() for m in (*REPO_MARKS, *marks) if m]
    found = [f for f in strays(logs, own, local, subs) if not NET_TOOL.search(f[1])]
    web = []
    for name, work, cwd, tool, params, skip in (c for c in tool_calls(logs, own, subs) if NET_TOOL.search(c[3])):
        texts = list(strings(params))
        named = next((n for n in names if any(n in x.lower() for x in texts)), None)
        near = outside(texts, cwd, work, local, skip)
        if named or near:
            found.append((name, tool, f"ネットの道具が「{named}」を名指した" if named else near[0]))
        else:
            web.append((name, tool, " ".join(texts)[:200]))
    return found, web


def paths_in(text, cwd):
    """引数の文字列が指すパス。語（空白・引用符・=・( で区切る）ごとに、ドライブからのパス・/ で始まるパス・作業の場所からの .. を含む
    相対のパスを全部拾う。file: の URI はその指すパスにし、ほかの :// を含む語（URL）はパスにしない。.. を含む絶対のパスは畳む。"""
    hits = []
    for word in (w for w in map(uri_path, WORD.findall(text)) if "://" not in w):
        found = [m.group(0) for m in DRIVE_PATH.finditer(word)] or ([word] if word.startswith("/") else [])
        found = [posixpath.normpath(h.replace("\\", "/")) if REL_UP.search(h) else h for h in found]
        hits += found or ([os.path.normpath(os.path.join(cwd, word))] if cwd and REL_UP.search(word) else [])
    return hits


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


def what_task(path):
    """What の見出しの「タスク: <id>」。読めなければ None。"""
    m = re.search(r"^タスク:\s*(\S+)", read_text(path), re.M) if Path(path).is_file() else None
    return m.group(1) if m else None


def index_text(base, k, run_id, rundir, results, found, web=(), at_start=None):
    """索引（抜き書きだけ。言い換えの要約は作らない）：落ちた隠しテスト・開始の木で通っていた新しい試験（at_start。取り出せなければ None）・
    道の検め・What の試行ごとの門と終わり方・最後の手番。"""
    lines = [f"# 索引：{base} 段 {k}（{run_id}）", "", "## 落ちた隠しテスト", ""]
    lines += [f"- 段 {stage_of(t, base)}：`{t.split('.')[-1]}` — {v}" for t, v in sorted(results.items()) if v != "ok"] or ["- 無し"]
    lines += ["", "## 開始の木で通っていた新しい試験", ""]
    lines += ["- 開始のコミットを取り出せませんでした"] if at_start is None else [
        "- %d/%d" % tuple(rate(at_start, at_start)), *[f"- `{t.split('.')[-1]}`" for t, v in sorted(at_start.items()) if v == "ok"]]
    lines += ["", "## 道の検め", ""] + ([f"- {log}：{tool} — {hit}" for log, tool, hit in found] or ["- 無し"])
    lines += ["", "## ネットの調べもの（無効にしない）", ""] + ([f"- {log}：{tool} — {text}" for log, tool, text in web] or ["- 無し"])
    items = bench_run.read_json(rundir / "factory" / "queue.json", {}).get("items") or {}
    for d in sorted(p for p in (rundir / "items").glob("*") if p.is_dir()):
        item = next((v for v in items.values() if isinstance(v, dict) and v.get("tid") == d.name), None)
        lines += item_lines(d, item or {"task": what_task(d / "what.md"), "status": "queue.json に無い（延長の前の What など）"})
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
    stems, res = {Path(f).stem: f for f in files}, {}
    for t, v in json.loads(line[len(MARK):]).items():
        mod = t[len(FAILED_IMPORT):] if t.startswith(FAILED_IMPORT) else None
        res.update(dict.fromkeys(declared(stems[mod]), v) if mod in stems else {t: v})
    return {t: v for t, v in res.items() if t.split(".")[-1] not in gone}


def declared(path):
    """読み込みで落ちた試験のファイルを、宣言した試験（<モジュール>.<クラス>.<test…>）の数だけの落ちに開く。
    1 件の疑似の試験のままだと、段の分け方（モジュールの名前で決める）と分母が狂う（Argon の事後の検め）。
    構文の誤りで宣言を読めなければ、そのモジュールの 1 件として数える（段の分け方は保つ）。"""
    mod = Path(path).stem
    try:
        body = ast.parse(read_text(path)).body
    except SyntaxError:
        return [f"{mod}.<読み込みの誤り>"]
    tests = [f"{mod}.{c.name}.{f.name}" for c in body if isinstance(c, ast.ClassDef)
             for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test")]
    return tests or [f"{mod}.<読み込みの誤り>"]


def rate(results, ids):
    ids = list(ids)
    return [sum(1 for t in ids if results.get(t) == "ok"), len(ids)]


def tip_tree(rundir, tmp, rev="tip", name="tree"):
    """走りの束を取り込み、rev（既定は先端）の木を tmp/name に出す。"""
    tree = Path(tmp) / name
    tree.mkdir()
    git(tree, ["init", "-q"])
    git(tree, ["fetch", "-q", str(Path(rundir) / BUNDLE), "refs/heads/tip:refs/heads/tip"])
    git(tree, ["checkout", "-q", rev])
    return tree


def start_results(rundir, tmp, st, files, gone):
    """段の開始のコミットの木での、段 k の隠しテストのファイル（files の最後）だけの採点。開始のコミットを取り出せなければ None。"""
    try:
        tree = tip_tree(rundir, tmp, st["start"], "start")
    except (BenchError, KeyError):
        return None
    return grade(tree, files[-1:], gone)


def finish_stage(rec, st, row, out, chains, cfg, priv, prev):
    """段 1 つの検めと採点。row を埋め、試験ごとの結果と索引を priv に書き、試験ごとの結果を返す。"""
    base, pkg, k = rec["base"], rec["package"], st["k"]
    rundir = bench_run.run_dir(out, base, st["run_id"])
    hits = bench_guard.leaks(cfg, [rundir])
    (rundir / "guard.txt").write_text("".join(f"漏れの跡: {f}:{n}\n" for f, n in hits), encoding="utf-8")
    logs, own = sorted((rundir / "items").glob("*/implementer-*.log")), bench_run.work_dir(out, base, st["run_id"])
    subs, missing = [], []
    for log in logs:   # 下請けの記録を What のディレクトリに写し、実装役と同じ道の検めを掛ける。写せない記録は当たり
        got = adopt(log, steps_of(log)[1], own)
        subs, missing = subs + got[0], missing + got[1]
    found, web = audit(logs, own, machine.path("local"), (base, pkg, "docs/bench", "chain/s"), subs)
    found += missing
    gone = replaced(chains, base, k)
    with tempfile.TemporaryDirectory(prefix="bench-chain-") as tmp:
        tree = tip_tree(rundir, tmp)
        try:
            names = git(tree, ["diff", "--name-only", "-z", st["start"], "tip"]).decode("utf-8", "replace").split("\0")
        except BenchError:   # 先端が開始のコミットから伸びていない（H ラインの枝なら起きない）。境界の違反として 0 点にする
            names = ["<開始のコミットが走りの記録の先端の祖先にありません>"]
        res = grade(tree, hidden_files(chains, base, k), gone)
        at_start = start_results(rundir, tmp, st, hidden_files(chains, base, k), gone)
    violations = [n for n in names if n and not bench_run.allowed_path(n, pkg)]
    new = [t for t in res if stage_of(t, base) == k]
    old = [t for t in res if t not in new]
    row.update(new=rate(res, new), old=rate(res, old), kept=rate(res, [t for t in old if prev.get(t) == "ok"]),
               violations=len(violations), leaks=len(hits), strays=len(found), web=len(web),
               new_at_start=None if at_start is None else rate(at_start, at_start))   # 境界の違反の 0 点は掛けない
    for key in ("new", "old", "kept") if violations else ():
        row[key][0] = 0   # 今の finish と同じく、境界の外を変えた段は 0 点
    if (why := invalid_reason(rundir, cfg, hits, found)):
        row["invalid"], row["reason"] = why
    hline_base.write_json(priv / f"s{k}.json", {"base": base, "k": k, "run_id": st["run_id"], "results": res, "replaced": sorted(gone),
                                               "violations": violations, "strays": found, "web": web, "leaks": hits,
                                               "at_start": at_start})
    (priv / f"s{k}.index.md").write_text(index_text(base, k, st["run_id"], rundir, res, found, web, at_start), encoding="utf-8")
    hline_base.write_json(rundir / "manifest.json", bench_run.manifest(rundir))
    row["manifest_sha256"] = bench_run.sha256_file(rundir / "manifest.json")
    return res


def invalid_reason(rundir, cfg, hits, found):
    """段の走りを無効にする理由 (印, 文)。凍結の finish と同じ順（漏れ・呼び出しの記録・モデル）の後に、道の検め。無ければ None。"""
    if hits:
        return "REQ-11", "漏れの跡があります（guard.txt）"
    if not list((Path(rundir) / "calls").glob("*/result.json")):
        return "REQ-22", "呼び出しの記録がありません"
    if (bad := bench_run.model_problem(Path(rundir), cfg)):
        return "REQ-02", f"許されないモデルが使われました: {', '.join(bad)}"
    if found:
        return "stray", "実装役が作業の場所の外の置き場かネットに触れました（索引の道の検め）"
    return None


def taint(row, tainted):
    """前の段（tainted）が無効なら、この段の理由より先に「前の段が無効」とし、この段の理由は文に添える。"""
    if tainted:
        own = f"。この段も {row['invalid']}：{row['reason']}" if row.get("invalid") else ""
        row["invalid"], row["reason"] = "tainted", f"前の段（{tainted}）が無効{own}"


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
        taint(row, tainted)
        tainted = tainted or (f"s{st['k']}" if row["invalid"] else None)
    return public


def finish(a):
    import bench_pair  # bench_pair が chain_score を読むので、ここで読む（公開の集計の書き方は bench_pair.publish）
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
            bench_pair.publish(dest, finish_chain(rec, out, chains, cfg, priv), priv)
    except (BenchError, OSError, KeyError, ValueError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0
