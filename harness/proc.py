"""子プロセスの起動。エンジンにも言語にも依存しない部分だけを置く。

pipeline とアダプタが共通で使う。scheduler は長い子プロセスの出力を逐次ファイルへ
書く別の起動方法（run_logged）を持つ。
"""
import shutil
import subprocess
import sys

# Windows でサブプロセスがコンソールウィンドウを開かないようにする。
# 非 Windows では属性が無いので 0 になり、無害に無視される。
# 効くのはコンソールアプリ（git / gh / 実装役の CLI / テストランナー）。
# GUI サブシステムの実行ファイル（エンジンのエディタなど）には効かないので、
# そちらはアダプタがバッチモードの引数で窓を出さないようにする。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# creationflags だけでは足りない。agy のように内部でさらに子を起こす
# プロセスは conhost.exe を一瞬立ち上げてフォーカスを奪う（実測）。
# STARTUPINFO で SW_HIDE を渡し、最初のウィンドウ表示自体を抑える。
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE


def run(args, cwd, ttl, label, env=None):
    """shell=False。stdin は塞ぐ。ウィンドウも出さない。TTL は必須。"""
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=ttl, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, env=env,
                           creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired as e:
        # 打ち切った時点までの標準出力は返す。実装役の stream-json は、終わった手番の利用量をそこに
        # 出しているので、捨てると打ち切った呼び出しの費用が丸ごと消える（v2.1 §1.2）
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out, f"TTL超過 ({ttl}s): {label}"
    except FileNotFoundError as e:
        return 127, "", f"コマンドが見つかりません: {e}"


def resolve_cli(name):
    """npm 導入の CLI は .cmd の shim。subprocess は拡張子なしを解決しないので実体を引く。"""
    for cand in (name + ".cmd", name + ".exe", name):
        p = shutil.which(cand)
        if p:
            return [p]
    sys.exit(f"ABORT: {name} CLI が PATH にありません")
