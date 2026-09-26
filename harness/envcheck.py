"""実行環境の記録と照合（v2r、docs/design/v2r_protocol.md §5）。

    python -m harness.envcheck [--out env.json]      実測して固定値（config/environment.json）と照合する

実測するもの：OS、Python、Python の依存（requirements.lock の各パッケージ）、CLI（agy・claude・dotnet）の版。
固定値と 1 つでも違えば起動しない（`require`）。実測値は env.json に書く（`write`）。

サンプリングのパラメータ（temperature・top_p・seed）は、agy 1.2.11・claude 2.1.258 の CLI では指定できない
（§6.2 の実測）。推測で値を書かず、その旨を env.json に書く。
"""
import argparse
import datetime
import json
import platform
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PINNED = ROOT / "config" / "environment.json"
LOCK = ROOT / "requirements.lock"
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
SAMPLING_NOTE = "temperature・top_p・seed：CLI で指定できない（提供側の既定。docs/design/v2r_protocol.md §6.2）"


class EnvError(Exception):
    pass


def os_string():
    """[Environment]::OSVersion.VersionString と同じ形（Microsoft Windows NT 10.0.26200.0）。Windows 以外は platform の表記。"""
    if hasattr(sys, "getwindowsversion"):
        v = sys.getwindowsversion()
        return f"Microsoft Windows NT {v.major}.{v.minor}.{v.build}.0"
    return f"{platform.system()} {platform.release()}"


def cli_version(name, run=subprocess.run):
    """`<name> --version` の最初の x.y.z。取れなければ None。"""
    try:
        r = run([name, "--version"], capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = VERSION_RE.search((r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m and r.returncode == 0 else None


def lock_packages(lock=LOCK):
    """requirements.lock の {パッケージ: 版}。"""
    out = {}
    for line in Path(lock).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, ver = line.partition("==")
            out[name.strip().lower()] = ver.strip()
    return out


def measure(clis=("agy", "claude", "dotnet"), run=subprocess.run, lock=LOCK):
    """実測値の表。"""
    pkgs = {}
    for name in lock_packages(lock):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    return {"os": os_string(), "python": platform.python_version(), "python_packages": pkgs,
            "cli": {c: cli_version(c, run) for c in clis}}


def problems(measured, pinned):
    """固定値と違う項目の一覧（空なら合格）。"""
    out = []
    for key in ("os", "python"):
        if measured.get(key) != pinned.get(key):
            out.append(f"{key}：実測 {measured.get(key)!r}、固定 {pinned.get(key)!r}")
    for group in ("python_packages", "cli"):
        for name, want in (pinned.get(group) or {}).items():
            got = (measured.get(group) or {}).get(name)
            if got != want:
                out.append(f"{group}.{name}：実測 {got!r}、固定 {want!r}")
    return out


def load_pinned(path=PINNED):
    pinned = json.loads(Path(path).read_text(encoding="utf-8"))
    lock = lock_packages()
    if {k.lower(): v for k, v in pinned.get("python_packages", {}).items()} != lock:
        raise EnvError(f"config/environment.json の python_packages と requirements.lock が違います：{lock}")
    return pinned


def record(measured, pinned):
    return {"measured_at": datetime.datetime.now().isoformat(timespec="seconds"), "measured": measured,
            "pinned": {k: v for k, v in pinned.items() if not k.startswith("_")},
            "problems": problems(measured, pinned), "sampling": SAMPLING_NOTE}


def write(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def require(out_path=None, measured=None, pinned=None):
    """実測して照合し、env.json を書く（out_path があれば）。違えば EnvError（起動しない）。記録を返す。"""
    pinned = pinned if pinned is not None else load_pinned()
    measured = measured if measured is not None else measure()
    rec = record(measured, pinned)
    if out_path:
        write(out_path, rec)
    if rec["problems"]:
        raise EnvError("実行環境が固定値（config/environment.json）と違います：" + "；".join(rec["problems"]))
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="実行環境の記録と照合")
    ap.add_argument("--out", help="env.json の書き出し先")
    args = ap.parse_args(argv)
    try:
        rec = require(args.out)
    except EnvError as e:
        print(f"NG: {e}")
        return 1
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    print("合格")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
