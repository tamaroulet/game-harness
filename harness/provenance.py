"""走行の来歴（追試のための記録。docs/design/v2r_reproducibility.md）。

    python -m harness.provenance [--out provenance.json] [--manifest experiments/v2r/tasks.json]

**なぜ要るか**: env.json（harness/envcheck.py）は道具の版を固定するが、次のものは走行の記録のどこにも残っていなかった
（2026-09-30 の自己監査）。これが無いと、第三者は同じ条件を組み立てられない。
- ハーネス自身のコミットと、作業ツリーの汚れ（測定器・再試行・文面の組み立ての版）
- 実装役（agy）がリポジトリの外で読む全体設定（`~/.gemini/GEMINI.md`・`settings.json`）。agy はこれに従う
- 実装役の設定（pipeline.json の implementer）と、マニフェストの sha256
- 走行に効く環境変数（`HARNESS_` で始まるもの）、地域の設定（ロケール・時間帯）

**記録するのは sha256 と名前だけ**：全体設定の本文、認証の情報（`oauth_creds.json` など）は読まない・書かない。

**起動の条件**（`require`）：ハーネスの実験に効く場所（harness・experiments・config・projects・requirements.lock）に、
コミットしていない変更があれば起動しない（どの版で走ったかを記録から戻せなくなる）。
"""
import argparse
import datetime
import hashlib
import json
import locale
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
from proc import run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRACKED = ("harness", "experiments", "config", "projects", "requirements.lock")
GEMINI_HOME = Path.home() / ".gemini"
AGY_GLOBAL = ("GEMINI.md", "settings.json")
ENV_PREFIX = "HARNESS_"


class ProvenanceError(Exception):
    pass


def sha256_of(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _git(args, cwd=ROOT):
    rc, out, err = run(["git"] + args, cwd, 60, "git（来歴）")
    if rc != 0:
        raise ProvenanceError(f"git {' '.join(args)} に失敗しました: {(err or out)[:200]}")
    return out


def harness_state(git=_git):
    """{"commit", "dirty"}。dirty は TRACKED の下のコミットしていない変更（追跡外のファイルを含む）のパス。"""
    commit = git(["rev-parse", "HEAD"]).strip()
    status = git(["status", "--porcelain", "--untracked-files=all", "--", *TRACKED])
    dirty = sorted(line[3:].strip() for line in status.splitlines() if line.strip())
    return {"commit": commit, "dirty": dirty}


def agy_global(home=GEMINI_HOME):
    """実装役の全体設定の {ファイル名: sha256 | None}（None は無い）。本文は読まない。"""
    return {name: sha256_of(Path(home) / name) for name in AGY_GLOBAL}


def harness_env(environ=None):
    environ = os.environ if environ is None else environ
    return {k: environ[k] for k in sorted(environ) if k.startswith(ENV_PREFIX)}


def collect(manifest=None, implementer=None, git=_git, home=GEMINI_HOME, environ=None):
    rec = {"recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "harness": harness_state(git), "agy_global": agy_global(home), "env": harness_env(environ),
           "python_executable": sys.executable,
           # dotnet の出力の言語が表の読み取りを変えた実例がある（envcheck.test_packages の注）。地域の設定も残す
           "locale": list(locale.getlocale()), "timezone": list(time.tzname)}
    if manifest is not None:
        rec["manifest"] = {"path": Path(manifest).resolve().relative_to(ROOT).as_posix()
                           if Path(manifest).resolve().is_relative_to(ROOT) else str(manifest),
                           "sha256": sha256_of(manifest)}
    if implementer is not None:
        rec["implementer"] = {k: v for k, v in implementer.items() if not k.startswith("_")}
    return rec


def write(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def require(out_path=None, **kw):
    """集めて書き、ハーネスが汚れていれば ProvenanceError（起動しない）。記録を返す。"""
    rec = collect(**kw)
    if out_path:
        write(out_path, rec)
    dirty = rec["harness"]["dirty"]
    if dirty:
        raise ProvenanceError("ハーネスにコミットしていない変更があります（走行の版を記録から戻せない）: "
                              + ", ".join(dirty[:5]) + (f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""))
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="走行の来歴（ハーネスのコミット・実装役の全体設定・環境変数）")
    ap.add_argument("--out")
    ap.add_argument("--manifest")
    args = ap.parse_args(argv)
    try:
        rec = require(args.out, manifest=args.manifest)
    except ProvenanceError as e:
        print(f"NG: {e}")
        return 1
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
