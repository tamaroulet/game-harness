"""report.md の「## probe」節：origin/main・統合ブランチ・CI・実行環境の実測。

総監督はシェルを使えないので、H ラインが report.md を書くたびに、既存の probe（harness/probe.py）と
H ラインがすでに使っている git・gh の呼び出しで取った値を、出典と取得時刻つきで出す。
取れなかった項目は「取得失敗：理由」と書く（古い値や unknown で埋めない）。失敗しても report.md の書き出しは止めない。
CI の照会（gh）は、直近の取得から CI_TTL 秒以内なら前回の値と取得時刻を出す。
"""
import datetime
import json
import time
from pathlib import Path

import yaml

import probe
import proc
from hline_base import ROOT
from hline_git import remote_ref

CI_TTL = 600
PROGRESS_REL = "docs/progress.yaml"


def stamp(at):
    return datetime.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")


def failure(reason):
    return f"取得失敗：{' '.join(str(reason).split())[:120] or '理由不明'}"


def run(cfg, args, label, runner):
    """(成功なら出力, 失敗なら理由)。例外も失敗の理由にする。"""
    try:
        code, out, err = runner(args, ROOT, cfg["ttl_seconds"]["git"], label)
    except Exception as e:   # noqa: BLE001 - probe の失敗でラインを止めない
        return None, f"{label}: {e}"
    if code != 0:
        return None, f"{label} が終了コード {code}（{(err.strip().splitlines() or [''])[0]}）"
    return out, None


def head_of(cfg, ref, runner):
    out, why = run(cfg, ["git", "log", "-1", "--format=%h %s", ref], "git log", runner)
    return failure(why) if why else out.strip() or failure("出力が空")


def active_task(cfg, runner):
    out, why = run(cfg, ["git", "show", f"{cfg['base']}:{PROGRESS_REL}"], "git show", runner)
    if why:
        return failure(why)
    try:
        return str(yaml.safe_load(out)["active_task_id"])
    except Exception as e:   # noqa: BLE001
        return failure(f"{PROGRESS_REL} を読めない: {type(e).__name__}")


def ci_fetch(cfg, runner):
    out, why = run(cfg, ["gh", "run", "list", "--branch", "main", "--limit", "1",
                         "--json", "workflowName,headSha,conclusion,status"], "gh run list", runner)
    if why:
        return failure(why)
    try:
        r = json.loads(out)[0]
        return (f"{r['workflowName']}／対象 {str(r['headSha'])[:7]}／結論 {r['conclusion'] or r['status']}")
    except Exception:   # noqa: BLE001
        return failure("gh の出力に run が無い")


def ci_line(cfg, runner, clock, cache):
    """(値, 取得時刻)。直近の取得から CI_TTL 秒以内なら gh を呼ばず前回の値を返す。失敗も取得の 1 回に数える。"""
    try:
        old = json.loads(cache.read_text(encoding="utf-8"))
        if 0 <= clock - old["at"] < CI_TTL:
            return old["value"], old["at"]
    except Exception:   # noqa: BLE001
        pass
    value = ci_fetch(cfg, runner)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": clock, "value": value}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value, clock


def section(cfg, runner=None, clock=None, cache=None):
    runner = runner or proc.run
    clock = time.time() if clock is None else clock
    cache = Path(cache) if cache else Path(cfg["out"]) / "probe_ci.json"
    at = stamp(clock)
    ci, ci_at = ci_line(cfg, runner, clock, cache)
    env = probe.get_env_info()
    git = "git log -1"
    return "\n".join([
        "## probe",
        f"- origin/main の HEAD: {head_of(cfg, cfg['base'], runner)}（出典: {git} {cfg['base']}／取得: {at}）",
        f"- 統合ブランチの HEAD: {head_of(cfg, remote_ref(cfg), runner)}（出典: {git} {remote_ref(cfg)}／取得: {at}）",
        f"- main の最新の CI: {ci}（出典: gh run list --branch main／取得: {stamp(ci_at)}）",
        f"- 実行環境: MuJoCo {env['mujoco']}／Python {env['python']}（出典: harness.probe／取得: {at}）",
        f"- origin/main の active_task_id: {active_task(cfg, runner)}"
        f"（出典: git show {cfg['base']}:{PROGRESS_REL}／取得: {at}）",
    ]) + "\n"


def safe_section(cfg, **kw):
    try:
        return section(cfg, **kw)
    except Exception as e:   # noqa: BLE001 - report.md の書き出しを止めない
        return f"## probe\n- {failure(f'{type(e).__name__}: {e}')}\n"
