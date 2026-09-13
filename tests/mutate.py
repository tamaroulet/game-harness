"""変異テスト。門の判定を 1 つずつわざと壊し、テストが赤になることを確かめる。

    python tests/mutate.py            # 全件
    python tests/mutate.py M1 M13     # 指定したものだけ

**なぜ要るか**: 一発で緑になったテストは、何も測っていない可能性がある
（自己検査の True 直書き・「汚れを残す ABORT」しか試しておらず rc=2 の取り違えが
隠れていた実例がある）。壊しても緑のままの変異は、テストの穴を指している。

元のファイルはバイト列で退避し、finally で書き戻す（git checkout は使わない）。
"""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "harness"

M = [
    ("M1 pipeline rc2 を REJECT 扱い", "scheduler.py",
     '        if rc == 1:\n            raise Reject("実装パイプライン',
     '        if rc in (1, 2):\n            raise Reject("実装パイプライン'),
    ("M2 不合格時の汚れ検査を外す", "scheduler.py",
     '        dirty = self.git.changed_paths()\n        if dirty:\n            raise Abort("不合格の後始末',
     '        dirty = self.git.changed_paths()\n        if False:\n            raise Abort("不合格の後始末'),
    ("M3 再試行前の読み直しを外す", "scheduler.py",
     "if done is not None and done():", "if False:"),
    ("M4 文字列 exit を 1 にする", "exitcode.py",
     "    print(code, file=sys.stderr)\n    return ABORT", "    print(code, file=sys.stderr)\n    return 1"),
    ("M5 merge --abort を外す", "scheduler.py",
     '            self._git("merge", "--abort", check=False)\n', ""),
    ("M6 CI の conclusion を見ない", "scheduler.py",
     'if conclusion != "success":', 'if conclusion == "never":'),
    ("M7 想定外パス検査を外す", "scheduler.py",
     "        bad = [p for p in changed if not any(a(p) for a in allowed)]", "        bad = []"),
    ("M8 着手後 ABORT でもロックを消す", "scheduler.py",
     "            if self.touched:", "            if False:"),
    ("M9 既存ブランチ検査を外す", "scheduler.py",
     "if self.git.local_branch_exists(branch) or self.git.remote_branch_exists(branch):", "if False:"),
    ("M10 ABORT 時に main へ戻す（掃除する）", "scheduler.py",
     '        try:\n            self.gh.comment(n, f"ms4:{n}:abort:',
     '        self.git._git("switch", "-f", self.base, check=False)\n        try:\n            self.gh.comment(n, f"ms4:{n}:abort:'),
    ("M11 再試行回数を 0 に", "scheduler.py",
     "    for i in range(retries + 1):", "    for i in range(1):"),
    ("M12 TTL で木ごと殺さない（待ち続ける）", "scheduler.py",
     "            return proc.wait(timeout=ttl)", "            return proc.wait()"),
    ("M13 decompose rc2 を不合格扱い", "scheduler.py",
     '        if rc == 1:\n            raise Reject("分解役',
     '        if rc in (1, 2):\n            raise Reject("分解役'),
    ("M14 共通設定の重複キー検査を外す", "scheduler.py",
     "    if dup:\n        raise project.ProjectError(", "    if False:\n        raise project.ProjectError("),
    ("M15 pipeline.json の repo 再掲検査を外す", "project.py",
     "        if k in paths:\n", "        if False:\n"),
    ("M16 taskkill に TTL を付けない", "scheduler.py",
     "creationflags=_NO_WINDOW, timeout=60)", "creationflags=_NO_WINDOW)"),
]


def main(only):
    survived = []
    for name, fn, old, new in M:
        if only and name.split()[0] not in only:
            continue
        p = T / fn
        orig = p.read_bytes()
        text = orig.decode("utf-8")
        if text.count(old) != 1:
            print(f"{name}: 変異の置換元が {text.count(old)} 箇所（1 箇所であるべき）。変異表が古い")
            return 2
        try:
            p.write_bytes(text.replace(old, new).encode("utf-8"))
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                               cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900)
            fails = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", r.stderr, re.M)))
            red = r.returncode != 0
            print(f"{name}: {'赤' if red else '緑（検出できず）'} {fails}")
            if not red:
                survived.append(name)
        finally:
            p.write_bytes(orig)
    if survived:
        print("\n検出できなかった変異: " + ", ".join(survived))
        return 1
    print("\n全変異を検出")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
