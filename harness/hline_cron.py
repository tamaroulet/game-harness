"""H ライン（harness/hline.py）の定期起動：タスク スケジューラから、画面を出さずに `poll` を呼ぶ。

    python -m harness.hline_cron xml          登録する内容（タスクの XML）を表示する。登録はしない
    python -m harness.hline_cron register     タスク スケジューラに登録する（10 分ごと。人間の明示の承認の後だけ使う）
    python -m harness.hline_cron unregister   登録を解く
    pythonw.exe harness/hline_cron.py run     定期起動から呼ばれる入口（コンソールを持たないので画面が出ない）

run は、起動のしかたに関係なく作業ディレクトリをこのリポジトリに固定し、出力を <out>/poll.log に追記する（pythonw では
標準出力が失われる）。1 回の走行の最初に時刻を 1 行書く。同時に 2 つ走らないことは poll のロック（acquire_lock）が保証し、
タスクの設定（前の回が走っていれば新しく起動しない）が重ねて防ぐ。子プロセスは proc.run が CREATE_NO_WINDOW で起こす。
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
from hline_base import ROOT, load_config  # noqa: E402

import proc  # noqa: E402

TASK = "game-harness-hline-poll"
EVERY_MINUTES = 10
LOG = "poll.log"


def pythonw(exe=None):
    """コンソールを持たない Python（python.exe の隣の pythonw.exe）。"""
    exe = Path(exe or sys.executable)
    return exe.with_name("pythonw.exe") if exe.name.lower() == "python.exe" else exe


def task_xml(exe=None, every=EVERY_MINUTES, start=None):
    """タスク スケジューラの定義。設定は 3 つ：逃した回はスリープからの復帰後に実行する（StartWhenAvailable）、
    前の回が走っていれば新しく起動しない（IgnoreNew）、走行時間の上限は既定のまま（ExecutionTimeLimit を書かない）。"""
    start = (start or datetime.datetime.now().replace(second=0, microsecond=0)).isoformat()
    action = (f"<Command>{escape(str(pythonw(exe)))}</Command><Arguments>\"{escape(str(Path(__file__).resolve()))}\" run</Arguments>"
              f"<WorkingDirectory>{escape(str(ROOT))}</WorkingDirectory>")
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <RegistrationInfo><Description>game-harness の H ライン（python -m harness.hline poll）を画面を出さずに呼ぶ。"
            "harness/hline_cron.py が登録した</Description></RegistrationInfo>\n"
            f"  <Triggers><TimeTrigger><Repetition><Interval>PT{every}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
            f"<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>\n"   # 要素の順はスキーマの順
            '  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n'
            "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable>"
            "<Enabled>true</Enabled></Settings>\n"
            f'  <Actions Context="Author"><Exec>{action}</Exec></Actions>\n'
            "</Task>\n")


def schtasks(*args):
    code, out, err = proc.run(["schtasks", *args], ROOT, 60, "schtasks")
    print((out or err).strip())
    return code


def register(every=EVERY_MINUTES):
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(task_xml(every=every), encoding="utf-16")   # schtasks /XML は UTF-16 を読む
        return schtasks("/Create", "/TN", TASK, "/XML", str(xml), "/F")


def unregister():
    return schtasks("/Delete", "/TN", TASK, "/F")


def run(cfg=None, poll=None, now=None):
    """定期起動の入口。作業ディレクトリを固定し、poll の出力を <out>/poll.log に追記する。終了コードは poll のもの。"""
    os.chdir(ROOT)
    cfg = cfg or load_config()
    if Path(sys.executable).name.lower() == "pythonw.exe":   # 子（harness.progress complete など）はコンソール用の python.exe で起こす
        sys.executable = str(Path(sys.executable).with_name("python.exe"))
    if poll is None:
        import hline
        poll = lambda: hline.main(["poll"])   # noqa: E731
    log = Path(cfg["out"]) / LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", errors="replace") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        print(f"=== {(now or datetime.datetime.now)():%Y-%m-%d %H:%M:%S} poll（定期起動、pid {os.getpid()}）", flush=True)
        try:
            code = poll()
        except Exception:   # noqa: BLE001 - 落ちた理由を poll.log に残す（画面が無いので、ここにしか出ない）
            traceback.print_exc()
            code = 2
        print(f"--- 終了コード {code}", flush=True)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="H ラインの定期起動（タスク スケジューラ）")
    ap.add_argument("cmd", choices=["run", "xml", "register", "unregister"])
    args = ap.parse_args(argv)
    if args.cmd == "xml":
        print(task_xml(), end="")
        return 0
    return {"run": run, "register": register, "unregister": unregister}[args.cmd]()


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
