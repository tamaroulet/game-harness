"""固定カナリアの準備と後片付け（docs/design/canary_issue12.md、ADR-002 手順 6）。

    python harness/canary.py prepare --project falling-blocks --unit <v2 の単位定義> --worktree <パス> [--base <ref>]
    python harness/canary.py cleanup --project falling-blocks --worktree <パス>

**なぜハーネスのコマンドにするのか**: カナリアの標本は「main から、単位定義の impl_files を機械的に
削除したもの」（canary_issue12.md §3 の 1）。総監督がこの削除や、生成テストの書き出しを直接打つと、
ゲームの実装・テスト置き場を触るコマンドになり、deny 設定（防壁①）に触れる。すり抜けずに済むよう、
手順をハーネスの決まった操作にする。人の手による削除や編集を挟まないので、標本も毎回同じになる。

prepare は次の順で行い、どこかで失敗したら止まる（標本を半端な状態で残さない）。
1. `--base`（既定 origin/<base_branch>）から捨て枝 test/canary-<id> の worktree を作る
2. 単位定義の impl_files を `git rm` で削除する
3. decompose.py --from-unit で、単位定義と生成テストを書き出す（スキーマ門とテスト生成を通す）
4. 1 つのコミットにまとめる

cleanup は worktree と捨て枝を、ローカルとリモートの両方から消す。main には何も残さない。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import project
from proc import run

TTL = 300


class CanaryError(Exception):
    pass


def branch_name(unit_id):
    return f"test/canary-{unit_id.replace('_', '-')}"


def _git(args, cwd, label):
    rc, out, err = run(["git"] + args, cwd, TTL, label)
    if rc != 0:
        raise CanaryError(f"{label} に失敗しました: {(err or out)[:300]}")
    return out


def prepare(proj, unit_path, worktree, base=None, runner=subprocess.run):
    repo = proj["repo_dir"]
    unit = json.loads(Path(unit_path).read_text(encoding="utf-8"))
    if unit.get("schema") != 2 or not unit.get("impl_files"):
        raise CanaryError("カナリアには impl_files のある v2 の単位定義が要ります")
    base = base or f"origin/{proj['base_branch']}"
    branch = branch_name(unit["id"])
    if Path(worktree).exists():
        raise CanaryError(f"worktree の置き場が既にあります。先に cleanup してください: {worktree}")

    _git(["fetch", "origin", proj["base_branch"]], repo, "git fetch")
    _git(["worktree", "add", "-b", branch, str(worktree), base], repo, "git worktree add")
    _git(["rm", "-q", "--"] + unit["impl_files"], worktree, "impl_files の削除")
    r = runner([sys.executable, str(project.ROOT / "harness" / "decompose.py"), "--project", proj["id"],
                "--repo-dir", str(worktree), "--from-unit", str(unit_path)],
               capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise CanaryError(f"decompose --from-unit が rc={r.returncode} で終了しました: {(r.stdout + r.stderr)[-600:]}")
    _git(["add", "-A"], worktree, "git add")
    _git(["commit", "-q", "-m", f"canary: {unit['id']} の固定標本（impl_files を削除、v2 の単位定義と生成テスト）。"
                                "捨て枝。main へ merge しない"], worktree, "git commit")
    head = _git(["rev-parse", "--short", "HEAD"], worktree, "git rev-parse").strip()
    return branch, head


def cleanup(proj, worktree):
    repo = proj["repo_dir"]
    wt = Path(worktree)
    branch = None
    if wt.exists():
        rc, out, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], wt, TTL, "branch of worktree")
        branch = out.strip() if rc == 0 else None
        _git(["worktree", "remove", "--force", str(wt)], repo, "git worktree remove")
    if branch and branch.startswith("test/canary-"):
        run(["git", "branch", "-D", branch], repo, TTL, "git branch -D")
        run(["git", "push", "-q", "origin", "--delete", branch], repo, TTL, "git push --delete")
    return branch


def main(argv=None):
    ap = argparse.ArgumentParser(description="固定カナリアの準備と後片付け")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--project", required=True)
    p.add_argument("--unit", required=True, help="v2 の単位定義（ファイル）")
    p.add_argument("--worktree", required=True)
    p.add_argument("--base", help="標本の元にする ref（既定 origin/<base_branch>）")
    c = sub.add_parser("cleanup")
    c.add_argument("--project", required=True)
    c.add_argument("--worktree", required=True)
    args = ap.parse_args(argv)
    proj = project.load(args.project)
    try:
        if args.cmd == "prepare":
            branch, head = prepare(proj, args.unit, args.worktree, args.base)
            print(f"標本: {branch} @ {head}（{args.worktree}）")
        else:
            branch = cleanup(proj, args.worktree)
            print(f"後片付け: {branch or '（枝なし）'} を消しました")
    except CanaryError as e:
        print(f"NG: {e}")
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
