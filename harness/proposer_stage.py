"""提案役を bench のアカウントで、材料だけの写しで起こす（v3 §7-5 と §15 の 11-05 の決め 1。L7 の前の残り 2、案 A。操縦士の ok 11-07）。

    python -m harness.proposer_stage prepare --tag <tag> [--tag <tag>]... [--config <hline.json>]
    （操縦士が runas で bench として、写しのバッチ（BATCH）を 1 回走らせる。その 1 行は prepare が出す）
    python -m harness.proposer_stage collect --dest <写しの置き場> [--config <hline.json>]
    python -m harness.proposer_stage discard --dest <写しの置き場>

周の見回りは、前の周の材料（提案・結果・開発の組の走りの記録）も写しに入れる（prepare の history。v3 §7-7 の 1）。
bench のアカウントは、oracles（隠しテストと保留の組の結果）・H ラインの out（開発の組の記録の正本と提案役の記録）・会話の席の記録を、
OS の権限で読めない。提案役をそこで起こすため、操縦士のアカウントで、bench の読める置き場（<local>/bench/proposer/<stamp>/）に
材料だけを写す：ハーネスの木（HEAD のコミットの git archive。追跡していないファイルは入らない）・tag ごとの開発の組の記録（索引・
s<k>.json・生ログの写し）・out だけを写しに向けた hline.json・bench で走らせるバッチ。関所は提案役の起動の道具と同じ
（指示のファイル・tag ごとの記録・生ログの写し・読む根が oracles と重ならないこと）。
collect は、写しの置き場の提案役の記録を H ラインの out の proposer/ に写してから、写しの置き場を消す。discard は起こさずに消す。
写しは起こす直前に作り、取り込んだら消す（bench は写しの開発の組の記録を読めるので、bench の実装役の走りと重ねない）。
OS の権限で守れないもの：bench の置き場の、bench が保留の組を走らせたときに自分で書いた跡（走りと作業の場所の記録・agy の会話の記録）。
これは CLI の閉じ込め（--restricted と読む根）と、走った後の道の検めで守る（11-07 の操縦士の決め）。
"""
import argparse
import datetime
import io
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_guard  # noqa: E402
import exitcode  # noqa: E402
import machine  # noqa: E402
import proposer_launch as pl  # noqa: E402
from bench_start import BenchError, git  # noqa: E402
from hline_base import CONFIG, ROOT, load_config, write_json  # noqa: E402

MANIFEST = "stage.json"
DONE = "launch.out"
BATCH = "launch.bat"            # bench で走らせるバッチ（Windows の cmd。runas で起こす）
BENCH_ENV = "bench_env.bat"     # bench の環境（<local>/bench の下。Git の外。bench の走りのバッチと同じものを呼ぶ）
TAG = re.compile(r"^[A-Za-z0-9._-]+$")   # バッチの 1 行に入るので、記号は通さない
HISTORY = "history"             # 写しの out の下の、前の周の置き場


class StageError(Exception):
    pass


def stage_root():
    return machine.path("local") / "bench" / "proposer"


def bench_env():
    return machine.path("local") / "bench" / BENCH_ENV


def archive(root, rev, dest):
    """root の rev の木（追跡しているファイルだけ）を dest に出す。"""
    with tarfile.open(fileobj=io.BytesIO(git(root, ["archive", "--format=tar", rev]))) as tar:
        tar.extractall(dest, filter="data")


def runas_line(dest):
    return f'runas /user:bench "cmd /c {dest / BATCH}"'


def launch_bat(dest, tags, history=False):
    """bench で 1 回走らせるバッチ（cmd が読むので、ASCII と CRLF）。提案役の起動の道具の 1 行の出力と終了コードを DONE に残す。"""
    lines = ["@echo off",
             f"rem Written by harness.proposer_stage prepare ({dest.name}). Run once as bench:",
             f"rem   {runas_line(dest)}",
             f"call {bench_env()}",
             f"cd /d {dest / 'repo'}",
             "python -m harness.proposer_launch " + " ".join(f"--tag {t}" for t in tags)
             + (f" --history {dest / 'out' / HISTORY}" if history else "")
             + f" --config {dest / 'hline.json'} > {dest / DONE} 2>&1",
             f"echo exit=%ERRORLEVEL% >> {dest / DONE}"]
    return "\r\n".join(lines) + "\r\n"


def copy_history(cfg, dest, history):
    """前の周の材料を写しの <out>/history/<周の id>/ に置く：提案・結果・開発の組の走りの記録（chains/<tag>/）。"""
    for past in history:
        place = dest / "out" / HISTORY / past["id"]
        place.mkdir(parents=True)
        shutil.copy2(past["proposal"], place / pl.PAST_PROPOSAL)
        write_json(place / pl.PAST_RESULT, past["result"])
        for tag in past["tags"]:
            shutil.copytree(pl.chains_dir(cfg, tag), place / "chains" / tag)


def prepare(config, tags, now=None, root=None, history=()):
    """材料だけの写しを作り、その置き場を返す。関所に当たれば StageError（何も作らない）。root はハーネスの木（既定は ROOT）。
    history は前の周の材料 [{id, proposal, result, tags}]（copy_history）。"""
    root = Path(root or ROOT)
    tags = list(tags)
    bad = [t for t in tags if not TAG.match(t)]
    if bad or len(set(tags)) != len(tags):
        raise StageError(f"tag の形が違うか、重なっています: {', '.join(tags)}")
    cfg = load_config(config)
    why = pl.gate(cfg, tags, [Path(root).absolute(), *(pl.chains_dir(cfg, t).absolute() for t in tags)])
    if why:
        raise StageError(why)
    if not bench_env().is_file():
        raise StageError(f"bench の環境のファイルがありません: {bench_env()}")
    now = now or datetime.datetime.now()
    dest = stage_root() / now.strftime("%Y%m%d-%H%M%S")
    if dest.exists():
        raise StageError(f"写しの置き場がもうあります: {dest}")
    try:
        rev = git(root, ["rev-parse", "HEAD"]).decode().strip()
    except BenchError as e:
        raise StageError(str(e))
    try:   # 途中で落ちたら、この呼び出しで作った写しの置き場を消す（stage.json が無いと discard で消せないため）
        archive(root, rev, dest / "repo")
        for t in tags:
            shutil.copytree(pl.chains_dir(cfg, t), dest / "out" / "chains" / t)
        copy_history(cfg, dest, history)
        raw = json.loads(Path(config).read_text(encoding="utf-8"))
        raw["out"] = str(dest / "out")   # oracles などは今の値のまま（関所が読む根と重ならないかを見る）
        (dest / "hline.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (dest / BATCH).write_bytes(launch_bat(dest, tags, bool(history)).encode("ascii"))
        write_json(dest / MANIFEST, {"at": now.isoformat(timespec="seconds"), "tags": tags, "rev": rev,
                                     "from": {t: str(pl.chains_dir(cfg, t)) for t in tags}, "history": [past["id"] for past in history],
                                     "files": sum(1 for p in (dest / "out").rglob("*") if p.is_file())})
    except BaseException as e:
        shutil.rmtree(dest, ignore_errors=True)
        if isinstance(e, BenchError):
            raise StageError(str(e)) from e
        raise
    return dest


def checked(dest):
    """写しの置き場（stage_root の直下で、stage.json がある）だけを通す。ほかの置き場は消さない。"""
    dest = Path(dest).absolute()
    if dest.parent != stage_root().absolute() or not (dest / MANIFEST).is_file():
        raise StageError(f"写しの置き場ではありません: {dest}")
    return dest


def collect(config, dest):
    """提案役の記録を H ラインの out の proposer/ に写し、写しの置き場を消す。取り込んだ記録の列を返す。"""
    dest = checked(dest)
    if (links := bench_guard.links_in(dest)):   # 操縦士のアカウントで辿って写すと、bench の読めない所の中身を運ぶことになる
        raise StageError(f"写しの置き場にリンクかジャンクションが {len(links)} 個あります（取り込みません）。消すなら discard")
    done = dest / DONE
    if not done.is_file() or b"exit=" not in done.read_bytes():
        raise StageError(f"bench の起動が終わっていません（{DONE} に exit= がありません）。起こさずにやめるなら discard")
    man = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    src, store = dest / "out" / "proposer", pl.store_dir(load_config(config))
    files = sorted(p for p in src.glob("*") if p.is_file()) if src.is_dir() else []
    clash = [p.name for p in files if (store / p.name).exists()]
    if clash:
        raise StageError("取り込み先に同じ名前の記録があります: " + "、".join(clash))
    store.mkdir(parents=True, exist_ok=True)
    recs = []
    for p in files:
        shutil.copy2(p, store / p.name)
    for p in (f for f in files if f.suffix == ".json"):   # 起動ごとの記録 <stamp>.json（ほかは .log・.txt・.md）
        rec = json.loads((store / p.name).read_text(encoding="utf-8"))
        if rec.get("proposal"):
            rec["proposal"] = str(store / Path(rec["proposal"]).name)
        rec["staged"] = {"dest": str(dest), "rev": man.get("rev"), "account": "bench"}
        write_json(store / p.name, rec)
        recs.append(rec)
    stem = next((p.stem for p in reversed(files) if p.suffix == ".json"), dest.name)
    shutil.copy2(done, store / f"{stem}.{DONE}")
    shutil.rmtree(dest)
    return recs


def discard(dest):
    shutil.rmtree(checked(dest))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.proposer_stage")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--tag", required=True, action="append", help="走りの記録の tag（2 回以上渡せる）")
    p.add_argument("--config", default=str(CONFIG))
    c = sub.add_parser("collect")
    c.add_argument("--dest", required=True)
    c.add_argument("--config", default=str(CONFIG))
    sub.add_parser("discard").add_argument("--dest", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "prepare":
            dest = prepare(a.config, a.tag)
            print(f"- 写し: {dest}")
            print(f"- bench で 1 回: {runas_line(dest)}")
            return 0
        if a.command == "collect":
            recs = collect(a.config, a.dest)
            for rec in recs:
                print(pl.line_of(rec))
            return 0 if recs and all(pl.ok(r) for r in recs) else 1
        discard(a.dest)
        print(f"- 写しを消した: {a.dest}")
        return 0
    except StageError as e:
        print(f"止めた: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
