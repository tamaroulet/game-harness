"""影響テスト：第 1 段の実行・実装役に許す実行・実装役への入力が同じ列から導かれるよう、テストのモジュールを 1 か所で決める（読み取りと文字列の組み立てだけ）。"""
import copy
import re
import shlex
from pathlib import Path

import base_whitelist
import symbolmap

_TEST_FILE = re.compile(r"tests/(test_\w+)\.py")
_NAME = re.compile(r"[A-Za-z_]\w*(?:\.\w+)*")
_VALUE_OPTIONS = {"-k", "-s", "-p", "-t", "--start-directory", "--pattern", "--top-level-directory"}
_CHAIN = {"&&", "||", ";", "|"}
_BASH = "Bash(python -m unittest"
_CALL = r"(?<![\w-])-m(?:\s+|['\"]\s*,\s*['\"])%s(?!\w|\.\w)"


def dotted(path):
    m = _TEST_FILE.fullmatch(path.replace("\\", "/").removeprefix("./")) if isinstance(path, str) else None
    return f"tests.{m.group(1)}" if m else None


def command_modules(text):
    try:
        words = shlex.split(text) if isinstance(text, str) else []
    except ValueError:
        return ()
    start = next((i + 2 for i in range(len(words) - 1) if words[i] == "-m" and words[i + 1] == "unittest"), None)
    names, skip = [], False
    for w in words[start:] if start is not None else []:
        if w in _CHAIN:
            break
        if skip or w.startswith("-") or not _NAME.fullmatch(w):
            skip = w in _VALUE_OPTIONS
        elif w == "discover":
            return ()
        else:
            names.append(w)
    return tuple(dict.fromkeys(names))


def _get(obj, key, kind):
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, kind) else None


def from_spec(spec):
    files = _get(_get(spec, "edit_boundary", dict), "allowed_files", list) or []
    command = _get(_get(spec, "test_oracle", dict), "verification_command", str)
    return tuple(dict.fromkeys([*filter(None, map(dotted, files)), *command_modules(command)]))


def _module_name(path):
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def _importers(root, targets):
    try:
        index = symbolmap.build(root)["modules"]
        return tuple(filter(None, (dotted(t) for target in targets for t in index.get(target, {}).get("tests", []))))
    except Exception:  # noqa: BLE001 - 目次が作れなくても、分かる分だけを返す
        return ()


def _callers(root, targets):
    patterns = [re.compile(_CALL % re.escape(_module_name(t))) for t in targets]
    found = []
    for file in sorted(Path(root).glob("tests/test_*.py")):
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if any(p.search(text) for p in patterns):
            found.append(dotted(file.relative_to(root).as_posix()))
    return tuple(filter(None, found))


def test_modules(root, spec=None, verification=None, changed=()):
    targets = [t for t in symbolmap.spec_modules(spec) if t.endswith(".py")]
    command = _get(verification, "command", str)
    found = [*(c for c in changed if isinstance(c, str)), *command_modules(command), *from_spec(spec),
             *(_importers(root, targets) + _callers(root, targets) if targets else ())]
    return tuple(dict.fromkeys(found))


def allowed_tools(modules):
    return tuple(f"{_BASH} {m}:*)" for m in modules)


def _scoped(value, tools):
    out, put = [], False
    for item in value.split(","):
        if not item.strip().startswith(_BASH):
            out.append(item)
        elif not put:
            out, put = out + list(tools), True
    return ",".join(out)


def scoped_agent(agent, modules):
    out, tools = copy.deepcopy(agent), allowed_tools(modules)
    flags = out.get("extra_flags")
    for i in range(len(flags) - 1 if isinstance(flags, list) else 0):
        if flags[i] == "--allowedTools" and isinstance(flags[i + 1], str):
            flags[i + 1] = _scoped(flags[i + 1], tools)
    return out


def stage_command(gate_command, modules, verification=None):
    if not verification:
        cmd = base_whitelist.unittest_command(gate_command, modules)
        return None if cmd is None else (shlex.join(cmd), cmd, 0)
    text, args = verification["command"], shlex.split(verification["command"])
    if command_modules(text) and (extra := [m for m in modules if m not in args]):
        args, text = args + extra, shlex.join(args + extra)
    return text, args, verification.get("expected_exit_code", 0)


def prompt_note(modules):
    return "" if not modules else (f"- 手元で走らせてよいテストは、次のモジュールだけ: {', '.join(modules)}。走らせ方は `python -m unittest <モジュール>`"
            f"（例: `python -m unittest {modules[0]}`）。これら以外と全件テストは走らせない")
