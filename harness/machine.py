"""この PC に固有の絶対パス（Git の外の machine.json。ADR-004 の束 F、W-126）。

設定のファイルには、パスを論理の名前で書く（"${local}/inbox"）。読む側は expand を通して、この PC の実のパスに替える。
実のパスは machine.json の paths にだけ置く。値の中の %VAR% は環境変数で替える。

    machine.json の場所：環境変数 HARNESS_MACHINE、無ければ <リポジトリの親>/.local/machine.json
    無いとき：config/machine.example.json で代用する（ADR-004 の前の直書きと同じ場所）。envcheck は代用を不合格にする

名前は machine.json の paths のほか、台帳（config/toolchain.json）の unity_editor（エディタの版）を使える。
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchain  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV = "HARNESS_MACHINE"
EXAMPLE = ROOT / "config" / "machine.example.json"
VAR_RE = re.compile(r"\$\{([A-Za-z_]+)\}")
# %VAR% は自前で替える。os.path.expandvars は Windows の外では %VAR% を替えない（Linux の CI で値が変わる）
ENV_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


class MachineError(Exception):
    pass


def machine_file():
    return Path(os.environ.get(ENV) or ROOT.parent / ".local" / "machine.json")


def source():
    """読む machine.json。無ければ例（config/machine.example.json）。"""
    path = machine_file()
    return path if path.exists() else EXAMPLE


def env_vars(text):
    """text の %VAR% を環境変数で替える。無い変数はそのまま残す。"""
    return ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), text)


def names(path=None):
    """{論理の名前: 実のパス}。"""
    path = Path(path) if path else source()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MachineError(f"machine.json を読めません（{path}）：{e}") from e
    out = {k: os.path.normpath(env_vars(str(v))) for k, v in (doc.get("paths") or {}).items()}
    out["unity_editor"] = toolchain.load().get("unity_editor") or ""
    return out


def expand(value, table=None):
    """value の中の文字列の ${名前} を実のパスに替える（表・配列は中まで）。替えた文字列は OS の区切りに直す。"""
    if isinstance(value, dict):
        table = names() if table is None else table
        return {k: expand(v, table) for k, v in value.items()}
    if isinstance(value, list):
        table = names() if table is None else table
        return [expand(v, table) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value
    table = names() if table is None else table

    def sub(m):
        if m.group(1) not in table:
            raise MachineError(f"machine.json の paths に {m.group(1)} がありません（{value}）")
        return table[m.group(1)]
    return os.path.normpath(VAR_RE.sub(sub, value))


def path(name):
    """論理の名前 1 つの実のパス。"""
    return Path(expand("${" + name + "}"))


def read_json(file):
    """JSON の設定を読み、${名前} を替えたもの。"""
    return expand(json.loads(Path(file).read_text(encoding="utf-8")))
