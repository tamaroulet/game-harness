"""第 1 関門の確かめを、課題の箱の中で走らせる（実験と計画 §5-3、E-1 の段 a。設計 §4-2 の出口）。

箱の中の testbed の Python（3.10 以上。標準ライブラリと pyflakes だけ）で走らせ、結果を JSON で書く。
制御核もリポジトリのほかの部品も読まない（箱に写すのは、このファイルと exitcode・job_object の 3 つ）。

    python fb_gate_probe.py <spec.json> <out.json>

spec の鍵：
  root：リポジトリの根（/testbed）
  packages：ソースの根の名の並び（例 ["pandas"]）
  ledger_paths：課題の文の口の Path（根からの相対）
  base：始まりのコミット（変えたファイルを git で数える）
  checks：走らせる検査（undefined・imports・attrs・tests・refs・test_imports）
  pyflakes_dir：pyflakes を置いた所
  test_cmd・timeout_one・file_timeout・test_budget：テストの走らせ方
  hidden_tests：消した公式のテストのファイル（選ばない）
  names・literals：refs で、始まりの状態のソースに出てくるかを調べる名と文言
"""
import ast
import glob
import io
import json
import os
import re
import signal
import subprocess
import sys
import sysconfig
import threading
import time
import tokenize

SKIP_DIRS = {"__pycache__", ".git", "build", "dist", "node_modules", ".tox", ".eggs"}
FREE_BASES = {"object", "ABC", "Generic", "Protocol"}
FREE_MODULES = {"abc", "typing", "typing_extensions"}
DYNAMIC_METHODS = {"__getattr__", "__getattribute__"}
# -rA の要約の行。conftest が状態の語と項目の間に「| MEM … | DISK … 」のような飾りを入れることがある（mlflow）
PASS_RE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)\s+(?:\|.*?\s(?=\S+\.py))?(\S+)(?:\s+-\s+(.*))?$", re.M)


def is_test_file(rel):
    parts = rel.split("/")
    name = parts[-1]
    return (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or any(p in ("tests", "test") for p in parts[:-1]))


def source_files(root, packages):
    """パッケージのソース（テストのファイルを除く .py）の、根からの相対のパス。"""
    out = []
    for pkg in packages:
        base = os.path.join(root, pkg)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in sorted(files):
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                    if not is_test_file(rel):
                        out.append(rel)
    return out


def module_name(rel):
    """相対のパス → モジュールの名（pkg/a/__init__.py → pkg.a）。名にならなければ None。"""
    if not rel.endswith(".py"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[-1] == "__main__" or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def read(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        return f.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 未定義の名（pyflakes）

def check_undefined(root, files, pyflakes_dir):
    sys.path.insert(0, pyflakes_dir)
    from pyflakes import api, messages
    keep = (messages.UndefinedName, messages.UndefinedExport, messages.UndefinedLocal)
    found = []

    class Reporter:
        def unexpectedError(self, filename, msg):
            found.append({"file": rel_of(filename), "kind": "error", "msg": str(msg)[:200]})

        def syntaxError(self, filename, msg, lineno, offset, text):
            found.append({"file": rel_of(filename), "line": lineno, "kind": "syntax", "msg": str(msg)[:200]})

        def flake(self, m):
            if isinstance(m, keep):
                found.append({"file": rel_of(m.filename), "line": m.lineno, "kind": type(m).__name__,
                              "name": str(m.message_args[0]) if m.message_args else ""})

    def rel_of(filename):
        return os.path.relpath(filename, root).replace(os.sep, "/")

    r = Reporter()
    unchecked = []
    for rel in files:
        try:
            api.checkPath(os.path.join(root, rel), r)
        except RecursionError:   # 大きいスタックでも上限に当たる深い式。検出に数えず、検査しなかったファイルとして記録する
            unchecked.append(rel)
    return {"found": found, "unchecked": unchecked}


def deep_stack(fn, stack_mb=512, limit=50000):
    """深い式（sympy の生成コードなど）で pyflakes の再帰が尽きないよう、大きいスタックのスレッドで走らせる。
    再帰の上限は 50000（1 段あたり 5KB を見込む）。Windows の Python はスタックを 256MB 未満しか受け付けないので、
    断られたら 255MB にする（コンテナの中は Linux）。"""
    box = {}

    def run():
        sys.setrecursionlimit(limit)
        try:
            box["value"] = fn()
        except BaseException as e:   # 呼んだ側に渡し直す
            box["error"] = e

    try:
        old = threading.stack_size(stack_mb * 1024 * 1024)
    except ValueError:
        old = threading.stack_size(255 * 1024 * 1024)
    try:
        t = threading.Thread(target=run)
        t.start()
        t.join()
    finally:
        threading.stack_size(old)
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- プロセスの後始末とビルドの検査（検査が子孫プロセスや C/C++ のビルドを残さない）

def session_procs(sid):
    """セッション sid（start_new_session で作った子がリーダー）に属するプロセスの PID。孫（ninja が起こした cc1 など）も、
    親が先に終わって PID 1 に付け替えられた後も、セッション ID は変わらないので拾える。"""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", "rb") as f:
                fields = f.read().rsplit(b")", 1)[1].split()
            if int(fields[3]) == sid and fields[0] != b"Z":
                out.append(int(d))
        except (OSError, ValueError, IndexError):
            continue
    return out


def kill_session(sid):
    """セッション sid のプロセスを全部止め、止めた数を返す。"""
    pids = session_procs(sid)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(pids)


def run_session(args, timeout, **kw):
    """新しいセッションで走らせ、終わったら（タイムアウトでも）セッションに残ったプロセスを全部止める。
    (終了コード, 標準出力のバイト列, 標準エラーのバイト列, 止めたプロセスの数)。タイムアウトの終了コードは 124。"""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, **kw)
    try:
        out, err = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        kill_session(p.pid)
        out, err = p.communicate()
        code = 124
    return code, out, err, kill_session(p.pid)


def orphans():
    """コンテナの中で PID 1 に付け替えられた孤児プロセス（ゾンビを除く）。前の検査の残りを見つける。"""
    out = []
    me = os.getpid()
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in (1, me):
            continue
        try:
            with open(f"/proc/{d}/stat", "rb") as f:
                head, tail = f.read().rsplit(b")", 1)
            fields = tail.split()
            if int(fields[1]) == 1 and fields[0] != b"Z":
                out.append({"pid": int(d), "comm": head.split(b"(", 1)[1].decode("utf-8", "replace")})
        except (OSError, ValueError, IndexError):
            continue
    return out


def sweep_orphans():
    found = orphans()
    for o in found:
        try:
            os.kill(o["pid"], signal.SIGKILL)
        except OSError:
            pass
    return [o["comm"] for o in found]


def parse_loader(text):
    """meson-python の editable install のローダー（_<名>_editable_loader.py）の末尾の install(...) から、
    ビルドディレクトリとビルドコマンドを取る。({"build": ディレクトリ, "cmd": [コマンド]} か None)"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "install":
            build = cmd = None
            for a in node.args:
                try:
                    v = ast.literal_eval(a)
                except (ValueError, TypeError, SyntaxError):
                    continue
                if isinstance(v, str) and build is None and v.startswith("/"):
                    build = v
                elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                    cmd = v
            if build and cmd:
                return {"build": build, "cmd": cmd}
    return None


def editable_builds(site_dirs=None):
    """この Python に入っている meson-python の editable install（ビルドディレクトリごとに 1 つ）。"""
    if site_dirs is None:
        paths = sysconfig.get_paths()
        site_dirs = sorted({paths["purelib"], paths["platlib"]})
    seen, out = set(), []
    for sd in site_dirs:
        for f in sorted(glob.glob(os.path.join(sd, "_*_editable_loader.py"))):
            with open(f, encoding="utf-8") as fh:
                b = parse_loader(fh.read())
            if b and b["build"] not in seen:
                seen.add(b["build"])
                out.append(dict(b, loader=os.path.basename(f)))
    return out


def compile_steps(dry_run_stdout):
    """ninja -n の出力のうち、C/C++・Cython のコンパイルとリンクのステップ（Compiling・Linking）と、build.ninja の
    再生成（Regenerating。再生成すると全体の再ビルドになりうる）。版の書き出しのような、毎回走る軽いステップは数えない。"""
    keys = ("Compiling", "Linking", "Regenerating")
    return [l for l in dry_run_stdout.splitlines() if l.startswith("[") and any(k in l for k in keys)]


def build_state(builds):
    """残りのビルドのステップ（ninja -n。ビルドは走らせない）と、ビルド成果物（.so・.o）の mtime の最大。"""
    out = []
    for b in builds:
        st = {"build": b["build"], "pending": None, "first": [], "built_mtime": 0}
        try:
            p = subprocess.run(b["cmd"] + ["-n"], cwd=b["build"], capture_output=True, timeout=300)
            if p.returncode == 0:
                steps = compile_steps(p.stdout.decode("utf-8", errors="replace"))
                st.update({"pending": len(steps), "first": steps[:3]})
        except (OSError, subprocess.TimeoutExpired):
            pass
        for dirpath, _, names in os.walk(b["build"]):
            for n in names:
                if n.endswith((".so", ".o")):
                    try:
                        st["built_mtime"] = max(st["built_mtime"], os.stat(os.path.join(dirpath, n)).st_mtime_ns)
                    except OSError:
                        pass
        out.append(st)
    return out


# ---------------------------------------------------------------- import

IMPORT_SCRIPT = r'''
import importlib, json, signal, sys
mods = json.load(open(sys.argv[1]))
def alarm(signum, frame):
    raise TimeoutError("import timed out")
signal.signal(signal.SIGALRM, alarm)
for m in mods:
    print(json.dumps({"start": m}), flush=True)
    signal.alarm(20)
    try:
        importlib.import_module(m)
        r = None
    except BaseException as e:
        r = {"module": m, "type": type(e).__name__, "msg": str(e)[:300],
             "missing": getattr(e, "name", None) if isinstance(e, ImportError) else None}
    finally:
        signal.alarm(0)
    if r:
        print(json.dumps(r), flush=True)
print(json.dumps({"done": True}), flush=True)
'''


def check_imports(root, files, packages, budget=1500):
    mods = [m for m in (module_name(f) for f in files) if m]
    script = os.path.join("/tmp", "fb_gate_imports.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(IMPORT_SCRIPT)
    env = dict(os.environ, MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1")
    found, todo, t0, killed = [], list(mods), time.time(), 0
    while todo and time.time() - t0 < budget:
        listing = os.path.join("/tmp", "fb_gate_mods.json")
        with open(listing, "w", encoding="utf-8") as f:
            json.dump(todo, f)
        _, out_b, _, n = run_session([sys.executable, script, listing], max(60, budget - (time.time() - t0)),
                                     cwd=root, env=env)
        killed += n
        out = out_b.decode("utf-8", errors="replace")
        started, done = None, False
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "start" in d:
                started = d["start"]
            elif d.get("done"):
                done = True
            elif "module" in d:
                found.append(d)
        if done:
            todo = []
        elif started is None:
            break
        else:
            found.append({"module": started, "type": "crash", "msg": "the import process ended", "missing": None})
            todo = todo[todo.index(started) + 1:]
    for d in found:
        miss = (d.get("missing") or "").split(".")[0]
        d["optional"] = d["type"] == "ModuleNotFoundError" and bool(miss) and miss not in packages
    return {"modules": len(mods), "failed": found, "not_run": todo, "killed_leftovers": killed}


# ---------------------------------------------------------------- 属性（self.名）

def _walk_top(body):
    """モジュールかクラスの本体の文（if・try・with の中も）。関数の中には入らない。"""
    for node in body:
        yield node
        for field in ("body", "orelse", "finalbody", "handlers"):
            sub = getattr(node, field, None)
            if isinstance(sub, list) and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from _walk_top(sub)


def _package_of(mod, is_pkg):
    return mod if is_pkg else mod.rpartition(".")[0]


class Index:
    """パッケージのモジュールとクラスの表。"""

    def __init__(self, root, files):
        self.mods, self.classes, self.imports = {}, {}, {}
        for rel in files:
            mod = module_name(rel)
            if not mod:
                continue
            try:
                tree = ast.parse(read(root, rel), filename=rel)
            except (SyntaxError, ValueError):
                continue
            self.mods[mod] = (rel, tree)
            is_pkg = rel.endswith("__init__.py")
            imp = {}
            for node in _walk_top(tree.body):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.asname:
                            imp[a.asname] = ("mod", a.name)
                        else:
                            imp[a.name.split(".")[0]] = ("mod", a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    src = node.module or ""
                    if node.level:
                        pkg = _package_of(mod, is_pkg)
                        for _ in range(node.level - 1):
                            pkg = pkg.rpartition(".")[0]
                        src = f"{pkg}.{src}" if src else pkg
                    for a in node.names:
                        imp[a.asname or a.name] = ("name", src, a.name)
                elif isinstance(node, ast.ClassDef):
                    self.classes[(mod, node.name)] = node
            self.imports[mod] = imp

    def resolve_name(self, mod, name, depth=0):
        if depth > 6:
            return None
        if (mod, name) in self.classes:
            return (mod, name)
        sub = f"{mod}.{name}"
        imp = self.imports.get(mod, {}).get(name)
        if imp and imp[0] == "name":
            return self.resolve_name(imp[1], imp[2], depth + 1)
        if sub in self.mods:
            return ("module", sub)
        return None

    def resolve_base(self, mod, expr):
        """クラスの基の式 → ('free',) か (mod, name) か None（辿れない）。"""
        if isinstance(expr, ast.Subscript):
            return self.resolve_base(mod, expr.value)
        if isinstance(expr, ast.Name):
            if (mod, expr.id) in self.classes:
                return (mod, expr.id)
            imp = self.imports.get(mod, {}).get(expr.id)
            if imp and imp[0] == "name":
                if imp[1].split(".")[0] in FREE_MODULES and imp[2] in FREE_BASES:
                    return ("free",)
                r = self.resolve_name(imp[1], imp[2])
                return r if r and r[0] != "module" else None
            if expr.id == "object":
                return ("free",)
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            imp = self.imports.get(mod, {}).get(expr.value.id)
            if imp and imp[0] == "mod":
                if imp[1] in FREE_MODULES and expr.attr in FREE_BASES:
                    return ("free",)
                r = self.resolve_name(imp[1], expr.attr)
                return r if r and r[0] != "module" else None
            if imp and imp[0] == "name":
                r = self.resolve_name(imp[1], imp[2])
                if r and r[0] == "module":
                    r2 = self.resolve_name(r[1], expr.attr)
                    return r2 if r2 and r2[0] != "module" else None
        return None


def _methods(cls):
    return [n for n in _walk_top(cls.body) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _first_arg(fn):
    args = fn.args.posonlyargs + fn.args.args
    if not args:
        return None
    for d in fn.decorator_list:
        if isinstance(d, ast.Name) and d.id == "staticmethod":
            return None
    return args[0].arg


def own_attrs(cls):
    """クラスが自分で定義する名と、動的な仕組みがあるか。"""
    names, dynamic = set(), False
    for node in _walk_top(cls.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__slots__" for t in node.targets):
            for n in ast.walk(node.value):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    names.add(n.value)
    for kw in cls.keywords:
        if kw.arg == "metaclass" and not (isinstance(kw.value, ast.Name) and kw.value.id == "ABCMeta"):
            dynamic = True
    for fn in _methods(cls):
        if fn.name in DYNAMIC_METHODS:
            dynamic = True
        me = _first_arg(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and isinstance(n.value, ast.Name):
                if n.value.id in (me, "self", "cls"):
                    names.add(n.attr)
            elif isinstance(n, ast.Call):
                f = n.func
                fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                if fname in ("setattr", "__setattr__") and len(n.args) >= 2:
                    key = n.args[1]
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        names.add(key.value)
                    else:
                        dynamic = True
                if fname in ("update",) and isinstance(f, ast.Attribute):
                    v = f.value
                    if isinstance(v, ast.Attribute) and v.attr == "__dict__":
                        dynamic = True
                if fname == "vars":
                    dynamic = True
    return names, dynamic


def check_attrs(root, files):
    idx = Index(root, files)
    info = {}
    for key, cls in idx.classes.items():
        names, dynamic = own_attrs(cls)
        bases = [idx.resolve_base(key[0], b) for b in cls.bases]
        info[key] = {"names": names, "dynamic": dynamic, "bases": bases}
    parents = {k: [b for b in v["bases"] if b and b[0] != "free"] for k, v in info.items()}
    resolvable = {}

    def ok(k, seen=()):
        if k in resolvable:
            return resolvable[k]
        if k in seen or k not in info:
            return False
        v = info[k]
        r = (not v["dynamic"]) and all(b is not None for b in v["bases"]) and all(ok(p, seen + (k,)) for p in parents[k])
        resolvable[k] = r
        return r

    def ancestors(k):
        out, todo = set(), list(parents.get(k, []))
        while todo:
            p = todo.pop()
            if p not in out and p in info:
                out.add(p)
                todo.extend(parents.get(p, []))
        return out

    children = {}
    for k, ps in parents.items():
        for p in ps:
            children.setdefault(p, set()).add(k)

    def descendants(k):
        out, todo = set(), list(children.get(k, []))
        while todo:
            c = todo.pop()
            if c not in out:
                out.add(c)
                todo.extend(children.get(c, []))
        return out

    def defined(k):
        s = set(info[k]["names"])
        for a in ancestors(k):
            s |= info[a]["names"]
        return s

    flags, checked, skipped = [], 0, 0
    for k, cls in idx.classes.items():
        desc = descendants(k)
        if not ok(k) or any(not ok(d) for d in desc):
            skipped += 1
            continue
        checked += 1
        have = defined(k)
        for d in desc:
            have |= defined(d)
        rel = idx.mods[k[0]][0]
        for fn in _methods(cls):
            me = _first_arg(fn)
            if not me:
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) and isinstance(n.value, ast.Name)
                        and n.value.id == me and not (n.attr.startswith("__") and n.attr.endswith("__"))
                        and n.attr not in have):
                    flags.append({"file": rel, "line": n.lineno, "class": k[1], "attr": n.attr})
    uniq = {(f["file"], f["class"], f["attr"]): f for f in flags}
    stored = set()
    for _rel, tree in idx.mods.values():
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store):
                stored.add(n.attr)
    return {"classes_checked": checked, "classes_skipped": skipped,
            "flags": sorted(uniq.values(), key=lambda f: (f["file"], f["line"])),
            "stored_anywhere": sorted({f["attr"] for f in uniq.values()} & stored)}


# ---------------------------------------------------------------- テスト

def git_changed(root, base):
    """始まりのコミットから変わった・足されたファイル（消したものを除く）。"""
    p = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, capture_output=True)
    out = []
    for line in p.stdout.decode("utf-8", errors="replace").splitlines():
        if len(line) < 4 or "D" in line[:2]:
            continue
        out.append(line[3:].split(" -> ")[-1].strip().strip('"'))
    return sorted(set(out))


def imported_modules(tree, mod=None, is_pkg=False):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            src = n.module or ""
            if n.level and mod:
                pkg = _package_of(mod, is_pkg)
                for _ in range(n.level - 1):
                    pkg = pkg.rpartition(".")[0]
                src = f"{pkg}.{src}" if src else pkg
            out.add(src)
            for a in n.names:
                out.add(f"{src}.{a.name}")
    return out


def all_test_files(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".py") and (f.startswith("test_") or f.endswith("_test.py")):
                rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                if is_test_file(rel):
                    out.append(rel)
    return out


def select_tests(root, ledger_paths, changed, hidden):
    want = {m for m in (module_name(p) for p in list(ledger_paths) + [c for c in changed if not is_test_file(c)]) if m}
    own = [c for c in changed if c.endswith(".py") and is_test_file(c) and os.path.basename(c).startswith("test_")]
    picked = []
    for rel in all_test_files(root):
        if rel in hidden or rel in own:
            continue
        try:
            tree = ast.parse(read(root, rel))
        except (SyntaxError, ValueError):
            continue
        if imported_modules(tree, module_name(rel), rel.endswith("__init__.py")) & want:
            picked.append(rel)
    return sorted(own), picked, sorted(want)


def test_imports(root):
    """テストのファイルごとの、import するモジュールの名の並び（R0 の群 2・3 を選ぶ材料）。構文木で読むだけで、
    import もテストもしない。読めないファイルは unparsed に入れる。"""
    files, unparsed = {}, []
    for rel in all_test_files(root):
        try:
            tree = ast.parse(read(root, rel))
        except (SyntaxError, ValueError):
            unparsed.append(rel)
            continue
        files[rel] = sorted(imported_modules(tree, module_name(rel), rel.endswith("__init__.py")))
    return {"files": files, "unparsed": unparsed}


def select_tests_r0(imports, ledger_paths, changed):
    """R0 のテストの選び方（実験と計画 §5-6 の 2）。imports は {t=0 にあるテストのファイル: [import するモジュールの名]}。
    群 1（own）：候補が足したか変えたテストのファイル（名前が test_ で始まるもの）。群 2（ledger）：台帳の Path の
    モジュールを import するもの。群 3（changed）：変えたソースのモジュールを import するもの。最上位のモジュールの名
    （. を含まない名）は選びに使わない（最上位の __init__.py の変更は、ほぼすべてのテストに当たる）。群の中はパスの順で、
    1 つのファイルは最初に当たった群にだけ入れる。"""
    def names(paths):
        found = {module_name(p) for p in paths} - {None}
        return {n for n in found if "." in n}, {n for n in found if "." not in n}

    want_ledger, top_ledger = names(ledger_paths)
    want_changed, top_changed = names([c for c in changed if not is_test_file(c)])
    want_changed -= want_ledger
    own = sorted(c for c in changed if c.endswith(".py") and is_test_file(c) and os.path.basename(c).startswith("test_"))
    taken = set(own)
    ledger = sorted(rel for rel, mods in imports.items() if rel not in taken and set(mods) & want_ledger)
    taken |= set(ledger)
    other = sorted(rel for rel, mods in imports.items() if rel not in taken and set(mods) & want_changed)
    return {"own": own, "ledger": ledger, "changed": other, "want_ledger": sorted(want_ledger),
            "want_changed": sorted(want_changed), "top_level_dropped": sorted(top_ledger | top_changed)}


# R0 の環境の型（実験と計画 §5-6 の 4）。E1〜E6 は押し返さない。U は誤りの文が空で判別できない。
NETWORK_WORDS = ("URLError", "urlopen error", "Name or service not known", "Temporary failure in name resolution",
                 "Network is unreachable", "Connection refused", "ConnectionError", "Max retries exceeded", "gaierror",
                 "auto-update failed", "is expired")
_NO_MODULE_RE = re.compile(r"No module named '([^']+)'")
_NO_ATTR_RE = re.compile(r"module '([^']+)' has no attribute")
_CANNOT_IMPORT_RE = re.compile(r"cannot import name '[^']+' from '([^']+)'")
_PATH_ERROR_RE = re.compile(r"\b(?:FileNotFoundError|NotADirectoryError|IsADirectoryError|PermissionError)\b.*?'(/[^']*)'")
_TEST_TIMEOUT_RE = re.compile(r"Timeout \(>[0-9.]+s\) from pytest-timeout")


def failure_class(msg, packages, root="/testbed"):
    """テストの落ちの原因の型：E1〜E6（環境）・U（誤りの文が空）・target（押し返す）。packages は対象のパッケージの名。
    対象のパッケージの名に当たる No module named・has no attribute・cannot import name は、消された部品の穴として target。"""
    text = (msg or "").strip()
    if not text:
        return "U"
    tops = set(packages or [])

    def outside(name):
        return name.split(".")[0] not in tops

    m = _NO_MODULE_RE.search(text)
    if m:
        return "E2" if outside(m.group(1)) else "target"
    for rx in (_NO_ATTR_RE, _CANNOT_IMPORT_RE):
        m = rx.search(text)
        if m:
            return "E3" if outside(m.group(1)) else "target"
    m = _PATH_ERROR_RE.search(text)
    base = root.rstrip("/")
    if m and not (m.group(1) == base or m.group(1).startswith(base + "/")):
        return "E4"
    if "exited with return code" in text or "command not found" in text:
        return "E5"
    if _TEST_TIMEOUT_RE.search(text):
        return "E6"
    if any(w in text for w in NETWORK_WORDS):
        return "E1"
    return "target"


def error_line(text):
    """pytest の誤りの本文（JUnit XML の誤りの本文か、標準エラー）から原因の 1 行：最後の「E 」の行（pytest が
    例外を示す行）。無ければ最後の行。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    marked = [ln[1:].strip() for ln in lines if ln.startswith("E ")]
    return (marked[-1] if marked else (lines[-1] if lines else ""))[:300]


def junit_messages(path):
    """JUnit XML（pytest --junitxml）から、落ちた項目の (クラスの名か None, 項目の名) → 落ちの 1 行。
    端末の幅で切れない落ちの文を取るため（端末の幅の環境変数は、表示を確かめるテストを変えるので触らない）。
    収集の誤りは message が "collection failure" に決まっているので、本文から原因の行を取る。"""
    import xml.etree.ElementTree as ET
    out = {}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return out
    for tc in root.iter("testcase"):
        for tag in ("failure", "error"):
            el = tc.find(tag)
            if el is not None:
                cls = (tc.get("classname") or "").rsplit(".", 1)[-1]
                key = (cls if cls[:1].isupper() else None, tc.get("name") or "")
                msg = el.get("message") or ""
                if not msg or msg == "collection failure":
                    msg = error_line(el.text) or msg
                out[key] = msg[:300]
                break
    return out


def node_key(node):
    parts = node.split("::")[1:]
    if not parts:   # ファイルの収集の誤り。JUnit XML の名は、パスをドットでつないだモジュールの名
        return (None, re.sub(r"\.py$", "", node).replace("/", "."))
    return (parts[-2] if len(parts) >= 2 else None, parts[-1])


def run_tests(root, files, test_cmd, timeout_one, file_timeout, budget):
    results, failed, t0, not_run, killed = {}, [], time.time(), [], 0
    xml = "/tmp/fb_gate_junit.xml"
    for rel in files:
        if time.time() - t0 > budget:
            not_run.append(rel)
            continue
        if os.path.exists(xml):
            os.remove(xml)
        cmd = (f"timeout {int(file_timeout)} {test_cmd} -p no:cacheprovider --timeout={int(timeout_one)} "
               f"--junitxml={xml} {rel}")
        s = time.time()
        code, out_b, err_b, n = run_session(["bash", "-c", cmd], int(file_timeout) + 60, cwd=root,
                                            env=dict(os.environ, MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1"))
        killed += n
        p = subprocess.CompletedProcess(cmd, code)
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        msgs = junit_messages(xml)
        counts, before = {}, len(failed)
        for m in PASS_RE.finditer(out):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            if m.group(1) in ("FAILED", "ERROR"):
                node = m.group(2)
                failed.append({"node": node, "kind": m.group(1),
                               "msg": msgs.get(node_key(node)) or (m.group(3) or "")[:300]})
        if p.returncode not in (0, 1, 5, 124) and len(failed) == before:
            # 項目の行が無い終わり（符号 4：conftest の import の誤りや使い方の誤りなど。原因は標準エラーに出る）
            failed.append({"node": rel, "kind": "ERROR", "file_level": True,
                           "msg": error_line(err) or error_line(out)})
        results[rel] = {"rc": p.returncode, "secs": round(time.time() - s, 1), "counts": counts,
                        "timeout": p.returncode == 124, "tail": out[-300:] if p.returncode not in (0, 1) else "",
                        "err_tail": err[-300:] if p.returncode not in (0, 1) else ""}
    return {"files": results, "failed": failed, "not_run": not_run, "killed_leftovers": killed}


# ---------------------------------------------------------------- refs（始まりの状態のソースに名と文言が出てくるか）

def name_tokens(src):
    out = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.NAME:
                out.add(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        out |= set(re.findall(r"[A-Za-z_]\w*", src))
    return out


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def check_refs(root, files, names, literals):
    toks, blob = set(), []
    for rel in files:
        src = read(root, rel)
        toks |= name_tokens(src)
        blob.append(norm(src))
    text = "\n".join(blob)
    return {"names": sorted(n for n in names if n in toks),
            "literals": sorted(lit for lit in literals if norm(lit) and norm(lit) in text)}


# ---------------------------------------------------------------- 本体

def main():
    spec_path, out_path = sys.argv[1], sys.argv[2]
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    root, packages = spec["root"], spec["packages"]
    checks = set(spec.get("checks") or [])
    files = source_files(root, packages)
    res = {"source_files": len(files), "secs": {}, "orphans_before": sweep_orphans()}

    def timed(name, fn):
        t = time.time()
        res[name] = fn()
        res["secs"][name] = round(time.time() - t, 1)

    # import とテストは、editable install（meson-python）の再ビルドを起こしうる。C/C++ のビルドのステップが残っていれば走らせない。
    builds = editable_builds() if checks & {"imports", "tests"} else []
    if builds:
        res["build"] = {"before": build_state(builds)}
        pending = [b for b in res["build"]["before"] if b["pending"] != 0]
        if pending:
            res["error"] = f"editable build pending (import and tests not run): {pending}"[:500]
            checks -= {"imports", "tests"}

    if "undefined" in checks:
        timed("undefined", lambda: deep_stack(lambda: check_undefined(root, files, spec["pyflakes_dir"])))
        res["undefined_unchecked"] = res["undefined"]["unchecked"]
        res["undefined"] = res["undefined"]["found"]
    if "imports" in checks:
        timed("imports", lambda: check_imports(root, files, set(packages), spec.get("import_budget", 1500)))
    if "attrs" in checks:
        timed("attrs", lambda: check_attrs(root, files))
    if "refs" in checks:
        timed("refs", lambda: check_refs(root, files, spec.get("names") or [], spec.get("literals") or []))
    if "test_imports" in checks:
        timed("test_imports", lambda: test_imports(root))
    if "tests" in checks:
        changed = git_changed(root, spec.get("base"))
        if spec.get("test_files") is not None:   # 決めたファイルだけを走らせる（正解の状態で、A の状態と同じファイルを当て直すため）
            own, want = [], []
            picked = [f for f in spec["test_files"] if os.path.exists(os.path.join(root, f))]
        else:
            own, picked, want = select_tests(root, spec.get("ledger_paths") or [], changed, set(spec.get("hidden_tests") or []))
        res["changed"] = changed
        res["test_modules"] = want
        res["tests_selected"] = {"own": own, "existing": picked}
        timed("tests", lambda: run_tests(root, own + picked, spec["test_cmd"], spec.get("timeout_one", 10),
                                         spec.get("file_timeout", 300), spec.get("test_budget", 600)))
    if builds:   # 検査の間に C/C++ のビルドが走っていない（ビルド成果物の mtime が変わっていない）ことを確認する
        res["build"]["after"] = build_state(builds)
        before = {b["build"]: b["built_mtime"] for b in res["build"]["before"]}
        rebuilt = [b["build"] for b in res["build"]["after"] if b["built_mtime"] != before.get(b["build"])]
        res["build"]["rebuilt"] = rebuilt
        if rebuilt:
            res["error"] = f"editable build ran during the checks: {rebuilt}"
    res["orphans_after"] = sweep_orphans()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 箱には exitcode と job_object も同じ所に写す
    import exitcode
    sys.exit(exitcode.normalized(main))
