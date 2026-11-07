"""改善ループの見回りの共通の部品（v3 §7-9、L7）：置き場・状態のファイル・命令の起こし方・通知・bench のジョブの見方。流れは cycle.py と cycle_steps.py。"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_job  # noqa: E402
import hline_base  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402

ROOT = hline_base.ROOT
RUNNING, STOPPED, DONE, ABORTED = "running", "stopped", "done", "aborted"
WAIT, OK, FAILED = "wait", "ok", "failed"
ID = re.compile(r"[a-z][a-z0-9]*")
ACTIVE, STATE = "active.json", "state.json"
MAX_TRIES = 3
TOAST_APP = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"   # 登録していない名の通知は Windows が黙って捨てる


class Stop(Exception):
    """人が見る。同じ段階から resume で続ける。"""


class Reject(Exception):
    """機械の判定で取り込まない。kind は公開に出す決まった語、detail は状態にだけ残す文。"""

    def __init__(self, kind, detail=""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


# ============================================================ 置き場と状態

def home():
    return Path(machine.read_json(hline_base.CONFIG)["out"]) / "cycle"


def runner():
    return home() / "runner"


def settings():
    return machine.read_json(ROOT / "config" / "cycle.json")


def chains_dir():
    return Path(machine.read_json(hline_base.CONFIG)["oracles"]) / "chains"


def bench_place(cid):
    return machine.path("local") / "bench" / "cycle" / cid


def state_path(cid):
    return home() / cid / STATE


def load(cid):
    return json.loads(state_path(cid).read_text(encoding="utf-8"))


def save(st):
    state_path(st["id"]).parent.mkdir(parents=True, exist_ok=True)
    hline_base.write_json(state_path(st["id"]), st)


def active():
    try:
        return json.loads((home() / ACTIVE).read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError):
        return None


def set_active(cid):
    if cid is None:
        (home() / ACTIVE).unlink(missing_ok=True)
    else:
        home().mkdir(parents=True, exist_ok=True)
        hline_base.write_json(home() / ACTIVE, {"id": cid})


def branch(st):
    return f"cycle/{st['id']}"


def now_text():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ============================================================ 命令の起こし方

def sh(args, ttl, label, cwd=None):
    """命令を走らせて標準出力を返す。0 でなければ Stop（詳しい文は状態にだけ残る）。"""
    code, out, err = proc.run([str(a) for a in args], cwd or runner(), ttl, label)
    if code:
        raise Stop(f"{label} が終了コード {code}: {(err or out).strip()[-300:]}")
    return out


def py(*args):
    return [sys.executable, "-m", *args]


def notify(text):
    """Windows の通知を出す。出せなくても周は止めない（理由は状態にある）。"""
    script = ("$t = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]::GetTemplateContent(0); "
              f"$t.GetElementsByTagName('text').Item(0).InnerText = '{text}'; "
              f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP}').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
    try:
        proc.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], home(), 30, "notify")
    except OSError:
        pass


# ============================================================ ジョブ

def spent(out, tags):
    """タグの走りの記録のトークンの合計。走りの記録はステージの終わりに書かれるので、ステージの途中の分は入らない（途中は 1 時間の上限が守る）。"""
    total = 0
    for tag in tags:
        for path in Path(out).glob(f"runs/*/factory-{tag}-s*/factory/run.json"):
            try:
                used = json.loads(path.read_text(encoding="utf-8"))["tokens"]["total"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            total += used if isinstance(used, int) and not isinstance(used, bool) else 0
    return total


def watch_cost(watch):
    """費用の線を越えたらジョブを止めて取り込まない（越えれば不合格が決まる。v3 §7-10）。"""
    if watch and (used := spent(watch["out"], watch["tags"])) > watch["line"]:
        bench_job.stop()
        raise Reject("費用の線", f"トークン {used} が線 {watch['line']:.0f} を越えた")


def job_step(st, key, cwd, cmds, watch=None):
    """ジョブ key を見る。無ければ起こす。WAIT・OK・FAILED（0 でない終了か、見失った）を返す。id はやり直しごとに替える（同じ id は起こせない）。"""
    job = st.setdefault("jobs", {}).setdefault(key, {"tries": 1})
    job["id"] = f"{st['id']}-{key}-{job['tries']}"
    got = bench_job.status(job["id"])
    if got["state"] == "none":
        save(st)   # 起こす前に保存する。ここで落ちても、次の見回りが同じ id を見て起こす
        bench_job.submit(job["id"], cwd, cmds)
        return WAIT
    if got["state"] in ("starting", "running"):
        watch_cost(watch)
        return WAIT
    if got["state"] == "done" and got["exit"] == 0:
        return OK
    job["last"] = got
    return FAILED


def job_done(st, key, cwd, cmds, watch=None):
    """走り直せるジョブ（済んだステージは使い回される）。終わって 0 なら True。駄目なら MAX_TRIES 回まで起こし直し、それでも駄目なら Stop。"""
    result = job_step(st, key, cwd, cmds, watch)
    if result == FAILED:
        job = st["jobs"][key]
        if job["tries"] >= MAX_TRIES:
            raise Stop(f"ジョブ {key} が {MAX_TRIES} 回とも終わりませんでした: {job.get('last')}")
        job["tries"] += 1
    return result == OK
