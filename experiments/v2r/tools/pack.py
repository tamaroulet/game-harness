"""v2r の走行の記録をアーカイブにまとめ、sha256 のマニフェストを作る（docs/design/v2r_protocol.md §7.2）。

    python experiments/v2r/tools/pack.py --name v2r-run --run-ids v2r-run-01 v2r-run-02 --dest <出力先>
        [--out-root C:/src/.local/out/ab] [--env C:/src/.local/out/ab/v2r-run.env.json]
        [--provenance C:/src/.local/out/ab/v2r-run.provenance.json] [--agy-global]

`--agy-global`：実装役（agy）がリポジトリの外で従う `~/.gemini/GEMINI.md` を `env/agy_global/` に入れる。来歴に記録した
sha256 と一致するときだけ（走行の後に書き換わっていれば止まる）。

出力：
- `<name>.tar.gz`：走行の記録（下の INCLUDE に当たるファイル）と env.json。中の並び・時刻・所有者は固定し、同じ入力からは
  同じバイト列になる（決定論）
- `<name>.manifest.sha256`：1 行目がアーカイブ自身、2 行目からが中の各ファイルの `sha256  パス`（sha256sum の形）

入れるもの（INCLUDE）：実装役に渡したプロンプトの全文と stream-json の全行（実装役のログ）、pipeline のテレメトリと記録、
metrics.jsonl、測定器の結果（TRX）、env.json。ビルドの生成物（bin・obj の下）は入れない。

**防壁①**：アーカイブにはゲームのコードとテストの生成物が入る。この道具はバイト列を写すだけで、総監督は中身を読まない。
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

INCLUDE = ("*.log", "*.trx", "*.jsonl", "*.json")
EXCLUDE_DIRS = {"bin", "obj"}
# 実装役の全体設定のうち、公開するもの（harness/provenance.py の AGY_GLOBAL のうち GEMINI.md だけ。2026-09-30 の操縦士の決定）
AGY_GLOBAL = ("GEMINI.md",)
GEMINI_HOME = Path.home() / ".gemini"


def agy_global_members(provenance_files, home):
    """実装役の全体設定（AGY_GLOBAL）を [(アーカイブの中のパス, ファイル)] で返す。

    来歴（provenance.json）に記録した sha256 と、今のファイルが一致するときだけ入れる。走行の後に書き換わっていたら
    ValueError（走った時の設定ではないものを、走った時の設定として出さない）。来歴が無ければ入れられない。
    """
    if not provenance_files:
        raise ValueError("実装役の全体設定を入れるには、走行の来歴（--provenance）が要ります")
    out = []
    for name in AGY_GLOBAL:
        recorded = {json.loads(Path(p).read_text(encoding="utf-8")).get("agy_global", {}).get(name)
                    for p in provenance_files}
        if len(recorded) != 1 or None in recorded:
            raise ValueError(f"来歴の {name} の sha256 が 1 つに決まりません（走行の間に変わったか、記録が無い）: {sorted(map(str, recorded))}")
        f = Path(home) / name
        if not f.is_file() or sha256_bytes(f.read_bytes()) != recorded.pop():
            raise ValueError(f"{f} が走行の時の sha256 と一致しません（走行の後に書き換わった）")
        out.append((f"env/agy_global/{name}", f))
    return out


def members(out_root, run_ids, env_files=()):
    """アーカイブに入れる [(アーカイブの中のパス, ファイル)]。決定論の順。"""
    found = {}
    for rid in run_ids:
        root = Path(out_root) / rid
        if not root.is_dir():
            raise FileNotFoundError(f"走行の記録がありません: {root}")
        for pattern in INCLUDE:
            for f in root.rglob(pattern):
                rel = f.relative_to(root)
                if f.is_file() and not (set(rel.parts[:-1]) & EXCLUDE_DIRS):
                    found[f"{rid}/{rel.as_posix()}"] = f
    for e in env_files:
        e = Path(e)
        if not e.is_file():
            raise FileNotFoundError(f"env.json がありません: {e}")
        found[f"env/{e.name}"] = e
    return sorted(found.items())


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def build(items):
    """(アーカイブのバイト列, [(パス, sha256)])。時刻・所有者・権限を固定する。"""
    raw, digests = io.BytesIO(), []
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in items:
            data = Path(path).read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = len(data), 0, 0o644, 0, 0, "", ""
            tar.addfile(info, io.BytesIO(data))
            digests.append((name, sha256_bytes(data)))
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, mtime=0) as g:
        g.write(raw.getvalue())
    return gz.getvalue(), digests


def manifest_text(archive_name, archive_sha, digests):
    return "\n".join([f"{archive_sha}  {archive_name}"] + [f"{d}  {n}" for n, d in digests]) + "\n"


def pack(name, out_root, run_ids, dest, env_files=(), provenance_files=(), agy_home=None):
    """(アーカイブのパス, マニフェストのパス, ファイルの数)。agy_home を渡すと、実装役の全体設定を来歴と照合して入れる。"""
    items = members(out_root, run_ids, tuple(env_files) + tuple(provenance_files))
    if agy_home is not None:
        items = sorted(items + agy_global_members(provenance_files, agy_home))
    if not items:
        raise FileNotFoundError("入れるファイルがありません")
    data, digests = build(items)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{name}.tar.gz"
    archive.write_bytes(data)
    manifest = dest / f"{name}.manifest.sha256"
    manifest.write_text(manifest_text(archive.name, sha256_bytes(data), digests), encoding="utf-8", newline="\n")
    return archive, manifest, len(digests)


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2r の走行の記録のアーカイブと sha256 のマニフェスト")
    ap.add_argument("--name", required=True)
    ap.add_argument("--run-ids", nargs="+", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--out-root", default="C:/src/.local/out/ab")
    ap.add_argument("--env", nargs="*", default=[])
    ap.add_argument("--provenance", nargs="*", default=[], help="走行の来歴（<prefix>.provenance.json）")
    ap.add_argument("--agy-global", action="store_true",
                    help="実装役の全体設定（~/.gemini/GEMINI.md）を、来歴の sha256 と照合して入れる（2026-09-30 の操縦士の決定）")
    args = ap.parse_args(argv)
    try:
        archive, manifest, n = pack(args.name, args.out_root, args.run_ids, args.dest, args.env, args.provenance,
                                    GEMINI_HOME if args.agy_global else None)
    except (FileNotFoundError, ValueError) as e:
        print(f"NG: {e}")
        return 1
    print(f"書き出し: {archive}（{n} ファイル）")
    print(f"書き出し: {manifest}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
