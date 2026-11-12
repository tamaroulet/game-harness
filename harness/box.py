"""実装役・門・採点の箱（v3 §8 の E4）：Docker の呼び方、像と中継の確かめ、箱の起動。

    python -m harness.box setup    WSL2 の準備のスクリプトを root で走らせる（box/setup_wsl.sh。やり直せる）
    python -m harness.box build    像を造る（box/ と config/box.json から）
    python -m harness.box check    像の指紋・網と中継・箱の中の見え方を確かめる。利用枠は使わない

回す側は Windows のままで、Docker は WSL2 の中にある（Windows からは wsl.exe 越し、Linux からは直に呼ぶ）。
箱とのやり取りは標準入出力の tar だけで、パスを変換しない。どの箱も 1 回ごとに使い捨て。
- 実装役の箱（agent）：作業の木の写し（課題の走りでは課題のパッケージ・試験・仕様だけ。task_scope）と、agy の認証。ホームは tmpfs。
  ネットは中継だけ。変更したファイルを受け取る（box_tree.receive）。A の会話の形だけは、ホームと作業の木を鎖ごとのボリュームにして残す。
- 門と採点の箱（tests）：木の写しを tmpfs に入れる。ネットなし。結果は標準出力で受け取る。隠しテストは採点の箱にだけ入れる。
実装役が書いたコードは、箱の外で走らせない。箱の実行そのものの失敗は BoxError（呼び手が環境の異常にする。実装役の失敗に数えない）。
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import box_tree  # noqa: E402
import exitcode  # noqa: E402
import proc  # noqa: E402
import toolchain  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "box.json"
BOX_DIR = ROOT / "box"
SETUP = "setup_wsl.sh"
LABEL = "gh.box.fingerprint"
TTL = 300                # 箱の準備の命令の上限（像を造るのは BUILD_TTL）
BUILD_TTL = 1800
TIMED_OUT = 124
DOCKER_FAILED = (125, 126, 127)   # docker 自身の失敗と、箱の中の命令を起こせない
USER_ID = "1000"
HOME, WORK = "/home/bench", "/work"
WORK_NAME = "work"
RUNNER_NAME = "runner.py"
PYTHON = "python"
TOKEN_IN = "/in/token"
NO_PROXY = "localhost,127.0.0.1"
GIT_ID = "box"
GIT_MAIL = "box@example.invalid"
TOKEN_SETUP = f'mkdir -p "$HOME/.gemini/antigravity-cli" && install -m 600 {TOKEN_IN} "$HOME/.gemini/antigravity-cli/antigravity-oauth-token"'
GIT_START = 'git init -q --template= && git -c core.safecrlf=false add -A && git commit -q --no-gpg-sign -m start'
TOKEN_DROP = '{ "$@"; rc=$?; rm -f "$HOME/.gemini/antigravity-cli/antigravity-oauth-token"; exit $rc; }'
# 実装役の箱の始まり：認証をホームへ写し、開始の木を 1 コミットにしてから（実装役が git diff などを使える。外の .git は見せない）実装役を起こす
START = f'{TOKEN_SETUP} && {GIT_START} && exec "$@"'
# 会話を続ける実装役（A の会話の形、v3 §15 の 11-12 その 2）の始まり：ホームは鎖ごとの名前つきボリュームで、agy の会話の記録が段をまたいで残る。
# 認証は呼び出しの間だけホームに置き、実装役が終われば消す（時間切れで止めた箱では残り、鎖の終わりにボリュームごと消す）
START_KEEP = f'{TOKEN_SETUP} && {GIT_START} && {TOKEN_DROP}'
# 作業の場所も鎖のあいだ残す実装役（A の会話の形、v3 §15 の 11-12 その 4）の始まり：箱の中の git は最初の呼び出しだけ作り、あとは実装役のもの
START_STAY = f'{TOKEN_SETUP} && {{ [ -d .git ] || {{ {GIT_START}; }}; }} && {TOKEN_DROP}'
UNPACK_AND_RUN = f'tar -x -C {WORK} && cd {WORK} && exec "$@"'
PROBE = r'''
import json, os, socket
out = {"uid": os.getuid(), "mnt": os.listdir("/mnt") if os.path.isdir("/mnt") else [],
       "wsl": [p for p in ("/init", "/run/WSL", "/usr/bin/wslpath") if os.path.exists(p)],
       "token": os.access("/in/token", os.R_OK)}
try:
    open("/etc/x", "w").close()
    out["root_writable"] = True
except OSError:
    out["root_writable"] = False
direct = []
for target in ("8.8.8.8:53", "1.1.1.1:443"):
    host, port = target.split(":")
    s = socket.socket()
    s.settimeout(4)
    try:
        s.connect((host, int(port)))
        direct.append(host)
    except OSError:
        pass
    finally:
        s.close()
out["direct"] = direct
try:
    socket.gethostbyname("pypi.org")
    out["dns"] = True
except OSError:
    out["dns"] = False
print(json.dumps(out))
'''


class BoxError(Exception):
    """箱の実行そのものの失敗（像が無い・Docker が動かない・受け渡しの失敗）。実装役の失敗ではない。"""


def settings(path=None):
    return json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))


def docker_cmd(cfg):
    """docker の呼び方。Windows では wsl.exe 越し（cwd の変換をさせない）。"""
    if sys.platform == "win32":
        return ["wsl.exe", "-d", cfg["distro"], "-u", "root", "--cd", "/", "--exec", "docker"]
    return ["docker"]


def _call(args, data=None, ttl=TTL):
    """命令を 1 回走らせる。(終了コード, 標準出力のバイト列, 標準エラーの文)。時間切れは終了コード 124。"""
    env = dict(os.environ, WSL_UTF8="1")
    p = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, creationflags=proc._NO_WINDOW, startupinfo=proc._STARTUPINFO)
    try:
        out, err = p.communicate(input=data, timeout=ttl)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        return TIMED_OUT, out or b"", proc.decoded(err)
    return p.returncode, out or b"", proc.decoded(err)


def docker(cfg, *args, data=None, ttl=TTL):
    """docker の準備の命令。落ちたら BoxError。標準出力のバイト列を返す。"""
    code, out, err = _call([*docker_cmd(cfg), *args], data, ttl)
    if code != 0:
        raise BoxError(f"docker {args[0]} が終了コード {code}: {err.strip()[-400:]}")
    return out


def _quiet(cfg, *args):
    """片付けの命令。落ちても止めない。"""
    try:
        _call([*docker_cmd(cfg), *args])
    except OSError:
        pass


# ============================================================ 像

def context_files(subdir=""):
    """box/（subdir の下）のファイルの {相対パス: バイト列}。改行は LF にそろえる（Windows の作業ツリーの CRLF を像に持ち込まない）。"""
    base = BOX_DIR / subdir
    return {rel: (base / rel).read_bytes().replace(b"\r\n", b"\n") for rel in box_tree.walk(base)}


def agy_version():
    """箱の agy の版（版の台帳 config/toolchain.json）。"""
    return toolchain.load()["box"]["agy"]


def fingerprint(cfg):
    """像の指紋：box/ の全部のファイルと、agy の版と URL と SHA-512。変えたら造り直す。"""
    h = hashlib.sha256()
    for rel, data in sorted(context_files().items()):
        h.update(rel.encode() + b"\0" + data + b"\0")
    h.update(json.dumps({**cfg["agy"], "version": agy_version()}, sort_keys=True).encode())
    return h.hexdigest()[:32]


def build(cfg):
    """像を 2 つ造る。文脈は標準入力の tar（Windows のパスを渡さない）。"""
    label = f"{LABEL}={fingerprint(cfg)}"
    agy = cfg["agy"]
    box_files = {"Dockerfile": context_files()["Dockerfile"]}
    docker(cfg, "build", "-q", "-t", cfg["image"], "--label", label, "--build-arg", f"AGY_URL={agy['url']}",
           "--build-arg", f"AGY_SHA512={agy['sha512']}", "-", data=box_tree.pack(None, box_files), ttl=BUILD_TTL)
    docker(cfg, "build", "-q", "-t", cfg["proxy_image"], "--label", label, "-", data=box_tree.pack(None, context_files("proxy")), ttl=BUILD_TTL)
    return image_id(cfg, cfg["image"])


def image_label(cfg, image):
    out = docker(cfg, "image", "inspect", "--format", "{{ index .Config.Labels \"" + LABEL + "\" }}", image)
    return out.decode().strip()


def image_id(cfg, image):
    return docker(cfg, "image", "inspect", "--format", "{{.Id}}", image).decode().strip()


def require(cfg):
    """像が今の box/ と設定から造られたものか。合えば像の ID（走りの記録に残す）。"""
    want = fingerprint(cfg)
    for image in (cfg["image"], cfg["proxy_image"]):
        have = image_label(cfg, image)
        if have != want:
            raise BoxError(f"像 {image} の指紋が今の box/ と設定に合いません（{have} ／ {want}）。python -m harness.box build で造り直してください")
    return image_id(cfg, cfg["image"])


def ensure_net(cfg):
    """閉じた網と中継を用意する。中継は Docker の起動で戻る（--restart always）。古い像の中継は立て直す。"""
    net, name = cfg["network"], cfg["proxy_name"]
    if _call([*docker_cmd(cfg), "network", "inspect", net])[0] != 0:
        docker(cfg, "network", "create", "--internal", net)
    code, out, _ = _call([*docker_cmd(cfg), "inspect", "--format", "{{.State.Running}} {{.Image}}", name])
    want = image_id(cfg, cfg["proxy_image"])
    if code == 0 and out.decode().split() == ["true", want]:
        return
    _quiet(cfg, "rm", "-f", name)
    docker(cfg, "run", "-d", "--name", name, "--network", "bridge", "--restart", "always", "--read-only", "--tmpfs", "/tmp",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--log-opt", "max-size=10m", cfg["proxy_image"])
    docker(cfg, "network", "connect", net, name)


class Keep:
    """走りの間、WSL2 を止めない接続を持つ（wsl.exe の接続が切れると、Docker が動いていても止まる。§15 の 11-08）。Linux では何もしない。"""

    def __init__(self, cfg):
        self.cfg, self.child = cfg, None

    def __enter__(self):
        if sys.platform == "win32":
            self.child = subprocess.Popen(["wsl.exe", "-d", self.cfg["distro"], "-u", "root", "--cd", "/", "--exec", "sleep", "infinity"],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=proc._NO_WINDOW, startupinfo=proc._STARTUPINFO)
        return self

    def __exit__(self, *exc):
        if self.child is not None:
            self.child.kill()
            self.child.wait()


# ============================================================ 箱

def guards(cfg, home=None):
    """どの箱にも付ける守り。ルートは読み取り専用、権限なし、利用者は bench、ホームは tmpfs。Windows のフォルダは渡さない。
    home（名前つきボリュームの名）を渡すと、ホームをそのボリュームにする（会話を続ける実装役だけ。chain_volume）。"""
    lim = cfg["limits"]
    keep = ["-v", f"{home}:{HOME}"] if home else ["--tmpfs", f"{HOME}:size={lim['tmp']},exec,uid={USER_ID},gid={USER_ID}"]
    return ["--read-only", "--tmpfs", f"/tmp:size={lim['tmp']},exec", *keep,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", str(lim["pids"]), "--memory", lim["memory"],
            "--user", USER_ID, "-e", f"HOME={HOME}", "-i", "-w", WORK]


def agent_opts(cfg, home=None, work=None):
    """実装役の箱：閉じた網と中継だけ。作業の木は名の無いボリューム（容器を消すときに一緒に消える）。認証は読むだけで渡す。
    work（名前つきボリュームの名）を渡すと、作業の木をそのボリュームにする（段をまたいで残す実装役だけ。chain_volume）。"""
    proxy = f"http://{cfg['proxy_name']}:{cfg['proxy_port']}"
    env = [x for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy") for x in ("-e", f"{k}={proxy}")]
    env += [x for k in ("NO_PROXY", "no_proxy") for x in ("-e", f"{k}={NO_PROXY}")]
    env += [x for k, v in (("GIT_AUTHOR_NAME", GIT_ID), ("GIT_AUTHOR_EMAIL", GIT_MAIL), ("GIT_COMMITTER_NAME", GIT_ID), ("GIT_COMMITTER_EMAIL", GIT_MAIL))
            for x in ("-e", f"{k}={v}")]
    return [*guards(cfg, home), "--network", cfg["network"], *env, "-v", f"{work}:{WORK}" if work else WORK, "-v", f"{cfg['token']}:{TOKEN_IN}:ro"]


def home_name(tag, base):
    """会話の形（A の bench_solo --chat）の、鎖ごとのホームのボリュームの名（続きから走るときに同じ名を引く）。"""
    return f"box-chat-{tag}-{base}".lower()


def work_name(tag, base):
    """会話の形の、鎖ごとの作業の場所のボリュームの名。"""
    return f"box-work-{tag}-{base}".lower()


def chain_volume(cfg, name):
    """鎖のあいだ残す名前つきボリューム（会話を続ける実装役のホームと作業の場所）。無ければ作る。名を返す。
    最初に付けたとき、像のその場所（/home/bench・/work）の中身と持ち主が写る。"""
    if _call([*docker_cmd(cfg), "volume", "inspect", name])[0] != 0:
        docker(cfg, "volume", "create", name)
    return name


def drop_volume(cfg, name):
    """鎖の終わりに、鎖のボリュームを消す（会話の記録・作業の場所の残り・時間切れで残った認証の写しも消える）。"""
    _quiet(cfg, "volume", "rm", "-f", name)


def task_scope(boxed):
    """箱の印（{"package", "docs", ...}）の、箱の作業の場所に置く範囲（相対パス → 真偽）：課題のパッケージ・tests/test_<package>*・
    仕様の置き場 docs（v3 §15 の 11-12 その 4。リポジトリの残りは課題に要らない）。docs の無い印は None（木の全部を渡す）。"""
    if not (boxed or {}).get("docs"):
        return None
    own, docs = new_files_rule(boxed["package"]), boxed["docs"]
    return lambda rel: own(rel) or rel.startswith(docs)


def tests_opts(cfg):
    """門と採点の箱：ネットなし。木は標準入力の tar で tmpfs に入れ、終われば消える。"""
    return [*guards(cfg), "--network", "none", "--tmpfs", f"{WORK}:size={cfg['limits']['tmp']},exec,uid={USER_ID},gid={USER_ID}"]


def new_files_rule(package):
    """実装役が新しく作ったファイルのうち、受け取ってよいものの判じ方（bench_run.allowed_path と同じ範囲）。
    作業の残りのファイルで境界の検めに当たって段が 0 点にならないように、外のものは受け取らず、次の試行の入力で知らせる。"""
    tests = re.compile(rf"tests/test_{re.escape(package)}[^/]*")
    return lambda rel: rel.startswith(package + "/") or tests.fullmatch(rel) is not None


def agent(cfg, argv, wt, ttl, stdin, allowed=None, home=None, work=None, scope=None, send=None):
    """実装役の 1 回の試行を箱で走らせ、変更を wt に受け取る。(終了コード, 標準出力, 標準エラー, 受け取りの記録)。
    時間切れは終了コード 124（箱は止める。そこまでの変更も受け取る）。docker 自身の失敗は BoxError。
    home（chain_volume の名）を渡すと、ホームをそのボリュームにし、会話の記録を次の呼び出しへ残す。
    scope（task_scope）を渡すと、写し入れと受け取りをその範囲に絞る。work（chain_volume の名。home と一緒に渡す）を渡すと、作業の木を
    そのボリュームにして段をまたいで残し（箱の中の git は最初の呼び出しだけ作る）、範囲の外のファイルは受け取らず箱に残す。
    send（相対パスの集まり）を渡すと、範囲のうちその名だけを写し入れる（残した作業の場所に、新しい段の仕様だけを足す）。"""
    name = f"box-agent-{secrets.token_hex(6)}"
    keep = None if scope is None and send is None else (lambda rel: (scope is None or scope(rel)) and (send is None or rel in send))
    try:
        docker(cfg, "create", "--name", name, *agent_opts(cfg, home, work), cfg["image"], "bash", "-c",
               START_STAY if work else START_KEEP if home else START, "_", *argv)
        docker(cfg, "cp", "-", f"{name}:{WORK}", data=box_tree.pack(wt, keep=keep))
        code, out, err = _call([*docker_cmd(cfg), "start", "-ai", name], stdin.encode("utf-8"), ttl)
        if code == TIMED_OUT:
            _quiet(cfg, "kill", name)
            err = f"{err}\nTTL超過 ({ttl}s): 実装役"
        elif code in DOCKER_FAILED:
            raise BoxError(f"実装役の箱を起こせません（終了コード {code}）: {err.strip()[-400:]}")
        data = docker(cfg, "cp", f"{name}:{WORK}", "-")
        try:
            got = box_tree.receive(data, wt, allowed, strip=WORK_NAME, scope=scope, keep_out=bool(work))
        except box_tree.TreeError as e:
            raise BoxError(str(e)) from e
        return code, proc.decoded(out), err, got
    except box_tree.TreeError as e:
        raise BoxError(str(e)) from e
    finally:
        _quiet(cfg, "rm", "-f", "-v", name)


def tests(cfg, argv, tree, ttl, extra=None):
    """門か採点を箱で走らせる（ネットなし）。(終了コード, 標準出力, 標準エラー)。tree の写しと extra（{名: バイト列}）を入れ、WORK で argv を走らせる。
    試験が落ちるのは普通の結果。時間切れは 124（箱は止める）。docker 自身の失敗は BoxError。"""
    name = f"box-test-{secrets.token_hex(6)}"
    try:
        data = box_tree.pack(tree, extra)
    except box_tree.TreeError as e:
        raise BoxError(str(e)) from e
    code, out, err = _call([*docker_cmd(cfg), "run", "--rm", "--name", name, *tests_opts(cfg), cfg["image"], "bash", "-c", UNPACK_AND_RUN, "_", *argv], data, ttl)
    if code == TIMED_OUT:
        _quiet(cfg, "rm", "-f", name)
        err = f"{err}\nTTL超過 ({ttl}s): 箱の試験"
    elif code in DOCKER_FAILED:
        raise BoxError(f"試験の箱を起こせません（終了コード {code}）: {err.strip()[-400:]}")
    return code, proc.decoded(out), err


def grade(cfg, runner, tree, files, ttl):
    """採点の箱で隠しテストを走らせる（chain_score の RUNNER）。(終了コード, 標準出力, 標準エラー)。段の木と隠しテストは別の根に置く。"""
    extra = {RUNNER_NAME: runner.encode("utf-8"),
             **{f"tree/{rel}": (Path(tree) / rel).read_bytes() for rel in box_tree.walk(tree)},
             **{f"tests/{Path(f).name}": Path(f).read_bytes() for f in files}}
    return tests(cfg, [PYTHON, "-I", f"{WORK}/{RUNNER_NAME}", f"{WORK}/tree", f"{WORK}/tests"], None, ttl, extra)


# ============================================================ 確かめ

def probe_problems(doc, networked):
    """箱の中の見え方の外れの文の列。実装役の箱には認証が読めること、試験の箱には認証が無いことも見る。"""
    problems = []
    if doc.get("uid") != int(USER_ID):
        problems.append(f"利用者が {USER_ID} ではない: {doc.get('uid')}")
    if doc.get("mnt"):
        problems.append(f"/mnt が空でない: {doc['mnt']}")
    if doc.get("wsl"):
        problems.append(f"WSL の部品が見える: {doc['wsl']}")
    if doc.get("root_writable") is not False:
        problems.append("ルートに書ける")
    if doc.get("direct"):
        problems.append(f"直の接続が届く: {doc['direct']}")
    if doc.get("dns") is not False:
        problems.append("名前の解決ができる")
    if bool(doc.get("token")) != networked:
        problems.append("agy の認証が読めない（WSL2 の bench でログインしているか）" if networked else "試験の箱に認証がある")
    return [f"{'実装役の箱' if networked else '試験の箱'}: {p}" for p in problems]


def proxy_problems(cfg):
    """中継の確かめ：許した宛先は通り、許していない宛先は拒まれる。"""
    proxy = f"http://{cfg['proxy_name']}:{cfg['proxy_port']}"
    problems = []
    for url, want in (("https://pypi.org/simple/", "200"), ("https://example.com/", "000")):
        code, out, _ = _call([*docker_cmd(cfg), "run", "--rm", *agent_opts(cfg), cfg["image"],
                              "curl", "-sS", "-m", "20", "-x", proxy, "-o", "/dev/null", "-w", "%{http_code}", url], b"")
        have = proc.decoded(out).strip()
        if have != want:
            problems.append(f"中継で {url} が {have}（終了コード {code}。期待 {want}）")
    return problems


def check(cfg):
    """箱の確かめの外れの文の列。空なら合格。像の確かめと、箱の中の見え方（利用枠は使わない）。走りの前にも呼ぶ。"""
    require(cfg)
    ensure_net(cfg)
    problems = []
    for networked, opts in ((True, agent_opts(cfg)), (False, tests_opts(cfg))):
        code, out, err = _call([*docker_cmd(cfg), "run", "--rm", *opts, cfg["image"], PYTHON, "-"], PROBE.encode(), TTL)
        try:
            problems += probe_problems(json.loads(proc.decoded(out)), networked)
        except ValueError:
            problems.append(f"箱の確かめが読めません（終了コード {code}）: {err.strip()[-300:]}")
    return problems + proxy_problems(cfg)


def check_cached(cfg, memo=None):
    """check の外れの列。memo（呼び手の持つ dict。走りの起動ごとに 1 つ）があれば合格を覚え、同じ起動の 2 回目からは省く
    （11-12 に測ると、check は 1 回 8〜10 秒で、ほとんどが中継の確かめ）。外れは覚えない（次の段でもう一度確かめる）。
    段ごとには呼び手が require（像の指紋、0.4 秒）を見て、実装役の呼び出しごとに ensure_net が中継の像と動きを見る。"""
    if memo is not None and memo.get("passed"):
        return []
    problems = check(cfg)
    if memo is not None:
        memo["passed"] = not problems
    return problems


def setup(cfg):
    """WSL2 の準備のスクリプトを root で走らせる（標準入力で渡す。automount を切った後でも動く）。"""
    script = (BOX_DIR / SETUP).read_bytes().replace(b"\r\n", b"\n")
    tail = ["bash", "-s", "--", agy_version(), cfg["agy"]["url"], cfg["agy"]["sha512"]]
    args = ["wsl.exe", "-d", cfg["distro"], "-u", "root", "--cd", "/", "--exec", *tail] if sys.platform == "win32" else tail
    code, out, err = _call(args, script, BUILD_TTL)
    print(proc.decoded(out)[-2000:])
    if code != 0:
        raise BoxError(f"準備が終了コード {code}: {err.strip()[-400:]}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.box", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("setup", "build", "check"))
    a = ap.parse_args(argv)
    cfg = settings()
    try:
        if a.command == "setup":
            setup(cfg)
        elif a.command == "build":
            print(build(cfg))
        else:
            problems = check(cfg)
            print("\n".join(problems) or "合格")
            return 1 if problems else 0
    except BoxError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
