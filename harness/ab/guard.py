"""A/B 実験の走行の監視（V2-6 の運用。v2-smoke-03・04 で使った総監督の道具をハーネスに入れた）。

    python -m harness.ab.guard <run-id> [<run-id> ...] [--limit-usd 30]

run-all（本走）のときは、その走行の run-id をすべて並べる（例 v2-run-01 v2-run-02 v2-run-03 v2-run-04）。
費用の上限は並べた走行の合計に当てる。止めるときは、動いているドライバ（run・run-all）をすべて止める。

実装役の呼び出しのログを 15 秒ごとに読み、1 行ずつ出す。止める条件（docs/design/v2_6_smoke_plan.md §8 の裁定）に
当たったら、その走行のドライバ（とその子）を自分で止めて終わる。
費用の上限（v2r、docs/design/v2r_protocol.md §4.2）：1 呼び出し 0.5 USD・1 繰り返し 15 USD を超えたらその走行を止め、
中間の線 25 USD は止めずに知らせ、全体の Hard Cap 50 USD に達したら全ドライバを止める（Ledger）。
- 止める：作業場所の外のファイルの中身を読む手番（view_file・読むコマンド）、ビルド・テストを走らせる手番、
  PROPERTY_UNSATISFIABLE の知らせ、実装役の起動の失敗、費用の上限、作業場所の残り
- 記録だけ：作業場所の外の一覧、自分の会話の記録（agy_stream.own_state）
- 警告だけ：200 秒を超えた呼び出し、利用量が下限の呼び出し、agy の ERROR、思考だけの手番・error_message の手番
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import agy_stream  # noqa: E402
import exitcode  # noqa: E402
import narrow_dir  # noqa: E402
from ab import common  # noqa: E402

READ_VERBS = {"get-content", "gc", "cat", "type", "more", "select-string", "sls", "findstr", "copy-item", "cp", "copy"}
BUILD_VERBS = {"dotnet", "msbuild", "csc", "nunit3-console", "vstest.console", "unity", "unity.exe"}
PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\"'\s|;,]*")
PRICES = (0.75e-6, 3.75e-6, 0.075e-6)   # experiments/v2/cost_model.json と同じ（入力・出力・キャッシュ読み）


def classify(stream_text, workdir, own=()):
    """(止める理由の列, 記録だけの列)。道具の引数だけを見る（本文とコマンドの出力は見ない）。"""
    stops, notes = [], []
    inside = [str(x).replace("/", "\\").rstrip("\\").lower() for x in (workdir,) + tuple(own)]

    def outside(p):
        p = p.replace("/", "\\").rstrip("\\").lower()
        return not any(p == b or p.startswith(b + "\\") for b in inside)
    for line in (stream_text or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        su = d.get("step_update") or {}
        if su.get("step_type") != "tool" or su.get("state") != "DONE":
            continue
        prm = (su.get("tool_info") or {}).get("parameters") or {}
        tool, idx = su.get("tool_name"), su.get("step_index")
        cmd = prm.get("CommandLine") if isinstance(prm.get("CommandLine"), str) else ""
        verb = cmd.split()[0].lower() if cmd.strip() else ""
        paths = [p for k in ("CommandLine", "AbsolutePath", "TargetFile") if isinstance(prm.get(k), str)
                 for p in PATH_RE.findall(prm[k])]
        outs = [p for p in paths if outside(p)]
        if verb in BUILD_VERBS or re.search(r"\bdotnet\s+(test|build|run)\b", cmd.lower()):
            stops.append(f"step {idx} {tool} build/test: {verb}")
        elif outs and (tool == "view_file" or verb in READ_VERBS):
            stops.append(f"step {idx} {tool} {verb} read outside: {outs[0][:100]}")
        elif outs:
            notes.append(f"step {idx} {tool} {verb or '-'} outside(list): {outs[0][:100]}")
    return stops, notes


def usd(usage):
    return ((usage.get("input_tokens") or 0) * PRICES[0] + (usage.get("output_tokens") or 0) * PRICES[1]
            + (usage.get("cache_read_tokens") or 0) * PRICES[2])


def split_log(text, condition=None):
    """実装役のログ → (stream-json の本文, プロンプト, stderr の先頭)。まだ書き終わっていなければ None。

    形はログの中身で見分ける（ドライバの形は "# stdout"、pipeline の形は "=== stdout ==="）。condition は使わない
    （v2r の 4 条件はすべてドライバの形）。
    """
    if text.startswith("# prompt"):
        if "\n# stderr" not in text:
            return None
        return (text[text.index("# stdout\n") + 9:text.index("\n\n# stderr")], text[:text.index("# stdout")],
                text[text.index("# stderr"):][:300])
    if "=== stderr ===" not in text:
        return None
    return (text[text.index("=== stdout ===\n") + 15:text.index("\n\n=== stderr ===")], text[:text.index("=== stdout ===")],
            text[text.index("=== stderr ==="):][:300])


# 見る条件：V2 の A・B と、v2r の 4 条件（ディレクトリが無い条件は飛ばす）
CONDITIONS = tuple(dict.fromkeys(common.CONDITIONS + ("A0", "A1", "B-G", "B")))
# 費用の上限（docs/design/v2r_protocol.md §4.2）。「超えたら」は >、Hard Cap の「達したら」は >=
LIMITS = {"per_call": 0.5, "per_replicate": 15.0, "warn": 25.0, "hard_cap": 50.0}


class Ledger:
    """費用の台帳。呼び出しごとの費用を足し、上限に当たった出来事を返す（判定だけ。止めるのは watch）。

    出来事：("stop_run", 走行)＝1 呼び出しの上限、("stop_replicate", 走行)＝1 繰り返し（1 つの run-id の全条件）の上限、
    ("warn", None)＝中間の線（1 回だけ知らせる。止めない）、("stop_all", None)＝全体の Hard Cap。
    """

    def __init__(self, per_call=LIMITS["per_call"], per_replicate=LIMITS["per_replicate"], warn=LIMITS["warn"],
                 hard_cap=LIMITS["hard_cap"]):
        self.per_call, self.per_replicate, self.warn, self.hard_cap = per_call, per_replicate, warn, hard_cap
        self.total, self.per_run, self.warned = 0.0, {}, False

    def add(self, run_id, cost):
        self.total += cost
        self.per_run[run_id] = self.per_run.get(run_id, 0.0) + cost
        events = []
        if cost > self.per_call:
            events.append(("stop_run", run_id, f"1 呼び出し {cost:.4f} USD > {self.per_call}"))
        if self.per_run[run_id] > self.per_replicate:
            events.append(("stop_replicate", run_id,
                           f"1 繰り返し（{run_id}）{self.per_run[run_id]:.4f} USD > {self.per_replicate}"))
        if self.warn is not None and not self.warned and self.total >= self.warn:
            self.warned = True
            events.append(("warn", None, f"中間の線 {self.total:.4f} USD >= {self.warn}（止めない。人間が確認する）"))
        if self.total >= self.hard_cap:
            events.append(("stop_all", None, f"Hard Cap {self.total:.4f} USD >= {self.hard_cap}"))
        return events


def stop_driver(run_id=None):
    """ドライバを止める。run_id を渡すと、その走行のドライバ（と、全走行を 1 つのプロセスで回す run-all）だけ。"""
    target = "'*harness.ab.driver*'"
    only = (f" -and ($_.CommandLine -like '*{run_id}*' -or $_.CommandLine -like '*run-all*')" if run_id else "")
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
          f"{target} -and $_.Name -like 'python*'{only} }} | "
          "ForEach-Object { taskkill /PID $_.ProcessId /T /F }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=120)


def watch(run_ids, limit_usd, interval=15, out=print, midpoint_usd=None, ledger=None, stop=stop_driver):
    """実装役のログを interval 秒ごとに読み、止める条件と費用の上限（Ledger）を見る。

    limit_usd：全体の Hard Cap（ledger を渡さないときに使う）。midpoint_usd：前半の走行（run_ids の前半）の費用が
    これを超えていたら、後半の最初の呼び出しで止める（V2-RUN の運用。v2r は ledger の中間の線で知らせる）。
    """
    ledger = ledger or Ledger(hard_cap=limit_usd)
    seen, spent, per_run = set(), {}, {}
    first_half = set(run_ids[:len(run_ids) // 2])
    narrow_root = Path(tempfile.gettempdir()) / narrow_dir.ROOT_NAME
    while True:
        for run_id, cond in [(r, c) for r in run_ids for c in CONDITIONS]:
            p = common.paths(run_id, cond)
            if not p["out"].exists():
                continue
            logs = sorted(set(p["out"].glob("T*_a*.implementer.log")) | set(p["out"].rglob("implementer_attempt_*.log")))
            for f in logs:
                key = (str(f), f.stat().st_mtime_ns)
                if key in seen:
                    continue
                parts = split_log(f.read_text(encoding="utf-8", errors="replace"), cond)
                if parts is None:
                    continue
                seen.add(key)
                stream, prompt, err = parts
                # 作業場所：ドライバの形のログは作業ツリー、pipeline の形はサンドボックスから作った細い作業場所
                wd = narrow_dir.path_for(p["wt"] if prompt.startswith("# prompt") else p["sandbox"])
                pr = agy_stream.parse(stream, wd)
                u, oc = pr["usage"], pr["outcome"]
                if midpoint_usd is not None and run_id not in first_half and first_half:
                    early = sum(per_run.get(r, 0.0) for r in first_half)
                    if early > midpoint_usd:
                        out(f"STOP midpoint {early:.2f} USD > {midpoint_usd}（前半の走行）")
                        stop()
                        return 1
                spent[cond] = spent.get(cond, 0.0) + usd(u)
                per_run[run_id] = per_run.get(run_id, 0.0) + usd(u)
                secs = round(sum(s.get("seconds") or 0 for s in pr["steps"]), 1)
                stops, notes = classify(stream, wd, agy_stream.own_state(pr["conversation_id"]))
                if "PROPERTY_UNSATISFIABLE" in prompt:
                    stops.append("PROPERTY_UNSATISFIABLE in feedback")
                if "WinError" in err or "rc: 127" in prompt:
                    stops.append("launch failure")
                if pr["token_problems"]:
                    # 思考が出力を超える手番：費用の式の前提（思考は出力に含まれる）が崩れている（v2r §9.1）
                    stops.append("thinking > output: " + "; ".join(pr["token_problems"][:3]))
                warns = [w for w, on in (("WARN:partial", u.get("partial")), ("WARN:>200s", secs > 200),
                                         ("WARN:agy-ERROR", oc["status"] == "ERROR"),
                                         ("WARN:error_steps", oc["error_steps"]),
                                         ("WARN:thinking_only", oc["thinking_only_steps"])) if on]
                out(f"[{run_id} {cond}] {f.relative_to(p['out']).as_posix()} steps={u.get('model_steps')} secs={secs} "
                    f"usd={usd(u):.4f} total={sum(spent.values()):.4f} {' '.join(warns)}")
                for n in notes:
                    out(f"  NOTE {n}")
                if stops:
                    out(f"STOP {cond} {f.name}: " + "; ".join(stops))
                    stop()
                    return 1
                halted = False
                for kind, rid, msg in ledger.add(run_id, usd(u)):
                    if kind == "warn":
                        out(f"WARN {msg}")
                        continue
                    out(f"STOP {kind} {msg}")
                    stop(rid) if kind in ("stop_run", "stop_replicate") else stop()
                    halted = True
                if halted:
                    return 1
        if narrow_root.exists() and len(list(narrow_root.iterdir())) > 1:
            out(f"STOP narrow dirs={len(list(narrow_root.iterdir()))}")
            stop()
            return 1
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="A/B 実験の走行の監視（止める条件で自分で止める）")
    ap.add_argument("run_ids", nargs="+")
    ap.add_argument("--limit-usd", type=float, default=LIMITS["hard_cap"], help="全体の Hard Cap（v2r は 50）")
    ap.add_argument("--per-call-usd", type=float, default=LIMITS["per_call"], help="1 呼び出しの上限（v2r は 0.5）")
    ap.add_argument("--per-replicate-usd", type=float, default=LIMITS["per_replicate"],
                    help="1 繰り返し（1 つの run-id の全条件）の上限（v2r は 15）")
    ap.add_argument("--warn-usd", type=float, default=LIMITS["warn"], help="中間の線。止めずに知らせる（v2r は 25）")
    ap.add_argument("--midpoint-usd", type=float, help="前半の走行の費用の線（V2-RUN の運用。本走は 15）")
    args = ap.parse_args(argv)
    ledger = Ledger(args.per_call_usd, args.per_replicate_usd, args.warn_usd, args.limit_usd)
    return watch(args.run_ids, args.limit_usd, out=lambda s: print(s, flush=True), midpoint_usd=args.midpoint_usd,
                 ledger=ledger)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
