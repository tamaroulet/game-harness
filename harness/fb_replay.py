"""E-1 の段 a：記録のパッチに第 1 関門を当てて再生する（実験と計画 §5-3）。利用枠 0。

WSL の FeatureBench の venv で、FeatureBench の評価と同じ手順で課題の始まりの状態（隠しのパッチを当て、公式のテストの
ファイルを消した状態）を作り、始まり・正解・A のパッチを 1 つずつ当てて、箱の中で harness/fb_gate_probe.py を走らせる。
同じ箱で、公式のテストの項目の根拠の分け（§5-2、harness/fb_ground.py）も計算する。

    python -m harness.fb_replay run --tasks <ID の並びのファイル> --runs <ランの置き場>... --gold <正解の評価の置き場> --out <置き場> [--jobs 3]
    python -m harness.fb_replay summary --out <置き場> [--runs ...] [--gold ...]
    python -m harness.fb_replay scan --tasks <ID の並びのファイル> --out <置き場> [--only pandas]
    python -m harness.fb_replay r0 --tasks <ID の並び> --runs <ランの置き場>... --stage <run3> --imports <scan の置き場> --out <置き場> [--fill <fill の置き場>]
    python -m harness.fb_replay fill --tasks <ID の並び> --stage <run3> --imports <scan の置き場> --out <置き場> [--only http_headers]
    python -m harness.fb_replay recheck --tasks <ID の並び> --runs <ランの置き場>... --stage <run3> --imports <scan の置き場> --out <置き場> [--only mlflow]

scan はイメージのテストのファイルごとの import を読み（import もテストもしない）、r0 は段 a の記録に、正解を使わない
第 1 関門の読み R0（実験と計画 §5-6）を当てて数える（G0-3。コンテナは使わない）。fill は、正解の状態で R0 が選ぶ
ファイルのうち run3 に結果が無いものを走らせる（記録不足を埋める）。

箱の網は切る（--network none）。課題の ID と結果は <置き場>/<ID>.json に書く。
"""
import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exitcode  # noqa: E402
import fb_gate_probe as gate  # noqa: E402
import fb_ground  # noqa: E402

PROBE_FILES = [HERE / "fb_gate_probe.py", HERE / "exitcode.py", HERE / "job_object.py"]
PROBE_DIR = "/tmp/fbgate"
TOOLS_SRC = "/opt/fb/tools/pyflakes_pkg"
TOOLS_DIR = "/opt/harness_tools"
CONDA = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && cd /testbed"
WHITE_RUNS = {"runs_x01w", "runs_x01l", "runs_x02_A"}
STATIC = ["undefined", "imports", "attrs"]
MEMORY = "6g"
log = logging.getLogger("fb_replay")


SHA_RE = re.compile(r"[0-9a-f]{40}")


def sh_split(args, data=None, timeout=None):
    """(終わりの符号, 標準出力, 標準エラー)。出力を値として読むときは、標準エラーを混ぜない
    （git add が標準エラーに出す改行の警告が、読んだ値に入ったことがある）。"""
    p = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", errors="replace"), p.stderr.decode("utf-8", errors="replace")


def sh(args, data=None, timeout=None):
    """(終わりの符号, 標準出力と標準エラーをつないだもの)。記録と誤りの文に使う。値として読まない。"""
    code, out, err = sh_split(args, data, timeout)
    return code, out + err


def commit_sha(stdout):
    """git rev-parse の標準出力から、コミットの SHA だけを取る。1 行の 40 桁でなければ止める。"""
    lines = stdout.strip().splitlines()
    if len(lines) != 1 or not SHA_RE.fullmatch(lines[0].strip()):
        raise RuntimeError(f"not a commit sha: {stdout.strip()[:120]!r}")
    return lines[0].strip()


class Box:
    """課題の像から起こした箱。exec は (終わりの符号, 出力)。"""

    def __init__(self, image, name, rt, network="none", volumes=None):
        self.name = name
        args = ["docker", "run", "-d", "--network", network, "--name", name, "-u", "root", "-w", "/testbed",
                "--memory", MEMORY, "--memory-swap", MEMORY]   # 箱ごとの上限（並べた箱で WSL のメモリを使い切らない）
        for src, dst, mode in volumes or []:
            args += ["-v", f"{src}:{dst}:{mode}"]
        if rt.get("shm_size"):
            args += ["--shm-size", rt["shm_size"]]
        for k, v in (rt.get("env_vars") or {}).items():
            args += ["-e", f"{k}={v}"]
        code, out = sh(args + [image, "/bin/bash", "-c", "sleep infinity"])
        if code != 0:
            raise RuntimeError(f"docker run failed: {out[-400:]}")
        self.exports = "; ".join(rt.get("env_exports") or [])

    def exec(self, command, timeout=3600, data=None):
        cmd = f"{self.exports}; {command}" if self.exports else command
        try:
            return sh(["docker", "exec", "-i", self.name, "bash", "-c", cmd], data=data, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    def exec_split(self, command, timeout=600):
        """標準出力と標準エラーを分けて返す。値を読む命令に使う。"""
        cmd = f"{self.exports}; {command}" if self.exports else command
        return sh_split(["docker", "exec", "-i", self.name, "bash", "-c", cmd], timeout=timeout)

    def put(self, path, text):
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(text.encode("utf-8"))
            tmp = f.name
        try:
            code, out = sh(["docker", "cp", tmp, f"{self.name}:{path}"])
            if code != 0:
                raise RuntimeError(f"docker cp failed: {out[-300:]}")
        finally:
            os.unlink(tmp)

    def get(self, path):
        with tempfile.TemporaryDirectory() as d:
            code, out = sh(["docker", "cp", f"{self.name}:{path}", d])
            if code != 0:
                return None
            return (Path(d) / Path(path).name).read_text(encoding="utf-8")

    def close(self):
        sh(["docker", "rm", "-f", self.name])


def load_rows(ids):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from datasets import load_dataset
    rows = {x["instance_id"]: x for x in load_dataset("LiberCoders/FeatureBench", split="fast", revision="v1.1")
            if x["instance_id"] in set(ids)}
    missing = sorted(set(ids) - set(rows))
    if missing:
        raise SystemExit(f"tasks not in the dataset: {len(missing)}")
    return rows


def repo_settings(row):
    rs = row["repo_settings"]
    return json.loads(rs) if isinstance(rs, str) else (rs or {})


def f2p_list(row):
    v = row["FAIL_TO_PASS"]
    return json.loads(v) if isinstance(v, str) else list(v)


def restore(box):
    """/root/my_repo から /testbed を戻す（FeatureBench の Level 1 の初めの手順。FeatureBench は cp -r でコピーする）。
    中身が一字一句同じ（diff -r で差が無い）ならコピーしない。コピーするとファイルの mtime が新しくなり、meson-python の
    editable install（pandas）が、検査の import のたびに C/C++ の全体の再ビルド（ninja の 154 ステップ）を始める。
    違うのは mtime だけなので、ファイルの中身は FeatureBench の手順と同じ。戻し方（same・copied）を返し、結果に記録する。"""
    code, _, err = box.exec_split("diff -rq --no-dereference /testbed /root/my_repo >/dev/null")
    if code == 0:
        mode, cmd = "same", "rm -rf /root/my_repo"
    elif code == 1:
        mode = "copied"
        cmd = f"{CONDA.rsplit(' && cd', 1)[0]} && rm -rf /testbed/* && cp -r /root/my_repo/* /testbed/ && rm -rf /root/my_repo"
    else:
        raise RuntimeError(f"restore check failed (rc {code}): {err[-300:]}")
    code, out = box.exec(cmd)
    if code != 0:
        raise RuntimeError(f"restore failed: {out[-300:]}")
    return mode


def prepare(box, row):
    """FeatureBench の Level 1 の初期化（テストを隠す条件）と同じ手順で、始まりの状態を作る。
    (始まりのコミット, 戻し方) を返す。"""
    from featurebench.environment_fixes import apply_environment_fixes
    from featurebench.utils.identifiers import testbed_test_file
    mode = restore(box)
    box.put("/tmp/mask.patch", row["patch"])
    code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/mask.patch && rm -f /tmp/mask.patch")
    if code != 0:
        raise RuntimeError(f"mask apply failed: {out[-300:]}")
    for t in f2p_list(row):
        box.exec(f"rm -f -- {shlex.quote(testbed_test_file(t))}")
    repo = str(repo_settings(row).get("repository") or row.get("repo") or "")
    if not apply_environment_fixes(repo, lambda c: box.exec(c), log):
        raise RuntimeError("environment fixes failed")
    clear_bytecode(box)
    code, out = box.exec('cd /testbed && rm -rf .git && git init -q && git config user.email "fb@bench.com" && '
                         'git config user.name "FeatureBench" && git add -A && git commit -qm base --allow-empty')
    if code != 0:
        raise RuntimeError(f"git init failed: {out[-300:]}")
    code, stdout, err = box.exec_split("cd /testbed && git rev-parse --verify HEAD")
    if code != 0:
        raise RuntimeError(f"rev-parse failed: {err[-300:]}")
    base = commit_sha(stdout)
    sh(["docker", "cp", f"{TOOLS_SRC}/.", f"{box.name}:{TOOLS_DIR}"])
    box.exec(f"mkdir -p {PROBE_DIR}")
    for p in PROBE_FILES:
        box.put(f"{PROBE_DIR}/{p.name}", p.read_text(encoding="utf-8"))
    reset(box, base)
    return base, mode


def reset(box, base):
    """/testbed を始まりのコミットに戻し、戻ったことを確認する：HEAD が base で、変更も追跡外のファイルも無い。
    戻らなければ止める（前の候補の変更の上に次の候補を当てない）。"""
    code, out = box.exec(f"cd /testbed && git reset -q --hard {base} && git clean -qfd")
    if code != 0:
        raise RuntimeError(f"reset failed: {out[-300:]}")
    code, stdout, err = box.exec_split("cd /testbed && git rev-parse HEAD && git status --porcelain --untracked-files=all")
    lines = stdout.strip().splitlines()
    if code != 0 or not lines or lines[0].strip() != base or len(lines) > 1:
        raise RuntimeError(f"testbed not clean after reset: {lines[:4]} {err[-200:]}")
    clear_bytecode(box)


def clear_bytecode(box):
    box.exec("cd /testbed && find . -type d -name __pycache__ -prune -exec rm -rf -- {} + ; find . -name '*.pyc' -delete")


def statement_paths(statement):
    import re
    out = []
    for m in re.finditer(r"^\s*Path:\s*`?([^\s`]+)`?\s*$", statement or "", re.M):
        p = m.group(1)
        p = p[len("/testbed/"):] if p.startswith("/testbed/") else p.lstrip("/")
        if p not in out:
            out.append(p)
    return out


def packages_of(row):
    rs = repo_settings(row)
    name = str(rs.get("library_name") or "").strip()
    repo = str(rs.get("repository") or row.get("repo") or "")
    return [name or repo.split("/")[-1]]


def probe(box, spec, timeout=5400, prelude=""):
    """prelude は conda activate の後・probe の前に走らせる命令（SWE-EVO の S0 が、準備の後の環境を読み直すため。
    Box.exports は conda activate の前に走るので、その後の PATH を上書きできない）。"""
    box.put("/tmp/spec.json", json.dumps(spec))
    pre = f"{prelude} && " if prelude else ""
    code, out = box.exec(f"{CONDA} && {pre}python {PROBE_DIR}/fb_gate_probe.py /tmp/spec.json /tmp/out.json",
                         timeout=timeout)
    text = box.get("/tmp/out.json")
    box.exec("rm -f /tmp/out.json")
    if text is None:
        return {"error": f"probe failed (rc {code}): {out[-500:]}"}
    return json.loads(text)


def a_patches(run_dirs, ids):
    """記録の A のパッチ：[(ラベル, ID, パッチ, 解決, 落ちた F2P の項目)]。"""
    out = []
    for rd in run_dirs:
        label = Path(rd).name
        for o in sorted(Path(rd).glob("*/output.jsonl")):
            for line in o.read_text(encoding="utf-8").splitlines():
                d = json.loads(line)
                iid = d["instance_id"]
                if iid not in ids:
                    continue
                rep = o.parent / "eval_outputs" / iid / "attempt-1" / "report.json"
                r = json.loads(rep.read_text(encoding="utf-8"))[iid] if rep.exists() else {}
                st = (r.get("tests_status") or {}).get("FAIL_TO_PASS") or {}
                out.append({"label": label, "id": iid, "patch": d.get("model_patch") or "",
                            "resolved": bool(r.get("resolved")), "f2p_failed": st.get("failure") or [],
                            "f2p_passed": st.get("success") or [], "white": label in WHITE_RUNS})
    return out


def gold_items(gold_dir, iid):
    rep = Path(gold_dir) / "eval_outputs" / iid / "attempt-1" / "report.json"
    if not rep.exists():
        return []
    st = (json.loads(rep.read_text(encoding="utf-8"))[iid].get("tests_status") or {}).get("FAIL_TO_PASS") or {}
    return sorted(set((st.get("success") or []) + (st.get("failure") or [])))


def ground(box, row, gold_dir, iid):
    """§5-2 の根拠の分け。A のパッチと評価の結果は使わない（項目の並びだけを正解の評価の記録から取る）。"""
    removed = fb_ground.removed_names(row["patch"])
    tests = fb_ground.original_tests(row["test_patch"])
    scopes = {}
    for path, src in tests.items():
        try:
            for k, nodes in fb_ground.test_scopes(src).items():
                scopes[f"{path}::{k}"] = nodes
        except SyntaxError:
            continue
    literals = fb_ground.literals_to_check({k: v for k, v in scopes.items()})
    res = probe(box, {"root": "/testbed", "packages": packages_of(row), "checks": ["refs"],
                      "names": sorted(removed), "literals": literals})
    refs = res.get("refs") or {}
    src_names, src_lits = set(refs.get("names") or []), set(refs.get("literals") or [])
    funcs = {}
    for k, nodes in scopes.items():
        label, why = fb_ground.classify(row["problem_statement"], removed, src_names, src_lits, nodes)
        funcs[k] = {"label": label, "why": why}
    items = {}
    for item in gold_items(gold_dir, iid):
        node = fb_ground.node_id(item)
        path = node.split("::", 1)[0]
        key = fb_ground.function_key(node)
        f = funcs.get(f"{path}::{key}")
        items[node] = f["label"] if f else "不明"
    counts = {}
    for lab in items.values():
        counts[lab] = counts.get(lab, 0) + 1
    return {"removed_names": len(removed), "mask_files": sorted(fb_ground.mask_files(row["patch"])),
            "removed": sorted(removed), "functions": funcs, "items": items, "counts": counts,
            "refs_error": res.get("error")}


def gold_patch(row):
    from featurebench.harness.utils import preprocess_hf_patch
    return preprocess_hf_patch(row["patch"], row["FAIL_TO_PASS"])


def strip_hidden(patch, row):
    from featurebench.harness.utils import parse_fail_to_pass_paths, remove_patch_operations_for_paths
    return remove_patch_operations_for_paths(patch, parse_fail_to_pass_paths(row["FAIL_TO_PASS"]))


def candidate(box, row, base, name, patch, checks, settings):
    from featurebench.utils.identifiers import testbed_test_file
    t0 = time.time()
    reset(box, base)
    applied = True
    if patch is not None:
        box.put("/tmp/cand.patch", patch if patch.endswith("\n") else patch + "\n")
        code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/cand.patch")
        applied = code == 0
        if not applied:
            return {"name": name, "applied": False, "apply_error": out[-400:]}
    spec = {"root": "/testbed", "packages": packages_of(row), "ledger_paths": statement_paths(row["problem_statement"]),
            "base": base, "checks": checks, "pyflakes_dir": TOOLS_DIR, "test_cmd": settings["test_cmd"],
            "timeout_one": settings["timeout_one"], "file_timeout": 300, "test_budget": 600, "import_budget": 900,
            "hidden_tests": sorted({testbed_test_file(t) for t in f2p_list(row)})}
    res = probe(box, spec)
    res.update({"name": name, "applied": applied, "wall": round(time.time() - t0, 1)})
    return res


def run_task(iid, row, cands, gold_dir, out_dir):
    out_path = Path(out_dir) / f"{iid}.json"
    if out_path.exists():
        return iid, "skip"
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    rs = repo_settings(row)
    settings = {"test_cmd": rs.get("test_cmd") or "pytest -rA -p no:cacheprovider --color=no",
                "timeout_one": rs.get("timeout_one") or 10}
    box = Box(row["image_name"], f"fbreplay-{os.getpid()}-{abs(hash(iid)) % 10**6}-{int(time.time())}", parse_docker_runtime_config(rs))
    try:
        base, mode = prepare(box, row)
        result = {"id": iid, "base": base, "restore": mode, "ground": ground(box, row, gold_dir, iid), "candidates": []}
        result["candidates"].append(candidate(box, row, base, "start", None, STATIC, settings))
        result["candidates"].append(candidate(box, row, base, "gold", gold_patch(row), STATIC + ["tests"], settings))
        for c in cands:
            p = strip_hidden(c["patch"], row) if c["white"] else c["patch"]
            r = candidate(box, row, base, c["label"], p, STATIC + ["tests"], settings)
            r.update({"resolved": c["resolved"], "f2p_failed": c["f2p_failed"], "white": c["white"]})
            result["candidates"].append(r)
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return iid, "ok"
    finally:
        box.close()


def cmd_run(args):
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = load_rows(ids)
    patches = a_patches(args.runs, set(ids))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    order = [i for i in ids if not args.only or i in args.only]

    def one(iid):
        try:
            return run_task(iid, rows[iid], [p for p in patches if p["id"] == iid], args.gold, args.out)
        except Exception as e:  # 1 課題の失敗で全体を止めない。記録して次へ
            (Path(args.out) / f"{iid}.error.txt").write_text(repr(e), encoding="utf-8")
            return iid, f"error: {e!r}"[:300]

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for iid, status in ex.map(one, order):
            print(f"{time.strftime('%H:%M:%S')} {iid.split('.')[0]}.{iid.split('.')[2]} {status}", flush=True)


def gold_retest(iid, row, out_dir):
    """A の状態で実際に走ったテストのファイルのうち、正解の状態で走らせていないものを、正解の状態で走らせる。
    A の状態での落ちが、パッチのせいか、環境のせいかを分けるため（正解の状態でも落ちるものは環境）。
    選んだが時間の上限で走らなかったファイルは、比べに使わないので当て直さない。"""
    path = Path(out_dir) / f"{iid}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    gold = next(c for c in d["candidates"] if c["name"] == "gold")
    done = set(((gold.get("tests") or {}).get("files") or {}))
    want = sorted({f for c in d["candidates"] if c["name"] not in ("start", "gold")
                   for f in ((c.get("tests") or {}).get("files") or {})} - done)
    if not want or d.get("gold_retest") is not None:
        return iid, f"skip ({len(want)} files)"
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    rs = repo_settings(row)
    settings = {"test_cmd": rs.get("test_cmd") or "pytest -rA -p no:cacheprovider --color=no",
                "timeout_one": rs.get("timeout_one") or 10}
    box = Box(row["image_name"], f"fbreplay-g-{os.getpid()}-{abs(hash(iid)) % 10**6}-{int(time.time())}", parse_docker_runtime_config(rs))
    try:
        base, _ = prepare(box, row)
        reset(box, base)
        box.put("/tmp/cand.patch", gold_patch(row))
        code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/cand.patch")
        if code != 0:
            raise RuntimeError(f"gold apply failed: {out[-300:]}")
        clear_bytecode(box)
        spec = {"root": "/testbed", "packages": packages_of(row), "checks": ["tests"], "test_files": want,
                "test_cmd": settings["test_cmd"], "timeout_one": settings["timeout_one"], "file_timeout": 300,
                "test_budget": 900, "pyflakes_dir": TOOLS_DIR}
        d["gold_retest"] = probe(box, spec)
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return iid, f"ok ({len(want)} files)"
    finally:
        box.close()


def reground(iid, row, gold_dir, out_dir):
    """始まりの状態を作り直して、根拠の分け（§5-2）だけを計算し直し、結果の ground を差し替える。第 1 関門の検査は走らせない。"""
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    path = Path(out_dir) / f"{iid}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    rs = repo_settings(row)
    box = Box(row["image_name"], f"fbreplay-r-{os.getpid()}-{abs(hash(iid)) % 10**6}-{int(time.time())}", parse_docker_runtime_config(rs))
    try:
        prepare(box, row)
        d["ground"] = ground(box, row, gold_dir, iid)
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return iid, f"ok (不明 {sum(1 for v in d['ground']['items'].values() if v == '不明')})"
    finally:
        box.close()


def cmd_reground(args):
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = load_rows(ids)
    for iid in ids:
        if not (Path(args.out) / f"{iid}.json").exists():
            continue
        try:
            _, status = reground(iid, rows[iid], args.gold, args.out)
        except Exception as e:  # 記録して次へ
            status = f"error: {e!r}"[:300]
        print(f"{time.strftime('%H:%M:%S')} {iid.split('.')[0]}.{iid.split('.')[2]} {status}", flush=True)


def cmd_goldtests(args):
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = load_rows(ids)
    for iid in ids:
        if not (Path(args.out) / f"{iid}.json").exists():
            continue
        try:
            _, status = gold_retest(iid, rows[iid], args.out)
        except Exception as e:  # 記録して次へ
            status = f"error: {e!r}"[:300]
        print(f"{time.strftime('%H:%M:%S')} {iid.split('.')[0]}.{iid.split('.')[2]} {status}", flush=True)


# ---------------------------------------------------------------- まとめ

def findings(c, packages):
    """1 つの状態の、第 1 関門の落ち。{確かめ: [落ち]}。import の任意の依存は数えない（§5-3）。"""
    out = {"undefined": c.get("undefined") or [],
           "imports": [f for f in ((c.get("imports") or {}).get("failed") or []) if not f.get("optional")],
           "attrs": (c.get("attrs") or {}).get("flags") or [],
           "tests": ((c.get("tests") or {}).get("failed") or [])}
    return out


def relevant(kind, f, removed, mfiles):
    import re
    words = set(re.findall(r"[A-Za-z_]\w*", json.dumps(f)))
    if words & removed:
        return True
    path = f.get("file") or ""
    if path and path in mfiles:
        return True
    mod = f.get("module")
    if mod:
        stem = mod.replace(".", "/")
        return any(p == stem + ".py" or p == stem + "/__init__.py" for p in mfiles)
    return False


def test_verdicts(c, gold_runs):
    """A の状態で落ちたテストを、正解の状態の結果で分ける：正解で通る（パッチのせい）・正解でも落ちる（環境）・正解で未走。"""
    out = {"pass_in_gold": [], "fail_in_gold": [], "not_run_in_gold": []}
    for f in ((c.get("tests") or {}).get("failed") or []):
        node = f["node"]
        if node in gold_runs["failed"]:
            out["fail_in_gold"].append(node)
        elif node.split("::", 1)[0] in gold_runs["files"]:
            out["pass_in_gold"].append(node)
        else:
            out["not_run_in_gold"].append(node)
    return out


def gold_runs_of(d):
    files, failed = set(), set()
    gold = next(c for c in d["candidates"] if c["name"] == "gold")
    for t in (gold.get("tests"), (d.get("gold_retest") or {}).get("tests")):
        t = t or {}
        files |= set((t.get("files") or {}))
        failed |= {f["node"] for f in (t.get("failed") or [])}
    return {"files": files, "failed": failed}


def keysets(c, generic=False):
    """1 つの状態の静的な落ちを、状態をまたいで比べられる鍵の集まりにする。
    generic=True は、正解を使わずに雑音を除く後付けの直しの案（§5-4 の限界）：任意の依存の skip（could not import）・
    __all__ の名の未定義（UndefinedExport）・パッケージのどこかで外から代入される属性を除く。"""
    und = {(f.get("file"), f.get("kind"), f.get("name")) for f in c.get("undefined") or []
           if not (generic and f.get("kind") == "UndefinedExport")}
    imp = {(f.get("module"), f.get("type")) for f in ((c.get("imports") or {}).get("failed") or [])
           if not f.get("optional") and not (generic and f.get("type") == "Skipped" and "could not import" in (f.get("msg") or ""))}
    stored = set((c.get("attrs") or {}).get("stored_anywhere") or []) if generic else set()
    att = {(f.get("file"), f.get("class"), f.get("attr")) for f in ((c.get("attrs") or {}).get("flags") or [])
           if f.get("attr") not in stored}
    return {"undefined": und, "imports": imp, "attrs": att}


def on_target(kind, key, removed, mfiles):
    import re
    words = set(re.findall(r"[A-Za-z_]\w*", " ".join(str(k) for k in key if k)))
    if words & removed:
        return True
    if kind in ("undefined", "attrs") and key[0] in mfiles:
        return True
    if kind == "imports" and key[0]:
        stem = key[0].replace(".", "/")
        return any(p == stem + ".py" or p == stem + "/__init__.py" for p in mfiles)
    return False


def hygiene(c):
    """1 つの状態の、検査の後始末と前提の記録：残りのビルドのステップと検査中の再ビルド・残ったプロセス・検査しなかったファイル。"""
    b = c.get("build") or {}
    return {"build_pending": [x.get("pending") for x in b.get("before") or []],
            "build_rebuilt": b.get("rebuilt") or [],
            "orphans": len(c.get("orphans_before") or []) + len(c.get("orphans_after") or []),
            "killed_leftovers": sum(((c.get(k) or {}).get("killed_leftovers") or 0) for k in ("imports", "tests")),
            "undefined_unchecked": c.get("undefined_unchecked") or []}


def summarize_task(d):
    g = d["ground"]
    removed, mfiles = set(g["removed"]), set(g["mask_files"])
    gold_runs = gold_runs_of(d)
    gold = next(c for c in d["candidates"] if c["name"] == "gold")
    gold_error = (gold.get("apply_error") or gold.get("error") or "") if (not gold.get("applied", True) or "error" in gold) else ""
    gk, gk_generic = keysets(gold), keysets(gold, generic=True)
    rows = []
    for c in d["candidates"]:
        if not c.get("applied", True) or "error" in c:
            rows.append({"state": c["name"], "error": (c.get("apply_error") or c.get("error") or "")[:200],
                         "hygiene": hygiene(c)})
            continue
        if gold_error:   # 正解の状態が壊れていると、正解との差の読みができない
            rows.append({"state": c["name"], "error": f"gold state failed: {gold_error[:150]}", "hygiene": hygiene(c)})
            continue
        ks, ks_generic = keysets(c), keysets(c, generic=True)
        absolute = {k: len(v) for k, v in ks.items()}
        generic = {k: len(v) for k, v in ks_generic.items()}
        beyond = {k: sorted(ks[k] - gk[k], key=str) for k in ks}
        beyond_n = {k: len(v) for k, v in beyond.items()}
        beyond_on = {k: sum(1 for key in v if on_target(k, key, removed, mfiles)) for k, v in beyond.items()}
        tv = test_verdicts(c, gold_runs) if c["name"] != "gold" else {"pass_in_gold": [], "fail_in_gold": [], "not_run_in_gold": []}
        tests = (c.get("tests") or {})
        by = {}
        for n in c.get("f2p_failed") or []:
            lab = g["items"].get(fb_ground.node_id(n), "不明")
            by[lab] = by.get(lab, 0) + 1
        caught_beyond = sum(beyond_n.values()) > 0 or len(tv["pass_in_gold"]) > 0
        rows.append({"state": c["name"], "resolved": c.get("resolved"), "white": c.get("white"),
                     "absolute": absolute, "absolute_tests_failed": len(tests.get("failed") or []),
                     "generic": generic, "beyond_gold": beyond_n, "beyond_gold_on_target": beyond_on,
                     "beyond_gold_sample": {k: [list(x) for x in v[:6]] for k, v in beyond.items()},
                     "tests_pass_in_gold": len(tv["pass_in_gold"]), "tests_fail_in_gold": len(tv["fail_in_gold"]),
                     "tests_not_run_in_gold": len(tv["not_run_in_gold"]),
                     "tests_files_run": len(tests.get("files") or {}), "tests_files_not_run": len(tests.get("not_run") or []),
                     "tests_msgs": sorted({(x.get("msg") or "")[:120] for x in (tests.get("failed") or [])
                                          if x["node"] in set(tv["pass_in_gold"])})[:6],
                     "caught_beyond_gold": caught_beyond, "f2p_failed_by_ground": by, "secs": c.get("secs"),
                     "hygiene": hygiene(c)})
    return {"id": d["id"], "base_ok": bool(SHA_RE.fullmatch(d.get("base") or "")), "restore": d.get("restore"),
            "ground_counts": g["counts"], "removed": len(removed), "mask_files": len(mfiles),
            "gold_generic": {k: len(v) for k, v in gk_generic.items()}, "gold_error": gold_error[:200], "states": rows}


def cmd_summary(args):
    out = [summarize_task(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(Path(args.out).glob("*.json"))]
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- R0 の再集計（G0-3。実験と計画 §5-6）

R0_BUDGET = 300          # 1 回の関門のテストの段の全体（test_budget）
R0_FILE_TIMEOUT = 300    # 1 ファイルの上限（残りの時間との短いほう）
FILE_RC_KNOWN = (0, 1, 5)


def image_tag(image):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", image.split("/")[-1])


def rel_path(path):
    return path[len("/testbed/"):] if path.startswith("/testbed/") else path.lstrip("/")


def cmd_scan(args):
    """課題のイメージの /testbed のテストのファイルごとの import を読む（R0 の群 2・3 の材料）。構文木で読むだけで、
    import もテストもしない。イメージごとに 1 回。--only で先に 1 つだけ読める（最も重い pandas で先に確かめる）。"""
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = load_rows(ids)
    first = {}
    for iid in ids:
        first.setdefault(rows[iid]["image_name"], iid)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for image, iid in first.items():
        if args.only and not any(o in image for o in args.only):
            continue
        path = Path(args.out) / f"imports_{image_tag(image)}.json"
        if path.exists():
            print(f"{image_tag(image)} skip", flush=True)
            continue
        row = rows[iid]
        t0 = time.time()
        box = Box(image, f"fbscan-{os.getpid()}-{int(time.time())}", parse_docker_runtime_config(repo_settings(row)))
        try:
            box.exec(f"mkdir -p {PROBE_DIR}")
            for p in PROBE_FILES:
                box.put(f"{PROBE_DIR}/{p.name}", p.read_text(encoding="utf-8"))
            res = probe(box, {"root": "/testbed", "packages": packages_of(row), "checks": ["test_imports"]})
            _, procs = box.exec("ps -eo comm= | sort | uniq -c | sort -rn")
        finally:
            box.close()
        res.update({"image": image, "wall": round(time.time() - t0, 1), "procs_after": procs.strip()})
        if "test_imports" not in res:
            print(f"{image_tag(image)} error: {res.get('error')}", flush=True)
            continue
        path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        ti = res["test_imports"]
        print(f"{image_tag(image)} files={len(ti['files'])} unparsed={len(ti['unparsed'])} wall={res['wall']}s "
              f"scan={res['secs'].get('test_imports')}s orphans_before={len(res.get('orphans_before') or [])} "
              f"orphans_after={len(res.get('orphans_after') or [])}", flush=True)


def plan_budget(order, records, budget=R0_BUDGET, file_timeout=R0_FILE_TIMEOUT):
    """R0 の時間の割り当てを、run3 のファイルごとの秒で作り直す。order は走らせる順のファイル、records は
    {ファイル: run3 の結果（rc・secs・timeout）}。({ファイル: done・cut・timeout・no_record・not_started}, 使った秒)。
    done だけが結果を使えるファイル。no_record（run3 に結果が無い）は 0 秒・結果は不明として扱う。"""
    left, plan = float(budget), {}
    for f in order:
        if left <= 0:
            plan[f] = "not_started"
            continue
        rec = records.get(f)
        if rec is None:
            plan[f] = "no_record"
            continue
        secs, limit = float(rec.get("secs") or 0), min(float(file_timeout), left)
        if secs > limit:
            plan[f], spent = "cut", limit
        elif rec.get("timeout") or rec.get("rc") == 124:
            plan[f], spent = "timeout", secs
        else:
            plan[f], spent = "done", secs
        left -= spent
    return plan, round(float(budget) - max(left, 0.0), 1)


def existing_test_edits(patch):
    """パッチのうち、t=0 にあるテストのファイル（.py で is_test_file に当たるもの）を変えるか消す差分（R0 の 5）。
    新しいファイルは数えない。[{"path", "kind": modified か deleted}]。"""
    out = []
    for block in re.split(r"(?m)^diff --git ", patch or "")[1:]:
        head, _, rest = block.partition("\n")
        m = re.match(r"a/(\S+) b/(\S+)", head)
        if not m:
            continue
        header = rest.split("\n@@", 1)[0]
        if re.search(r"(?m)^new file mode", header):
            continue
        old = m.group(1)
        if old.endswith(".py") and gate.is_test_file(old):
            kind = "deleted" if re.search(r"(?m)^deleted file mode", header) else "modified"
            out.append({"path": old, "kind": kind})
    return out


def state_tests(d, c):
    """1 つの状態の run3 のテストの結果：({ファイル: rc・secs・timeout}, {ファイル: [落ちた項目]})。
    正解の状態は、当て直した分（gold_retest）と、記録不足を埋めた分（gold_fill。fill の結果）も足す
    （ファイルは重ならない。重なれば先のものを使う）。測り直した状態（recheck）は、その結果だけを使う。"""
    again = (d.get("recheck") or {}).get(c.get("name"))
    if again is not None:   # 測り直した状態（recheck）は、その結果だけを使う
        parts = [again.get("tests") or {}]
    else:
        parts = [c.get("tests") or {}]
        if c.get("name") == "gold":
            parts.append((d.get("gold_retest") or {}).get("tests") or {})
            parts.append((d.get("gold_fill") or {}).get("tests") or {})
    files, failed = {}, {}
    for t in parts:
        mine = {rel: rec for rel, rec in (t.get("files") or {}).items() if rel not in files}
        files.update(mine)
        for x in t.get("failed") or []:
            rel = x["node"].split("::", 1)[0]
            if rel in mine:
                failed.setdefault(rel, []).append(x)
    return files, failed


def r0_order(imports_t0, ledger_paths, changed):
    """R0 の選びと走らせる順（群 1→2→3）。(選び, 順のファイル)。"""
    sel = gate.select_tests_r0(imports_t0, ledger_paths, changed)
    return sel, sel["own"] + sel["ledger"] + sel["changed"]


def imports_at_t0(row, imports_dir):
    """scan の結果から、t=0 のテストのファイルごとの import（消した公式のテストを除く）。(import, scan, 除いたファイル)。"""
    from featurebench.utils.identifiers import testbed_test_file
    scan = json.loads((Path(imports_dir) / f"imports_{image_tag(row['image_name'])}.json").read_text(encoding="utf-8"))
    hidden = {rel_path(testbed_test_file(t)) for t in f2p_list(row)}
    return {k: v for k, v in scan["test_imports"]["files"].items() if k not in hidden}, scan, hidden


def r0_state(c, start_keys, imports_t0, ledger_paths, packages, removed, mfiles, records, failed_by_file, edits):
    """1 つの状態に R0 を当てる（実験と計画 §5-6）。的に当たるか（removed・mfiles）は評価にだけ使う。"""
    sel, order = r0_order(imports_t0, ledger_paths, c.get("changed") or [])
    plan, used = plan_budget(order, records)
    ks = keysets(c)
    new = {k: sorted(ks[k] - start_keys[k], key=str) for k in ks}
    classes, target, unknown_fails, unknown_files = {}, [], [], []
    for f in order:
        if plan[f] != "done":
            continue
        fails = failed_by_file.get(f) or []
        if records[f].get("rc") not in FILE_RC_KNOWN and not fails:
            unknown_files.append(f)
            continue
        for x in fails:
            cls = gate.failure_class(x.get("msg"), packages)
            classes[cls] = classes.get(cls, 0) + 1
            if cls == "target":
                target.append(x)
            elif cls == "U":
                unknown_fails.append(x)
    static_on = sum(1 for k, v in new.items() for key in v if on_target(k, key, removed, mfiles))
    tests_on = sum(1 for x in target if relevant("tests", x, removed, mfiles))
    pushed = bool(sum(len(v) for v in new.values()) or target or edits)
    on_t = bool(static_on or tests_on)
    counts = {}
    for v in plan.values():
        counts[v] = counts.get(v, 0) + 1
    return {"state": c["name"], "resolved": c.get("resolved"), "white": c.get("white"),
            "pushed": pushed, "on_target": on_t,
            "pushed_with_unknown": pushed or bool(unknown_fails or unknown_files),
            "on_target_with_unknown": on_t or any(relevant("tests", x, removed, mfiles) for x in unknown_fails),
            "static_new": {k: len(v) for k, v in new.items()}, "static_new_on_target": static_on,
            "static_new_sample": {k: [list(x) for x in v[:4]] for k, v in new.items() if v},
            "tests_target": len(target), "tests_target_on_target": tests_on, "test_classes": classes,
            "tests_target_sample": [[x["node"], (x.get("msg") or "")[:160]] for x in target[:4]],
            "tests_unknown": len(unknown_fails), "unknown_files": len(unknown_files), "edits": edits,
            "selected": {g: len(sel[g]) for g in ("own", "ledger", "changed")},
            "top_level_dropped": sel["top_level_dropped"], "plan": counts, "secs_used": used,
            "record_gap_files": sum(1 for f in order if plan[f] == "no_record")}


def r0_task(d, row, imports_t0, patch_of):
    """1 つの課題の、正解と記録の A のパッチの状態に R0 を当てる。patch_of(状態の名) は、run3 が当てたのと同じパッチ。"""
    start = next(c for c in d["candidates"] if c["name"] == "start")
    removed, mfiles = set(d["ground"]["removed"]), set(d["ground"]["mask_files"])
    args = (keysets(start), imports_t0, statement_paths(row["problem_statement"]), packages_of(row), removed, mfiles)
    states = []
    for c in d["candidates"]:
        if c["name"] == "start":
            continue
        if not c.get("applied", True) or "error" in c:
            states.append({"state": c["name"], "error": (c.get("apply_error") or c.get("error") or "")[:200]})
            continue
        records, failed = state_tests(d, c)
        edits = [] if c["name"] == "gold" else existing_test_edits(patch_of(c["name"]))
        states.append(r0_state(c, *args, records, failed, edits))
    return {"id": d["id"], "states": states}


def r0_lines(results):
    """判定線（実験と計画 §5-6）と、添える数。"""
    gold = [(r["id"], s) for r in results for s in r["states"] if s["state"] == "gold"]
    cands = [(r["id"], s) for r in results for s in r["states"] if s["state"] != "gold" and "error" not in s]
    unresolved = [(i, s) for i, s in cands if s.get("resolved") is False]
    resolved = [(i, s) for i, s in cands if s.get("resolved")]

    def short(i, s=None):
        p = i.split(".")
        return p[0].split("__")[0] + ":" + p[2] + (f"[{s['state']}]" if s else "")

    out = {"gold_pushed": [short(i) for i, s in gold if s.get("pushed")],
           "unresolved_caught": [short(i, s) for i, s in unresolved if s["pushed"] and s["on_target"]],
           "unresolved_pushed_off_target": [short(i, s) for i, s in unresolved if s["pushed"] and not s["on_target"]],
           "resolved_pushed": [short(i, s) for i, s in resolved if s["pushed"]],
           "gold_pushed_with_unknown": [short(i) for i, s in gold if s.get("pushed_with_unknown")],
           "unresolved_caught_with_unknown": [short(i, s) for i, s in unresolved
                                              if s["pushed_with_unknown"] and s["on_target_with_unknown"]],
           "states_with_record_gap": [short(i, s) for i, s in gold + cands if s.get("record_gap_files")],
           "counts": {"gold": len(gold), "unresolved": len(unresolved), "resolved": len(resolved)}}
    out["line1_gold_le_2"] = len(out["gold_pushed"]) <= 2
    out["line2_caught_ge_5"] = len(out["unresolved_caught"]) >= 5
    out["pass"] = out["line1_gold_le_2"] and out["line2_caught_ge_5"]
    return out


def cmd_fill(args):
    """G0-3 の記録不足を埋める（利用枠 0）。正解の状態で、R0 の順のファイルのうち run3 に結果が無いものだけを、R0 の順に、
    run3 と同じ走らせ方（1 ファイル 300 秒）で走らせ、<out>/<ID>.json に書く。run3 の記録は書き換えない
    （r0 の --fill で正解の状態に足す）。時間の割り当て（300 秒）は r0 の plan_budget が当てる。"""
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [i for i in ids if not args.only or any(o in i for o in args.only)]
    rows = load_rows(ids)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for iid in ids:
        out_path = Path(args.out) / f"{iid}.json"
        tag = f"{iid.split('.')[0]}.{iid.split('.')[2]}"
        if out_path.exists():
            print(f"{tag} skip", flush=True)
            continue
        d = json.loads((Path(args.stage) / f"{iid}.json").read_text(encoding="utf-8"))
        row = rows[iid]
        gold = next(c for c in d["candidates"] if c["name"] == "gold")
        imports_t0, _, _ = imports_at_t0(row, args.imports)
        _, order = r0_order(imports_t0, statement_paths(row["problem_statement"]), gold.get("changed") or [])
        records, _ = state_tests(d, gold)
        want = [f for f in order if f not in records]
        if not want:
            print(f"{tag} nothing to fill", flush=True)
            continue
        rs = repo_settings(row)
        t0 = time.time()
        box = Box(row["image_name"], f"fbfill-{os.getpid()}-{int(time.time())}", parse_docker_runtime_config(rs))
        try:
            base, mode = prepare(box, row)
            reset(box, base)
            box.put("/tmp/cand.patch", gold_patch(row))
            code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/cand.patch")
            if code != 0:
                raise RuntimeError(f"gold apply failed: {out[-300:]}")
            clear_bytecode(box)
            spec = {"root": "/testbed", "packages": packages_of(row), "checks": ["tests"], "test_files": want,
                    "base": base, "test_cmd": rs.get("test_cmd") or "pytest -rA -p no:cacheprovider --color=no",
                    "timeout_one": rs.get("timeout_one") or 10, "file_timeout": R0_FILE_TIMEOUT,
                    # ファイルの間の数ミリ秒の手間で、300 秒の境のファイルを取りこぼさないための余り（割り当ては r0 が 300 秒で当てる）
                    "test_budget": R0_BUDGET + 30, "pyflakes_dir": TOOLS_DIR}
            res = probe(box, spec)
            _, procs = box.exec("ps -eo comm= | sort | uniq -c | sort -rn")
        finally:
            box.close()
        res.update({"id": iid, "base": base, "restore": mode, "applied": True, "want": want,
                    "changed_same_as_run3": sorted(res.get("changed") or []) == sorted(gold.get("changed") or []),
                    "wall": round(time.time() - t0, 1), "procs_after": procs.strip()})
        out_path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        t = res.get("tests") or {}
        before = [b.get("pending") for b in (res.get("build") or {}).get("before") or []]
        print(f"{tag} want={len(want)} ran={len(t.get('files') or {})} not_run={len(t.get('not_run') or [])} "
              f"failed={len(t.get('failed') or [])} tests={res.get('secs', {}).get('tests')}s wall={res['wall']}s "
              f"build_pending={before} rebuilt={(res.get('build') or {}).get('rebuilt')} "
              f"orphans_before={len(res.get('orphans_before') or [])} orphans_after={len(res.get('orphans_after') or [])} "
              f"killed={t.get('killed_leftovers')} changed_same={res['changed_same_as_run3']} error={res.get('error')}",
              flush=True)


def cmd_recheck(args):
    """状態をまるごと測り直す（利用枠 0）。run3 の probe は、mlflow の要約の行（conftest の飾り付き）を読めず、
    収集の誤りの本文と標準エラーも残していなかった。今の probe で、課題の正解と記録の A のパッチの状態ごとに、
    その状態の R0 の順のファイルを、run3 と同じ走らせ方（1 ファイル 300 秒）で走らせ、<out>/<ID>.json に書く。
    r0 の --recheck で、その状態の記録を差し替える。時間の割り当て（300 秒）は r0 の plan_budget が当てる。"""
    from collections import Counter
    from featurebench.utils.runtime_config import parse_docker_runtime_config
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [i for i in ids if not args.only or any(o in i for o in args.only)]
    rows = load_rows(ids)
    patches = {(p["label"], p["id"]): p for p in a_patches(args.runs, set(ids))}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for iid in ids:
        out_path = Path(args.out) / f"{iid}.json"
        tag = f"{iid.split('.')[0]}.{iid.split('.')[2]}"
        if out_path.exists():
            print(f"{tag} skip", flush=True)
            continue
        d = json.loads((Path(args.stage) / f"{iid}.json").read_text(encoding="utf-8"))
        row = rows[iid]
        imports_t0, _, _ = imports_at_t0(row, args.imports)
        ledger = statement_paths(row["problem_statement"])
        jobs, keep = [], set(args.states or [])
        for c in d["candidates"]:
            if c["name"] == "start" or not c.get("applied", True) or "error" in c:
                continue
            if keep and c["name"] not in keep and not (c["name"] == "gold" and "start" in keep):
                continue
            if c["name"] == "gold":
                patch = gold_patch(row)
            else:
                p = patches[(c["name"], iid)]
                patch = strip_hidden(p["patch"], row) if p["white"] else p["patch"]
            _, order = r0_order(imports_t0, ledger, c.get("changed") or [])
            if c["name"] == "gold" and "start" in keep:   # 参考：t=0 で、正解の状態と同じファイルを走らせる
                jobs.insert(0, ("start", None, order, []))
            if not keep or c["name"] in keep:
                jobs.append((c["name"], patch, order, sorted(c.get("changed") or [])))
        rs = repo_settings(row)
        t0 = time.time()
        box = Box(row["image_name"], f"fbrecheck-{os.getpid()}-{int(time.time())}", parse_docker_runtime_config(rs))
        states = {}
        try:
            base, _ = prepare(box, row)
            for name, patch, order, changed in jobs:
                reset(box, base)
                if patch is not None:
                    box.put("/tmp/cand.patch", patch if patch.endswith("\n") else patch + "\n")
                    code, out = box.exec("cd /testbed && git apply --whitespace=fix /tmp/cand.patch")
                    if code != 0:
                        raise RuntimeError(f"{name} apply failed: {out[-300:]}")
                    clear_bytecode(box)
                spec = {"root": "/testbed", "packages": packages_of(row), "checks": ["tests"], "test_files": order,
                        "base": base, "test_cmd": rs.get("test_cmd") or "pytest -rA -p no:cacheprovider --color=no",
                        "timeout_one": rs.get("timeout_one") or 10, "file_timeout": R0_FILE_TIMEOUT,
                        "test_budget": R0_BUDGET + 30, "pyflakes_dir": TOOLS_DIR}   # 余りは fill と同じ
                r = probe(box, spec)
                r.update({"applied": True, "changed_same_as_run3": sorted(r.get("changed") or []) == changed})
                states[name] = r
            _, procs = box.exec("ps -eo comm= | sort | uniq -c | sort -rn")
        finally:
            box.close()
        for name, r in states.items():
            t = r.get("tests") or {}
            for x in t.get("failed") or []:
                x["class"] = gate.failure_class(x.get("msg"), packages_of(row))
            r["classes"] = dict(Counter(x["class"] for x in t.get("failed") or []))
            rc = dict(Counter(str(v.get("rc")) for v in (t.get("files") or {}).values()))
            print(f"{tag} {name} ran={len(t.get('files') or {})} not_run={len(t.get('not_run') or [])} rc={rc} "
                  f"classes={r['classes']} tests={r.get('secs', {}).get('tests')}s changed_same={r['changed_same_as_run3']} "
                  f"orphans_after={len(r.get('orphans_after') or [])} killed={t.get('killed_leftovers')} "
                  f"error={r.get('error')}", flush=True)
        out_path.write_text(json.dumps({"id": iid, "states": states, "wall": round(time.time() - t0, 1),
                                        "procs_after": procs.strip()}, ensure_ascii=False), encoding="utf-8")
        print(f"{tag} wall={round(time.time() - t0, 1)}s procs_after={procs.strip().replace(chr(10), ' |')}", flush=True)


def cmd_r0(args):
    """run3 の記録に R0 を当てて数える（G0-3）。コンテナは使わない（テストの import は scan の結果を読む）。
    --fill を渡すと、fill の結果を正解の状態の記録に足し、--recheck を渡すと、測り直した状態の記録を差し替える。"""
    ids = [x.strip() for x in Path(args.tasks).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = load_rows(ids)
    patches = {(p["label"], p["id"]): p for p in a_patches(args.runs, set(ids))}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    results, notes = [], []
    for iid in ids:
        d = json.loads((Path(args.stage) / f"{iid}.json").read_text(encoding="utf-8"))
        fill = Path(args.fill) / f"{iid}.json" if args.fill else None
        if fill is not None and fill.exists():
            d["gold_fill"] = json.loads(fill.read_text(encoding="utf-8"))
        again = Path(args.recheck) / f"{iid}.json" if args.recheck else None
        if again is not None and again.exists():
            d["recheck"] = json.loads(again.read_text(encoding="utf-8"))["states"]
        row = rows[iid]
        imports_t0, scan, hidden = imports_at_t0(row, args.imports)
        masked_tests = sorted(f for f in fb_ground.mask_files(row["patch"]) if gate.is_test_file(f) and f not in hidden)
        if masked_tests:   # 隠しのパッチが、消した公式のテスト以外のテストのファイルを変えた（イメージの import と t=0 がずれうる）
            notes.append({"id": iid, "mask_patch_test_files": masked_tests})

        def patch_of(name, iid=iid, row=row):
            p = patches.get((name, iid))
            if p is None:
                return ""
            return strip_hidden(p["patch"], row) if p["white"] else p["patch"]

        res = r0_task(d, row, imports_t0, patch_of)
        res["hidden_excluded"] = len(set(scan["test_imports"]["files"]) & hidden)
        results.append(res)
        (Path(args.out) / f"{iid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = r0_lines(results)
    lines["notes"] = notes
    (Path(args.out) / "lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in results:
        for s in r["states"]:
            if "error" in s:
                print(f"{r['id'].split('.')[2]:<24} {s['state']:<11} error {s['error']}")
                continue
            print(f"{r['id'].split('.')[2]:<24} {s['state']:<11} res={str(s['resolved'])[:1]} "
                  f"push={int(s['pushed'])} on={int(s['on_target'])} static={s['static_new']} on_s={s['static_new_on_target']} "
                  f"tests={s['tests_target']} on_t={s['tests_target_on_target']} cls={s['test_classes']} "
                  f"unk_files={s['unknown_files']} edits={len(s['edits'])} sel={s['selected']} plan={s['plan']} "
                  f"gap={s['record_gap_files']}")
    print(json.dumps(lines, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.fb_replay", description=__doc__.splitlines()[0].replace("%", "%%"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tasks", required=True)
    r.add_argument("--runs", nargs="+", required=True)
    r.add_argument("--gold", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--only", nargs="*")
    s = sub.add_parser("summary")
    s.add_argument("--out", required=True)
    g = sub.add_parser("goldtests")
    g.add_argument("--tasks", required=True)
    g.add_argument("--out", required=True)
    rg = sub.add_parser("reground")
    rg.add_argument("--tasks", required=True)
    rg.add_argument("--gold", required=True)
    rg.add_argument("--out", required=True)
    sc = sub.add_parser("scan")
    sc.add_argument("--tasks", required=True)
    sc.add_argument("--out", required=True)
    sc.add_argument("--only", nargs="*")
    r0 = sub.add_parser("r0")
    r0.add_argument("--tasks", required=True)
    r0.add_argument("--runs", nargs="+", required=True)
    r0.add_argument("--stage", required=True, help="段 a の記録の置き場（run3）")
    r0.add_argument("--imports", required=True, help="scan の置き場")
    r0.add_argument("--out", required=True)
    r0.add_argument("--fill", help="fill の置き場（正解の状態の記録不足を埋めた結果）")
    r0.add_argument("--recheck", help="recheck の置き場（測り直した状態の結果）")
    fl = sub.add_parser("fill")
    fl.add_argument("--tasks", required=True)
    fl.add_argument("--stage", required=True, help="段 a の記録の置き場（run3）")
    fl.add_argument("--imports", required=True, help="scan の置き場")
    fl.add_argument("--out", required=True)
    fl.add_argument("--only", nargs="*", help="ID に含まれる文字列で絞る")
    rc = sub.add_parser("recheck")
    rc.add_argument("--tasks", required=True)
    rc.add_argument("--stage", required=True, help="段 a の記録の置き場（run3）")
    rc.add_argument("--imports", required=True, help="scan の置き場")
    rc.add_argument("--runs", nargs="+", required=True)
    rc.add_argument("--out", required=True)
    rc.add_argument("--only", nargs="*", help="ID に含まれる文字列で絞る")
    rc.add_argument("--states", nargs="*",
                    help="走らせる状態（既定は正解と記録の A のすべて。start は参考の t=0 で、正解の状態と同じファイル）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    return {"run": cmd_run, "summary": cmd_summary, "goldtests": cmd_goldtests, "reground": cmd_reground,
            "scan": cmd_scan, "r0": cmd_r0, "fill": cmd_fill, "recheck": cmd_recheck}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
