"""Multi-step SWE の実行環境のアダプタ（制御核 core.py の関門の検査と任意の口）。harness/ms_run.py が使う。

制御核とアダプタの契約（設計 §5-1）：検査ごとに pass・fail・infra を返し、合否と押し返しは制御核が決める。測りの意味
（差の読み・数えない項目）はアダプタが持つ。公式のテストは使わない（A にも見えるものだけ。設計 §5-1 の公平の決まり）。
ファイルの大きさの上限（46,080 バイト）のため、2026-11-27 に harness/ms_run.py から中身を変えずに分けた。
"""
import collections
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import sandbox  # noqa: E402

WORKDIR = "/app"
PROGRESS_FILE = "harness_progress.json"
HARNESS_FILES = (core.HANDOFF_FILE, PROGRESS_FILE)      # ハーネスが作業場に置くファイル（採点の写しと状態の SHA-256 から除く）
SKIP_DIRS = (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def safe_extract(tar_bytes, dest):
    """Harbor と同じく tarfile の data の filter で展開する（絶対パス・外へのリンク・特殊なファイルを拒む）。件数を返す。"""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        members = tf.getmembers()
        tf.extractall(dest, filter="data")
    return len(members)


def parse_manifest(text):
    """sha256sum の出力（`<sha>  ./<path>`）→ {path: sha}。"""
    out = {}
    for line in (text or "").splitlines():
        digest, _, name = line.partition("  ")
        if len(digest) == 64 and name:
            out[name[2:] if name.startswith("./") else name] = digest
    return out


def manifest_digest(man):
    return hashlib.sha256("".join(f"{man[p]}  {p}\n" for p in sorted(man)).encode("utf-8")).hexdigest()


def diff_manifest(before, after):
    added = sorted(p for p in after if p not in before)
    modified = sorted(p for p in after if p in before and before[p] != after[p])
    deleted = sorted(p for p in before if p not in after)
    return added, modified, deleted


# 第 1 関門の参照の完全さで数える pyflakes の検出（harness/fb_gate_probe.py の check_undefined と同じ組。P-038）：未定義の名・
# __all__ の未定義・代入の前の参照と、構文の誤り。使われない import などは数えない（設計 §4-2 の 1）。標準入力で渡し、
# 引数のファイルに当てて「path:line: 種類: 文」を出す（深い式で再帰が尽きないよう大きいスタックのスレッドで走らせる）
PYFLAKES_SCRIPT = r'''
import sys, threading
from pyflakes import api, messages
KEEP = (messages.UndefinedName, messages.UndefinedExport, messages.UndefinedLocal)
class R:
    def unexpectedError(self, filename, msg):
        print("%s:0: error: %s" % (filename, (str(msg).splitlines() or ["error"])[0][:200]))
    def syntaxError(self, filename, msg, lineno, offset, text):
        print("%s:%s: syntax: %s" % (filename, lineno or 0, (str(msg).splitlines() or ["syntax error"])[0][:200]))
    def flake(self, m):
        if isinstance(m, KEEP):
            print("%s:%s: %s: %s" % (m.filename, m.lineno, type(m).__name__, m.message % m.message_args))
def main():
    r = R()
    for f in sys.argv[1:]:
        try:
            api.checkPath(f, r)
        except RecursionError:
            print("unchecked (RecursionError): %s" % f, file=sys.stderr)
sys.setrecursionlimit(50000)
threading.stack_size(512 * 1024 * 1024)
t = threading.Thread(target=main)
t.start()
t.join()
'''
PYTEST_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS) (\S+)(?: - (.*))?$")
FLAKE_LINE = re.compile(r"^(.+?):(\d+)(?::\d+)?:? (.*)$")
TEST_GLOBS = ("test_*.py", "*_test.py")


def parse_pytest(out):
    """pytest -rA の短い要約の行 → (passed, failed, errors)。"""
    passed, failed, errors = [], [], []
    for line in (out or "").splitlines():
        m = PYTEST_LINE.match(line.strip())
        if not m:
            continue
        kind, node, msg = m.groups()
        if kind in ("PASSED", "XPASS"):
            passed.append(node)
        else:
            failed.append(node)
            errors.append(f"{node} - {msg or kind}")
    return passed, failed, errors


def split_preexisting(failed, errors, base_failed):
    """群 1 の差の読み：単位の始まりに落ちていた項目（base_failed）を落ちから外す。始まりに集めの段で落ちていたファイル
    （base_failed にファイルの名だけがある）の中の項目も、始まりに通っていたと言えないので外す。
    (残る落ち, 残るエラーの文, 外した項目)。"""
    def known(n):
        return n in base_failed or n.split("::")[0] in base_failed
    pre = [n for n in failed if known(n)]
    keep = [n for n in failed if not known(n)]
    errs = [e for e in errors if not any(e.startswith(n + " ") or e == n for n in pre)]
    return keep, errs, pre


def parse_pyflakes(out):
    """pyflakes の出力 → Counter{(path, 位置を除いた文): 数}（差の読みのため、行の番号に依らない形にする）。"""
    c = collections.Counter()
    for line in (out or "").splitlines():
        m = FLAKE_LINE.match(line)
        if not m:
            continue
        path = m.group(1)
        c[(path[2:] if path.startswith("./") else path, re.sub(r"\bline \d+", "line N", m.group(3).strip()))] += 1
    return c


def expand(target, changed):
    """cmd の引数の {changed:GLOB}（変わったファイル）と {changed_dirs:GLOB}（そのディレクトリ ./dir）を置き換える。
    置き換えが空なら None（走らせずに合格にする）。"""
    out = []
    for tok in target:
        m = re.fullmatch(r"\{(changed|changed_dirs):(.+)\}", tok)
        if not m:
            out.append(tok)
            continue
        hits = [p for p in changed if fnmatch.fnmatch(p, m.group(2)) or fnmatch.fnmatch(os.path.basename(p), m.group(2))]
        if m.group(1) == "changed_dirs":
            hits = sorted({"./" + os.path.dirname(p) if os.path.dirname(p) else "." for p in hits})
        if not hits:
            return None
        out += hits
    return out


def is_test_file(path):
    return any(fnmatch.fnmatch(os.path.basename(path), g) for g in TEST_GLOBS)


# ---------------------------------------------------------------- 実行環境のアダプタ（制御核の関門）

class InfraError(RuntimeError):
    """測定系の異常（コンテナや docker の落ち・道具が無い）。検査の結果を infra にする。"""


class MsAdapter:
    """Multi-step SWE の実行環境のアダプタ（制御核の verify と任意の口）。検査は実装役のコンテナの中で実装役の uid で走らせ
    （pytest・cmd）、pyflakes はホストで作業場の写しに当てる（コンテナに道具を足さない）。公式のテストは使わない。
    kind：
    - pyflakes_diff：単位の始まりから変わった .py に pyflakes を当て、未定義の名と構文の誤り（PYFLAKES_SCRIPT の組）のうち、
      単位の始まりに無かった検出だけを落ちにする（差の読み。設計 §4-2 の 1。P-037・P-038）
    - pytest：items があればその項目（非退行）、target が ["changed"] なら単位の始まりから変わったテストのファイル（群 1）を、
      実装役の絞りに頼らずに走らせ、-rA の要約で項目ごとの合否を読む。作業場の Python に pytest が無ければ infra。
      群 1 と積んだ項目は差の読み：単位の始まりに既存のテストのファイルを 1 回走らせ、そのとき落ちていた項目（と、集めで
      落ちていたファイルの中の項目）は押し返さない（数は要約に残す）。正しい実装が同じファイルの別の所を変えただけで前から
      の落ちを押し返さないため（pandas の 1 段目の正解で実測）、段の setup の道具の入れ替えで前の段の項目が正解でも落ちる
      のを押し返さないため（pandas の 3 段目の正解で実測。P-042 の型）。積んだ項目はファイルごとに走らせて項目ごとに読み、
      消えた項目は数えない（_k_pytest_items）
    - cmd：target のコマンドを /app で走らせ、終わりの符号 0 なら合格。{changed:GLOB}・{changed_dirs:GLOB} を置き換える
      （置き換えが空なら走らせずに合格）
    - protect：guard で記録した、守る検査の元のファイル（pytest の項目のファイル）の SHA-256 が変わっていないこと
    コンテナが動いていない・docker の操作が落ちたら infra。検査の時間切れは落ち（押し返す。設計 §5-1 の時間の予算）。"""

    KINDS = ("pyflakes_diff", "pytest", "cmd", "protect")
    protects = True

    def __init__(self, box, sbx, out_dir, *, pyflakes_cmd=None, check_timeout_s=300, keep_checkpoints=False):
        self.box, self.sbx, self.out_dir = box, sbx, Path(out_dir)
        self.pyflakes_cmd, self.timeout, self.keep = list(pyflakes_cmd or []), int(check_timeout_s), keep_checkpoints
        self.unit, self.start, self.base_flakes, self.base_failed = None, {}, collections.Counter(), set()
        self.guarded, self.guard_map, self.ckpt, self.notes = {}, {}, {}, []

    # ---- docker の操作
    def _exec(self, argv, *, user=None, timeout=None, input_bytes=None, env=None):
        uid = self.sbx["uid"] if user is None else user
        cmd = ["docker", "exec", "-i", "--user", f"{uid}:{uid}", "-e", f"HOME={self.sbx['home']}"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        io_kw = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
        p = subprocess.run(cmd + ["-w", WORKDIR, self.box] + list(argv), capture_output=True,
                           timeout=timeout or self.timeout, **io_kw)
        return p.returncode, p.stdout, p.stderr

    def _alive(self):
        code, out = sandbox.sh(["docker", "inspect", "-f", "{{.State.Running}}", self.box], timeout=60)
        if code != 0 or out.strip() != "true":
            raise InfraError(f"実装役のコンテナが動いていない: {out.strip()[-200:]}")

    def manifest(self):
        prune = " -o ".join(f"-name {d}" for d in SKIP_DIRS)
        skip = " ".join(f"! -name {n}" for n in HARNESS_FILES + ("'*.pyc'",))
        script = (f"cd {WORKDIR} && find . \\( {prune} \\) -prune -o -type f {skip} -print0 | sort -z | "
                  "xargs -0 -r sha256sum")
        code, out, err = self._exec(["bash", "-c", script], user=0, timeout=600)
        if code != 0:
            raise InfraError(f"作業場の一覧が取れない (rc={code}): {err.decode('utf-8', 'replace')[-200:]}")
        return parse_manifest(out.decode("utf-8", "replace"))

    def _files(self, paths, dest):
        """作業場のファイルをホストの dest に写す（tar を docker exec の標準出力で受け、data の filter で展開）。"""
        code, out, err = self._exec(["tar", "-C", WORKDIR, "-cf", "-", "--"] + list(paths), user=0, timeout=600)
        if code != 0:
            raise InfraError(f"作業場のファイルが取れない (rc={code}): {err.decode('utf-8', 'replace')[-200:]}")
        return safe_extract(out, dest)

    def _flakes(self, paths):
        if not paths:
            return collections.Counter()
        if not self.pyflakes_cmd:
            raise InfraError("pyflakes の命令が設定に無い（config/ms_run.json の pyflakes）")
        with tempfile.TemporaryDirectory(prefix="ms_flakes_") as d:
            self._files(paths, d)
            try:
                p = subprocess.run(self.pyflakes_cmd + list(paths), cwd=d, input=PYFLAKES_SCRIPT, capture_output=True,
                                   text=True, timeout=self.timeout)
            except FileNotFoundError as e:
                raise InfraError(f"pyflakes を起こせない: {e}") from e
        if p.returncode != 0:
            raise InfraError(f"pyflakes が落ちた (rc={p.returncode}): {(p.stderr or p.stdout)[-200:]}")
        return parse_pyflakes(p.stdout)

    # ---- 制御核の任意の口
    def begin_unit(self, name, checks):
        """単位の始まり：作業場の状態と、差の読みの基準を記録する（pyflakes_diff があれば始まりの .py 全部の検出、群 1 の
        pytest があれば始まりのテストのファイル全部を走らせて落ちていた項目）。基準が取れなければ空にして notes に残す。"""
        self.unit, self.start = name, self.manifest()
        self.base_flakes, self.base_failed = collections.Counter(), set()
        if any(c.kind == "pyflakes_diff" for c in checks):
            self.base_flakes = self._flakes(sorted(p for p in self.start if p.endswith(".py")))
        if any(c.kind == "pytest" and c.target == ["changed"] for c in checks):
            files = sorted(p for p in self.start if is_test_file(p))
            note = {"unit": name, "baseline_test_files": len(files)}
            if files:
                t0 = time.time()
                try:
                    code, text = self._pytest(files)
                    _, failed, _ = parse_pytest(text)
                    self.base_failed = set(failed)
                    note.update(rc=code, failed=len(failed))
                except (InfraError, subprocess.TimeoutExpired) as e:
                    sandbox.stop_user(self.box, self.sbx)
                    note["error"] = str(e)[:200] or "timeout"
                note["seconds"] = round(time.time() - t0, 1)
            self.notes.append(note)

    def fingerprint(self):
        try:
            return manifest_digest(self.manifest())
        except (InfraError, subprocess.TimeoutExpired):
            return None

    def changes(self):
        try:
            added, modified, deleted = diff_manifest(self.start, self.manifest())
        except (InfraError, subprocess.TimeoutExpired):
            return None
        return {"added": added[:50], "modified": modified[:50], "deleted": deleted[:50],
                "n": {"added": len(added), "modified": len(modified), "deleted": len(deleted)}}

    def guard(self, checks):
        cur = self.manifest()
        for c in checks:
            paths = sorted({i.split("::")[0] for i in c.items if i.split("::")[0] in cur})
            self.guard_map[c.id] = paths
            for p in paths:
                self.guarded.setdefault(p, cur[p])

    def checkpoint(self, label):
        """確定とロールバックの点。keep_checkpoints（rollback の比較条件）のときだけ /app の tar をホストに残す。"""
        if not self.keep:
            self.notes.append({"checkpoint": label, "kept": False})
            return
        path = self.out_dir / "ckpt" / (re.sub(r"[^A-Za-z0-9._-]", "_", label) + ".tar")
        path.parent.mkdir(parents=True, exist_ok=True)
        ex = [a for e in HARNESS_FILES for a in ("--exclude", e)]
        with open(path, "wb") as f:
            p = subprocess.run(["docker", "exec", "--user", "0:0", self.box, "tar", "-C", WORKDIR, "-cf", "-"] + ex + ["."],
                               stdout=f, stderr=subprocess.PIPE, timeout=1800)
        if p.returncode != 0:
            raise InfraError(f"checkpoint の tar が落ちた (rc={p.returncode})")
        self.ckpt[label] = path
        self.notes.append({"checkpoint": label, "kept": True, "bytes": path.stat().st_size})

    def restore(self, label):
        path = self.ckpt.get(label)
        if path is None:
            self.notes.append({"restore": label, "done": False})
            return
        script = f"find {WORKDIR} -mindepth 1 -delete && tar -C {WORKDIR} -xf -"
        if int(self.sbx["uid"]) != 0:
            script += f" && chown -R {self.sbx['uid']}:{self.sbx['uid']} {WORKDIR}"
        code, _, err = self._exec(["bash", "-c", script], user=0, timeout=1800, input_bytes=path.read_bytes())
        if code != 0:
            raise InfraError(f"restore が落ちた (rc={code}): {err.decode('utf-8', 'replace')[-200:]}")
        self.notes.append({"restore": label, "done": True})

    def write_handoff(self, text, data):
        """構造化の引き継ぎを作業場の頭に置く（root の持ち物・読み取りだけ。採点の写しからは除く）。写しを置き場に残す。"""
        body = {core.HANDOFF_FILE: text, PROGRESS_FILE: json.dumps(data, ensure_ascii=False, indent=1)}
        for name, content in body.items():
            code, _, err = self._exec(["bash", "-c", f"cat > {WORKDIR}/{name} && chmod 444 {WORKDIR}/{name}"], user=0,
                                      timeout=60, input_bytes=content.encode("utf-8"))
            if code != 0:
                raise InfraError(f"{name} を置けない (rc={code}): {err.decode('utf-8', 'replace')[-200:]}")
            (self.out_dir / f"{self.unit}.{name}").write_text(content, encoding="utf-8")

    # ---- 検査
    def verify(self, checks):
        try:
            self._alive()
            cur = self.manifest()
        except (InfraError, subprocess.TimeoutExpired) as e:
            return [core.CheckResult(c.id, status=core.INFRA, summary=str(e)[:300]) for c in checks]
        changed = sorted(p for p in cur if self.start.get(p) != cur[p])
        out = []
        for c in checks:
            t0 = time.time()
            try:
                r = getattr(self, "_k_" + c.kind)(c, cur, changed)
            except subprocess.TimeoutExpired:
                sandbox.stop_user(self.box, self.sbx)
                r = core.CheckResult(c.id, status=core.FAIL, failed=[c.id], summary=f"timeout after {self.timeout} s",
                                     errors=[f"{c.id}: timed out after {self.timeout} s"])
            except InfraError as e:
                r = core.CheckResult(c.id, status=core.INFRA, summary=str(e)[:300])
            r.seconds = round(time.time() - t0, 1)
            out.append(r)
        return out

    def _k_pyflakes_diff(self, c, cur, changed):
        files = [p for p in changed if p.endswith(".py")]
        if not files:
            return core.CheckResult(c.id, status=core.PASS, summary="no changed .py files")
        got = self._flakes(files)
        base = collections.Counter({k: v for k, v in self.base_flakes.items() if k[0] in set(files)})
        new = got - base
        if not new:
            return core.CheckResult(c.id, status=core.PASS, summary=f"{len(files)} changed .py files, no new findings")
        errors = [f"{p}: {msg}" for (p, msg), n in sorted(new.items()) for _ in range(n)]
        return core.CheckResult(c.id, status=core.FAIL, failed=sorted({p for p, _ in new}), errors=errors,
                                summary=f"{len(errors)} new pyflakes findings")

    def _pytest(self, nodes):
        """作業場の Python の pytest で nodes を走らせる。(終わりの符号, 出力)。pytest が無ければ infra。1 つのファイルの
        集めの落ちで全体が中断しないよう --continue-on-collection-errors を付ける（中断すると、ほかの項目の合否が取れない）。"""
        code, out, err = self._exec(["python", "-m", "pytest", "-q", "-rA", "--tb=no", "--color=no", "-p",
                                     "no:cacheprovider", "--continue-on-collection-errors", "--"] + list(nodes),
                                    env={"PYTHONDONTWRITEBYTECODE": "1"})
        text = out.decode("utf-8", "replace") + err.decode("utf-8", "replace")
        if "No module named pytest" in text:
            raise InfraError("作業場の Python に pytest が無い")
        return code, text

    def _k_pytest_items(self, c, cur):
        """積んだ項目（非退行）：項目のファイルごとに走らせ、項目ごとに読む（1 つの項目が見つからないだけで pytest が全体を
        止めないように）。始まりから落ちていた項目は数えない。消えた項目（ファイルか関数が無い）は数えず件数を残す
        （protect を切った比較条件で、後の段の正解がテストを消す・名を変えるため。pandas の 3 段目の正解が test_corr を消した）。"""
        files = sorted({i.split("::")[0] for i in c.items if i.split("::")[0] in cur})
        passed_all, failed_all, errors_all, text, code = [], [], [], "", 0
        if files:
            code, text = self._pytest(files)
            passed_all, failed_all, errors_all = parse_pytest(text)
            if not passed_all and not failed_all and code not in (0, 5):   # pytest 自体が走れていない（項目を消えた扱いにしない）
                return core.CheckResult(c.id, status=core.FAIL, failed=[c.id], summary=f"pytest rc={code}",
                                        errors=[f"{c.id}: pytest rc={code}: " + " | ".join(text.strip().splitlines()[-3:])],
                                        raw=text[-2000:])
        passed = [i for i in c.items if i in passed_all]
        failing = [i for i in c.items if i in failed_all or i.split("::")[0] in failed_all]
        gone = [i for i in c.items if i not in passed and i not in failing]
        errors = [e for e in errors_all if any(e.startswith(i + " ") or e.startswith(i.split("::")[0] + " ")
                                               for i in failing)]
        failing, errors, pre = split_preexisting(failing, errors, self.base_failed)
        summary = (f"{len(passed)} passed, {len(failing)} failed"
                   + (f", {len(pre)} failing at the start of this step (not counted)" if pre else "")
                   + (f", {len(gone)} not found (removed or renamed; not counted)" if gone else ""))
        return core.CheckResult(c.id, status=core.FAIL if failing else core.PASS, passed=passed, failed=failing,
                                errors=errors or [f"{i} - failed" for i in failing], summary=summary, raw=text[-2000:],
                                skipped=pre + gone)

    def _k_pytest(self, c, cur, changed):
        if c.items:
            return self._k_pytest_items(c, cur)
        group1 = c.target == ["changed"]
        if group1:
            nodes = [p for p in changed if p in cur and is_test_file(p)]
        else:
            nodes = list(c.target)
        if not nodes:
            return core.CheckResult(c.id, status=core.PASS, summary="no changed test files")
        code, text = self._pytest(nodes)
        passed, failed, errors = parse_pytest(text)
        pre = []
        if group1:
            failed, errors, pre = split_preexisting(failed, errors, self.base_failed)
        if code == 5 and not failed:
            return core.CheckResult(c.id, status=core.PASS, summary="no tests collected", raw=text[-2000:])
        ok = not failed and (code == 0 or (code in (1, 2) and bool(pre)))   # 0 でない符号が前からの落ちだけで説明できる
        if not ok and not failed:
            failed, errors = [c.id], [f"{c.id}: pytest rc={code}: " + " | ".join(text.strip().splitlines()[-3:])]
        summary = f"{len(passed)} passed, {len(failed)} failed" + (
            f", {len(pre)} failing at the start of this step (not counted)" if pre else "")
        return core.CheckResult(c.id, status=core.PASS if ok else core.FAIL, passed=passed, failed=failed,
                                errors=errors, summary=summary, raw=text[-2000:])

    def _k_cmd(self, c, cur, changed):
        argv = expand(c.target, changed)
        if argv is None:
            return core.CheckResult(c.id, status=core.PASS, summary="no changed files to check")
        code, out, err = self._exec(argv)
        text = out.decode("utf-8", "replace") + err.decode("utf-8", "replace")
        if code == 0:
            return core.CheckResult(c.id, status=core.PASS, summary=f"rc=0 ({' '.join(argv)[:120]})", raw=text[-2000:])
        tail = [line for line in text.strip().splitlines() if line.strip()][-core.FEEDBACK_ITEMS:]
        return core.CheckResult(c.id, status=core.FAIL, failed=[c.id], errors=tail or [f"{c.id}: rc={code}"],
                                summary=f"rc={code} ({' '.join(argv)[:120]})", raw=text[-2000:])

    def _k_protect(self, c, cur, changed):
        paths = sorted({p for cid in c.target for p in self.guard_map.get(cid, [])})
        bad = [p for p in paths if cur.get(p) != self.guarded.get(p)]
        if not bad:
            return core.CheckResult(c.id, status=core.PASS, summary=f"{len(paths)} protected files unchanged")
        errors = [f"{p}: {'deleted' if p not in cur else 'changed'} (a protected non-regression test file)" for p in bad]
        return core.CheckResult(c.id, status=core.FAIL, failed=bad, errors=errors,
                                summary=f"{len(bad)} protected files changed")

