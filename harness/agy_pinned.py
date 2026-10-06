"""実装役（agy）の導入先のファイルの SHA-256 の照合（S1-4）。

    python -m harness.agy_pinned                                   config/agy_digests.json と実物を照合する
    python -m harness.agy_pinned --measure <導入先> <ファイル名>...   実測を JSON で出す（人間が期待値を作る）

期待値は config/agy_digests.json にだけ置く（既定値は持たない）。記録が無い・空・実物と違えば AgyDigestError。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PINNED = ROOT / "config" / "agy_digests.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class AgyDigestError(Exception):
    pass


def sha256_file(path):
    """中身の SHA-256（小文字の 16 進）。無い・読めないときは None（例外を出さない）。"""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def load_record(path=PINNED):
    """期待値の記録。読めない・JSON でない・表でないときは AgyDigestError。"""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise AgyDigestError(f"期待値の記録を読めません（{path}）：{e}") from e
    if not isinstance(rec, dict):
        raise AgyDigestError(f"期待値の記録が表ではありません：{path}")
    return rec


def _target(install_dir, rel):
    """install_dir の中の rel の絶対パス。外を指す（絶対パス・..・リンク）なら None。"""
    try:
        base = Path(install_dir).resolve()
        p = (base / rel.replace("\\", "/")).resolve()
        p.relative_to(base)
    except (OSError, ValueError, AttributeError):
        return None
    return p


def problems(record, digest=sha256_file):
    """照合に落ちた理由の一覧（空なら合格）。"""
    out = []
    install_dir, files = record.get("install_dir"), record.get("files")
    has_dir = isinstance(install_dir, str) and bool(install_dir.strip())
    if not has_dir:
        out.append(f"install_dir：空でない文字列ではありません（{install_dir!r}）")
    if not isinstance(files, dict) or not files:
        return out + ["files：空でない表ではありません（期待値が未記録）"]
    for rel, want in files.items():
        valid = isinstance(want, str) and SHA256_RE.fullmatch(want) is not None
        if not valid:
            out.append(f"{rel}：期待値が小文字 64 桁の 16 進ではありません（{want!r}）")
        if not has_dir:
            continue
        target = _target(install_dir, rel)
        got = digest(target) if target is not None else None
        if got is None:
            out.append(f"{rel}：ファイルがありません（{target or install_dir}）")
        elif valid and got != want:
            out.append(f"{rel}：実測 {got}、期待 {want}")
    return out


def require(record=None, path=PINNED, digest=sha256_file):
    """照合して、合格した記録を返す。1 件でも落ちれば AgyDigestError（全理由を連ねる）。"""
    rec = record if record is not None else load_record(path)
    found = problems(rec, digest)
    if found:
        raise AgyDigestError("agy の導入先のファイルが期待値（config/agy_digests.json）と違います：" + "；".join(found))
    return rec


def measure(install_dir, names, digest=sha256_file):
    """{名前: 実測の SHA-256 か None}。install_dir の外は読まない。"""
    keys = {str(n).replace("\\", "/") for n in names}
    return {k: (digest(t) if (t := _target(install_dir, k)) is not None else None) for k in sorted(keys)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="agy の導入先のファイルの SHA-256 の照合")
    ap.add_argument("--measure", metavar="導入先", help="実測を JSON で出す（人間が実行する）。ファイル名を続ける")
    ap.add_argument("names", nargs="*", help="--measure で測るファイル名")
    args = ap.parse_args(argv)
    if args.measure:
        print(json.dumps({"install_dir": args.measure, "files": measure(args.measure, args.names)},
                         ensure_ascii=False, indent=2))
        return 0
    try:
        require()
    except AgyDigestError as e:
        print(f"NG: {e}")
        return 1
    print("合格")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
