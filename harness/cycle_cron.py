"""改善ループの見回りの定期起動（v3 §7-9、L7）：タスク スケジューラから、画面を出さずに harness.cycle tick を呼ぶ。hline_cron の形を使い回す。

    python -m harness.cycle_cron xml          登録する内容（タスクの XML）を表示する。登録はしない
    python -m harness.cycle_cron setup        専用のクローンを作る（H ラインの out の下。bench は読めも書けもしない）
    python -m harness.cycle_cron register     専用のクローンを作り、タスクを登録する（10 分ごと。操縦士の明示の ok の後だけ使う）
    python -m harness.cycle_cron unregister   登録を解く
    pythonw.exe <専用のクローン>/harness/cycle_cron.py run --machine <machine.json>   定期起動から呼ばれる入口

走っている周が無いときの見回りは何もしない。周を始めるのは harness.cycle start（操縦士の ok の後）。
"""
import argparse
import contextlib
import datetime
import os
import sys
import tempfile
import traceback
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cycle  # noqa: E402
import cycle_base as base  # noqa: E402
import exitcode  # noqa: E402
import hline_cron  # noqa: E402
import machine  # noqa: E402
import proc  # noqa: E402
from hline_base import ROOT  # noqa: E402

TASK = "game-harness-cycle"
EVERY_MINUTES = 10
TIME_LIMIT = "PT4H"   # 採点と Argon が 1 回の見回りの中で走る。held-out の採点は長くても数十分
LOG = "tick.log"


def task_xml(exe=None, machine_file=None, every=EVERY_MINUTES, start=None):
    """見回りのタスクの定義。ログインしている間だけ走る。前の回が走っていれば起こさず、逃した回はスリープの後に走り、電源の条件では止めない。"""
    start = (start or datetime.datetime.now().replace(second=0, microsecond=0)).isoformat()
    script = base.runner() / "harness" / "cycle_cron.py"
    args = f'"{script}" run --machine "{machine_file or machine.machine_file()}"'
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <RegistrationInfo><Description>game-harness の改善ループの見回り（python -m harness.cycle tick）を画面を出さずに呼ぶ。"
            "harness/cycle_cron.py が登録した</Description></RegistrationInfo>\n"
            f"  <Triggers><TimeTrigger><Repetition><Interval>PT{every}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
            f"<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>\n"
            '  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n'
            "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
            f"<StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled><ExecutionTimeLimit>{TIME_LIMIT}</ExecutionTimeLimit></Settings>\n"
            f'  <Actions Context="Author"><Exec><Command>{escape(str(hline_cron.pythonw(exe)))}</Command><Arguments>{escape(args)}</Arguments>'
            f"<WorkingDirectory>{escape(str(base.runner()))}</WorkingDirectory></Exec></Actions>\n"
            "</Task>\n")


def setup():
    """専用のクローンを作る。あれば何もしない。"""
    run = base.runner()
    if run.exists():
        print(f"- 専用のクローンはもうある: {run}")
        return 0
    code, out, err = proc.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], ROOT, 60, "origin")
    if code:
        print(f"origin を読めません: {(err or out).strip()}", file=sys.stderr)
        return 2
    run.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = proc.run(["git", "clone", "-q", out.strip(), str(run)], ROOT, 900, "クローン")
    if code:
        print(f"クローンできません: {(err or out).strip()}", file=sys.stderr)
        return 2
    print(f"- 専用のクローンを作った: {run}")
    return 0


def register(every=EVERY_MINUTES):
    code = setup()
    if code:
        return code
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(task_xml(every=every), encoding="utf-16")   # schtasks /XML は UTF-16 を読む
        code, out, err = proc.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/F"], ROOT, 60, "schtasks")
    print((out or err).strip())
    return code


def unregister():
    code, out, err = proc.run(["schtasks", "/Delete", "/TN", TASK, "/F"], ROOT, 60, "schtasks")
    print((out or err).strip())
    return code


def run(machine_file=None, tick=None, now=None):
    """定期起動の入口。作業ディレクトリを固定し、見回りの出力を <out>/cycle/tick.log に追記する。終了コードは見回りのもの。"""
    os.chdir(ROOT)
    if machine_file:
        os.environ[machine.ENV] = machine_file   # 専用のクローンの親には .local が無いので、machine.json の場所を渡す
    if Path(sys.executable).name.lower() == "pythonw.exe":   # 子はコンソール用の python.exe で起こす
        sys.executable = str(Path(sys.executable).with_name("python.exe"))
    log = base.home() / LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", errors="replace") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        print(f"=== {(now or datetime.datetime.now)():%Y-%m-%d %H:%M:%S} 見回り（定期起動、pid {os.getpid()}）", flush=True)
        try:
            code = (tick or cycle.tick)()
        except Exception:   # noqa: BLE001 - 落ちた理由をログに残す（画面が無いので、ここにしか出ない）
            traceback.print_exc()
            code = 2
        print(f"--- 終了コード {code}", flush=True)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="改善ループの見回りの定期起動（タスク スケジューラ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("xml", "setup", "register", "unregister"):
        sub.add_parser(name)
    sub.add_parser("run").add_argument("--machine")
    args = ap.parse_args(argv)
    if args.cmd == "xml":
        print(task_xml(), end="")
        return 0
    if args.cmd == "run":
        return run(args.machine)
    return {"setup": setup, "register": register, "unregister": unregister}[args.cmd]()


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
