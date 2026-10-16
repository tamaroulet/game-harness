"""要件の網羅検査器：仕様の要件 ID が、テストと What に漏れなく現れているかを決定論的に判定する（C2 の合格条件 2）。

    python -m harness.reqcov --spec docs/spec.md --tests "tests/test_*.py" --whats "tasks/*.md" [--list]

- 定義：仕様の行頭の `- REQ-01：本文`。それ以外の場所の ID は参照とみなす
- `--tests`：本文のどこかに出てくる ID を数える。`--whats`：`要件:` の行に出てくる ID だけを数える
- 終了コード：0 網羅している／1 網羅していない（足りない ID・仕様に無い ID）／2 入力の異常

LLM・ネットワーク・現在時刻は使わない。同じ入力なら出力も終了コードも同じ。
"""
import argparse
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

ID = r"REQ-\d{2,}"
ID_RE = re.compile(rf"(?<![A-Za-z0-9-])({ID})(?!\d)")
DEFINITION_RE = re.compile(rf"^[ \t]*-[ \t]+({ID})：")
WHAT_LINE_RE = re.compile(r"^\s*要件\s*[:：]")


class InputError(Exception):
    pass


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"読めないファイル：{Path(path).as_posix()}（{type(e).__name__}）")


def definitions(spec_path):
    """仕様に定義された ID を仕様の順に返す。重複・0 件は InputError。"""
    ids, seen = [], set()
    for line in read_text(spec_path).splitlines():
        m = DEFINITION_RE.match(line)
        if not m:
            continue
        if m.group(1) in seen:
            raise InputError(f"定義が重複している：{m.group(1)}")
        seen.add(m.group(1))
        ids.append(m.group(1))
    if not ids:
        raise InputError(f"要件 ID の定義が 1 件も無い：{Path(spec_path).as_posix()}")
    return ids


def expand(patterns, side):
    """グロブを展開して、重複の無い整列済みのファイル一覧にする。合うファイルが無ければ InputError。"""
    files = set()
    for pattern in patterns:
        hits = [p for p in glob.glob(pattern, recursive=True) if Path(p).is_file()]
        if not hits:
            raise InputError(f"{side} のグロブに合うファイルが無い：{pattern}")
        files.update(Path(p).as_posix() for p in hits)
    return sorted(files)


def ids_in(path, side):
    text = read_text(path)
    if side == "whats":
        text = "\n".join(line for line in text.splitlines() if WHAT_LINE_RE.match(line))
    return set(ID_RE.findall(text))


def check(defined, sides):
    """sides: {"tests": [ファイル], "whats": [ファイル]}。→ (足りない [(側, ID)], 未定義 [(側, ファイル, ID)])。"""
    missing, unknown = [], []
    for side, files in sides.items():
        found = set()
        for f in files:
            here = ids_in(f, side)
            found |= here
            unknown += [(side, f, i) for i in sorted(here - set(defined))]
        missing += [(side, i) for i in defined if i not in found]
    return missing, unknown


def render(defined, sides, missing, unknown):
    lines = [f"仕様の要件：{len(defined)} 件"]
    for side, files in sides.items():
        lines.append(f"{side}：{len(files)} ファイル")
    lines += [f"足りない：{i}（{side} に無い）" for side, i in missing]
    lines += [f"仕様に定義の無い ID：{i}（{side}：{f}）" for side, f, i in unknown]
    lines.append("網羅している" if not missing and not unknown
                 else f"網羅していない（足りない {len(missing)} 件・未定義の参照 {len(unknown)} 件）")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="要件 ID の網羅検査")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--tests", action="append", default=[])
    ap.add_argument("--whats", action="append", default=[])
    ap.add_argument("--list", action="store_true", dest="list_ids")
    args = ap.parse_args(argv)
    try:
        defined = definitions(args.spec)
        if not (args.list_ids or args.tests or args.whats):
            raise InputError("--list も --tests も --whats も無い")
        sides = {}
        if args.tests:
            sides["tests"] = expand(args.tests, "tests")
        if args.whats:
            sides["whats"] = expand(args.whats, "whats")
        if args.list_ids:
            print("\n".join(defined))
        if not sides:
            return 0
        missing, unknown = check(defined, sides)
    except InputError as e:
        print(f"入力の異常：{e}")
        return 2
    print(render(defined, sides, missing, unknown))
    return 1 if missing or unknown else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
