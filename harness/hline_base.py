"""H ライン（harness/hline.py）の土台：設定・環境の異常・子プロセス・エージェントの呼び出し・ロック。

**なぜ分けるか**: hline.py を 300 行以内に保つ（受け入れ条件）。ここには状態を持つものを置かない。
"""
import contextlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_pin  # noqa: E402
import proc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hline.json"
RESERVED = {"report.md", "TODO.md", "CLAUDE.md"}


class Infra(Exception):
    """環境の異常（git・gh・CLI の起動の失敗、モデルの照合の失敗）。終了コード 2。"""


def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    model_pin.require(cfg["implementer"], "config/hline.json の implementer")
    model_pin.require(cfg["decomposer"], "config/hline.json の decomposer")
    return cfg


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")
    return s[:40] or "task"


def title_of(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "H ラインのタスク"


def write_json(path, obj):
    """途中で落ちても読める JSON を残す（一時ファイルに書いて置き換える）。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ============================================================ ロック

def acquire_lock(inbox, stale_seconds, now=None):
    """前回が走行中なら None。stale_seconds より古いロックは落ちた走行の残骸として取り直す。"""
    lock = Path(inbox) / ".hline.lock"
    if now is None:
        now = time.time()
    if lock.exists():
        if now - lock.stat().st_mtime < stale_seconds:
            return None
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return lock


@contextlib.contextmanager
def heartbeat(lock, interval):
    """走行中はロックの更新時刻を新しく保つ。走行が lock_stale を超えても、次の起動が残骸と見誤って二重に走らない。
    強制終了されると更新が止まり、lock_stale 後に残骸として取り直される。"""
    stop = threading.Event()

    def beat():
        while not stop.wait(interval):
            with contextlib.suppress(OSError):
                os.utime(lock, None)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=interval + 1)


# ============================================================ 子プロセスとエージェント

def must(args, cwd, ttl, label):
    code, out, err = proc.run(args, cwd, ttl, label)
    if code != 0:
        raise Infra(f"{label} が失敗しました（終了コード {code}）: {(err or out)[-800:]}")
    return out


def implementer_args(agent, cli):
    return (cli + [agent["headless_flag"], agent["model_flag"], agent["model"]]
            + agent["extra_flags"] + agent["output_format_args"])


def pinned_models(agent, out, label):
    used, why = model_pin.claude_models(out)
    try:
        return model_pin.check_claude(agent, used, why, f"H ラインの{label}")
    except model_pin.ModelPinError as e:   # 認証切れなどもここに来る。CLI の結果の文を添える
        detail = json.loads(out).get("result", "") if why is None else why
        raise Infra(f"{e}（CLI: {str(detail)[:200]}）")


def run_agent(agent, wt, ttl, label, prompt, log):
    """エージェント（実装役・分解役）を 1 回呼び、使ったモデルを照合して記録する（作業規約：model_pin）。
    (CLI の終了コード, 使われたモデルの列, CLI の標準出力)。"""
    args = implementer_args(agent, proc.resolve_cli(agent["cli"]))
    code, out, err = proc.run(args, wt, ttl, label, input=prompt)
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    return code, pinned_models(agent, out, label), out
