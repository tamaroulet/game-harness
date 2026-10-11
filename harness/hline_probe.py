"""report.md の「## probe」節：origin/main・統合ブランチ・CI・実行環境の実測。

総監督はシェルを使えないので、H ラインが report.md を書くたびに、既存の probe（harness/probe.py）と
H ラインがすでに使っている git・gh の呼び出しで取った値を、出典と取得時刻つきで出す。
取れなかった項目は「取得失敗：理由」と書く（古い値や unknown で埋めない）。失敗しても report.md の書き出しは止めない。
CI の照会（gh）は、直近の取得から CI_TTL 秒以内なら前回の値と取得時刻を出す。
"""
import datetime
import json
import statistics
import time
from pathlib import Path

import yaml

import hline_merge
import probe
import proc
from hline_base import ROOT
from hline_git import remote_ref

CI_TTL = 600
APPROVED_LABEL = "ms4:approved"
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


def cached(cache, clock, fetch, key=None):
    """(値, 取得時刻)。直近の取得から CI_TTL 秒以内（かつ key が同じ）なら fetch を呼ばず前回の値を返す。失敗も取得の 1 回に数える。"""
    try:
        old = json.loads(cache.read_text(encoding="utf-8"))
        if old.get("key") == key and 0 <= clock - old["at"] < CI_TTL:
            return old["value"], old["at"]
    except Exception:   # noqa: BLE001
        pass
    value = fetch()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": clock, "value": value, "key": key}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value, clock


def ci_line(cfg, runner, clock, cache):
    """(値, 取得時刻)。直近の取得から CI_TTL 秒以内なら gh を呼ばず前回の値を返す。失敗も取得の 1 回に数える。"""
    return cached(cache, clock, lambda: ci_fetch(cfg, runner))


def span(seconds):
    """秒数を「M 分 S 秒」（1 時間以上は「H 時間 M 分 S 秒」）に直す。"""
    s = max(0, int(round(seconds)))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return (f"{h} 時間 " if h else "") + f"{m} 分 {s} 秒"


def _time(text):
    return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()


def _elapsed(run_):
    """走行・チェックの startedAt から終了の時刻（updatedAt か completedAt）までの秒数。取れなければ None。"""
    try:
        end = run_.get("updatedAt") or run_.get("completedAt")
        return max(0.0, _time(end) - _time(run_["startedAt"]))
    except Exception:   # noqa: BLE001
        return None


def duration_fetch(cfg, runner):
    """直近 5 回の走行のうち完了したものの所要（startedAt から updatedAt）の中央値と最大と、数えた走行の数。"""
    out, why = run(cfg, ["gh", "run", "list", "--branch", "main", "--limit", "5",
                         "--json", "status,conclusion,startedAt,updatedAt"], "gh run list", runner)
    if why:
        return failure(why)
    try:
        runs = json.loads(out)
        spans = [d for r in runs if str(r.get("status")).lower() == "completed" and (d := _elapsed(r)) is not None]
    except Exception:   # noqa: BLE001
        return failure("gh の出力を読めない")
    if not spans:
        return f"完了した走行なし（直近 {len(runs)} 回のうち 0 回）"
    return f"中央値 {span(statistics.median(spans))}／最大 {span(max(spans))}（数えた走行 {len(spans)} 回／直近 {len(runs)} 回）"


def _check(rollup, workflow, job):
    latest = hline_merge._runs(rollup, workflow)   # ワークフローごとに最新の走行だけ
    if not latest:
        return f"{workflow}: チェックなし"
    c = latest[0]
    took = _elapsed(c) if str(c.get("status")).upper() == "COMPLETED" else None   # 未完了の completedAt は 0001-01-01 で入る
    return (f"{workflow}（{job}）: 状態 {c.get('status') or '不明'}／結論 {c.get('conclusion') or 'なし'}"
            f"／所要 {span(took) if took is not None else '-'}")


def pr_fetch(cfg, runner, url):
    """統合 PR の今の head の harness-tests・approval のチェックと、ms4:approved のラベルの有無。"""
    out, why = run(cfg, ["gh", "pr", "view", url, "--json", "statusCheckRollup,labels"], "gh pr view", runner)
    if why:
        return failure(why)
    try:
        data = json.loads(out)
        rollup, labels = data.get("statusCheckRollup") or [], data.get("labels") or []
        approved = any(isinstance(x, dict) and x.get("name") == APPROVED_LABEL for x in labels)
        return "／".join([_check(rollup, "harness-tests", "test"), _check(rollup, "approval", "approval"),
                          f"{APPROVED_LABEL} {'あり' if approved else 'なし'}"])
    except Exception:   # noqa: BLE001
        return failure("gh の出力を読めない")


def wait_lines(cfg, runner, clock, cache, pr):
    """人の待ちの 2 行：main の CI の所要、統合 PR の CI（pr があるときだけ）。"""
    dur, dur_at = cached(cache.with_name("probe_ci_duration.json"), clock, lambda: duration_fetch(cfg, runner))
    lines = [f"- main の CI の所要: {dur}（出典: gh run list --branch main／取得: {stamp(dur_at)}）"]
    if pr:
        val, at = cached(cache.with_name("probe_pr_ci.json"), clock, lambda: pr_fetch(cfg, runner, pr), key=pr)
        lines.append(f"- 統合 PR の CI: {val}（出典: gh pr view {pr} --json statusCheckRollup,labels／取得: {stamp(at)}）")
    return lines


def section(cfg, runner=None, clock=None, cache=None, pr=None, waits=False):
    runner = runner or proc.run
    clock = time.time() if clock is None else clock
    cache = Path(cache) if cache else Path(cfg["out"]) / "probe_ci.json"
    at = stamp(clock)
    ci, ci_at = ci_line(cfg, runner, clock, cache)
    env = probe.get_env_info()
    git = "git log -1"
    lines = [
        "## probe",
        f"- origin/main の HEAD: {head_of(cfg, cfg['base'], runner)}（出典: {git} {cfg['base']}／取得: {at}）",
        f"- 統合ブランチの HEAD: {head_of(cfg, remote_ref(cfg), runner)}（出典: {git} {remote_ref(cfg)}／取得: {at}）",
        f"- main の最新の CI: {ci}（出典: gh run list --branch main／取得: {stamp(ci_at)}）",
    ]
    lines += [
        f"- 実行環境: MuJoCo {env['mujoco']}／Python {env['python']}（出典: harness.probe／取得: {at}）",
        f"- origin/main の active_task_id: {active_task(cfg, runner)}"
        f"（出典: git show {cfg['base']}:{PROGRESS_REL}／取得: {at}）",
    ]
    if waits:   # 人の待ちの 2 行は report.md の書き出しだけが付ける。既存の行の後ろ
        lines += wait_lines(cfg, runner, clock, cache, pr)
    return "\n".join(lines) + "\n"


def safe_section(cfg, **kw):
    try:
        return section(cfg, **kw)
    except Exception as e:   # noqa: BLE001 - report.md の書き出しを止めない
        return f"## probe\n- {failure(f'{type(e).__name__}: {e}')}\n"
