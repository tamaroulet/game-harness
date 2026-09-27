"""ファイル操作の再試行（Windows のファイルロック対策）。

Windows では、ほかのプロセスが開いているファイルを消したり置き換えたりすると
`PermissionError`（WinError 32: 共有違反 / 5: アクセス拒否 / 33: ロック違反）で失敗する。
TTL で子プロセスの木を止めた直後や、エンジン・ビルドの残りプロセス、読み手（--status や
ウイルス対策）が掴んでいる間に起きる。数秒で離されることが多いので、短く待って再試行する。

再試行するのはロック系のエラーだけ。ファイルが無い・パスが不正などは、待っても直らないので
そのまま上げる（原因を隠さない）。
"""
import os
import shutil
import time

DELAYS = (0.2, 0.5, 1, 2, 4)
LOCK_WINERRORS = (5, 32, 33)


class FileLockError(OSError):
    """再試行しても掴まれたままだった。呼び出し側は ABORT の理由として表示する。"""


def is_lock_error(e):
    if isinstance(e, FileNotFoundError):
        return False
    return isinstance(e, PermissionError) or getattr(e, "winerror", None) in LOCK_WINERRORS


def retry_os(fn, what, delays=DELAYS, sleep=None):
    """fn() を実行し、ロック系のエラーなら delays の間隔で再試行する。sleep は呼ぶ時点の time.sleep（テストで差し替えられる）。"""
    sleep = sleep or time.sleep
    for i in range(len(delays) + 1):
        try:
            return fn()
        except OSError as e:
            if not is_lock_error(e):
                raise
            if i == len(delays):
                raise FileLockError(f"{what}: {len(delays) + 1} 回とも掴まれていました"
                                    f"（ほかのプロセスが開いている可能性）: {e}") from e
            sleep(delays[i])


def unlink(path, sleep=None):
    """無ければ何もしない。"""
    return retry_os(lambda: path.unlink(missing_ok=True), f"削除 {path}", sleep=sleep)


def replace(src, dst, sleep=None):
    return retry_os(lambda: os.replace(src, dst), f"置き換え {dst}", sleep=sleep)


def rmtree(path, sleep=None):
    """ディレクトリを消す。無ければ何もしない。ロック系のエラーなら再試行する（原則 P5）。

    shutil.rmtree を直に呼ぶと、ビルドやウイルス対策が掴んでいる 1 ファイルで走行ごと落ちる。消せなかったときは
    FileLockError（何が掴まれていたかを添える）で上げ、呼び出し側が ABORT の理由として出す。
    """
    path = os.fspath(path)

    def go():
        if os.path.exists(path):
            shutil.rmtree(path)
    return retry_os(go, f"削除 {path}", sleep=sleep)
