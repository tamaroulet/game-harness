"""実装役のコンテナの閉じ込め（設計 §5-1 の実行環境のアダプタのうち、ベンチと実装役に依らない部分。実験と計画 §6-3）。

- 内部のネットワーク（外への経路が無い）と、許可リストのプロキシ（tinyproxy）。プロキシのイメージは、入力（Dockerfile・
  設定・許可リスト）のハッシュを名前に付けて作り、入力が変わったときだけ新しく作る（同じ名前で作り直して古いものを
  名前の無いイメージにしない）。動いているプロキシは使い回し、ほかの走りの実装役がつながっている間は作り直さない
- 非 root のコンテナ：作業場をマウントし、実装役の HOME を uid に渡す。root で行う操作は `--user 0:0` を明示する
  （イメージの既定のユーザーは root とは限らない。SWE-Game のイメージは agent）
- 境界の検査（標準入力で渡し、コンテナにファイルを残さない）・uid のプロセスの停止と生きている残りの数え・
  残ったコンテナの数え
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "config" / "agy_sandbox"


# ---------------------------------------------------------------- コマンド

def sh3(cmd, input=None, timeout=600, env=None, cwd=None, replace_env=False):
    """(終わりの符号, stdout, stderr)。replace_env なら、ホストの環境を引き継がずに env だけで起動する。"""
    p = subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                       env=dict(env or {}) if replace_env else dict(os.environ, **(env or {})))
    return p.returncode, p.stdout or "", p.stderr or ""


def sh(cmd, input=None, timeout=600, env=None, cwd=None):
    code, out, err = sh3(cmd, input=input, timeout=timeout, env=env, cwd=cwd)
    return code, out + err


def must(cmd, what, **kw):
    code, out = sh(cmd, **kw)
    if code != 0:
        raise RuntimeError(f"{what} failed (rc={code}): {out.strip()[-400:]}")
    return out


def as_root(box, script, timeout=120):
    """コンテナの中で root として bash の 1 行を走らせる。(終わりの符号, 出力)。"""
    return sh(["docker", "exec", "--user", "0:0", box, "bash", "-c", script], timeout=timeout)


def must_root(box, script, what, timeout=120):
    return must(["docker", "exec", "--user", "0:0", box, "bash", "-c", script], what, timeout=timeout)


def copy_in(box, src, dst, what):
    must(["docker", "cp", str(src), f"{box}:{dst}"], what)


# ---------------------------------------------------------------- 境界（内部のネットワークと許可リストのプロキシ）

def filter_lines(allow):
    """tinyproxy の filter（拡張の正規表現。完全一致）。"""
    return "".join("^" + re.escape(h) + "$\n" for h in allow)


def egress_inputs(net):
    """プロキシのイメージの入力（ビルドの文脈の名前 → 中身）。filter は許可リストから作る。"""
    return {"Dockerfile": (ASSETS / "egress.Dockerfile").read_text(encoding="utf-8"),
            "tinyproxy.conf": (ASSETS / "tinyproxy.conf").read_text(encoding="utf-8"),
            "filter": filter_lines(net["allow"])}


def inputs_hash(files):
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    return h.hexdigest()


def egress_image(net, files=None):
    """入力のハッシュを付けたプロキシのイメージの名前（例 scb-egress:tinyproxy-1a2b3c4d5e6f）。"""
    return f"{net['proxy_image']}-{inputs_hash(files or egress_inputs(net))[:12]}"


def proxy_state(net):
    """プロキシのコンテナの状態（無ければ None）：動いているか・イメージの名前・つながっているネットワークの名前。"""
    code, out = sh(["docker", "inspect", "-f", "{{.State.Running}}|{{.Config.Image}}|{{json .NetworkSettings.Networks}}",
                    net["proxy_name"]])
    if code != 0:
        return None
    running, image, nets = out.strip().split("|", 2)
    return {"running": running == "true", "image": image, "networks": sorted(json.loads(nets or "null") or {})}


def attached(net):
    """内部のネットワークにつながっている、プロキシのほかのコンテナの名前（走っている実装役）。"""
    code, out = sh(["docker", "network", "inspect", "-f", "{{json .Containers}}", net["internal"]])
    if code != 0:
        return []
    return sorted(c.get("Name") for c in (json.loads(out.strip() or "null") or {}).values()
                  if c.get("Name") != net["proxy_name"])


def egress_plan(state, image, internal, others):
    """reuse：同じイメージで動き、内部のネットワークにつながっている（作り直さない）。refuse：作り直しが要るが、ほかの
    コンテナが内部のネットワークにいる（走っている実装役の外への道を切らない）。replace：作り直す。"""
    if state and state["running"] and state["image"] == image and internal in state["networks"]:
        return "reuse"
    return "refuse" if others else "replace"


def ensure_egress(net):
    """内部のネットワークと許可リストのプロキシを用意する。イメージは、入力のハッシュの名前が無いときだけ作る。プロキシは、
    同じイメージで動いていれば使い回し、作り直すのは、内部のネットワークにほかのコンテナがいないときだけ（同時に起こした
    走りの外への道を、後の走りが切らない）。"""
    if sh(["docker", "network", "inspect", net["internal"]])[0] != 0:
        must(["docker", "network", "create", "--internal", net["internal"]], "network create")
    files = egress_inputs(net)
    image = egress_image(net, files)
    built = sh(["docker", "image", "inspect", image])[0] != 0
    if built:
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                (Path(d) / name).write_bytes(text.encode("utf-8"))
            must(["docker", "build", "-q", "-t", image, d], "egress build")
    others = attached(net)
    plan = egress_plan(proxy_state(net), image, net["internal"], others)
    if plan == "refuse":
        raise RuntimeError(f"内部のネットワークにほかのコンテナがある間は、プロキシを作り直さない（同時の走りを切らない）: {others}")
    if plan == "replace":
        sh(["docker", "rm", "-f", net["proxy_name"]])
        must(["docker", "run", "-d", "--name", net["proxy_name"], "--network", "bridge", image], "egress run")
        must(["docker", "network", "connect", net["internal"], net["proxy_name"]], "egress connect")
    return {"internal": net["internal"], "proxy": net["proxy_name"], "image": image, "built": built,
            "reused": plan == "reuse", "allow": net["allow"]}


def proxy_env(net):
    url = f"http://{net['proxy_name']}:{net['proxy_port']}"
    env = {k: url for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
    env.update(NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
    return env


# ---------------------------------------------------------------- 実装役のコンテナ

def start(net, sbx, image, name, workspace):
    """実装役のコンテナを起こす（内部のネットワークだけ・プロキシの環境・作業場を workdir に）。本体の sleep は root で
    走らせ、実装役のコマンドは uid で exec する。HOME を作って uid に渡す。名前を返す。"""
    cmd = ["docker", "run", "-d", "--name", name, "--network", net["internal"], "--user", "0:0",
           "-e", f"HOME={sbx['home']}", "-v", f"{workspace}:{sbx['workdir']}", "-w", sbx["workdir"]]
    for k, v in proxy_env(net).items():
        cmd += ["-e", f"{k}={v}"]
    must(cmd + [image, "sleep", "infinity"], "box run")
    must_root(name, f"mkdir -p {sbx['home']} && chown -R {sbx['uid']}:{sbx['uid']} {sbx['home']}", "box home")
    return name


def remove(box):
    sh(["docker", "rm", "-f", box])


def boxes_left(prefix):
    """名前が prefix で始まるコンテナの残りの数（止まったものも数える）。"""
    return len(must(["docker", "ps", "-a", "--filter", f"name={prefix}", "-q"], "ps").split())


def scratch(box, sbx):
    """コンテナの /tmp と実装役の ~/.cache の中の名前（境界の検査の残りを数える）。"""
    home = sbx["home"]
    code, out = as_root(box, f"ls -A1 /tmp | sed 's,^,/tmp/,'; ls -A1 {home}/.cache 2>/dev/null | sed 's,^,{home}/.cache/,'",
                        timeout=60)
    return set(out.split())


def net_check(box, sbx):
    """境界の検査。netcheck.py は標準入力で渡してコンテナに写さず、検査の一時のフォルダは消す。
    実装役が始まる前に、検査が /tmp と ~/.cache に何も残していないこと（residue が空）も合格の条件にする。"""
    uid = sbx["uid"]
    before = scratch(box, sbx)
    code, out = sh(["docker", "exec", "-i", "--user", f"{uid}:{uid}", box, "python3", "-"],
                   input=(ASSETS / "netcheck.py").read_text(encoding="utf-8"), timeout=300)
    as_root(box, "rm -rf /tmp/scb-netcheck-*", timeout=60)
    residue = sorted(scratch(box, sbx) - before)
    return {"ok": "BOUNDARY_OK" in out and not residue, "tail": out.strip().splitlines()[-6:], "residue": residue}


def stop_user(box, sbx, tries=10):
    """コンテナの中の uid のプロセスを止め、生きている残りの数を返す（時間切れの後始末）。docker exec の側が止まっても
    コンテナの中のプロセスは走り続け、次のターンや、作業場の写しと採点に混ざるため。止めたプロセスは回収されるまで
    ゾンビ（State: Z）で /proc に残るので数えず、0 になるまで少し待って止め直す。"""
    uid = sbx["uid"]
    live = ("for p in /proc/[0-9]*; do [ \"$(stat -c %u $p 2>/dev/null)\" = " + str(uid) + " ] || continue; "
            "s=$(awk '/^State:/{print $2}' $p/status 2>/dev/null); [ -n \"$s\" ] && [ \"$s\" != Z ] && echo $p; done")
    left = None
    for _ in range(tries):
        sh(["docker", "exec", "--user", f"{uid}:{uid}", box, "bash", "-c", "kill -9 -1"], timeout=60)
        left = len(as_root(box, live, timeout=60)[1].split())
        if left == 0:
            break
        time.sleep(0.5)
    return left
