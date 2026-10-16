"""プレイ確認（H2）用のビルド。PR の head SHA から、人間が遊ぶ実行ファイルを作る。

    python harness/playtest.py --project unity-2d --pr 12 --sha <head の 40 桁>

スケジューラは、単位定義が playtest: "required" のときだけ、PR を作った後にこれを呼ぶ。
人間は dispatch --playtest から、できた実行ファイルを遊ぶ（ソースもエディタも開かない）。

終了コード: 0 = ビルドあり（作った、または同じ SHA のものが既にある）
            1 = ビルド失敗（コンパイルエラーなど。人間に見せて判断させる）
            2 = 環境異常（設定の不足・SHA の不一致・ワークツリーを戻せない・ビルドが追跡中のファイルを変えた）

**なぜ専用のワークツリーか**: 本体のリポジトリは次の Issue に使うし、実装のサンドボックスは
試行ごとに消える。エンジンのキャッシュ（ignored）を残したまま、SHA だけを合わせ直せる場所が要る。

**非破壊の確認**: ビルド後に、ワークツリーの追跡中のファイルが 1 つも変わっていないことを確かめる。
変わっていたら、ビルドスクリプトかエンジンが設定やシーンを書き換えている。
次のビルドに持ち越さず、黙って成功にもしない（rc=2）。
"""
import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import adapters
import fileops
import project
import telemetry
from proc import run

SHA_RE = re.compile(r"[0-9a-f]{40}")


class Stop(Exception):
    def __init__(self, rc, msg):
        super().__init__(msg)
        self.rc = rc


def git(args, cwd, ttl, label):
    return run(["git"] + args, cwd, ttl, label)


def dirty(worktree, ttl):
    _, out, _ = git(["status", "--porcelain"], worktree, ttl, "status (playtest)")
    return [l for l in out.splitlines() if l.strip()]


def ensure_commit(repo, sha, pr, ttl):
    """SHA がローカルに無ければ PR の head を取ってくる。取れたものが指定と違えば止める。"""
    rc, _, _ = git(["cat-file", "-e", f"{sha}^{{commit}}"], repo, ttl, "cat-file")
    if rc == 0:
        return
    rc, out, err = git(["fetch", "origin", f"pull/{pr}/head"], repo, ttl, "fetch pr head")
    if rc != 0:
        raise Stop(2, f"PR #{pr} の head を取得できません: {(err or out)[:200]}")
    _, fetched, _ = git(["rev-parse", "FETCH_HEAD"], repo, ttl, "rev-parse FETCH_HEAD")
    if fetched.strip() != sha:
        raise Stop(2, f"PR #{pr} の head は {fetched.strip()[:8]} で、指定の {sha[:8]} と違います"
                      "（push された可能性。今の head で呼び直してください）")


def sync_worktree(repo, worktree, sha, ttl, sleep=time.sleep):
    """専用のワークツリーを SHA に合わせ、作業ツリーが空であることを確かめる。"""
    if not (worktree / ".git").exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        rc, out, err = git(["worktree", "add", "--detach", str(worktree), sha], repo, ttl, "worktree add")
        if rc != 0:
            raise Stop(2, f"プレイ確認用のワークツリーを作れません: {(err or out)[:200]}")
    leftover = []
    for delay in (0,) + fileops.DELAYS:
        if delay:
            sleep(delay)
        git(["reset", "--hard", sha], worktree, ttl, "reset (playtest)")
        git(["clean", "-fd"], worktree, ttl, "clean (playtest)")
        leftover = dirty(worktree, ttl)
        if not leftover:
            break
    if leftover:
        raise Stop(2, "プレイ確認用のワークツリーを戻せません（ファイルが掴まれている可能性）: "
                      + ", ".join(l[3:] for l in leftover[:5]))
    _, head, _ = git(["rev-parse", "HEAD"], worktree, ttl, "rev-parse (playtest)")
    if head.strip() != sha:
        raise Stop(2, f"ワークツリーが {sha[:8]} になりません（{head.strip()[:8]}）")


def output_dir(cfg, pr, sha):
    return Path(cfg["paths"]["playtest_out"]) / cfg["project"]["id"] / f"pr{pr}-{sha[:8]}"


def build(c, engine, pr, sha, sleep=time.sleep):
    """(rc, 理由, 記録)。環境異常は Stop を上げる。"""
    out = output_dir(c.cfg, pr, sha)
    exe, meta = out / "build" / "Playtest.exe", out / "build.json"
    prev, _ = telemetry.read(meta)
    if prev is not None and prev.get("sha") == sha and exe.exists():
        return 0, f"同じ SHA のビルドがあります: {exe}", {"skipped": True, "exe": str(exe)}

    worktree = Path(c.cfg["paths"]["playtest_worktree"])
    ensure_commit(c.repo, sha, pr, c.ttl["git"])
    sync_worktree(c.repo, worktree, sha, c.ttl["git"], sleep)

    log = out / "build.log"
    t0 = time.monotonic()
    rc, why = engine.build_player(c, worktree, exe, log)
    seconds = round(time.monotonic() - t0, 1)
    info = {"skipped": False, "exe": str(exe), "seconds": seconds, "log": str(log)}

    changed = dirty(worktree, c.ttl["git"])
    if changed:
        raise Stop(2, "ビルドがワークツリーの追跡中のファイルを変えました（非破壊ではありません）: "
                      + ", ".join(l[3:] for l in changed[:5]))
    if rc != 0 or not exe.exists():
        return 1, (f"ビルドに失敗しました（rc={rc}{'、exe がありません' if rc == 0 else ''}）。"
                   f"ログ: {log} {why}"), info

    telemetry.write(meta, {"sha": sha, "pr": pr, "exe": str(exe), "seconds": seconds, "unity_log": str(log),
                           "built_at": datetime.now().isoformat(timespec="seconds")})
    return 0, f"ビルドしました（{seconds}s）: {exe}", info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--sha", required=True, help="PR の head（40 桁）")
    ap.add_argument("--telemetry", help="テレメトリの書き出し先（JSON）。判定には使わない")
    a = ap.parse_args(argv)

    tel = {"schema": telemetry.SCHEMA, "tool": "playtest", "pr": a.pr, "sha": a.sha,
           "started": datetime.now().isoformat(timespec="seconds")}
    rc = None
    try:
        if not SHA_RE.fullmatch(a.sha):
            print(f"ABORT: --sha は 40 桁の SHA で指定してください: {a.sha}")
            rc = 2
            return rc
        cfg = project.pipeline_config(project.load(a.project))
        missing = [k for k in ("playtest_worktree", "playtest_out") if k not in cfg["paths"]]
        if missing:
            print("ABORT: pipeline.json の paths に " + ", ".join(missing) + " がありません")
            rc = 2
            return rc
        c = SimpleNamespace(cfg=cfg, ttl=cfg["ttl_seconds"], repo=Path(cfg["paths"]["repo"]))
        try:
            engine = adapters.load("engine", cfg["adapters"]["engine"])
            rc, msg, info = build(c, engine, a.pr, a.sha)
            tel.update(info)
        except Stop as e:
            rc, msg = e.rc, str(e)
        except adapters.AdapterError as e:
            rc, msg = 2, str(e)
        print(("OK: " if rc == 0 else "NG: " if rc == 1 else "ABORT: ") + msg)
        return rc
    finally:
        telemetry.put(tel, "exit_code", rc, "sys.exit か例外で終了した（終了コードは呼び出し側の記録を見る）")
        if a.telemetry:
            telemetry.write(a.telemetry, tel)


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
