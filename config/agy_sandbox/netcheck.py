import os, shutil, socket, subprocess, sys, tempfile, urllib.error, urllib.request
# 境界の検査（harness/sandbox.py の net_check が標準入力で渡す。コンテナにファイルを残さない：pip の置き場は一時の
# フォルダで最後に消し、キャッシュと版の確認を切る）。許可はモデルの API だけ（config/agy_sandbox.json の network.allow）。
# パッケージのレジストリ（ミラーを含む）とコードの置き場は、プロキシ越しでも直にも届かないこと（操縦士の決め、2026-11-25）
ALLOWED = ["https://oauth2.googleapis.com/", "https://daily-cloudcode-pa.googleapis.com/", "https://www.googleapis.com/"]
BLOCKED = ["https://pypi.org/simple/pyyaml/", "https://files.pythonhosted.org/", "https://pypi.tuna.tsinghua.edu.cn/simple/",
           "https://mirrors.tuna.tsinghua.edu.cn/", "https://mirrors.aliyun.com/pypi/simple/", "https://registry.npmjs.org/",
           "https://registry.yarnpkg.com/", "https://registry.npmmirror.com/", "https://repo.yarnpkg.com/",
           "https://crates.io/", "https://index.crates.io/", "https://static.crates.io/", "https://proxy.golang.org/",
           "https://sum.golang.org/", "https://goproxy.cn/", "https://github.com/", "https://raw.githubusercontent.com/",
           "https://api.github.com/", "https://codeload.github.com/", "https://www.google.com/", "https://huggingface.co/",
           "http://example.com/"]
def via_proxy(u):
    try:
        r = urllib.request.urlopen(u, timeout=20); return f"open({r.status})"
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode("utf-8", "replace")
        return f"blocked(proxy {e.code} Filtered)" if e.code == 403 and "tinyproxy" in body.lower() else f"open({e.code})"
    except Exception as e: return f"blocked({type(e).__name__}: {' '.join(str(e).split())[:70]})"
def direct(u):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = op.open(u, timeout=8); return f"open({r.status})"
    except urllib.error.HTTPError as e: return f"open({e.code})"
    except Exception as e: return f"blocked({type(e).__name__})"
res = {u: {"proxy": via_proxy(u), "direct": direct(u)} for u in ALLOWED + BLOCKED}
for u, v in res.items(): print(f"{u:45s} proxy={v['proxy']:60s} direct={v['direct']}")
try:
    s = socket.create_connection(("1.1.1.1", 443), timeout=5); s.close(); raw = "open"
except Exception as e: raw = f"blocked({type(e).__name__})"
print("raw tcp 1.1.1.1:443 ->", raw)
tmp = tempfile.mkdtemp(prefix="scb-netcheck-")
try:
    has_pip = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True).returncode == 0
    pip = [sys.executable, "-m", "pip", "download", "--no-deps", "--no-cache-dir", "--disable-pip-version-check", "-q",
           "--retries", "0", "--timeout", "15", "-d"]
    pip_pypi = subprocess.run(pip + [os.path.join(tmp, "pypi"), "pyyaml==6.0.2"], capture_output=True, text=True)
    pip_gh = subprocess.run(pip + [os.path.join(tmp, "gh"), "git+https://github.com/yaml/pyyaml"], capture_output=True, text=True)
    got = os.listdir(os.path.join(tmp, "pypi")) if os.path.isdir(os.path.join(tmp, "pypi")) else "-"
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("pip present:", has_pip)
print("pip download pyyaml via PyPI rc=", pip_pypi.returncode, got)
print("pip download from github rc=", pip_gh.returncode, (pip_gh.stderr.strip().splitlines() or [""])[-1][:120])
ok = all(res[u]["proxy"].startswith("open") for u in ALLOWED) and all(res[u]["proxy"].startswith("blocked") for u in BLOCKED) \
     and all(v["direct"].startswith("blocked") for v in res.values()) and raw.startswith("blocked") \
     and pip_pypi.returncode != 0 and got in ("-", []) and pip_gh.returncode != 0
print("BOUNDARY_OK" if ok else "BOUNDARY_NG")
