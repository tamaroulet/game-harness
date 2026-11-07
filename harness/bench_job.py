"""bench のアカウントのジョブ（v3 §7-9、L7）：操縦士のアカウントから、bench で走る予定のタスクを起こす。

    python -m harness.bench_job xml               登録する内容（タスクの XML）を表示する。登録はしない
    python -m harness.bench_job register          管理者のコンソールで 1 回だけ。bench の密語は schtasks が聞く（このコードは見ない）
    python -m harness.bench_job grant             操縦士のアカウントにタスクの権限を足す（register がする。やり直すときだけ）
    python -m harness.bench_job unregister        管理者のコンソールで、登録を解く
    python -m harness.bench_job run --id <id> --cwd <dir> --cmd <行> [--cmd <行>]...
    python -m harness.bench_job status --id <id>  ジョブの状態を 1 行で出す
    python -m harness.bench_job stop              走っているジョブを止める

タスクはトリガーを持たず、起こされたときだけ、決まったバッチ（<local>/bench/cycle/job.bat）を 1 本走らせる。ジョブごとにバッチの中身を
差し替えるので、密語を入れるのは登録の 1 回だけで済む。登録には管理者の権限が要る（ほかのアカウントのタスク）。起こす・見る・止めるのは、
権限を上げていない操縦士のアカウントでできるように、登録のときにタスクの権限へ足す。
走らせ方の条件は、L1〜L6 の runas と同じになるようにする：優先度は Normal（タスクの既定は Below Normal）、環境は bench_env.bat。
記録はジョブごとに <local>/bench/cycle/jobs/<id>/（job.json・job.log・started・done）。この置き場には bench も書けるので、
操縦士のアカウントからはデータとして読むだけにし、ここのものを実行しない。
"""
import argparse
import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402
from hline_base import ROOT, write_json  # noqa: E402

TASK = "game-harness-bench"
ACCOUNT = "bench"
TIME_LIMIT = "PT24H"     # held-out の 18 ステージは 6 時間 21 分（L6）。枠切れの待ちを見込んでも越えない上限
PRIORITY = 5             # NORMAL_PRIORITY_CLASS・THREAD_PRIORITY_NORMAL。タスクの既定の 7 は Below Normal で、runas の走りと条件が変わる
BATCH, JOBS, META, LOG, STARTED, DONE = "job.bat", "jobs", "job.json", "job.log", "started", "done"
ID = re.compile(r"[A-Za-z0-9._-]+")
EXIT = re.compile(r"exit=(-?\d+)")
RUNNING = ("Running", "Queued")
GRACE_SECONDS = 120      # 起こした直後は、タスクの状態がまだ Ready でも見失ったとしない
TTL = 60

STATE_SCRIPT = ("$t = Get-ScheduledTask -TaskName '{task}' -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
                "Write-Output \"$($t.State) $($i.LastTaskResult)\"")
GRANT_SCRIPT = ("$s = New-Object -ComObject Schedule.Service; $s.Connect(); $t = $s.GetFolder('\\').GetTask('{task}'); "
                "$d = $t.GetSecurityDescriptor(4); if ($d -notmatch '{sid}') {{ "
                "$t.SetSecurityDescriptor(($d -replace '^(D:[A-Z]*)', '$1(A;;FA;;;{sid})'), 0) }}")


class JobError(Exception):
    pass


def home():
    return machine.path("local") / "bench" / "cycle"


def bench_env():
    return home().parent / "bench_env.bat"


def job_dir(job_id):
    return home() / JOBS / job_id


# ============================================================ 登録

def task_xml():
    """bench のタスクの定義。トリガーは持たず、起こされたときだけ走る。走っている間は重ねない。
    電源の条件では止めない（ジョブは走り直せる。条件を付けると、コードを抜いただけで長いジョブが止まる）。"""
    batch = home() / BATCH
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <RegistrationInfo><Description>game-harness の bench のジョブ（1 本ずつ）。harness/bench_job.py が登録した</Description></RegistrationInfo>\n"
            "  <Triggers />\n"
            f'  <Principals><Principal id="Author"><UserId>{ACCOUNT}</UserId><LogonType>Password</LogonType>'
            "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
            "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
            "<AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable>"
            "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
            "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
            "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle>"
            f"<WakeToRun>false</WakeToRun><ExecutionTimeLimit>{TIME_LIMIT}</ExecutionTimeLimit><Priority>{PRIORITY}</Priority></Settings>\n"
            f'  <Actions Context="Author"><Exec><Command>cmd.exe</Command><Arguments>/c "{escape(str(batch))}"</Arguments>'
            f"<WorkingDirectory>{escape(str(home()))}</WorkingDirectory></Exec></Actions>\n"
            "</Task>\n")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):   # Windows の外
        return False


def schtasks(*args):
    """(終了コード, 出力)。"""
    code, out, err = proc.run(["schtasks", *args], ROOT, TTL, "schtasks")
    return code, (out or err).strip()


def powershell(script, label):
    return proc.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], ROOT, TTL, label)


def my_sid():
    """今のアカウントの SID。"""
    code, out, err = proc.run(["whoami", "/user", "/fo", "csv", "/nh"], ROOT, TTL, "whoami")
    m = re.search(r"S-1-5-[0-9-]+", out or "")
    if code or not m:
        raise JobError(f"アカウントの SID を読めません: {(out or err).strip()}")
    return m.group(0)


def grant():
    """今のアカウントが、権限を上げずにタスクを起こす・見る・止めることができるように、タスクの権限に足す（足してあれば何もしない）。"""
    code, out, err = powershell(GRANT_SCRIPT.format(task=TASK, sid=my_sid()), "grant")
    if code:
        print(f"タスクの権限を足せませんでした: {(err or out).strip()}", file=sys.stderr)
        return 1
    print(f"- タスク {TASK} の権限に、今のアカウントを足した")
    return 0


def register():
    """密語は schtasks が端末で聞く（標準入出力をつないだまま起こし、このプロセスは密語を受け取らない）。"""
    if not is_admin():
        print("管理者のコンソールで走らせてください（ほかのアカウントのタスクの登録には、管理者の権限が要ります）", file=sys.stderr)
        return 2
    home().mkdir(parents=True, exist_ok=True)
    batch = home() / BATCH
    if not batch.exists():
        batch.write_bytes(b"@echo off\r\nrem No job yet. harness.bench_job replaces this file.\r\nexit /b 0\r\n")
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(task_xml(), encoding="utf-16")   # schtasks /XML は UTF-16 を読む
        code = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/RU", ACCOUNT, "/RP", "*", "/F"],
                              timeout=600).returncode
    return grant() if code == 0 else (code or 1)


def unregister():
    code, out = schtasks("/Delete", "/TN", TASK, "/F")
    print(out)
    return code


# ============================================================ ジョブ

def problems(job_id, cwd, cmds):
    """ジョブの形の誤りの文の列。バッチに入れるので、ASCII だけで、改行と % を通さない（cmd が替えてしまう）。"""
    errs = [] if ID.fullmatch(job_id) else [f"id の形が違います（英数字と . _ - だけ）: {job_id!r}"]
    if not cmds:
        errs.append("--cmd がありません")
    for text in (str(cwd), *cmds):
        if not text.isascii() or any(c in text for c in "\r\n%"):
            errs.append(f"バッチに入れられない文字があります（ASCII だけで、改行と % は不可）: {text!r}")
    return errs


def batch_text(job_id, cwd, cmds):
    """1 本のジョブのバッチ（cmd が読むので ASCII と CRLF）。行が落ちたらそこで止め、最後の終了コードを done に書く。
    「if errorlevel 1」でなく「||」で見る：bench_env.bat の「set X=」は、X が無いと ERRORLEVEL を 1 にし、cd の成功はそれを戻さない。
    if errorlevel は負の終了コード（例外で落ちたプロセス）も見逃す。"""
    d = job_dir(job_id)
    return "\r\n".join(["@echo off", f"rem Written by harness.bench_job ({job_id}). The scheduled task {TASK} runs this file.",
                        f"call {bench_env()}", f"echo started > {d / STARTED}", f"cd /d {cwd} || goto end",
                        *(f"{cmd} >> {d / LOG} 2>&1 || goto end" for cmd in cmds),
                        ":end", f"echo exit=%ERRORLEVEL% > {d / DONE}"]) + "\r\n"


def task_state():
    """(タスクの状態, 前の走りの結果)。今のアカウントから見えなければ JobError。"""
    code, out, err = powershell(STATE_SCRIPT.format(task=TASK), "task state")
    parts = (out or "").split()
    if code or len(parts) != 2:
        raise JobError(f"タスク {TASK} の状態を読めません: {(err or out).strip()}")
    return parts[0], parts[1]


def submit(job_id, cwd, cmds, now=None):
    """ジョブを 1 本起こす。形の誤り・走っているジョブがある・同じ id のジョブがある・起こせないときは JobError（どれも走らせない）。"""
    if (errs := problems(job_id, cwd, cmds)):
        raise JobError("；".join(errs))
    d = job_dir(job_id)
    if d.exists():
        raise JobError(f"同じ id のジョブがあります: {job_id}")
    state, _ = task_state()
    if state in RUNNING:
        raise JobError(f"走っているジョブがあります（タスクの状態 {state}）")
    d.mkdir(parents=True)
    at = (now or datetime.datetime.now)().astimezone().isoformat(timespec="seconds")
    write_json(d / META, {"id": job_id, "cwd": str(cwd), "cmds": list(cmds), "at": at, "epoch": time.time()})
    tmp = home() / (BATCH + ".tmp")
    tmp.write_bytes(batch_text(job_id, cwd, cmds).encode("ascii"))
    os.replace(tmp, home() / BATCH)
    code, out = schtasks("/Run", "/TN", TASK)
    if code:
        raise JobError(f"タスクを起こせません: {out}")
    return d


def read_meta(d):
    try:
        return json.loads((d / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def exit_code(d):
    """done の終了コード。まだ無い・読めなければ None。"""
    try:
        m = EXIT.search((d / DONE).read_text(encoding="ascii", errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def status(job_id, clock=time.time):
    """{state: none | starting | running | done | lost, ...}。done には exit、lost には前の走りの結果（last_result）を添える。
    lost は、タスクが走っていないのに done が無いもの（止められた・PC が落ちた・起こせなかった）。"""
    d = job_dir(job_id)
    meta = read_meta(d)
    if meta is None:
        return {"id": job_id, "state": "none"}
    code = exit_code(d)
    if code is not None:
        return {"id": job_id, "state": "done", "exit": code}
    state, last = task_state()
    if state in RUNNING:
        return {"id": job_id, "state": "running", "started": (d / STARTED).exists()}
    if clock() - float(meta.get("epoch") or 0) < GRACE_SECONDS:
        return {"id": job_id, "state": "starting"}
    return {"id": job_id, "state": "lost", "task_state": state, "last_result": last, "started": (d / STARTED).exists()}


def stop():
    code, out = schtasks("/End", "/TN", TASK)
    print(out)
    return code


# ============================================================ 命令

def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.bench_job", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("xml", "register", "grant", "unregister", "stop"):
        sub.add_parser(name)
    r = sub.add_parser("run")
    r.add_argument("--id", required=True)
    r.add_argument("--cwd", required=True)
    r.add_argument("--cmd", required=True, action="append")
    sub.add_parser("status").add_argument("--id", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "xml":
            print(task_xml(), end="")
            return 0
        if a.command == "run":
            print(f"- 起こした: {submit(a.id, a.cwd, a.cmd)}")
            return 0
        if a.command == "status":
            print(status(a.id))
            return 0
        return {"register": register, "grant": grant, "unregister": unregister, "stop": stop}[a.command]()
    except JobError as e:
        print(f"止めた: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
