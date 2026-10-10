"""report.md の「## 照合」節：総監督が受信箱の checks/ に宣言した照合を走らせて、結果を出す。

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
HEAD_LINES = 5
CACHE_NAME = "checks_cache.json"


class Invalid(Exception):
    """宣言の誤り。"""


def checks_dir(cfg):
    return Path(cfg["inbox"]) / "checks"


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
    return kind, ref


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


def run_in_tree(cfg, decl, sha, runner):
    """ref の作業ツリーを detach して作り、その中でコマンドを走らせる。(終了コード, 出力)。作れなければ (None, 理由)。"""
    base = Path(cfg["out"]) / "checks_wt"
    base.mkdir(parents=True, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix="chk-", dir=base))
    ttl_git = cfg["ttl_seconds"]["git"]
    try:
        code, out, err = runner(["git", "worktree", "add", "--detach", str(tree), sha], ROOT, ttl_git, "git worktree add")
        if code != 0:
            return None, f"作業ツリーを作れない（終了コード {code}）：{(err or out).strip()}"
        code, out, err = runner(command(cfg, decl), tree, cfg["ttl_seconds"].get("gate", 1800), f"check {decl['kind']}")
        return code, out + ("\n" + err if err.strip() else "")
    finally:
        try:
            runner(["git", "worktree", "remove", "--force", str(tree)], ROOT, ttl_git, "git worktree remove")
        except Exception:   # noqa: BLE001
            pass
        shutil.rmtree(tree, ignore_errors=True)


def head(text):
    return [x.rstrip() for x in str(text).splitlines() if x.strip()][:HEAD_LINES]


def execute(cfg, decl, sha, runner):
    """{"status", "code", "head"}。"""
    try:
        if decl["kind"] == "git_log":
            code, out = run_git_log(cfg, decl, sha, runner)
        else:
            code, out = run_in_tree(cfg, decl, sha, runner)
    except Exception as e:   # noqa: BLE001 - 照合の例外で止めない
        return {"status": "取得失敗", "code": None, "head": [f"{type(e).__name__}: {e}"]}
    if code is None:
        return {"status": "取得失敗", "code": None, "head": head(out)}
    return {"status": "合格" if code == 0 else "不合格", "code": code, "head": head(out)}


def key_of(decl, sha):
    blob = json.dumps(decl, sort_keys=True, ensure_ascii=False) + "\0" + sha
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
    except Invalid as e:
        res = {"status": "宣言の誤り", "code": None, "head": [f"宣言の誤り：{e}"]}
        return line_of(name, "-", "-", res, clock)
    sha, why = resolve(cfg, ref, runner)
    if sha is None:
        res = {"status": "取得失敗", "code": None, "head": [hline_probe.failure(why)]}
        return line_of(name, kind, "-", res, clock)
    key = key_of(decl, sha)
    old = cache.get(name)
    if isinstance(old, dict) and old.get("key") == key and isinstance(old.get("res"), dict) and "at" in old:
        return line_of(name, kind, sha[:7], old["res"], old["at"])
    res = execute(cfg, decl, sha, runner)
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


def safe_section(cfg, **kw):
    try:
        return section(cfg, **kw)
    except Exception as e:   # noqa: BLE001 - report.md の書き出しを止めない
        return f"## 照合\n- {hline_probe.failure(f'{type(e).__name__}: {e}')}\n"
