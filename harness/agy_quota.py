"""Gemini の利用枠の目盛りを、agy の /quota で直接読んで残す（操縦士の指示、2026-11-14。WSL の上で走らせる）。

    python -m harness.agy_quota <ラベル> [--log <JSONL の置き場>]

agy -p /quota --output-format json は、モデルを呼ばない（num_turns 0・total_tokens 0）。JSON の
command.data.groups[].buckets[] に、バケツごとの remaining_fraction（1% に丸める前の小数）と reset_time がある。
読むのは BUCKETS の 2 つ（Gemini Models の週の枠と 5 時間の枠）。トークンを使っていたら、記録に印を付ける。

走りの直前と直後に読む。目盛りは、使っていないあいだにも動く（2026-11-14 の 13 時台に、使っていないのに、
数十分で週と 5 時間の残りがどちらも 0.8 ポイントほど戻った）。前後の差には、走りのあいだの戻りが混じるので、
直後の読みに加えて、少し置いた読みも残し、差は丸めと戻りの幅を添えて書く。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

BUCKETS = ("gemini-weekly", "gemini-5h")
HOME = os.environ.get("AGY_QUOTA_HOME", "/home/bench")   # agy の認証のトークンがある HOME（中身は読まない）


def parse(text):
    """/quota の JSON → {"buckets": {id: {"remaining_fraction", "reset_time"}}, "tokens": int, "turns": int}。"""
    d = json.loads(text)
    found = {}
    for g in ((d.get("command") or {}).get("data") or {}).get("groups") or []:
        for b in g.get("buckets") or []:
            if b.get("id") in BUCKETS:
                found[b["id"]] = {"remaining_fraction": b.get("remaining_fraction"), "reset_time": b.get("reset_time")}
    missing = [b for b in BUCKETS if b not in found]
    if missing:
        raise ValueError(f"/quota の出力に {missing} が無い")
    usage = d.get("usage") or {}
    return {"buckets": found, "tokens": int(usage.get("total_tokens") or 0), "turns": int(d.get("num_turns") or 0)}


def read(agy="agy"):
    env = dict(os.environ, HOME=HOME, AGY_CLI_DISABLE_AUTO_UPDATE="1")
    p = subprocess.run([agy, "-p", "/quota", "--output-format", "json"], capture_output=True, text=True, env=env,
                       timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"agy /quota exit {p.returncode}")
    return parse(p.stdout)


def line(label, rec):
    b = rec["buckets"]
    w, h = b["gemini-weekly"], b["gemini-5h"]
    flag = "" if rec["tokens"] == 0 and rec["turns"] == 0 else f" (tokens {rec['tokens']}, turns {rec['turns']}!)"
    return (f"{label} {rec['at']} weekly={w['remaining_fraction']:.4f} (reset {w['reset_time']}) "
            f"5h={h['remaining_fraction']:.4f} (reset {h['reset_time']}){flag}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gemini の利用枠の目盛りを読んで残す")
    ap.add_argument("label")
    ap.add_argument("--log")
    a = ap.parse_args(argv)
    rec = read()
    rec["label"] = a.label
    rec["at"] = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    if a.log:
        with open(a.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(line(a.label, rec))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import exitcode  # noqa: E402

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
