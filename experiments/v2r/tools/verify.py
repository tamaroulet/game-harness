"""v2r のアーカイブを、マニフェストの sha256 と照合する（docs/design/v2r_protocol.md §7.2）。第三者が Release から
取得した後に、改ざん・欠け・余分が無いことを確かめる。

    python experiments/v2r/tools/verify.py <name>.tar.gz <name>.manifest.sha256 [--extract <展開先>]

見ること：
1. アーカイブ自身の sha256 がマニフェストの 1 行目と一致する
2. 中の各ファイルの sha256 がマニフェストと一致する。マニフェストにあって中に無いもの、中にあってマニフェストに無いものが無い
3. 中のパスが安全（絶対パス・`..`・リンクを含まない）

すべて通れば終了コード 0。`--extract` を渡すと、通ったときだけ展開する（集計の道具はその展開先を引数に取る）。
"""
import argparse
import hashlib
import io
import sys
import tarfile
from pathlib import Path, PurePosixPath


def read_manifest(text):
    """(アーカイブの (sha256, 名前), {パス: sha256})。形が違えば ValueError。"""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("マニフェストが空です")
    rows = []
    for l in lines:
        digest, sep, name = l.partition("  ")
        if not sep or len(digest) != 64 or not name:
            raise ValueError(f"マニフェストの行の形が違います: {l[:80]}")
        rows.append((digest, name))
    files = {}
    for digest, name in rows[1:]:
        if name in files:
            raise ValueError(f"マニフェストに同じパスが 2 回あります: {name}")
        files[name] = digest
    return rows[0], files


def safe(name):
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and not name.startswith(("/", "\\")) and ":" not in name


def problems(archive_bytes, archive_name, manifest):
    """問題の一覧（空なら合格）と、中のファイルの {パス: バイト列}。"""
    (want_archive, listed_name), files = read_manifest(manifest)
    out = []
    if listed_name != archive_name:
        out.append(f"マニフェストのアーカイブ名 {listed_name} と、照合するファイル名 {archive_name} が違います")
    if hashlib.sha256(archive_bytes).hexdigest() != want_archive:
        out.append("アーカイブの sha256 がマニフェストと違います")
    contents = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    out.append(f"ファイルでない項目があります: {m.name}")
                    continue
                if not safe(m.name):
                    out.append(f"安全でないパスがあります: {m.name}")
                    continue
                contents[m.name] = tar.extractfile(m).read()
    except (tarfile.TarError, OSError, EOFError) as e:
        return out + [f"アーカイブを読めません: {e}"], {}
    for name, digest in sorted(files.items()):
        if name not in contents:
            out.append(f"マニフェストにあるのに、アーカイブに無い: {name}")
        elif hashlib.sha256(contents[name]).hexdigest() != digest:
            out.append(f"sha256 が違う: {name}")
    out += [f"アーカイブにあるのに、マニフェストに無い: {n}" for n in sorted(set(contents) - set(files))]
    return out, contents


def verify(archive, manifest, extract=None):
    archive, manifest = Path(archive), Path(manifest)
    found, contents = problems(archive.read_bytes(), archive.name, manifest.read_text(encoding="utf-8"))
    if not found and extract:
        root = Path(extract)
        for name, data in contents.items():
            target = root / PurePosixPath(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return found, len(contents)


def main(argv=None):
    ap = argparse.ArgumentParser(description="v2r のアーカイブとマニフェストの照合")
    ap.add_argument("archive")
    ap.add_argument("manifest")
    ap.add_argument("--extract")
    args = ap.parse_args(argv)
    try:
        found, n = verify(args.archive, args.manifest, args.extract)
    except (OSError, ValueError) as e:
        print(f"NG: {e}")
        return 1
    for f in found:
        print(f"NG: {f}")
    print(f"合格（{n} ファイル）" if not found else f"不合格（{len(found)} 件）")
    return 0 if not found else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
