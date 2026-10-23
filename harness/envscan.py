"""環境の直書きの目録と歯止め（ADR-004 の束 F、W-125）：外部の命令の名前・絶対パス・OS に固有の処理・文字コードの手当て・
拡張子・版の文字列・モデル ID の直書きを数え、境界の外で増えたら落とす。

    python -m harness.envscan                 種類ごとの合計と、多いファイルを出す
    python -m harness.envscan --check         記録（config/envscan_baseline.json）より増えたファイルがあれば終了コード 1
    python -m harness.envscan --update        記録を今の数に下げる（増えていれば書かずに終了コード 1）

**なぜ要るか**: 環境に依る直書きが各所に散ると、環境が 1 か所変わるたびに、どこかが黙って壊れる（棚卸しの D21。
走らせ役の JSON の文字コード、CLI の版の固定の食い違い）。その場所を人が全部は知らない。そこで人が探すのでなく、機械で数え、
境界（boundary）の外では数を増やせなくする。減らすのは自由で、--update で記録を下げる。上げるのは記録の手書きだけで、PR に見える。

数えるのは正規表現による近似で、文字列の中の語も拾う。目的は「増えたこと」に気づくことで、1 件ずつの正しさではない。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "config" / "envscan_baseline.json"
SCAN_GLOBS = ("harness/**/*.py", "config/*.json", "projects/*/*.json")
CATEGORIES = {
    "cli_name": r"[\"'](?:git|gh|dotnet|claude|agy|gemini|Unity|unity|powershell|cmd|python|pythonw|taskkill|schtasks|icacls)[\"']",
    "abs_path": r"[A-Za-z]:(?:\\\\|\\|/)(?:src|Users|Program Files|Windows|tools)\b",
    "os_specific": r"taskkill|cmd\.exe|CREATE_NO_WINDOW|STARTUPINFO|WinError|windll|winreg|msvcrt|sys\.platform|os\.name\b|"
                   r"pythonw|schtasks|wscript|icacls|CreateJobObject|AssignProcessToJobObject",
    "encoding": r"cp932|mbcs|shift_jis|PYTHONUTF8|PYTHONIOENCODING|errors=[\"']replace[\"']",
    "exe_suffix": r"\.(?:exe|cmd|bat|ps1|vbs)\b",
    "version_literal": r"[\"'](?:\d+\.\d+\.\d+(?:\.\d+)?|net\d+\.\d+|\d{4}\.\d+\.\d+[a-z]\d+)[\"']",
    "model_id": r"\b(?:claude-(?:opus|sonnet|haiku)-[0-9][0-9a-z.-]*|gemini-\d[0-9a-z.-]*)",
}
_COMPILED = {k: re.compile(v) for k, v in CATEGORIES.items()}


def count(text):
    """種類ごとの出現の数（0 は入れない）。"""
    return {k: n for k, rx in _COMPILED.items() if (n := len(rx.findall(text)))}


def files(root=ROOT):
    out = set()
    for g in SCAN_GLOBS:
        out |= {p for p in Path(root).glob(g) if p.is_file() and "__pycache__" not in p.parts}
    return sorted(out)


def rel(path, root=ROOT):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def in_boundary(name, boundary):
    return any(name == b or (b.endswith("/") and name.startswith(b)) for b in boundary)


def scan(root=ROOT, boundary=()):
    """{ファイル: {種類: 数}}。境界の中のファイルと、何も無いファイルは入れない。"""
    out = {}
    for p in files(root):
        name = rel(p, root)
        if in_boundary(name, boundary):
            continue
        if found := count(p.read_text(encoding="utf-8", errors="replace")):
            out[name] = found
    return out


def load_baseline(path=BASELINE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def increases(current, recorded):
    """記録より増えたもの。[(ファイル, 種類, 記録の数, 今の数)]。記録に無いファイル・種類は 0 と比べる。"""
    return [(f, k, recorded.get(f, {}).get(k, 0), n) for f, kinds in sorted(current.items())
            for k, n in sorted(kinds.items()) if n > recorded.get(f, {}).get(k, 0)]


def totals(counts):
    out = {k: 0 for k in CATEGORIES}
    for kinds in counts.values():
        for k, n in kinds.items():
            out[k] += n
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="環境の直書きの目録と歯止め")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--update", action="store_true")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)
    base = load_baseline(args.baseline)
    current = scan(args.root, base["boundary"])
    up = increases(current, base["counts"])
    if args.check or args.update:
        for f, k, was, now in up:
            print(f"増えた: {f} の {k} が {was} → {now}（境界の内側へ移すか、増やさずに書く）")
        if up:
            return 1
        if args.update:
            Path(args.baseline).write_text(json.dumps({**base, "counts": current}, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8", newline="\n")
            print(f"記録を今の数にしました（{sum(totals(current).values())} か所）")
        else:
            print("増えたところはありません")
        return 0
    t = totals(current)
    for k in CATEGORIES:
        top = sorted(((kinds.get(k, 0), f) for f, kinds in current.items() if kinds.get(k)), reverse=True)[:5]
        print(f"{k}: {t[k]} か所・{sum(1 for kinds in current.values() if k in kinds)} ファイル（{', '.join(f'{f} {n}' for n, f in top)}）")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
