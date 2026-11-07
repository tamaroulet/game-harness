"""箱とのファイルの受け渡し（v3 §8 の E4）：作業の木を tar に固める（pack）。箱から返った tar のうち、受け取ってよいものだけを作業の木に写す（receive）。

箱の中は信用しない。返ってきた tar の普通のファイルだけを受け取る。.git の下と Python の書き出し（__pycache__・.pyc）は、渡しも受け取りもしない。
ほかに受け取らないもの（dropped に理由を残す。黙って捨てない）：リンク、デバイスなどの特殊なファイル、木の外を指す名前、Windows で作れない名前、
大文字と小文字だけが違う 2 つ目、大きすぎるファイル、呼び手が許さない新しいファイル（allowed）。
中身が同じファイルは書かない。tar に無いファイルは消す（箱の中で消した）。落としたファイルの名は消す対象にしない（前の版が残る）。
"""
import io
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

GIT = ".git"
JUNK_DIR = "__pycache__"
MAX_FILE = 50 * 1024 * 1024    # 課題のパッケージと試験のファイルには十分な上限
MAX_TOTAL = 512 * 1024 * 1024  # 木の全体（今のリポジトリの木は 16 MB）
FILE_MODE, DIR_MODE = 0o644, 0o755
BENCH_UID = 1000
BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
DROPPED_HEAD = "前の試行で、次のファイルはハーネスが受け取らなかったので、作業ツリーにありません。"   # hline_followup.narrow がこの文で知らせを見分ける
DROPPED_SHOWN = 20   # 知らせに書く名の上限（箱の中で仮想環境などを作ると、受け取らないファイルが数千になりうる）


class TreeError(Exception):
    """受け渡しが続けられない（返ってきたものが tar でない、木が大きすぎる）。"""


def skipped(parts):
    """渡しも受け取りもしないパスか（.git と、Python の書き出し）。"""
    return parts[0] == GIT or JUNK_DIR in parts or parts[-1].endswith(".pyc")


def walk(root):
    """root の下の普通のファイルの相対パス（/ 区切り）の列。.git と Python の書き出しは入れない。リンクの先へは降りない。"""
    root = Path(root)
    found = []
    for base, dirs, files in os.walk(root, followlinks=False):
        rel = Path(base).relative_to(root).parts
        dirs[:] = [d for d in dirs if not skipped((*rel, d))]
        for name in files:
            path = Path(base) / name
            if not skipped((*rel, name)) and path.is_file() and not path.is_symlink():
                found.append(PurePosixPath(*rel, name).as_posix())
    return sorted(found)


def _member(name, mode, size=0):
    info = tarfile.TarInfo(name)
    info.mode, info.size = mode, size
    info.uid, info.gid = BENCH_UID, BENCH_UID
    return info


def pack(root, extra=None):
    """root の木の tar のバイト列（.git を除く）。extra は {tar の中の名: バイト列} で、箱へ一緒に渡すもの（走らせ役の短いスクリプトなど）。
    root が None なら extra だけ。持ち主は箱の利用者にし、権限はファイル 644・ディレクトリ 755 にそろえる（Windows の属性を持ち込まない）。"""
    buf, total, made = io.BytesIO(), 0, set()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        def add(name, data):
            for parent in reversed(list(PurePosixPath(name).parents)[:-1]):
                if parent.as_posix() not in made:
                    made.add(parent.as_posix())
                    info = _member(parent.as_posix(), DIR_MODE)
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
            tar.addfile(_member(name, FILE_MODE, len(data)), io.BytesIO(data))
        for rel in walk(root) if root is not None else []:
            data = (Path(root) / rel).read_bytes()
            total += len(data)
            if total > MAX_TOTAL:
                raise TreeError(f"木が大きすぎます（{MAX_TOTAL} バイトを越えました）: {rel}")
            add(rel, data)
        for name, data in (extra or {}).items():
            add(name, data)
    return buf.getvalue()


def bad_name(parts):
    """Windows の作業ツリーに作れない名前か。"""
    return any(BAD_CHARS.search(p) or p.endswith((".", " ")) or p.split(".")[0].lower() in RESERVED for p in parts)


def why_dropped(member, parts, rel, seen):
    """この名を受け取らない理由。受け取ってよければ None。"""
    if not member.isfile():
        return "リンク" if member.issym() or member.islnk() else "特殊なファイル"
    if bad_name(parts):
        return "Windows で作れない名前"
    if rel.lower() in seen:
        return "大文字と小文字だけが違う名前"
    return "大きすぎるファイル" if member.size > MAX_FILE else None


def receive(data, dest, allowed=None, strip=None):
    """箱から返った tar を dest に写す。{"written": [...], "removed": [...], "dropped": [[名, 理由], ...]}。
    allowed(相対パス) が偽の新しいファイルは受け取らない（すでにあるファイルの変更は受け取る）。
    strip は tar の中の頭のディレクトリの名（docker cp が付ける）。これを外した相対パスで扱う。"""
    dest = Path(dest)
    before, written, dropped, named, seen, total = set(walk(dest)), [], [], set(), set(), 0
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as e:
        raise TreeError(f"返ってきたものが tar ではありません: {e}") from e
    with tar:
        for member in tar:
            parts = tuple(p for p in PurePosixPath(member.name).parts if p != ".")
            if strip and parts[:1] == (strip,):
                parts = parts[1:]
            if member.name.startswith("/") or ".." in parts:
                dropped.append([member.name, "木の外を指す名前"])
                continue
            if not parts or skipped(parts) or member.isdir():
                continue
            rel = "/".join(parts)
            named.add(rel)
            why = why_dropped(member, parts, rel, seen)
            if why is None and rel not in before and allowed is not None and not allowed(rel):
                why = "許されたパスの外の新しいファイル"
            if why:
                dropped.append([rel, why])
                continue
            seen.add(rel.lower())
            total += member.size
            if total > MAX_TOTAL:
                raise TreeError(f"返ってきた木が大きすぎます（{MAX_TOTAL} バイトを越えました）: {rel}")
            body = tar.extractfile(member).read()
            path = dest.joinpath(*parts)
            if not path.is_file() or path.read_bytes() != body:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                except OSError as e:
                    dropped.append([rel, f"書けない: {e}"])
                    continue
                written.append(rel)
    kept = {os.path.normcase(n) for n in named}   # Windows では、大文字と小文字だけを変えた名を消さない（同じファイル）
    removed = sorted(r for r in before if os.path.normcase(r) not in kept)
    for rel in removed:
        (dest / rel).unlink()
    for rel in removed:
        _prune(dest, Path(rel).parent)
    return {"written": sorted(written), "removed": removed, "dropped": dropped}


def dropped_note(dropped):
    """受け取らなかったファイルを実装役に知らせる文（次の試行の入力に足す）。知らせないと、実装役は何が起きたか分からず、試行を無駄にする。"""
    rows = [f"- {name}：{why}" for name, why in dropped[:DROPPED_SHOWN]]
    rest = len(dropped) - DROPPED_SHOWN
    return "\n".join([f"{DROPPED_HEAD}要るものは、課題のパッケージの中か、tests/test_<パッケージ名>*.py の中に置いてください。",
                      *rows, *([f"- ほか {rest} 件"] if rest > 0 else [])])


def _prune(dest, rel):
    """消したファイルの親のディレクトリが空なら消す（根までたどる）。"""
    while rel.parts:
        try:
            (dest / rel).rmdir()
        except OSError:
            return
        rel = rel.parent
