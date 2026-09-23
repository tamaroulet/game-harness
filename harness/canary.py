"""固定カナリアの準備と後片付け（docs/design/canary_issue12.md、ADR-002 手順 6）。

    python harness/canary.py prepare --project falling-blocks --unit <v2 の単位定義> --worktree <パス> [--base <ref>] [--retire-v1-tests]
    python harness/canary.py cleanup --project falling-blocks --worktree <パス>

**なぜハーネスのコマンドにするのか**: カナリアの標本は「main から、単位定義の impl_files を機械的に
削除したもの」（canary_issue12.md §3 の 1）。総監督がこの削除や、生成テストの書き出しを直接打つと、
ゲームの実装・テスト置き場を触るコマンドになり、deny 設定（防壁①）に触れる。すり抜けずに済むよう、
手順をハーネスの決まった操作にする。人の手による削除や編集を挟まないので、標本も毎回同じになる。

prepare は次の順で行い、どこかで失敗したら止まる（標本を半端な状態で残さない）。
1. `--base`（既定 origin/<base_branch>）から捨て枝 test/canary-<id> の worktree を作る
2. 単位定義の impl_files を `git rm` で削除する
3. `--retire-v1-tests` のとき：リポジトリにある同じ id の v1 単位定義が受入テストに挙げていたクラスの
   ファイル（test_dir 以下の <クラス名>.cs）を `git rm` で退役させる。v2 では受入テストは生成物だけにする
   （2026-09-23 裁定）。1 つでも見つからなければ止まる
4. decompose.py --from-unit で、単位定義と生成テストを書き出す（スキーマ門とテスト生成を通す）
5. 1 つのコミットにまとめる

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


def retire_v1_tests(proj, unit_id, worktree):
    """同じ id の v1 単位定義が挙げていた受入テストのファイルを git rm する。退役させたパスの一覧。"""
    v1_path = Path(worktree) / proj["units_dir"] / f"{unit_id}.json"
    try:
        v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CanaryError(f"退役させる v1 の単位定義を読めません: {e}")
    if v1.get("schema") == 2:
        raise CanaryError("リポジトリの単位定義は既に v2 です。退役させる手書きテストはありません")
    classes = (v1.get("acceptance") or {}).get("required_tests") or []
    if not classes:
        raise CanaryError("v1 の単位定義に acceptance.required_tests がありません")
    tracked = _git(["ls-files", "--", proj["test_dir"]], worktree, "git ls-files").splitlines()
    retired = []
    for cls in classes:
        hits = [p for p in tracked if Path(p).name == f"{cls}." + "cs"]
        if len(hits) != 1:
            raise CanaryError(f"受入テスト {cls} のファイルが {proj['test_dir']} にちょうど 1 つありません（{len(hits)} 件）")
        retired += hits
    _git(["rm", "-q", "--"] + retired, worktree, "v1 の受入テストの退役")
    return retired


def prepare(proj, unit_path, worktree, base=None, runner=subprocess.run, retire_v1=False):
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
    if retire_v1:
        retire_v1_tests(proj, unit["id"], worktree)
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
    p.add_argument("--retire-v1-tests", action="store_true",
                   help="同じ id の v1 単位定義が挙げていた手書きの受入テストを退役させる")
    c = sub.add_parser("cleanup")
    c.add_argument("--project", required=True)
    c.add_argument("--worktree", required=True)
    args = ap.parse_args(argv)
    proj = project.load(args.project)
    try:
        if args.cmd == "prepare":
            branch, head = prepare(proj, args.unit, args.worktree, args.base, retire_v1=args.retire_v1_tests)
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
