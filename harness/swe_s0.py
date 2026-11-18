"""S0：SWE-EVO 版の事前再生ゲート（実験と計画 §6-2。利用枠 0）。

開発用の課題の t=0（リリースのタグ）と、正解のパッチ（コードの変更だけ。テストのファイルの差分は外す）の状態に、
SWE-EVO 版の第 1 関門（pyflakes と、R0 の選びのテストと、後からの t=0 の対照）を当て、時間・選んだファイルの数・
正解の誤った押し返しを測る。WSL の SWE-EVO の venv で走らせる。

    /opt/swe-evo/venv/bin/python -m harness.swe_s0 run --tasks <ID>... --out <置き場>

- 公式の評価（SWE-EVO の run_evaluation）と同じく、パッチの前に `git reset --hard <base_commit>` で作業ツリーを
  始まりのコミットに戻す（pydantic のイメージは pdm.lock と pyproject.toml が変わったままで、正解のパッチが当たらない）。
- 環境の準備は、公式の評価のスクリプトの、テストの前の行（版を決めた pip の install など）をそのまま走らせる。
  公式の評価と同じく、正解のパッチを当ててから走らせる。準備はネットワーク越し（ブリッジ）で行い、終わったら
  ネットワークを外してから関門を当てる。
- 公式の評価は、準備と同じシェルでテストを走らせる。そのため準備の終わりの環境（export -p）を残し、probe の前に、
  conda activate の後で読み直す（pydantic の準備は PATH の先頭に $HOME/.local/bin を足す）。
- t=0 のコンテナは、t=0 の静的な検出の基準・テストの import の読み・後からの対照に使う（課題ごとに 1 つ）。
- 群 1 は、正解のパッチからテストのファイルを外すので空。群 2（台帳のモジュール）は、S0 には台帳が無いので空。
  測るのは群 3（変えたモジュールを import する既存のテスト）。
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exitcode  # noqa: E402
import fb_gate_probe as gate  # noqa: E402
from fb_replay import (PROBE_DIR, PROBE_FILES, R0_BUDGET, R0_FILE_TIMEOUT, TOOLS_DIR, TOOLS_SRC, Box,  # noqa: E402
                       clear_bytecode, keysets, probe, r0_order, sh)

ARROW = "/opt/swe-evo/SWE-EVO/hf_out/hf_dataset/test/data-00000-of-00001.arrow"
CONDA = "source /opt/miniconda3/bin/activate && conda activate testbed && cd /testbed"
PER_TEST_TIMEOUT = 60      # pytest-timeout があるイメージで、1 項目に付ける上限
GATE_LIMIT = 600           # 判定線 2 の 1 回の関門の上限（静的・テスト・後からの対照）
ENV_FILE = "/tmp/s0_env.sh"   # 準備の終わりの環境
PRELUDE = f"source {ENV_FILE}"


def load_rows(ids):
    from datasets import Dataset
    want = set(ids)
    rows = {x["instance_id"]: x for x in Dataset.from_file(ARROW) if x["instance_id"] in want}
    missing = sorted(want - set(rows))
    if missing:
        raise SystemExit(f"tasks not in the dataset: {len(missing)}")
    return rows


def setup_lines(eval_script):
    """公式の評価のスクリプトの、テストの前の準備の行：最後の `git ... diff <base>` の行の後から、
    `: '>>>>> Start Test Output'` の前まで（空の行を除く）。"""
    lines = eval_script.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r"^git (-c \S+ )?diff ", ln)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(": '>>>>> Start Test Output'")]
    if not starts or not ends or ends[0] <= starts[-1]:
        raise ValueError("eval script has no setup section")
    return [ln for ln in lines[starts[-1] + 1:ends[0]] if ln.strip()]


def setup_command(lines, env_file=ENV_FILE):
    """準備の行を 1 つの bash で走らせ、終わりの環境を env_file に残す命令。終わりの符号は、準備の最後の行の符号。"""
    return "\n".join(["cd /testbed", *lines, "rc=$?", f"export -p > {env_file}", "exit $rc"])


def testbed_status(box):
    """/testbed の git status（追跡外と無視のファイルも。ディレクトリはまとめて 1 行）。準備が作ったファイルを見るため。"""
    code, out, err = box.exec_split("cd /testbed && git status --porcelain --ignored")
    return sorted(ln for ln in out.splitlines() if ln.strip()) if code == 0 else [f"error: {err.strip()[-200:]}"]


def split_patch(patch):
    """(コードの変更だけのパッチ, 外したテストのファイル, コードのファイル)。テストのファイルは probe の is_test_file。"""
    keep, dropped, files = [], [], []
    for block in re.split(r"(?m)^(?=diff --git )", patch or ""):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", block)
        if not m:
            if block.strip():
                keep.append(block)
            continue
        if gate.is_test_file(m.group(2)):
            dropped.append(m.group(2))
        else:
            keep.append(block)
            files.append(m.group(2))
    return "".join(keep), dropped, files


def exception_type(msg):
    """落ちの 1 行から例外の型（後からの対照で、t=0 の落ちと比べる）。"""
    m = (msg or "").strip()
    if not m:
        return ""
    if m.startswith("assert "):
        return "AssertionError"
    head = m.split(":", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z_][\w.]*", head):
        return head.rsplit(".", 1)[-1]
    return head.split()[0]


def decide(gold_failed, t0_failed):
    """後からの対照：t=0 で同じ項目が同じ例外の型で落ちていた落ちは押し返さない。(押し返す, 外した)。"""
    at_t0 = {}
    for x in t0_failed:
        at_t0.setdefault(x["node"], exception_type(x.get("msg")))
    pushed, excused = [], []
    for x in gold_failed:
        same = x["node"] in at_t0 and at_t0[x["node"]] == exception_type(x.get("msg"))
        (excused if same else pushed).append(x)
    return pushed, excused


def test_cmd_of(row):
    cmd = row["test_cmds"]
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


def start_box(row, tag, patch=None):
    """課題のイメージのコンテナを起こし、（パッチを当てて）公式の準備を走らせ、ネットワークを外し、probe を写す。"""
    box, info = prepare_box(row, tag, patch)
    try:
        code, out = sh(["docker", "network", "disconnect", "bridge", box.name])
        if code != 0:
            raise RuntimeError(f"network disconnect failed: {out[-200:]}")
        sh(["docker", "cp", f"{TOOLS_SRC}/.", f"{box.name}:{TOOLS_DIR}"])
        box.exec(f"mkdir -p {PROBE_DIR}")
        for p in PROBE_FILES:
            box.put(f"{PROBE_DIR}/{p.name}", p.read_text(encoding="utf-8"))
        _, out = box.exec(f"{CONDA} && {PRELUDE} && command -v python pytest")
        info["test_tools"] = out.split()
        code, _ = box.exec(f"{CONDA} && {PRELUDE} && python -c 'import pytest_timeout'")
        info["pytest_timeout"] = code == 0
        return box, info
    except Exception:
        box.close()
        raise


def prepare_box(row, tag, patch=None, volumes=None, prefix="s0"):
    """課題のイメージのコンテナを起こし（ブリッジのネットワーク）、`git reset --hard <base_commit>` の後に（パッチを
    当てて）公式の準備を走らせ、準備の終わりの環境を ENV_FILE に残す。ネットワークはブリッジのまま返す。
    volumes は [(ホストの置き場, コンテナの置き場, "ro"/"rw")]（§6-3 の実行口が、許可リストのプロキシのソケットに使う）。"""
    from swebench.harness.test_spec.test_spec import make_test_spec
    image = f"sweb.eval.x86_64.{row['instance_id']}:latest"
    box = Box(image, f"{prefix}-{tag}-{int(time.time())}", {}, network="bridge", volumes=volumes)
    info = {"image": image}
    try:
        info["reset_discarded"] = [ln for ln in testbed_status(box) if not ln.startswith(("!!", "??"))]
        code, out = box.exec(f"cd /testbed && git reset -q --hard {row['base_commit']}")
        if code != 0:
            raise RuntimeError(f"git reset failed: {out[-300:]}")
        if patch:
            box.put("/tmp/cand.patch", patch if patch.endswith("\n") else patch + "\n")
            code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/cand.patch && rm -f /tmp/cand.patch")
            if code != 0:
                raise RuntimeError(f"patch apply failed: {out[-300:]}")
        info["applied"] = bool(patch)
        lines = setup_lines(make_test_spec(row, namespace=None).eval_script)
        before = testbed_status(box)
        t = time.time()
        code, out = box.exec(setup_command(lines), timeout=1800)
        tail = "\n".join(ln for ln in out.splitlines() if "Running pip as the 'root' user" not in ln)
        info.update({"setup_lines": lines, "setup_rc": code, "setup_secs": round(time.time() - t, 1),
                     "setup_tail": tail[-600:]})
        if box.exec(f"test -s {ENV_FILE}")[0] != 0:
            raise RuntimeError("setup left no environment file")
        clear_bytecode(box)
        info["setup_created"] = sorted(set(testbed_status(box)) - set(before))[:50]
        return box, info
    except Exception:
        box.close()
        raise


def run_task(row, out_dir):
    iid = row["instance_id"]
    pkg = row["repo"].split("/")[-1]
    code_patch, dropped, changed = split_patch(row["patch"])
    res = {"id": iid, "package": pkg, "test_files_dropped_from_gold": dropped, "changed_code_files": len(changed)}
    t_all = time.time()
    box0, info0 = start_box(row, "t0")
    try:
        res["t0_setup"] = info0
        base = probe(box0, {"root": "/testbed", "packages": [pkg], "checks": ["undefined", "test_imports"],
                            "pyflakes_dir": TOOLS_DIR}, prelude=PRELUDE)
        res["t0_static"] = {"found": len(base.get("undefined") or []), "secs": (base.get("secs") or {}).get("undefined"),
                            "error": base.get("error")}
        imports_t0 = (base.get("test_imports") or {}).get("files") or {}
        sel, order = r0_order(imports_t0, [], [c for c in changed if c.endswith(".py")])
        res["selected"] = {g: len(sel[g]) for g in ("own", "ledger", "changed")}
        res["test_files_t0"] = len(imports_t0)
        boxg, infog = start_box(row, "gold", code_patch)
        try:
            res["gold_setup"] = infog
            spec = {"root": "/testbed", "packages": [pkg], "checks": ["undefined", "tests"], "test_files": order,
                    "pyflakes_dir": TOOLS_DIR, "test_cmd": test_cmd_of(row),
                    "timeout_one": PER_TEST_TIMEOUT if infog["pytest_timeout"] else None,
                    "file_timeout": R0_FILE_TIMEOUT, "test_budget": R0_BUDGET, "strict_budget": True}
            gold = probe(boxg, spec, prelude=PRELUDE)
            _, procs_g = boxg.exec("ps -eo comm= | sort | uniq -c | sort -rn")
        finally:
            boxg.close()
        tests = gold.get("tests") or {}
        failed_files = sorted({x["node"].split("::")[0] for x in tests.get("failed") or []})
        t = time.time()
        control = {}
        if failed_files:
            control = probe(box0, {"root": "/testbed", "packages": [pkg], "checks": ["tests"], "test_files": failed_files,
                                   "pyflakes_dir": TOOLS_DIR, "test_cmd": test_cmd_of(row),
                                   "timeout_one": spec["timeout_one"], "file_timeout": R0_FILE_TIMEOUT,
                                   "test_budget": 3600}, prelude=PRELUDE)
        control_secs = round(time.time() - t, 1)
        _, procs_0 = box0.exec("ps -eo comm= | sort | uniq -c | sort -rn")
    finally:
        box0.close()
    new_static = sorted(keysets(gold)["undefined"] - keysets(base)["undefined"], key=str)
    pushed, excused = decide(tests.get("failed") or [], (control.get("tests") or {}).get("failed") or [])
    secs = gold.get("secs") or {}
    gate_secs = round((secs.get("undefined") or 0) + (secs.get("tests") or 0) + control_secs, 1)
    rc_counts, items = {}, {}
    for v in (tests.get("files") or {}).values():
        rc_counts[str(v.get("rc"))] = rc_counts.get(str(v.get("rc")), 0) + 1
        for k, n in (v.get("counts") or {}).items():
            items[k] = items.get(k, 0) + n
    res.update({
        "gold_static_new": [list(k) for k in new_static], "gold_tests": {"ran": len(tests.get("files") or {}),
        "not_run": len(tests.get("not_run") or []), "rc": rc_counts, "items": items,
        "failed": len(tests.get("failed") or []),
        "secs": secs.get("tests"), "killed_leftovers": tests.get("killed_leftovers")},
        "control": {"files": len(failed_files), "secs": control_secs, "failed_at_t0": len((control.get("tests") or {}).get("failed") or [])},
        "pushed": [{"node": x["node"], "msg": (x.get("msg") or "")[:200], "type": exception_type(x.get("msg")),
                    "class": gate.failure_class(x.get("msg"), [pkg])} for x in pushed],
        "excused": len(excused), "gold_pushed": bool(new_static or pushed),
        "excused_items": [{"node": x["node"], "type": exception_type(x.get("msg"))} for x in excused][:50],
        "gate_secs": gate_secs, "gate_within_limit": gate_secs <= GATE_LIMIT,
        "static_secs": {"t0": res["t0_static"]["secs"], "gold": secs.get("undefined")},
        "orphans": {"gold_before": gold.get("orphans_before"), "gold_after": gold.get("orphans_after"),
                    "t0_after": control.get("orphans_after") if control else base.get("orphans_after")},
        "probe_errors": [e for e in (base.get("error"), gold.get("error"), control.get("error") if control else None) if e],
        "procs_after": {"gold": procs_g.strip(), "t0": procs_0.strip()}, "wall": round(time.time() - t_all, 1)})
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"{iid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def cmd_run(args):
    rows = load_rows(args.tasks)
    for iid in args.tasks:
        out = Path(args.out) / f"{iid}.json"
        if out.exists():
            print(f"{iid} skip", flush=True)
            continue
        r = run_task(rows[iid], args.out)
        print(f"{iid} gold_pushed={r['gold_pushed']} static_new={len(r['gold_static_new'])} pushed_tests={len(r['pushed'])} "
              f"excused={r['excused']} selected={r['selected']} ran={r['gold_tests']['ran']} not_run={r['gold_tests']['not_run']} "
              f"gate={r['gate_secs']}s (static {r['static_secs']['gold']}s tests {r['gold_tests']['secs']}s control "
              f"{r['control']['secs']}s) setup_t0={r['t0_setup']['setup_secs']}s rc={r['t0_setup']['setup_rc']} "
              f"setup_gold={r['gold_setup']['setup_secs']}s rc={r['gold_setup']['setup_rc']} wall={r['wall']}s "
              f"orphans={r['orphans']} errors={r['probe_errors']}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.swe_s0", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tasks", nargs="+", required=True)
    r.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {"run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
