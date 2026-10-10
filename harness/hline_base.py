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


class Fatal(Infra):
    """待っても直らない異常（進捗の記録の拒否・push の拒否）。呼び直さず、その What を未収束にして止める。"""
    fatal = True

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Quota(Infra):
    """利用枠切れ（429）。待てば直るが、プロセスの中では待たない：試行に数えず What を待ちに戻し、走行を終える（hline_quota）。"""
    fatal = True   # infra_retry.retry に眠らせない

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class SyncConflict(Infra):
    """統合ブランチに base（main）を取り込むとき衝突した。人間が直す。呼び直さず、What は待ちに戻して走行を終える。"""
    fatal = True   # infra_retry.retry に眠らせない

    def __init__(self, paths):
        super().__init__(f"統合ブランチへ main を取り込めません（衝突: {', '.join(paths)}）")
        self.paths = list(paths)


def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    model_pin.require(cfg["implementer"], "config/hline.json の implementer")
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

UNKNOWN_TOKEN = "unknown"   # 識別子が取れない（プラットフォーム・権限）。奪う根拠にしない
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259


def _win32_token(pid):
    import ctypes
    from ctypes import wintypes
    if not 0 <= pid < 2 ** 32:
        return UNKNOWN_TOKEN
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    ft = ctypes.POINTER(wintypes.FILETIME)
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ft, ft, ft, ft]
    handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else UNKNOWN_TOKEN
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None   # 終了済み（誰かがハンドルを握っていて pid が残っているだけ）
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, kernel, user))):
            return UNKNOWN_TOKEN
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        k32.CloseHandle(handle)


def _linux_token(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError:
        return UNKNOWN_TOKEN
    fields = text[text.rfind(")") + 1:].split()   # 3 番目の欄（state）から。comm に空白や括弧があってもよい
    return fields[19] if len(fields) > 19 and fields[19].isdigit() else UNKNOWN_TOKEN


def process_token(pid):
    """pid のプロセスの生成を表す識別子。pid の使い回しの見分けに使う。無いと確かめられたときだけ None。
    os.kill(pid, 0) は Windows では終了させてしまうので使わない。"""
    try:
        if sys.platform == "win32":
            return _win32_token(pid)
        if sys.platform.startswith("linux"):
            return _linux_token(pid)
    except Exception:  # noqa: BLE001 - 取れないことは「分からない」であって、例外にしない
        pass
    return UNKNOWN_TOKEN


def owner_state(record):
    """ロックの持ち主の死活："alive" | "dead" | "unknown"。"""
    if not isinstance(record, dict):
        return "unknown"
    pid = record.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return "unknown"
    token = process_token(pid)
    if token is None:
        return "dead"
    if token == UNKNOWN_TOKEN or not isinstance(record.get("token"), str):
        return "unknown"
    return "alive" if token == record["token"] else "dead"


def read_lock(lock):
    """ロックの本文が JSON のオブジェクトならその dict。古い形式・壊れた本文・読めないファイルは None。"""
    try:
        record = json.loads(Path(lock).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def acquire_lock(inbox, stale_seconds, now=None):
    """前回が走行中なら None。持ち主が死んでいれば更新時刻が新しくても取り直し、生きていれば古くても奪わない。
    持ち主の死活が分からないときだけ、stale_seconds を超えた更新時刻を落ちた走行の残骸と見なす。"""
    lock = Path(inbox) / ".hline.lock"
    if now is None:
        now = time.time()
    try:
        mtime = lock.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    except OSError:
        return None
    if mtime is not None:
        state = owner_state(read_lock(lock))
        if state == "alive" or (state == "unknown" and now - mtime < stale_seconds):
            return None
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            return None
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:   # FileExistsError は競合で、取れなかったほうが二重に走らない
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "token": process_token(os.getpid()), "created": time.time()}, f)
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            lock.unlink(missing_ok=True)
        return None
    return lock


@contextlib.contextmanager
def heartbeat(lock, interval):
    """走行中はロックの更新時刻を新しく保つ。持ち主の死活が分からない環境でも、次の起動が残骸と見誤って二重に走らない。"""
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
