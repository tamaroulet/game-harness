"""ベンチの走行の守りの確かめ。非公開オラクル・受信箱・本線の生ログを読めないことと、漏れの跡が無いことを検める。

    python -m harness.bench_guard check [--config PATH]    制限のアカウントで。3 つの場所のファイルを開けたら、そのパスを出して 1
    python -m harness.bench_guard scan DIR... [--config PATH]  操縦士のアカウントで。oracles のパスか canary.txt の文字列があれば、ファイルと行を出して 1

3 つの場所は config/hline.json の oracles・inbox・out（oracle_deny.py と同じ出どころ）。鍵が無いときは 2。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402  python -m harness.bench_guard でも python harness/bench_guard.py でも読めるように
import machine  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "hline.json"
PLACES = ("oracles", "inbox", "out")


class ConfigError(Exception):
    pass


def places(cfg):
    missing = [k for k in PLACES if not isinstance(cfg.get(k), str) or not cfg[k].strip()]
    if missing:
        raise ConfigError(f"config/hline.json に {', '.join(missing)} がありません")
    return {k: Path(cfg[k]) for k in PLACES}


def openable(cfg):
    """3 つの場所の下で、読みのために開けたファイルのパス。無い・一覧が取れない・空は、開けたことにならない。"""
    found = []
    for root in places(cfg).values():
        for dirpath, _dirs, files in _walk(root):
            for name in files:
                path = Path(dirpath) / name
                try:
                    with open(path, "rb") as f:
                        f.read(1)
                except OSError:
                    continue
                found.append(str(path))
    return found


def _walk(root):
    import os

    return os.walk(root)  # onerror を渡さないので、一覧の取れないディレクトリは黙って飛ばす


def _norm(text):
    return re.sub(r"[\\/]+", "/", text).lower()


def canaries(oracles):
    """凍結の課題（bench/<課題>/canary.txt）と、累積の筋書きの段（chains/<土台>/s<k>/canary.txt）の canary の文字列。"""
    out = []
    for p in sorted([*Path(oracles).glob("bench/*/canary.txt"), *Path(oracles).glob("chains/*/s*/canary.txt")]):
        try:
            s = p.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if s:
            out.append(s)
    return out


def leaks(cfg, dirs):
    """(ファイル, 行の番号) の列。"""
    oracles = places(cfg)["oracles"]
    needle = _norm(str(oracles)).rstrip("/")
    marks = canaries(oracles)
    hits = []
    for d in dirs:
        for p in sorted(x for x in Path(d).rglob("*") if x.is_file()):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if needle in _norm(line) or any(m in line for m in marks):
                    hits.append((str(p), n))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bench_guard")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", parents=[common])
    sub.add_parser("scan", parents=[common]).add_argument("dirs", nargs="+")
    args = ap.parse_args(argv)
    cfg = machine.read_json(args.config)
    try:
        if args.cmd == "check":
            bad = openable(cfg)
            for p in bad:
                print(f"開けた: {p}")
        else:
            bad = leaks(cfg, args.dirs)
            for p, n in bad:
                print(f"漏れの跡: {p}:{n}")
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
