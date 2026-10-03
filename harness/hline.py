"""H ライン：ハーネス自身の改修の自律ループ（段 0、docs/design/foundation_v3_review.md 改訂 5 §3）。

    python -m harness.hline poll     受信箱の What を 1 件取り、PR にする（タスク スケジューラが 15 分ごとに呼ぶ）
    python -m harness.hline setup    受信箱と総監督の部屋（.claude/settings.json・CLAUDE.md）を書く

**なぜ要るか**: 総監督の道具（シェル・編集）を剥ぐと、ハーネスを改修する者がいなくなる。受信箱の What 1 件を
PR 1 件に変える最小のループをここに置き、以後の改修はすべてこれに流す。

終了コード: 0 = PR を作った／取る What が無い／前回が走行中、1 = 収束せず TODO.md に記録、2 = 環境の異常。
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import model_pin  # noqa: E402
import proc  # noqa: E402
import progress  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hline.json"
RESERVED = {"report.md", "TODO.md", "CLAUDE.md"}
TEMPLATE = ROOT / "harness" / "templates" / "director_room" / "CLAUDE.md"


class Infra(Exception):
    """環境の異常（git・gh・CLI の起動の失敗、モデルの照合の失敗）。終了コード 2。"""


def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    model_pin.require(cfg["implementer"], "config/hline.json の implementer")
    return cfg


# ============================================================ 受信箱

def pick(inbox):
    """名前の順で最初の What。H ラインが書くファイルは取らない。"""
    found = sorted(p for p in Path(inbox).glob("*.md") if p.is_file() and p.name not in RESERVED)
    return found[0] if found else None


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")
    return s[:40] or "task"


def title_of(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "H ラインのタスク"


def acquire_lock(inbox, stale_seconds, now=None):
    """前回が走行中なら False。stale_seconds より古いロックは落ちた走行の残骸として取り直す。"""
    lock = Path(inbox) / ".hline.lock"
    now = now if now is not None else datetime.datetime.now().timestamp()
    if lock.exists() and now - lock.stat().st_mtime < stale_seconds:
        return None
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


# ============================================================ 子プロセス

def must(args, cwd, ttl, label):
    code, out, err = proc.run(args, cwd, ttl, label)
    if code != 0:
        raise Infra(f"{label} が失敗しました（終了コード {code}）: {(err or out)[-800:]}")
    return out


def implementer_args(imp, cli):
    return (cli + [imp["headless_flag"], imp["model_flag"], imp["model"]]
            + imp["extra_flags"] + imp["output_format_args"])


def build_prompt(what, feedback=None):
    p = ["あなたはハーネス（Python のリポジトリ game-harness）の実装役です。作業ディレクトリはその作業ツリーです。",
         "次の What を満たす変更を、作業ディレクトリの中だけで行ってください。",
         "- 合格条件ごとに unittest のテストを tests/ に書く（既存の書き方に合わせる）",
         "- docs/progress.yaml と .claude/ は変えない。git の操作はしない（コミットはハーネスが行う）",
         "- 判定は `python -m unittest discover -s tests` の終了コード 0。既存のテストを壊さない",
         "", "---", what.strip()]
    if feedback:
        p += ["", "---", "前回の変更は判定に通りませんでした。次の出力を読んで直してください。", feedback]
    return "\n".join(p) + "\n"


def implement(cfg, wt, what, feedback, log):
    """実装役を 1 回呼ぶ。使ったモデルを照合し、記録する（作業規約：model_pin）。"""
    imp = cfg["implementer"]
    args = implementer_args(imp, proc.resolve_cli(imp["cli"]))
    code, out, err = proc.run(args, wt, cfg["ttl_seconds"]["implementer"], "実装役",
                              input=build_prompt(what, feedback))
    log.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    used, why = model_pin.claude_models(out)
    try:
        models = model_pin.check_claude(imp, used, why, "H ラインの実装役")
    except model_pin.ModelPinError as e:   # 認証切れなどもここに来る。CLI の結果の文を添える
        detail = json.loads(out).get("result", "") if why is None else why
        raise Infra(f"{e}（CLI: {str(detail)[:200]}）")
    return code, models


def changed_paths(wt, cfg):
    out = must(["git", "status", "--porcelain", "-uall"], wt, cfg["ttl_seconds"]["git"], "git status")
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def gate(cfg, wt, paths):
    """H ラインの Gate 1（段 0）。(通ったか, 実装役に返す出力)。"""
    if not paths:
        return False, "作業ツリーに変更がありません。What を満たす変更を加えてください。"
    bad = [p for p in paths if any(p == q or p.startswith(q) for q in cfg["protected_paths"])]
    if bad:
        return False, f"変えてはならないパスを変えています: {', '.join(bad)}。元に戻してください。"
    env = dict(os.environ, PYTHONUTF8="1")
    code, out, err = proc.run(cfg["gate_command"], wt, cfg["ttl_seconds"]["gate"], "Gate 1", env=env)
    return code == 0, (err + out)[-cfg["gate_tail_chars"]:]


# ============================================================ 1 件の処理

def new_worktree(cfg, tid):
    t = cfg["ttl_seconds"]["git"]
    must(["git", "fetch", "-q", "origin"], ROOT, t, "git fetch")
    branch = f"hline/{tid}-{uuid.uuid4().hex[:8]}"
    wt = Path(cfg["worktrees"]) / branch.replace("/", "-")
    must(["git", "worktree", "add", "-q", "-b", branch, str(wt), cfg["base"]], ROOT, t, "git worktree add")
    return wt, branch


def run_task(cfg, tid, what, outdir):
    """作業ツリーを作り直しながら試す。通った (作業ツリー, ブランチ, 試行の記録) か None。作業ツリーは消さない（Detach）。"""
    tries = []
    for run in range(1 + cfg["reruns"]):
        wt, branch = new_worktree(cfg, tid)
        feedback = None
        for attempt in range(1, cfg["max_attempts"] + 1):
            log = outdir / f"implementer-{run}-{attempt}.log"
            code, models = implement(cfg, wt, what, feedback, log)
            ok, feedback = gate(cfg, wt, changed_paths(wt, cfg))
            tries.append({"run": run, "attempt": attempt, "cli_exit": code, "models": models, "gate": ok})
            (outdir / f"gate-{run}-{attempt}.log").write_text(feedback, encoding="utf-8")
            if ok:
                return wt, branch, tries
    return None, None, tries


def pr_body(what, tries):
    rows = "\n".join(f"| {t['run']} | {t['attempt']} | {t['cli_exit']} | {', '.join(t['models'])} | "
                     f"{'通過' if t['gate'] else '不合格'} |" for t in tries)
    return (f"## What（受信箱から取り込んだ全文）\n\n{what.strip()}\n\n"
            "## H ラインの記録\n\n| 作業ツリー | 試行 | CLI の終了コード | 使われたモデル | Gate 1 |\n"
            f"|:--|:--|:--|:--|:--|\n{rows}\n\n"
            "- Gate 1：`python -m unittest discover -s tests` の終了コード 0（段 0）\n"
            "- この PR は H ライン（`harness/hline.py`）が作った。マージの承認は人間が非同期に行う\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")


def deliver(cfg, wt, branch, title, what, tries):
    """コミット → push → PR → 進捗の記録（PR のブランチに積む）。PR の URL を返す。"""
    t, msg = cfg["ttl_seconds"], f"feat(hline): {title}\n\n{cfg['commit_trailer']}\n"
    must(["git", "add", "-A"], wt, t["git"], "git add")
    must(["git", "commit", "-q", "-m", msg], wt, t["git"], "git commit")
    must(["git", "push", "-q", "-u", "origin", branch], wt, t["git"], "git push")
    gh = proc.resolve_cli("gh")
    url = must(gh + ["pr", "create", "--base", "main", "--head", branch, "--title", f"feat(hline): {title}",
                     "--body", pr_body(what, tries)], wt, t["gh"], "gh pr create").strip().splitlines()[-1]
    state = progress.load(Path(wt) / progress.REL_PATH).get("active_task_id")
    if state:
        must([sys.executable, "-m", "harness.progress", "review", state, url], wt, t["git"], "harness.progress review")
        must(["git", "commit", "-q", "-am", f"chore(progress): {state} のレビュー待ちを記録する（harness.progress の出力）\n\n"
              f"{cfg['commit_trailer']}\n"], wt, t["git"], "git commit")
        must(["git", "push", "-q"], wt, t["git"], "git push")
    return url


def write_report(cfg, wt):
    out = must([sys.executable, "-m", "harness.progress", "report"], wt or ROOT, cfg["ttl_seconds"]["git"], "report")
    (Path(cfg["inbox"]) / "report.md").write_text(out, encoding="utf-8")


def record_todo(cfg, tid, title, tries):
    with open(Path(cfg["inbox"]) / "TODO.md", "a", encoding="utf-8") as f:
        f.write(f"- {datetime.date.today().isoformat()} {tid}「{title}」：{len(tries)} 回の試行で Gate 1 に通らず、"
                "パッチを捨てた（下流の依存は段 3 で扱う）\n")


def poll(cfg):
    inbox = Path(cfg["inbox"])
    if not inbox.is_dir():
        raise Infra(f"受信箱がありません: {inbox}（python -m harness.hline setup）")
    lock = acquire_lock(inbox, cfg["ttl_seconds"]["lock_stale"])
    if lock is None:
        print("前回の走行が続いています。何もしません")
        return 0
    try:
        src = pick(inbox)
        if src is None:
            write_report(cfg, None)
            print("受信箱に What がありません")
            return 0
        tid = f"{datetime.datetime.now():%Y%m%d-%H%M}-{slug(src.name)}"
        outdir = Path(cfg["out"]) / tid
        outdir.mkdir(parents=True, exist_ok=True)
        what = src.read_text(encoding="utf-8")
        shutil.move(str(src), str(outdir / "what.md"))   # 取った What は受信箱から消える（二度取らない）
        title = title_of(what)
        print(f"[{tid}] {title}")
        try:
            wt, branch, tries = run_task(cfg, tid, what, outdir)
        except Infra:
            shutil.copyfile(outdir / "what.md", src)   # 環境の異常では What を失わない（次の起動で取り直す）
            raise
        (outdir / "tries.json").write_text(json.dumps(tries, ensure_ascii=False, indent=1), encoding="utf-8")
        if wt is None:
            record_todo(cfg, tid, title, tries)
            write_report(cfg, None)
            print(f"[{tid}] 収束しませんでした。TODO.md に記録しました")
            return 1
        url = deliver(cfg, wt, branch, title, what, tries)
        write_report(cfg, wt)
        print(f"[{tid}] PR: {url}")
        return 0
    finally:
        lock.unlink(missing_ok=True)


# ============================================================ 総監督の部屋

def director_settings(inbox, src_root=Path("C:/src"), home_dirs=(".claude", ".gemini")):
    """総監督のセッションの設定。シェルを無効にし、受信箱の外と、自分の設定・H ラインの書くファイルへの書き込みを拒否する。

    deny は allow より強いので「受信箱だけ許す」とは書けない。受信箱の外にあるものを、作った時点の一覧で全部拒否する。
    """
    inbox = Path(inbox)

    def rule(p):
        return "//" + p.as_posix().replace(":", "", 1).lower() + "/**"

    outside = [p for p in sorted(Path(src_root).iterdir()) if p != inbox.parent]
    outside += [p for p in sorted(inbox.parent.iterdir()) if p != inbox]
    edit = [rule(p) if p.is_dir() else rule(p)[:-3] for p in outside] + [f"~/{d}/**" for d in home_dirs]
    edit += [rule(inbox / ".claude")] + [rule(inbox).replace("/**", "/" + n) for n in sorted(RESERVED)]
    read = [rule(Path(src_root) / ".local" / d) for d in ("wt", "out")] + ["**/*.cs"]
    deny = (["Bash", "PowerShell", "NotebookEdit", "mcp__terminal"]
            + [f"Edit({r})" for r in edit] + [f"Read({r})" for r in read])
    return {"permissions": {"deny": deny, "disableBypassPermissionsMode": "disable"}}


def setup(cfg):
    inbox = Path(cfg["inbox"])
    (inbox / ".claude").mkdir(parents=True, exist_ok=True)
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    (inbox / ".claude" / "settings.json").write_text(
        json.dumps(director_settings(inbox), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(TEMPLATE, inbox / "CLAUDE.md")
    (inbox / "TODO.md").touch()
    write_report(cfg, None)
    print(f"受信箱と総監督の部屋を書きました: {inbox}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="H ライン（段 0）")
    ap.add_argument("cmd", choices=["poll", "setup"])
    args = ap.parse_args(argv)
    cfg = load_config()
    try:
        return poll(cfg) if args.cmd == "poll" else setup(cfg)
    except Infra as e:
        print(f"ABORT（環境の異常）: {e}")
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
