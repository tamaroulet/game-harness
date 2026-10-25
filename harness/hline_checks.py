"""report.md の「## 照合」節：照合宣言（docs/checks/ または受信箱の checks/）を走らせて、結果を出す。

宣言は 1 ファイル 1 件の JSON（checks/<名前>.json）。種類は reqcov・git_log・progress_check・unittest の 4 つだけ。
どの照合も ref の時点の中身（detach した作業ツリー）に対して走らせ、根の作業ツリーの状態に依らない。
コマンドはシェルを通さず引数の列で呼ぶ。宣言の中身と ref の sha が前回と同じなら、走らせ直さず前回の結果を出す。
失敗や例外で report.md の書き出しは止めない。
"""
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import hline_probe
import proc
from hline_base import ROOT

NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
MODULE_RE = re.compile(r"^tests\.[A-Za-z0-9_]+$")
REFS = ("origin/main", "origin/hline/integration")
KINDS = ("reqcov", "git_log", "progress_check", "unittest")
TREE_FIELDS = ("overlay", "repeat", "expect")
MAX_REPEAT = 20
HEAD_LINES = 5
CACHE_NAME = "checks_cache.json"


class Invalid(Exception):
    """宣言の誤り。"""


def checks_dir(cfg):
    """照合の宣言の置き場。
    cfg['checks'] だけを読む（相対なら ROOT 起点）。無ければ KeyError で落とす。
    受信箱や根の checks/ を探す道は消す（総監督 42 本目 §3.1）。
    """
    if "checks" not in cfg:
        raise KeyError("cfg に 'checks' が指定されていません")
    p = Path(cfg["checks"])
    return p if p.is_absolute() else ROOT / p


def ref_of(decl):
    ref = decl.get("ref", "origin/main")
    if not isinstance(ref, str) or not (ref in REFS or HEX_RE.match(ref)):
        raise Invalid(f"ref に許されない値: {ref!r}")
    return ref


def rel_path(value, what):
    """相対パス（glob 可）。.. も駆動の文字（: や先頭の区切り）も許さない。"""
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{what} が文字列でない")
    parts = value.replace("\\", "/").split("/")
    if ":" in value or value.startswith(("/", "\\")) or ".." in parts or "\0" in value:
        raise Invalid(f"{what} に許されないパス: {value!r}")
    return value


def str_list(value, what, single=False):
    if single and isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise Invalid(f"{what} が空でない列でない")
    return value


def validate(decl):
    """宣言を検査して (kind, ref) を返す。誤りは Invalid。"""
    if not isinstance(decl, dict):
        raise Invalid("宣言が JSON の object でない")
    kind = decl.get("kind")
    if kind not in KINDS:
        raise Invalid(f"未知の kind: {kind!r}")
    ref = ref_of(decl)
    if kind == "reqcov":
        rel_path(decl.get("spec"), "spec")
        for t in str_list(decl.get("tests"), "tests", single=True):
            rel_path(t, "tests")
        if "whats" in decl:
            for w in str_list(decl["whats"], "whats", single=True):
                rel_path(w, "whats")
    elif kind == "git_log":
        for p in str_list(decl.get("paths"), "paths"):
            rel_path(p, "paths")
        count = decl.get("expect_commits")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise Invalid(f"expect_commits が 0 以上の整数でない: {count!r}")
        if "since" in decl:
            since = decl["since"]
            if not isinstance(since, str) or not HEX_RE.match(since):
                raise Invalid(f"since に許されない値: {since!r}")
    elif kind == "unittest":
        for m in str_list(decl.get("modules"), "modules"):
            if not isinstance(m, str) or not MODULE_RE.match(m):
                raise Invalid(f"modules に許されない値: {m!r}（tests.<英数字と _> のみ）")
    tree_fields = [k for k in TREE_FIELDS if k in decl]
    if kind == "git_log" and tree_fields:
        raise Invalid(f"git_log に使えない欄: {', '.join(tree_fields)}")
    if "overlay" in decl:
        overlay = decl["overlay"]
        if not isinstance(overlay, dict) or not overlay:
            raise Invalid("overlay が空でない object でない")
        for dest, src in overlay.items():
            rel_path(dest, "overlay の写し先")
            rel_path(src, "overlay の写し元")
    if "repeat" in decl:
        n = decl["repeat"]
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_REPEAT:
            raise Invalid(f"repeat が 1〜{MAX_REPEAT} の整数でない: {n!r}")
    if decl.get("expect", "pass") not in ("pass", "fail"):
        raise Invalid(f"expect に許されない値: {decl['expect']!r}（pass か fail）")
    return kind, ref


def overlay_sources(cfg, decl):
    """overlay の写し先（木の中の相対パス）→ 写し元の絶対パス。drafts/ または docs/ の外・無いファイルは Invalid。"""
    drafts = (Path(cfg["inbox"]) / "drafts").resolve() if "inbox" in cfg else None
    git_docs = (ROOT / "docs").resolve()
    found = {}
    for dest, src in decl.get("overlay", {}).items():
        path = None
        if drafts:
            p = (Path(cfg["inbox"]) / src).resolve()
            if p.is_file() and drafts in p.parents:
                path = p
        if path is None:
            p = (ROOT / src).resolve()
            if p.is_file() and git_docs in p.parents:
                path = p
        if path is None:
            raise Invalid(f"overlay の写し元が無いか許されないパス: {src!r}")
        found[dest] = path
    return found


def apply_overlay(tree, sources):
    root = tree.resolve()
    for dest, src in sources.items():
        target = (tree / dest).resolve()
        if root not in target.parents:
            raise Invalid(f"overlay の写し先が木の外: {dest!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)


def command(cfg, decl):
    """作業ツリーの中で走らせる引数の列（シェルを通さない）。"""
    kind = decl["kind"]
    if kind == "progress_check":
        return [sys.executable, "-m", "harness.progress", "check"]
    if kind == "unittest":
        return [sys.executable, "-m", "unittest", *decl["modules"]]
    args = [sys.executable, "-m", "harness.reqcov", "--spec", decl["spec"]]
    for t in str_list(decl["tests"], "tests", single=True):
        args += ["--tests", t]
    queue = Path(cfg["out"]) / "queue"
    if "whats" in decl:
        for w in str_list(decl["whats"], "whats", single=True):
            args += ["--whats", str(queue / w)]
    return args


def resolve(cfg, ref, runner):
    """ref を指すコミットの sha（40 桁）。取れなければ Invalid ではなく None と理由。"""
    try:
        code, out, err = runner(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], ROOT,
                                cfg["ttl_seconds"]["git"], "git rev-parse")
    except Exception as e:   # noqa: BLE001
        return None, f"git rev-parse: {type(e).__name__}: {e}"
    sha = out.strip()
    if code != 0 or not HEX_RE.match(sha):
        return None, f"{ref} を解決できない（終了コード {code}）"
    return sha, None


def run_git_log(cfg, decl, sha, runner):
    rng = f"{decl['since']}..{sha}" if decl.get("since") else sha
    code, out, err = runner(["git", "log", "--format=%h", rng, "--", *decl["paths"]], ROOT,
                            cfg["ttl_seconds"]["git"], "git log")
    if code != 0:
        return code, err or out
    shas = out.split()
    want = decl["expect_commits"]
    head = f"コミット {len(shas)} 件（期待 {want} 件）"
    return (0 if len(shas) == want else 1), "\n".join([head, *shas])


def run_in_tree(cfg, decl, sha, runner, sources=None):
    """ref の作業ツリーを detach して作り、overlay を重ねて、その中でコマンドを repeat 回走らせる。
    (終了コード, 出力)。作れなければ (None, 理由)。repeat を宣言したときは出力の先頭に期待どおりだった回数を付ける。"""
    base = Path(cfg["out"]) / "checks_wt"
    base.mkdir(parents=True, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix="chk-", dir=base))
    ttl_git = cfg["ttl_seconds"]["git"]
    try:
        code, out, err = runner(["git", "worktree", "add", "--detach", str(tree), sha], ROOT, ttl_git, "git worktree add")
        if code != 0:
            return None, f"作業ツリーを作れない（終了コード {code}）：{(err or out).strip()}"
        if sources:
            apply_overlay(tree, sources)
        want_pass = decl.get("expect", "pass") == "pass"
        times = decl.get("repeat", 1)
        good, bad = 0, None
        for _ in range(times):
            code, out, err = runner(command(cfg, decl), tree, cfg["ttl_seconds"].get("gate", 1800), f"check {decl['kind']}")
            text = out + ("\n" + err if err.strip() else "")
            if (code == 0) == want_pass:
                good += 1
                last = (code, text)
            elif bad is None:
                bad = (code, text)
        # 終了コードは合否の正規化：全回が期待どおりなら 0、外れた回があれば 0 以外（expect: fail で 0 だった回は 1）
        code, text = (0, last[1]) if bad is None else (bad[0] or 1, bad[1])
        if "repeat" in decl:
            text = f"期待どおり {good}／{times} 回\n{text}"
        return code, text
    finally:
        try:
            runner(["git", "worktree", "remove", "--force", str(tree)], ROOT, ttl_git, "git worktree remove")
        except Exception:   # noqa: BLE001
            pass
        shutil.rmtree(tree, ignore_errors=True)


def head(text):
    return [x.rstrip() for x in str(text).splitlines() if x.strip()][:HEAD_LINES]


def execute(cfg, decl, sha, runner, sources=None):
    """{"status", "code", "head"}。"""
    try:
        if decl["kind"] == "git_log":
            code, out = run_git_log(cfg, decl, sha, runner)
        else:
            code, out = run_in_tree(cfg, decl, sha, runner, sources)
    except Exception as e:   # noqa: BLE001 - 照合の例外で止めない
        return {"status": "取得失敗", "code": None, "head": [f"{type(e).__name__}: {e}"]}
    if code is None:
        return {"status": "取得失敗", "code": None, "head": head(out)}
    return {"status": "合格" if code == 0 else "不合格", "code": code, "head": head(out)}


def key_of(decl, sha, sources=None):
    """sources（写し先 → 写し元）があれば、写し元の中身も鍵に入れる。"""
    blob = json.dumps(decl, sort_keys=True, ensure_ascii=False) + "\0" + sha
    for dest, src in sorted((sources or {}).items()):
        blob += "\0" + dest + "\0" + hashlib.sha256(src.read_bytes()).hexdigest()
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def line_of(name, kind, sha7, res, at):
    code = "-" if res["code"] is None else res["code"]
    lines = [f"- {name}: {res['status']}（{kind}／{sha7}／{code}／{hline_probe.stamp(at)}）"]
    return lines + [f"    {x}" for x in res["head"]]


def load_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:   # noqa: BLE001
        return {}


def one(cfg, file, runner, clock, cache):
    """宣言 1 件の結果の行。cache は名前 → {"key", "res", "at", "sha7", "kind"} で、ここで更新する。"""
    name = file.stem
    try:
        if not NAME_RE.match(name):
            raise Invalid(f"名前に許されない文字: {name!r}（英数字とハイフンのみ）")
        try:
            decl = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Invalid(f"JSON として読めない: {type(e).__name__}") from e
        kind, ref = validate(decl)
        sources = overlay_sources(cfg, decl)
    except Invalid as e:
        res = {"status": "宣言の誤り", "code": None, "head": [f"宣言の誤り：{e}"]}
        return line_of(name, "-", "-", res, clock)
    sha, why = resolve(cfg, ref, runner)
    if sha is None:
        res = {"status": "取得失敗", "code": None, "head": [hline_probe.failure(why)]}
        return line_of(name, kind, "-", res, clock)
    key = key_of(decl, sha, sources)
    old = cache.get(name)
    if isinstance(old, dict) and old.get("key") == key and isinstance(old.get("res"), dict) and "at" in old:
        return line_of(name, kind, sha[:7], old["res"], old["at"])
    res = execute(cfg, decl, sha, runner, sources)
    if res["status"] in ("合格", "不合格"):
        cache[name] = {"key": key, "res": res, "at": clock}
    return line_of(name, kind, sha[:7], res, clock)


def section(cfg, runner=None, clock=None):
    runner = runner or proc.run
    clock = time.time() if clock is None else clock
    d = checks_dir(cfg)
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return "## 照合\n宣言なし\n"
    path = Path(cfg["out"]) / CACHE_NAME
    cache = {k: v for k, v in load_cache(path).items() if k in {f.stem for f in files}}
    lines = ["## 照合"]
    for f in files:
        try:
            lines += one(cfg, f, runner, clock, cache)
        except Exception as e:   # noqa: BLE001 - 1 件の例外で他の照合を止めない
            lines.append(f"- {f.stem}: 取得失敗（-／-／-／{hline_probe.stamp(clock)}）")
            lines.append(f"    {type(e).__name__}: {e}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return "\n".join(lines) + "\n"


def cached_section(cfg):
    """「## 照合」を、走らせずに前回の結果だけで書く（section と同じ行の形。記録の無い宣言は「記録なし」）。H ラインが止まっている間に使う。"""
    d = checks_dir(cfg)
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return "## 照合\n宣言なし\n"
    cache = load_cache(Path(cfg["out"]) / CACHE_NAME)
    lines = ["## 照合"]
    for f in files:
        old = cache.get(f.stem)
        if isinstance(old, dict) and "at" in old and isinstance(old.get("res"), dict):
            lines += line_of(f.stem, "-", "-", old["res"], old["at"])
        else:
            lines.append(f"- {f.stem}: 記録なし（-／-／-／-）")
    return "\n".join(lines) + "\n"


def stopped_section(cfg):
    """H ラインが止まっている間の「## 照合」：走らせず、宣言ごとに、走っていないことと最後に走った時刻・前回の結果を出す（W-144）。"""
    try:
        d = checks_dir(cfg)
        files = sorted(d.glob("*.json")) if d.is_dir() else []
        if not files:
            return "## 照合\n宣言なし\n"
        cache = load_cache(Path(cfg["out"]) / CACHE_NAME)
        lines = ["## 照合", "- H ラインが止まっているので、照合は走らせていない"]
        for f in files:
            old = cache.get(f.stem)
            if isinstance(old, dict) and "at" in old and isinstance(old.get("res"), dict):
                lines.append(f"- {f.stem}: 走っていない（最後に走った時刻: {hline_probe.stamp(old['at'])}／その結果: {old['res'].get('status')}）")
            else:
                lines.append(f"- {f.stem}: 走っていない（最後に走った記録なし）")
        return "\n".join(lines) + "\n"
    except Exception as e:   # noqa: BLE001 - report.md の書き出しを止めない
        return f"## 照合\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"


def safe_section(cfg, **kw):
    try:
        return section(cfg, **kw)
    except Exception as e:   # noqa: BLE001 - report.md の書き出しを止めない
        return f"## 照合\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"
