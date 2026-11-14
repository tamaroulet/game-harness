"""第 1 関門の確かめを、課題の箱の中で走らせる（実験と計画 §5-3、E-1 の段 a。設計 §4-2 の出口）。

箱の中の testbed の Python（3.10 以上。標準ライブラリと pyflakes だけ）で走らせ、結果を JSON で書く。
制御核もリポジトリのほかの部品も読まない（箱に写すのは、このファイルと exitcode・job_object の 3 つ）。

    python fb_gate_probe.py <spec.json> <out.json>

spec の鍵：
  root：リポジトリの根（/testbed）
  packages：ソースの根の名の並び（例 ["pandas"]）
  ledger_paths：課題の文の口の Path（根からの相対）
  base：始まりのコミット（変えたファイルを git で数える）
  checks：走らせる確かめ（undefined・imports・attrs・tests・refs）
  pyflakes_dir：pyflakes を置いた所
  test_cmd・timeout_one・file_timeout・test_budget：テストの走らせ方
  hidden_tests：消した公式のテストのファイル（選ばない）
  names・literals：refs で、始まりの状態のソースに出てくるかを調べる名と文言
"""
import ast
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize

SKIP_DIRS = {"__pycache__", ".git", "build", "dist", "node_modules", ".tox", ".eggs"}
FREE_BASES = {"object", "ABC", "Generic", "Protocol"}
FREE_MODULES = {"abc", "typing", "typing_extensions"}
DYNAMIC_METHODS = {"__getattr__", "__getattribute__"}
PASS_RE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)\s+(\S+)(?:\s+-\s+(.*))?$", re.M)


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
    for rel in files:
        api.checkPath(os.path.join(root, rel), r)
    return found


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
    found, todo, t0 = [], list(mods), time.time()
    while todo and time.time() - t0 < budget:
        listing = os.path.join("/tmp", "fb_gate_mods.json")
        with open(listing, "w", encoding="utf-8") as f:
            json.dump(todo, f)
        try:
            p = subprocess.run([sys.executable, script, listing], cwd=root, env=env, capture_output=True,
                               timeout=max(60, budget - (time.time() - t0)))
            out = p.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", errors="replace")
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
    return {"modules": len(mods), "failed": found, "not_run": todo}


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
    return {"classes_checked": checked, "classes_skipped": skipped, "flags": sorted(uniq.values(), key=lambda f: (f["file"], f["line"]))}


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


def run_tests(root, files, test_cmd, timeout_one, file_timeout, budget):
    results, failed, t0, not_run = {}, [], time.time(), []
    for rel in files:
        if time.time() - t0 > budget:
            not_run.append(rel)
            continue
        cmd = f"timeout {int(file_timeout)} {test_cmd} -p no:cacheprovider --timeout={int(timeout_one)} {rel}"
        s = time.time()
        p = subprocess.run(["bash", "-c", cmd], cwd=root, capture_output=True,
                           env=dict(os.environ, MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1", COLUMNS="300"))
        out = p.stdout.decode("utf-8", errors="replace")
        counts = {}
        for m in PASS_RE.finditer(out):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            if m.group(1) in ("FAILED", "ERROR"):
                failed.append({"node": m.group(2), "kind": m.group(1), "msg": (m.group(3) or "")[:200]})
        results[rel] = {"rc": p.returncode, "secs": round(time.time() - s, 1), "counts": counts,
                        "timeout": p.returncode == 124, "tail": out[-300:] if p.returncode not in (0, 1) else ""}
    return {"files": results, "failed": failed, "not_run": not_run}


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
    res = {"source_files": len(files), "secs": {}}

    def timed(name, fn):
        t = time.time()
        res[name] = fn()
        res["secs"][name] = round(time.time() - t, 1)

    if "undefined" in checks:
        timed("undefined", lambda: check_undefined(root, files, spec["pyflakes_dir"]))
    if "imports" in checks:
        timed("imports", lambda: check_imports(root, files, set(packages), spec.get("import_budget", 1500)))
    if "attrs" in checks:
        timed("attrs", lambda: check_attrs(root, files))
    if "refs" in checks:
        timed("refs", lambda: check_refs(root, files, spec.get("names") or [], spec.get("literals") or []))
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
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 箱には exitcode と job_object も同じ所に写す
    import exitcode
    sys.exit(exitcode.normalized(main))
