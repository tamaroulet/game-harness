"""FeatureBench の fb infer で、制御核（harness/core.py）を通した agy を走らせる（X-02 の B の腕。実験と計画 §4）。

腕は config/fb_core.json に、名前と部品の設定（gate・show_ledger・static・max_rounds）で書き、fb infer の --agent で選ぶ
（harness/fb_agy.py の register が、ここの arm_agents で足す）。

素の A（--agent agy）と同じにするもの：像・課題の文（白箱の注記を含む）・agy の版とモデルと思考の強さ・網の閉じ方・
時間の上限（fb infer の --timeout を、押し返しと確かめを含む 1 課題の全体に当てる）・採点（fb eval）。
ハーネスが使ってよいのは、A にも見えるもの（リポジトリ・課題の文・見えるテストのファイル）だけ。データの P2P の一覧は使わない。

関門の確かめ（実行環境のアダプタ。箱の中で走らせる。制御核は中身を知らない）：
- C1 tests：見えるテストのファイル（白箱の注記のパス）を pytest で走らせる。-rA で項目ごとの合否を読む。終わりの符号が 0 で合格。必須。
- C2 static：変えた .py（git の差分と、追跡していない新しいファイル。テストのファイルを除く）に pyflakes をかけ、
  「undefined name」と構文の誤りだけを落ちに数える（呼び先の無い参照）。pyflakes は箱の TOOLS_DIR に置き、
  エージェントの PATH と PYTHONPATH には入れない。必須。

要件の台帳（入口）：課題の文の Interface Description（Path と class・def の頭）と、見えるテストのファイルを
pytest --collect-only で集めた項目（テストのクラスごとにまとめる）から、機械で作る。
"""
import json
import os
import re
import shlex
import tempfile
import time
from pathlib import Path

import agy_stream
import core

ROOT = Path(__file__).resolve().parent.parent
CORE_CONFIG = ROOT / "config" / "fb_core.json"
LOG_DIR = "/agent-logs"
TOOLS_DIR = "/opt/harness_tools"
ENV = "source /installed-agent/setup-env.sh && cd /testbed && export PYTHONDONTWRITEBYTECODE=1 && "
EXIT_MARK = "__HARNESS_EXIT__="

_NOTE_RE = re.compile(r"You are given access to the test file `([^`]+)`")
_PATH_RE = re.compile(r"^\s*Path:\s*`?([^\s`]+)`?\s*$", re.M)
_HEAD_RE = re.compile(r"^(class|def|async def) ([A-Za-z_]\w*)", re.M)
_SUMMARY_RE = re.compile(r"^=*\s*(\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed)\b.*?|no tests ran.*?)"
                         r"\s*=*\s*$", re.M)
_RA_RE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL)\s+(\S+)(?:\s+-\s+(.*))?$", re.M)
_STATIC_RE = re.compile(r"undefined name|invalid syntax|unexpected indent|unexpected EOF|unindent does not match")


def load_arms(path=None):
    """腕の設定。既定は config/fb_core.json。FB_CORE_CONFIG で別の置き場にできる（偽物の agy での確かめ用）。"""
    cfg = json.loads(Path(path or os.environ.get("FB_CORE_CONFIG") or CORE_CONFIG).read_text(encoding="utf-8"))
    return cfg["arms"], cfg


def visible_test_path(instruction):
    """白箱の注記のテストのファイル（/testbed からの相対のパス）。注記が無ければ None（テストを隠した走り）。"""
    m = _NOTE_RE.search(instruction or "")
    if not m:
        return None
    path = m.group(1).strip()
    return path[len("/testbed/"):] if path.startswith("/testbed/") else path


def statement_items(instruction):
    """課題の文の Interface Description から「パス: class か def の名」を並べる（重なりなし、出てきた順）。"""
    items, seen = [], set()
    text = instruction or ""
    marks = [(m.start(), m.group(1)) for m in _PATH_RE.finditer(text)]
    for i, (pos, path) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        for h in _HEAD_RE.finditer(text[pos:end]):
            item = f"{path.replace('/testbed/', '')}: {h.group(1)} {h.group(2)}"
            if item not in seen:
                seen.add(item)
                items.append(item)
    return items


def collected_nodes(output):
    """pytest --collect-only -q の出力から、テストの項目の名を拾う。"""
    return [line.strip() for line in (output or "").splitlines() if "::" in line and not line.startswith(("=", " "))]


def group_nodes(nodes):
    """テストの項目を、ファイル::クラスごと（クラスの無いものはファイルごと）にまとめる。順は出てきた順。"""
    groups = {}
    for n in nodes:
        parts = n.split("::")
        key = "::".join(parts[:2]) if len(parts) > 2 else parts[0]
        groups.setdefault(key, []).append(n)
    return groups


def build_ledger(unit, instruction, test_path, nodes, static=True):
    """機械で台帳を作る。要件は、課題の文の部品（C1 全体で確かめる）と、テストのまとまり（項目ごとに確かめる）。"""
    checks = [core.Check("C1", "tests", [test_path] if test_path else [], required=bool(test_path))]
    if static:
        checks.append(core.Check("C2", "static", []))
    reqs = []
    for i, item in enumerate(statement_items(instruction), 1):
        reqs.append(core.Requirement(f"S{i}", "statement", item, ["C1"] if test_path else []))
    for i, (key, members) in enumerate(group_nodes(nodes).items(), 1):
        reqs.append(core.Requirement(f"T{i}", "test", f"{key} ({len(members)} tests)", [f"C1::{n}" for n in members]))
    return core.Ledger(unit, reqs, checks)


def split_exit(output):
    """出力の中の EXIT_MARK から終わりの符号を取り出す。(符号, 印を除いた本文)。
    FeatureBench の exec_command は標準エラーを標準出力の後ろにつなぐので、印の後ろの文も本文に残す。"""
    text = output or ""
    i = text.rfind(EXIT_MARK)
    if i < 0:
        return None, text
    rest = text[i + len(EXIT_MARK):]
    head, _, tail = rest.partition("\n")
    try:
        code = int(head.strip())
    except ValueError:
        code = None
    return code, text[:i] + tail


def parse_pytest(output, code):
    """pytest -rA の出力 → CheckResult の材料（passed・failed・errors・summary）。合否は終わりの符号が 0 か。"""
    passed, failed, errors = [], [], []
    for kind, node, msg in _RA_RE.findall(output or ""):
        if kind in ("PASSED", "XFAIL", "XPASS"):
            passed.append(node)
        else:
            failed.append(node)
            errors.append(f"{node} - {msg}".strip(" -") if msg else node)
    summ = _SUMMARY_RE.findall(output or "")
    summary = summ[-1] if summ else (f"exit {code}" if code is not None else "no result")
    if code not in (0, None) and not failed:
        tail = [line for line in (output or "").splitlines() if line.strip()][-5:]
        errors = errors or [f"pytest exit {code}: " + " | ".join(tail)]
    return passed, failed, errors, summary


def changed_py(status_output):
    """git status --porcelain の出力から、変わった（消したものを除く）.py のうち、テストのファイルでないもの。"""
    files = []
    for line in (status_output or "").splitlines():
        if len(line) < 4 or line[:2].strip() == "D" or "D" in line[:2]:
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        name = path.rsplit("/", 1)[-1]
        if path.endswith(".py") and "/tests/" not in "/" + path and not name.startswith("test_") and name != "conftest.py":
            files.append(path)
    return files


def parse_static(output):
    """pyflakes の出力から、落ちに数える行（undefined name と構文の誤り）だけを拾う。"""
    return [line.strip() for line in (output or "").splitlines() if _STATIC_RE.search(line)]


class ContainerAdapter:
    """実行環境のアダプタ（FeatureBench の課題の箱）。exec(command, timeout) -> (符号, 出力) を渡して作る。"""

    def __init__(self, exec_fn, log_dir, test_timeout=900):
        self.exec = exec_fn
        self.log_dir = Path(log_dir)
        self.test_timeout = test_timeout
        self.round = 0

    def run(self, command, timeout):
        code, out = self.exec(f"{ENV}{command}; echo {EXIT_MARK}$?", timeout)
        mark, body = split_exit(out)
        return (mark if mark is not None else code), body

    def collect(self, test_path):
        _, out = self.run(f"timeout 300 python -m pytest --collect-only -q -p no:cacheprovider {shlex.quote(test_path)}", 360)
        return collected_nodes(out)

    def verify(self, checks):
        self.round += 1
        results = []
        for c in checks:
            t0 = time.monotonic()
            if c.kind == "tests":
                res = self._tests(c)
            elif c.kind == "static":
                res = self._static(c)
            else:
                res = core.CheckResult(c.id, False, errors=[f"unknown check kind {c.kind}"])
            res.seconds = round(time.monotonic() - t0, 1)
            results.append(res)
        return results

    def _save(self, cid, text):
        path = self.log_dir / f"gate_{self.round}_{cid}.txt"
        path.write_text(text or "", encoding="utf-8", errors="replace")
        return path.name

    def _tests(self, c):
        if not c.target:
            return core.CheckResult(c.id, True, summary="no visible test file")
        targets = " ".join(shlex.quote(t) for t in c.target)
        code, out = self.run(f"COLUMNS=250 timeout {self.test_timeout} python -m pytest {targets} -rA --tb=line -q "
                             f"-p no:cacheprovider",
                             self.test_timeout + 60)
        passed, failed, errors, summary = parse_pytest(out, code)
        return core.CheckResult(c.id, code == 0, passed=passed, failed=failed, errors=errors, summary=summary,
                                raw=self._save(c.id, out))

    def _static(self, c):
        _, status = self.run("git status --porcelain --untracked-files=all", 120)
        files = changed_py(status)
        if not files:
            return core.CheckResult(c.id, True, summary="no changed source files")
        args = " ".join(shlex.quote(f) for f in files)
        _, out = self.run(f"PYTHONPATH={TOOLS_DIR} python -m pyflakes {args}", 300)
        if "No module named pyflakes" in out:
            # 道具が無いのに合格にしない（設計 §10）。エージェントへ押し返さず、走りごと止める
            raise RuntimeError("static check tool (pyflakes) is missing in the box")
        bad = parse_static(out)
        return core.CheckResult(c.id, not bad, passed=[f for f in files if not any(b.startswith(f + ":") for b in bad)],
                                failed=bad, errors=bad, summary=f"{len(bad)} problems in {len(files)} changed files",
                                raw=self._save(c.id, out))


class AgyPort:
    """エージェントの実行口（箱の中の agy）。write(local, dest)・exec_stream(command, timeout) -> 符号・fetch(src, local) を渡して作る。"""

    def __init__(self, imp, write, exec_stream, fetch, log_dir, home="/root"):
        self.imp, self.write, self.exec_stream, self.fetch = imp, write, exec_stream, fetch
        self.log_dir = Path(log_dir)
        self.home = home
        self.n = 0
        self.conversation_id = None

    def start(self, prompt, budget_s):
        return self._call(prompt, budget_s, None)

    def send(self, text, budget_s):
        return self._call(text, budget_s, self.conversation_id)

    def _call(self, text, budget_s, conversation_id):
        self.n += 1
        prompt_path, stream_path = f"{LOG_DIR}/agy_prompt_{self.n}.jsonl", f"{LOG_DIR}/agy_stream_{self.n}.jsonl"
        (self.log_dir / f"agy_prompt_{self.n}.txt").write_text(text, encoding="utf-8")  # 手番ごとに送った文（監査用）
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "prompt.jsonl"
            local.write_text(agy_stream.stdin_for(self.imp, text), encoding="utf-8")
            self.write(local, prompt_path)
        ttl = max(int(budget_s), 60)
        argv = agy_stream.args(self.imp, ["agy"], "", ttl, conversation_id=conversation_id)
        command = (f"source /installed-agent/setup-env.sh && cd /testbed && set -o pipefail; HOME={self.home} "
                   f"AGY_CLI_DISABLE_AUTO_UPDATE=1 {shlex.join(argv)} < {prompt_path} | tee {stream_path}")
        t0 = time.monotonic()
        code = self.exec_stream(command, ttl)
        seconds = round(time.monotonic() - t0, 1)
        local_stream = self.log_dir / f"agy_stream_{self.n}.jsonl"
        try:
            self.fetch(stream_path, local_stream)
            text_out = local_stream.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # 走りが箱の中の記録を残せなかった（打ち切りなど）
            text_out = ""
            local_stream.write_text(f"(stream not copied: {type(e).__name__})\n", encoding="utf-8")
        parsed = agy_stream.parse(text_out)
        self.conversation_id = parsed.get("conversation_id") or self.conversation_id
        return core.Turn(ok=(code == 0 and bool(parsed.get("complete"))), model=parsed.get("model") or "",
                         usage=parsed.get("usage") or {}, seconds=seconds, response=(parsed.get("response") or "")[:4000],
                         conversation_id=self.conversation_id or "", note=f"exit {code}; outcome {parsed.get('outcome')}")


def write_records(log_dir, record, expected_model):
    """記録を残す：core_record.json（周回の全部）・agy_usage.json（素の A と同じ形。手番の利用量を足したもの）・
    agy_stream.jsonl（手番ごとの stream をつないだもの。X-01 の読む道具がそのまま使える）。"""
    log_dir = Path(log_dir)
    (log_dir / "core_record.json").write_text(core.dumps(record), encoding="utf-8")
    models = sorted({(r.get("turn") or {}).get("model") or "" for r in record.get("rounds", [])})
    usage = core.total_usage(record)
    (log_dir / "agy_usage.json").write_text(json.dumps({
        "requested_model": expected_model, "reported_model": models[0] if len(models) == 1 else models,
        "usage": usage, "outcome": {"status": record.get("stop"), "decision": record.get("decision")},
        "complete": record.get("stop") not in ("agent_error", "model_mismatch")}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    parts = sorted(log_dir.glob("agy_stream_*.jsonl"), key=lambda p: int(p.stem.rsplit("_", 1)[-1]))
    (log_dir / "agy_stream.jsonl").write_text("".join(p.read_text(encoding="utf-8", errors="replace") for p in parts),
                                              encoding="utf-8")


def tools_ready(tools_src):
    """箱に写す静的解析の道具（pyflakes のパッケージ）が、WSL の上の置き場にあるか。"""
    return bool(tools_src) and (Path(tools_src) / "pyflakes" / "__init__.py").is_file()


def arm_agents(base_cls, arms, tools_src=None):
    """腕ごとのエージェントの類を作る（base_cls は fb_agy の AgyAgent）。{腕の名: 類}。"""
    out = {}
    for arm_name, arm in arms.items():
        out[arm_name] = _arm_class(base_cls, arm_name, dict(arm), tools_src)
    return out


def _arm_class(base_cls, arm_name, arm, tools_src):
    class CoreAgent(base_cls):
        @property
        def name(self):
            return arm_name

        def pre_run_setup(self, container, instance, log_file):
            self.unit = getattr(instance, "instance_id", None) or "unit"
            return super().pre_run_setup(container, instance, log_file)

        def run(self, container, instruction, log_file, timeout=None):
            self.agent_exit_status = None
            budget = int(timeout or 3600)
            t0 = time.monotonic()
            log_dir = Path(log_file).parent
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + f"\nBEGIN Agent Execution: {arm_name}\n" + "=" * 60 + "\n\n")
                f.write(f"Instruction:\n{instruction}\n\n" + "-" * 60 + "\n\n")
            try:
                self.pre_run_hook(container, log_file)
                self.place_token(container)
                if arm.get("static"):
                    if not tools_ready(tools_src):
                        # 利用枠を使う前に止める（静的解析の道具が無いまま、関門を素通りさせない）
                        self.agent_exit_status = "static_tool_missing"
                        raise RuntimeError(f"static check tool is missing: {tools_src!r}")
                    self.cm.copy_to_container(container, Path(tools_src), TOOLS_DIR)

                def exec_fn(command, timeout_s):
                    return self.cm.exec_command(container, command, timeout=timeout_s, log_file=None)

                adapter = ContainerAdapter(exec_fn, log_dir, test_timeout=int(arm.get("test_timeout", 900)))
                port = AgyPort(self.imp,
                               write=lambda local, dest: self.cm.copy_to_container(container, local, dest),
                               exec_stream=lambda command, ttl: self.cm.exec_command_stream(
                                   container, command, log_file=log_file, timeout=ttl),
                               fetch=lambda src, local: self.cm.copy_from_container(container, src, local),
                               log_dir=log_dir)
                test_path = visible_test_path(instruction)
                nodes = adapter.collect(test_path) if (test_path and arm.get("show_ledger")) else []
                ledger = build_ledger(getattr(self, "unit", "unit"), instruction, test_path, nodes,
                                      static=bool(arm.get("static")))
                record = core.run_unit(port, adapter, ledger, instruction, expected_model=self.imp["model_name"],
                                       budget_s=budget - (time.monotonic() - t0), gate=bool(arm.get("gate")),
                                       show_ledger=bool(arm.get("show_ledger")), max_rounds=int(arm.get("max_rounds", 5)))
                record["arm"] = arm_name
                write_records(log_dir, record, self.imp["model_name"])
                if record["stop"] == "model_mismatch":
                    self.agent_exit_status = "model_mismatch"
                    return False
                if not any((r.get("turn") or {}).get("ok") for r in record["rounds"]):
                    self.agent_exit_status = "agent_error"
                    return False
                return True
            except Exception as e:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\nERROR: {type(e).__name__}: {e}\n")
                return False
            finally:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 60 + f"\nEND Agent Execution: {arm_name}\n" + "=" * 60 + "\n\n")

    CoreAgent.__name__ = "CoreAgent_" + re.sub(r"\W", "_", arm_name)
    return CoreAgent
