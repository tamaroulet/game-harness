"""実行環境の記録と照合（v2r、docs/design/v2r_protocol.md §5）。

    python -m harness.envcheck [--out env.json]      実測して固定値（config/environment.json）と照合する

実測するもの：OS、Python、Python の依存（requirements.lock の各パッケージ）、CLI（agy・claude・dotnet）の版、
.NET の道具（dotnet-stryker。§8 の変異の死滅率、U2）、ゲームのテストのプロジェクトの NuGet パッケージ（NUnit など。U1）。
テストのパッケージは `dotnet list <テストの csproj> package` の出力から機械的に取る（総監督はテストのコードを見ない）。
固定値の test_packages が null（未固定）なら、起動しない（U1 が閉じていない）。
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


def tool_versions(tools, run=subprocess.run):
    """.NET の道具の版（`dotnet <道具> --version`）。{道具のパッケージ名: 版 or None}。dotnet-stryker は `dotnet stryker`。"""
    out = {}
    for pkg in tools:
        cmd = pkg.split("dotnet-", 1)[1] if pkg.startswith("dotnet-") else pkg
        try:
            r = run(["dotnet", cmd, "--version"], capture_output=True, text=True, timeout=120,
                    shell=(sys.platform == "win32"))
        except (OSError, subprocess.TimeoutExpired):
            out[pkg] = None
            continue
        m = VERSION_RE.search((r.stdout or "") + (r.stderr or ""))
        out[pkg] = m.group(1) if m and r.returncode == 0 else None
    return out


PACKAGE_LINE_RE = re.compile(r"^\s*>\s*(\S+)\s+(\S+)\s+(\S+)")


def test_packages(csproj, run=subprocess.run):
    """テストのプロジェクトの NuGet パッケージ {名前: 解決した版}（`dotnet list package` の出力の `>` の行）。取れなければ None。"""
    try:
        r = run(["dotnet", "list", str(csproj), "package"], capture_output=True, text=True, timeout=300,
                shell=(sys.platform == "win32"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    pkgs = {}
    for line in (r.stdout or "").splitlines():
        m = PACKAGE_LINE_RE.match(line)
        if m:
            pkgs[m.group(1)] = m.group(3)
    return dict(sorted(pkgs.items())) or None


def lock_packages(lock=LOCK):
    """requirements.lock の {パッケージ: 版}。"""
    out = {}
    for line in Path(lock).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, ver = line.partition("==")
            out[name.strip().lower()] = ver.strip()
    return out


def measure(clis=("agy", "claude", "dotnet"), run=subprocess.run, lock=LOCK, tools=(), csproj=None):
    """実測値の表。tools（.NET の道具）と csproj（ゲームのテストのプロジェクト）は、渡したときだけ測る。"""
    pkgs = {}
    for name in lock_packages(lock):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    out = {"os": os_string(), "python": platform.python_version(), "python_packages": pkgs,
           "cli": {c: cli_version(c, run) for c in clis}}
    if tools:
        out["tools"] = tool_versions(tools, run)
    if csproj is not None:
        out["test_packages"] = test_packages(csproj, run)
    return out


def problems(measured, pinned):
    """固定値と違う項目の一覧（空なら合格）。"""
    out = []
    for key in ("os", "python"):
        if measured.get(key) != pinned.get(key):
            out.append(f"{key}：実測 {measured.get(key)!r}、固定 {pinned.get(key)!r}")
    if "test_packages" in pinned and pinned["test_packages"] is None:
        out.append("test_packages：固定値が未設定（U1。人間が python -m harness.envcheck --test-packages の出力を "
                   "config/environment.json に書く）")
    for group in ("python_packages", "cli", "tools", "test_packages"):
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


def game_csproj(project_id="falling-blocks"):
    """ゲームのテストのプロジェクトの csproj（projects/<id>/project.json の repo_dir と fast_test_project）。"""
    import project
    p = project.load(project_id)
    return Path(p["repo_dir"]) / p["fast_test_project"]


def require(out_path=None, measured=None, pinned=None, project_id="falling-blocks"):
    """実測して照合し、env.json を書く（out_path があれば）。違えば EnvError（起動しない）。記録を返す。"""
    pinned = pinned if pinned is not None else load_pinned()
    if measured is None:
        measured = measure(tools=tuple(pinned.get("tools") or ()),
                           csproj=game_csproj(project_id) if "test_packages" in pinned else None)
    rec = record(measured, pinned)
    if out_path:
        write(out_path, rec)
    if rec["problems"]:
        raise EnvError("実行環境が固定値（config/environment.json）と違います：" + "；".join(rec["problems"]))
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="実行環境の記録と照合")
    ap.add_argument("--out", help="env.json の書き出し先")
    ap.add_argument("--test-packages", action="store_true",
                    help="ゲームのテストのプロジェクトの NuGet パッケージだけを測って JSON で出す（U1 の固定値を作る。人間が実行する）")
    args = ap.parse_args(argv)
    if args.test_packages:
        print(json.dumps(test_packages(game_csproj()), ensure_ascii=False, indent=2))
        return 0
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
