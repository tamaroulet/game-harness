#!/usr/bin/env python3
"""WSL のディスクとメモリの安全網（Multi-step SWE の実行口 harness/ms_run.py の走りと並べて、WSL の root で走らせる）。

    python3 harness/ms_watch.py --box <実装役のコンテナの名前の頭> --until <走りの record.json> --log <jsonl>
                                [--mem-floor-gib 1.5] [--disk-floor-gib 10] [--disk-path /var/lib/docker]
                                [--every 5] [--log-every 60] [--max-s 180000]

- 読むことと、止める線に当たったときに、名前が box で始まるコンテナの中の agy のプロセスだけを kill -9 で止めることだけを
  する。採点の段のプロセスとコンテナは止めない（後始末は実行口の finally）
- 止める線：WSL の MemAvailable < mem_floor_gib、disk_path のファイルシステムの空き < disk_floor_gib
- 利用枠は見ない。予算は実行口の実験単体の上限（config/ms_run.json の limits）と、走りの前の安全弁（config/agy_sandbox.json
  の quota_guard）で持つ（操縦士の裁定 2026-11-26：見張りは WSL のディスクとメモリの安全網だけにする）
- every 秒ごとに docker stats（コンテナごとのメモリの最大 MiB）・MemAvailable の最小・ディスクの空きの最小を取り、log_every
  秒ごとと止めたときに log に 1 行を足す。until のファイルが現れるか max_s 秒で、要約（JSON）を出して終わる
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402

KILL_AGY = ("for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); case \"$c\" in "
            "\"agy \"*|\"/usr/local/bin/agy \"*) kill -9 ${p#/proc/} && echo killed ${p#/proc/};; esac; done")
MIB = 1024 * 1024


def now():
    return time.strftime("%H:%M:%S")


def mem_available_mib(path="/proc/meminfo"):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def disk_free_mib(path):
    try:
        return shutil.disk_usage(path).free // MIB
    except OSError:
        return None


def to_mib(s):
    """docker stats の MemUsage の左（"1.5GiB" など）→ MiB。読めなければ None。"""
    m = re.match(r"([\d.]+)\s*([KMG]i?B|B)", (s or "").strip())
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return round(v * {"B": 1 / MIB, "KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}[u])


def stop_reasons(mem_mib, disk_mib, mem_floor_gib, disk_floor_gib):
    """止める線に当たった理由の並び（空なら止めない）。読めなかった値（None）では止めない。"""
    out = []
    if mem_mib is not None and mem_mib < mem_floor_gib * 1024:
        out.append(f"MemAvailable {mem_mib} MiB < {mem_floor_gib} GiB")
    if disk_mib is not None and disk_mib < disk_floor_gib * 1024:
        out.append(f"disk free {disk_mib} MiB < {disk_floor_gib} GiB")
    return out


def stats():
    p = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"], capture_output=True,
                       text=True, timeout=60)
    out = {}
    for line in p.stdout.splitlines():
        name, _, usage = line.partition("\t")
        v = to_mib(usage.split("/")[0])
        if v is not None:
            out[name] = v
    return out


def kill_agy(prefix):
    p = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=60)
    res = {}
    for name in p.stdout.split():
        if name.startswith(prefix):
            k = subprocess.run(["docker", "exec", "--user", "0:0", name, "bash", "-c", KILL_AGY], capture_output=True,
                               text=True, timeout=60)
            res[name] = k.stdout.split()
    return res


def log_line(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="WSL のディスクとメモリの安全網")
    ap.add_argument("--box", required=True, help="実装役のコンテナの名前の頭")
    ap.add_argument("--until", required=True, help="このファイルが現れたら終わる（走りの record.json）")
    ap.add_argument("--log", required=True)
    ap.add_argument("--mem-floor-gib", type=float, default=1.5)
    ap.add_argument("--disk-floor-gib", type=float, default=10.0)
    ap.add_argument("--disk-path", default="/var/lib/docker" if os.path.isdir("/var/lib/docker") else "/")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--log-every", type=int, default=60)
    ap.add_argument("--max-s", type=int, default=180000, help="実験単体の壁時計の上限（run_seconds 172,800）より長く")
    a = ap.parse_args(argv)
    t0, last_log = time.time(), 0.0
    peak, mem_min, disk_min, alerts = {}, None, None, []
    print(now(), "START box", a.box, "mem_floor_gib", a.mem_floor_gib, "disk_floor_gib", a.disk_floor_gib,
          "disk_path", a.disk_path, flush=True)
    while time.time() - t0 < a.max_s:
        try:
            for k, v in stats().items():
                peak[k] = max(peak.get(k, 0), v)
        except Exception as e:  # noqa: BLE001 - the watch keeps going
            print(now(), "stats error", type(e).__name__, flush=True)
        m, d = mem_available_mib(), disk_free_mib(a.disk_path)
        mem_min = m if mem_min is None or (m is not None and m < mem_min) else mem_min
        disk_min = d if disk_min is None or (d is not None and d < disk_min) else disk_min
        reasons = stop_reasons(m, d, a.mem_floor_gib, a.disk_floor_gib)
        at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if reasons:
            res = kill_agy(a.box)
            alerts.append({"at": at, "reasons": reasons, "killed": res})
            log_line(a.log, {"at": at, "mem_available_mib": m, "disk_free_mib": d, "stop": reasons, "killed": res})
            print(now(), "ALERT STOP", reasons, "killed", res, flush=True)
        elif time.time() - last_log >= a.log_every:
            last_log = time.time()
            log_line(a.log, {"at": at, "mem_available_mib": m, "disk_free_mib": d})
        if os.path.exists(a.until):
            break
        time.sleep(a.every)
    print(now(), "END", json.dumps({"peak_mib": peak, "mem_available_min_mib": mem_min, "disk_free_min_mib": disk_min,
                                    "alerts": alerts}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
