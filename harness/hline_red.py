"""H ライン（S3-2）の Gate C：合成した受入テストが実装の前に import できて、1 件以上落ちる（Red）ことを確かめる門。
**受入テストは実装役から隠し、実装の後に注入する**: 読めると、中身を写した実装で通せてしまう。そこで作業ツリーの外に置き、
Gate C のあいだだけ置いて（place）片付け（discard）、実装役の入力にも漏らさない（leak_problems）。実装の後に注入し（inject）、
編集境界にも足す（with_accept）ので、満たさない実装は Gate 1 で落ち、回帰でも走る。外のプロセス・LLM・git・gh・ネットワークは
呼ばず、動かすのは gate_c に渡された run だけ。副作用は place・discard・inject だけ。
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_spec  # noqa: E402  python -m harness.x でも読めるように
import model_pin  # noqa: E402

MARKERS = ("ImportError", "ModuleNotFoundError", "Traceback")
MIN_LEAK_LINE = 20


def accept_path(outdir: str) -> str:
    return os.path.abspath(os.path.join(outdir, "accept", "test_accept.py"))


def red_prompt(spec: dict, problems: list[str] | None = None) -> str:
    c, o = spec["contracts"], spec["test_oracle"]
    lines = ["次の TaskSpec を満たす実装の『受入テスト』を unittest で 1 ファイルに書いてください。実装はまだ無く、",
             "テストは実装の前に 1 件以上落ち（Red）、import はできなければなりません。子プロセス・LLM・git・gh・ネットワークは使わないこと。",
             "", "対象のシンボル:"] + [f"- {t['module']} の {t['name']}（{t['kind']}）" for t in spec["target_symbols"]]
    lines += ["", "シグネチャ:"] + [f"- {s['symbol']}" for s in spec["signatures"]] + ["", "テストの指針:", o["guidance"]]
    for title, key in (("事前条件", "preconditions"), ("事後条件", "postconditions"), ("不変量", "invariants")):
        lines += ["", f"{title}:"] + [f"- {x}" for x in c.get(key, [])]
    if problems:
        lines += ["", "前回の受入テストの違反:"] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def module_rel(module: str) -> str:
    return module.replace("\\", "/").removesuffix(".py").replace(".", "/") + ".py"


def _def(spec: dict, symbol: str, name: str, pad: str, method: bool) -> list[str]:
    got = next((s["params"] for s in spec["signatures"] if s["symbol"] == symbol), [])
    names = [p["name"] if isinstance(p, dict) else str(p) for p in got]
    if method and names[:1] != ["self"]:
        names.insert(0, "self")
    return [f"{pad}def {name}({', '.join(names)}):", f"{pad}    raise NotImplementedError"]


def stub_source(spec: dict, module: str) -> str:
    want, top = module_rel(module), {}
    for t in spec["target_symbols"]:
        name, kind = t["name"], t["kind"]
        if module_rel(t["module"]) != want or kind == "module":
            continue
        if kind == "method" and "." in name:
            cls, _, meth = name.rpartition(".")
            top.setdefault(cls, {})[meth] = name
        elif kind == "class":
            top.setdefault(name, {})
        else:
            top[name] = None
    blocks = []
    for name, methods in top.items():
        body = [x for meth, symbol in (methods or {}).items() for x in _def(spec, symbol, meth, "    ", True)]
        block = _def(spec, name, name, "", False) if methods is None else [f"class {name}:"] + (body or ["    raise NotImplementedError"])
        blocks.append("\n".join(block))
    return "\n\n\n".join(blocks) + "\n" if blocks else ""


def place(spec: dict, accept: str, wt: str) -> list[str]:
    placed = ["tests/" + os.path.basename(accept)]
    try:
        inject(accept, wt)
        for module in dict.fromkeys(module_rel(t["module"]) for t in spec["target_symbols"]):
            path = os.path.join(wt, *module.split("/"))
            if os.path.exists(path):
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            placed.append(module)
            Path(path).write_text(stub_source(spec, module), encoding="utf-8")
    except BaseException:
        discard(wt, placed)
        raise
    return placed


def discard(wt: str, placed: list[str]) -> None:
    for rel in placed:
        Path(wt, *rel.split("/")).unlink(missing_ok=True)


def compile_problems(accept: str) -> list[str]:
    try:
        compile(Path(accept).read_bytes(), accept, "exec")
        return []
    except (OSError, SyntaxError, ValueError) as e:
        return [f"受入テストを compile できません（読めない・構文が壊れています）: {type(e).__name__}: {e}"]


def red_problems(code: int, out: str) -> list[str]:
    if code == 0:
        return ["実装の前に 1 件も落ちません（受入テストは Red でなければなりません）"]
    if any(m in out for m in MARKERS) and not any(line.startswith("Ran ") for line in out.splitlines()):
        return ["受入テストを import できません（実装の前に落ちるのは、import の失敗ではなく、検査の失敗でなければなりません）"]
    return []


def gate_c(spec: dict, accept: str, wt: str, run: object) -> list[str]:
    if (problems := compile_problems(accept)):
        return problems
    placed = []
    try:
        placed = place(spec, accept, wt)
        code, out = run([sys.executable, "-m", "unittest", "tests." + Path(accept).stem], wt)
        return red_problems(code, out)
    finally:
        discard(wt, placed)


def with_accept(spec: dict, rel: str) -> dict:
    out = copy.deepcopy(spec)
    allowed = out["edit_boundary"]["allowed_files"]
    if not any(hline_spec.matches(rel, p) for p in allowed):
        allowed.append(rel)
    return out


def inject(accept: str, wt: str) -> str:
    dest = os.path.join(wt, "tests", os.path.basename(accept))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    Path(dest).write_bytes(Path(accept).read_bytes())
    return "tests/" + os.path.basename(accept)


def leak_problems(text: str, accept: str) -> list[str]:
    try:
        source = Path(accept).read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    name = os.path.basename(accept)
    out = [f"受入テストの行が入力に漏れています: {x[:60]}" for x in dict.fromkeys(s.strip() for s in source.splitlines())
           if len(x) >= MIN_LEAK_LINE and x in text]
    return out + ([f"受入テストのファイル名が入力に漏れています: {name}"] if name in text else [])


def red_agent(cfg: dict) -> dict:
    agent = cfg["decomposer"]
    model_pin.require(agent, "config/hline.json の decomposer（受入テストの合成）")
    return agent
