"""実装役の細い作業場所（docs/design/v2_1c_structure_review.md §1.3・§3 の 3、2026-09-25 の裁定 2）。

実装役（agy）を、サンドボックス（リポジトリ全体）ではなく、書き換えてよいファイルと契約（参照データを含む）と
base の既存の型だけを置いた一時ディレクトリで動かす。置いていないもの（テスト・仕様書・GDD・単位定義）は
その場所に無い。「読まないでください」という頼みを、置かないという仕組みに置き換える。

- 置き場は OS の一時ディレクトリの下で、元の場所（サンドボックスか作業ツリー）ごとに決まる固定のパス。
  プロンプトに書くパスが呼び出しをまたいで同じになり、キャッシュに乗る
- 呼び出しの後、作業場所で変わったファイル（新しく作ったもの・消したものを含む）を、元の場所の同じ相対パスへ書き戻す。
  書き換えてよいかどうかの判定は、いままでどおり元の場所で門（B の whitelist）が行う。ここでは絞らない
- 一時ディレクトリの外のパスを指す相対パスは作らない（rglob で見つかるものだけを書き戻す）

OS の権限で読み取りを禁じてはいない（同じユーザーで動くので、絶対パスを知っていれば読める）。作業場所の外のパスは
プロンプトに出さない。
"""
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

ROOT_NAME = "harness-narrow"


def path_for(origin):
    """origin（サンドボックスか作業ツリー）に対応する作業場所のパス。決定論。"""
    key = hashlib.sha256(str(Path(origin).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / ROOT_NAME / key


def _files(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def populate(origin, rels, dst=None):
    """作業場所を空にして、origin から rels を写す。(作業場所, 置いたものの {相対パス: バイト列})。"""
    dst = Path(dst) if dst is not None else path_for(origin)
    if dst.exists():
        if ROOT_NAME not in dst.parts:
            raise ValueError(f"作業場所ではないディレクトリは消しません: {dst}")
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for rel in rels:
        src = Path(origin) / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst / rel)
    return dst, _files(dst)


def discard(dst):
    """作業場所を消す。呼び出しの後に残さない（v2.1d）。

    v2-dry-05c では、作業場所を呼び出しの後も残していたので、実装役が親の harness-narrow を一覧し、ほかの走行
    （前の走行や、先に走った条件 A）の作業場所の GameState.cs を読んでいた。
    """
    dst = Path(dst)
    if dst.exists():
        if ROOT_NAME not in dst.parts:
            raise ValueError(f"作業場所ではないディレクトリは消しません: {dst}")
        shutil.rmtree(dst, ignore_errors=True)


def write_back(dst, origin, placed):
    """作業場所で変わったものを origin に書き戻す。変わった相対パスの列（決定論の順）。"""
    now, changed = _files(dst), []
    for rel, body in now.items():
        if placed.get(rel) != body:
            target = Path(origin) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            changed.append(rel)
    for rel in placed:
        if rel not in now:
            target = Path(origin) / rel
            if target.exists():
                target.unlink()
            changed.append(rel)
    return sorted(changed)


def relocate(text, origin, dst):
    """文章の中の origin（サンドボックスか作業ツリー）の絶対パスを、作業場所 dst のパスに置き換える（v2-smoke-01）。

    反例やビルド・テストの出力（再試行の指示に入れるもの）には、元の場所の絶対パスが出る。v2-smoke-01 の A は、
    再試行の指示に入った作業ツリーのパスをたどって、作業場所の外のテストのファイルを読み、dotnet を走らせた。
    作業場所は元の場所と同じ相対パスの並びなので、置き換えても行番号つきのファイル名はそのまま通じる。
    """
    if not text or dst is None:
        return text
    src = str(Path(origin).resolve())
    for form in {src, src.replace("\\", "/"), str(origin), str(origin).replace("\\", "/")}:
        text = re.sub(re.escape(form), lambda m: str(dst), text, flags=re.IGNORECASE)
    return text
