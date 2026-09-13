"""MS3 パイプライン（1 タスク実行ワーカー）。

    python harness/pipeline.py --project unity-2d --unit tools/units/metapoint.json --selftest
    python harness/pipeline.py --project unity-2d --unit tools/units/metapoint.json

--unit はゲームのリポジトリからの相対パス（スケジューラは cwd をリポジトリにして呼ぶ）。

人間を呼ばない。標準入力を読まない。終了コードは 0 / 1 / 2 のみ。

承認済みの 7 制約:
  1. 単位は --unit で外部から受け取る。ここに単位を書かない
  2. 標準入力を読まない（stdin=DEVNULL）
  3. 終了コードは 0=成功 / 1=門番 REJECT / 2=システム ABORT
  4. サブスク枠の残量を見ない。待つのは呼び出し側（MS4 スケジューラ）の責務
  5. 1 プロセス = 1 単位。グローバル状態を持ち越さない
  6. サンドボックス初期化は冪等
  7. TTL は設定から注入する
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import project

TRX_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"

# Windows でサブプロセスがコンソールウィンドウを開かないようにする。
# 非 Windows では属性が無いので 0 になり、無害に無視される。
# 効くのは git / dotnet / gh / agy（コンソールアプリ）。Unity.exe は GUI
# サブシステムなので効かないが、-batchmode -nographics で元々出ない。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# creationflags だけでは足りない。agy のように内部でさらに子を起こす
# プロセスは conhost.exe を一瞬立ち上げてフォーカスを奪う（実測）。
# STARTUPINFO で SW_HIDE を渡し、最初のウィンドウ表示自体を抑える。
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE


# ============================================================ 基本

def run(args, cwd, ttl, label, env=None):
    """shell=False。stdin は塞ぐ（制約 2）。ウィンドウも出さない。"""
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=ttl, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, env=env,
                           creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"TTL超過 ({ttl}s): {label}"
    except FileNotFoundError as e:
        return 127, "", f"コマンドが見つかりません: {e}"


def resolve_cli(name):
    for cand in (name + ".cmd", name + ".exe", name):
        p = shutil.which(cand)
        if p:
            return [p]
    sys.exit(f"ABORT: {name} CLI が PATH にありません")


class Ctx:
    """1 単位分の文脈。グローバル状態を持たない（制約 5）。"""

    def __init__(self, cfg, unit_path):
        self.cfg = cfg
        self.unit = json.loads(Path(unit_path).read_text(encoding="utf-8"))

        p = self.cfg["paths"]
        self.repo = Path(p["repo"])
        self.proj_sub = p["unity_project_subdir"]
        self.out = Path(p["out_dir"]) / "pipeline"
        self.sandbox = Path(p.get("sandbox", r"C:\src\.local\wt\ms3-sandbox"))
        self.stage = self.out / "golden"

        self.ttl = self.cfg["ttl_seconds"]
        self.ttl.update(self.unit.get("ttl_seconds", {}))  # 単位側で上書き可（制約 7）

        self.ctrl_fail = self.cfg["control_groups"]["must_fail"]
        self.ctrl_pass = self.cfg["control_groups"]["must_pass"]
        # 単位には 2 種類ある。
        #   リファクタリング: 既存実装から採取したゴールデンが正解（MS1〜3）
        #   新規機能        : 既存の正解が無い。分解役が先に書いたテストが正解
        # 後者を「テスト駆動モード」と呼ぶ。golden が無ければそちら。
        self.g = self.unit.get("golden")
        self.test_driven = self.g is None
        self.out.mkdir(parents=True, exist_ok=True)

        # TIMELINE への追記に使う実測値。各門が通るたびに埋まる。
        # 手で書くと実態とずれても誰も気づかないので、判定に使った値をそのまま残す。
        self.metrics = {}

    # ---- パス
    def sb(self, rel):
        return self.sandbox / rel.replace("/", "\\")

    @property
    def unity_proj(self):
        return self.sandbox / self.proj_sub


# ============================================================ サンドボックス

def sandbox_reset(c):
    """冪等（制約 6）。中断が残っていても自力で復帰する。

    git clean -fd は ignored を消さないので Library は残る。消すと Unity の
    フルインポート（実測 11.9 分）が毎回走る。
    """
    if not (c.sandbox / ".git").exists():
        c.sandbox.parent.mkdir(parents=True, exist_ok=True)
        rc, _, err = run(["git", "worktree", "add", "--detach", str(c.sandbox), "HEAD"],
                         c.repo, c.ttl["git"], "worktree add")
        if rc != 0:
            sys.exit(f"ABORT: サンドボックスを作れません: {err}")

    # リポジトリの現在位置へ合わせる。"HEAD" を指定すると detached worktree は
    # 自分の古いコミットに留まり、リポジトリ側の修正が永遠に届かない。
    # MS1 の sandbox_reset に同じ教訓をコメントで書いたのに、MS3 で再演した。
    # 散文は再発を防がないので、下で機械検査する。
    rc, out, _ = run(["git", "rev-parse", "HEAD"], c.repo, c.ttl["git"], "repo head")
    target = out.strip()
    if rc != 0 or not target:
        sys.exit("ABORT: リポジトリの HEAD を取得できません")

    purge_holdout(c)
    run(["git", "reset", "--hard", target], c.sandbox, c.ttl["git"], "reset to repo head")
    run(["git", "clean", "-fd"], c.sandbox, c.ttl["git"], "clean")
    purge_holdout(c)

    # 同期できたことの検査。ここを散文ではなく検査にしないと、同じ間違いが
    # 次に書かれたとき「実装が悪い」という形で 3 回 REJECT されるだけで、
    # 原因に到達できない（実測でそうなった）。
    _, sb_head, _ = run(["git", "rev-parse", "HEAD"], c.sandbox, c.ttl["git"], "sandbox head")
    if sb_head.strip() != target:
        sys.exit(f"ABORT: サンドボックスがリポジトリに追従していません "
                 f"(sandbox={sb_head.strip()[:8]} repo={target[:8]})")

    if c.test_driven:
        return
    if (c.sb(c.g["holdout_rel"])).exists():
        sys.exit("ABORT: ホールドアウトの残骸を消せませんでした")
    if not (c.sb(c.g["disclosed_rel"])).exists():
        sys.exit(f"ABORT: サンドボックスに開示ゴールデンがありません: {c.g['disclosed_rel']}")


def guid_of(meta_path):
    """.meta から guid を取り出す。読めなければ None。"""
    try:
        for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("guid:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def append_timeline(c, sha):
    """CI が緑になったときだけ、末尾に 1 件追記する。

    追記のみ。既存行を書き換える経路をコードに持たせない（監査証跡のため）。
    数値はすべて c.metrics から取る。手書きすると実態とずれても誰も気づかない
    （自己検査の True 直書きと同じ型の事故になる）。
    """
    path = c.repo / "reports" / "TIMELINE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    m = c.metrics

    def g(key, default="?"):
        v = m.get(key)
        return default if v is None else v

    hcp = c.unit.get("human_check_point") or "（単位定義に human_check_point がありません）"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    entry = (
        f"\n## [{stamp}] {c.unit['id']}: {c.unit.get('title', '')}\n\n"
        f"- **Status**: PASSED — commit `{sha[:8]}` / CI run {g('ci_run_id')}\n"
        f"- **差分**: {g('diff_lines')} 行（上限 {c.unit['max_impl_lines']}）\n"
        f"- **高速検査**: {g('fast_total')} 件 / 失敗 {g('fast_failed')} / skip {g('fast_notExecuted')}\n"
        f"- **Unity**: 総数 {g('unity_total')} / skip {g('unity_skipped')} / "
        f"開示 {g('unity_disclosed')} / 非開示 {g('unity_holdout')} / "
        f"control_must_fail={g('ctrl_fail')}\n"
        f"- **試行**: {g('attempt')} 回目で合格\n"
        f"- **Human Check Point**: {hcp}\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"    追記: {path}")


def wait_for_ci(c):
    """push した commit の run を名指しで待つ。

    `gh run watch` を run ID 無しで呼ぶと直近の run を拾う。push 直後は
    まだ run が生成されていないことがあり、その瞬間には前回の差し戻し run
    （赤）を見てしまう。標準入力を塞いでいるので対話選択にも応じられない。
    実測: 正しい実装が CI で緑だったのに 3 回とも赤と誤判定し、差し戻した。

    したがって SHA で run を特定してから watch する。
    """
    _, out, _ = run(["git", "rev-parse", "HEAD"], c.repo, c.ttl["git"], "head")
    sha = out.strip()
    if not sha:
        return 2, "HEAD を取得できません"

    run_id = None
    for _ in range(30):  # run が現れるまで待つ
        _, listed, _ = run(["gh", "run", "list", "--limit", "20",
                            "--json", "databaseId,headSha"],
                           c.repo, c.ttl["git"], "gh run list")
        try:
            for r in json.loads(listed or "[]"):
                if r.get("headSha") == sha:
                    run_id = str(r["databaseId"])
                    break
        except (ValueError, KeyError):
            pass
        if run_id:
            break
        time.sleep(5)

    if not run_id:
        return 2, f"CI の run が見つかりません (sha={sha[:8]})"

    c.metrics["ci_run_id"] = run_id
    rc, _, err = run(["gh", "run", "watch", run_id, "--exit-status"],
                     c.repo, c.ttl["gh"], "gh run watch")
    if rc != 0:
        return rc, f"CI が赤 (run={run_id}): {err[:200]}"
    return 0, f"CI 緑 (run={run_id})"


def heads_match(c):
    """リポジトリとサンドボックスの HEAD を比べる。(repo, sandbox, 一致か) を返す。

    比較を関数に出しておくのは、自己検査から「わざとずらした状態」で直接呼べる
    ようにするため。sandbox_reset の中で比べるだけだと、外からずらしても
    reset が先に走って直してしまい、負のテストが成立しない。
    """
    _, r, _ = run(["git", "rev-parse", "HEAD"], c.repo, c.ttl["git"], "repo head")
    _, s, _ = run(["git", "rev-parse", "HEAD"], c.sandbox, c.ttl["git"], "sandbox head")
    return r.strip(), s.strip(), (r.strip() == s.strip() and bool(r.strip()))


TEST_PATH_RE = re.compile(r"(Tests?\.cs$|[/\\][Tt]ests?[/\\]|golden.*\.json$)")

# 自己検査が [A] で一時的に書き込む中身。実装を消して赤が出ることを確かめるため。
# 残っていたら selftest が異常終了している。
STUB_MARKER = (
    "// selftest が一時的に置いたスタブ。残っていたら selftest が途中で死んでいる。\n"
    "namespace SelftestStub { public class Placeholder { } }\n"
)


def require_unit_safe(c):
    """単位定義そのものを検査する。

    新規機能では、分解役（LLM）が書いたテストが唯一のオラクルになる。
    そのテストがホワイトリストに入っていれば、実装役はテストを書き換えて
    通せる（報酬ハッキング）。単位定義も LLM が生成するので、
    **監査に任せず機械で弾く。** これは推奨ではなく前提条件。
    """
    bad = [p for p in c.unit["whitelist"] if TEST_PATH_RE.search(p)]
    if bad:
        sys.exit("ABORT: ホワイトリストにテストまたはゴールデンが含まれています。"
                 "実装役がオラクルを書き換えられるため実行しません: " + ", ".join(bad))

    if not c.unit["whitelist"]:
        sys.exit("ABORT: ホワイトリストが空です")

    if c.test_driven and not c.unit.get("acceptance", {}).get("required_tests"):
        sys.exit("ABORT: テスト駆動の単位に acceptance.required_tests がありません。"
                 "正解が定義されていないため、合否を判定できません")


def require_repo_clean(c):
    """起動時にリポジトリがクリーンであることを要求する。

    汚れたまま起動すると 2 つの害がある:
      1. 未コミットの修正はサンドボックスへ届かない（同期はコミット単位のため）
      2. 実行後の gate_repo_untouched が、その汚れを「AIの脱走」と誤報告する
    起動時の汚れ（運用ミス）と実行中の書き換え（脱走）を区別するため、
    ここで先に落とす。
    """
    dirty = gate_repo_untouched(c)
    if dirty:
        sys.exit("ABORT: リポジトリに未コミットの変更があります。"
                 "コミットするか破棄してから実行してください: " + ", ".join(dirty[:5]))


def purge_holdout(c):
    """ソースと出力の両方から消す。片方だけでは PreserveNewest で次のランに混入する。"""
    if c.test_driven:
        return []          # この単位には非開示が無い
    removed = []
    for p in [c.sb(c.g["holdout_rel"])] + \
             list(c.sandbox.glob("tests/**/bin/**/" + Path(c.g["holdout_rel"]).name)) + \
             list(c.stage.glob(Path(c.g["holdout_rel"]).name)):
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return removed


# ============================================================ 実装AI

def call_implementer(c, feedback=""):
    # 相対パスで渡すと、実装AIが本体リポジトリを編集しうる（実測で発生した）。
    # サンドボックスの絶対パスに展開して曖昧さを消す。ただしこれは
    # 「間違えにくくする」だけで、脱走を防ぐ機構ではない。
    # 実際の防波堤は gate_repo_untouched（検出して ABORT）。
    prompt = c.unit["prompt"]
    # 単位の種類でキーが違う。無いトークンは置換しないだけで、エラーにしない。
    tokens = {"{sandbox_abs}": str(c.sandbox)}
    if c.unit.get("core_impl"):
        tokens["{core_abs}"] = str(c.sb(c.unit["core_impl"]))
    if c.unit.get("so_impl"):
        tokens["{so_abs}"] = str(c.sb(c.unit["so_impl"]))
    for i, rel in enumerate(c.unit.get("impl_files") or c.unit["whitelist"]):
        tokens[f"{{impl_abs_{i}}}"] = str(c.sb(rel))
    for token, value in tokens.items():
        prompt = prompt.replace(token, value)

    # 作業場所を必ず伝える。相対パスだけだと本体を編集しうる（実測で発生した）。
    prompt = (f"作業対象は {c.sandbox} の中だけです。この外にあるファイルは"
              f"絶対に読み書きしないでください。\n\n" + prompt)
    if feedback:
        prompt += "\n\n前回の失敗:\n" + feedback

    imp = c.cfg["implementer"]
    args = resolve_cli(imp["cli"]) + [imp["headless_flag"], prompt,
                                      imp["auto_approve_flag"],
                                      imp["model_flag"], imp["model_name"]]
    rc, out, err = run(args, c.sandbox, c.ttl["implementer"], "実装AI")
    if rc != 0:
        return False, f"実装AI が異常終了 (rc={rc}): {(err or out)[:400]}"
    return True, ""


# ============================================================ 静的門

def gate_repo_untouched(c):
    """本体リポジトリが触られていないことを確認する。

    サンドボックス（git worktree）は機構ではなく慣習である。同一ユーザー・
    同一権限で動く実装AIは本体を書き換えられるし、実際に書き換えた。
    防げないので、破れたことを検出する。検出したらリトライせず ABORT する
    （同じことを 3 回繰り返すだけなので）。
    """
    _, out, _ = run(["git", "status", "--porcelain"], c.repo, c.ttl["git"], "repo status")
    return [l[3:].strip() for l in out.splitlines() if l.strip()]


def gate_whitelist(c):
    _, out, _ = run(["git", "status", "--porcelain"], c.sandbox, c.ttl["git"], "status")
    allowed = set(c.unit["whitelist"])
    bad = []
    for line in out.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1].strip().strip('"')
        path = path.replace("\\", "/")
        if path.endswith(".meta"):
            continue
        if path not in allowed:
            bad.append(path)
    return bad


def gate_static_common(c):
    """単位の種類によらない検査。実装ファイル全部に対して行う。"""
    impls = c.unit.get("impl_files") or c.unit["whitelist"]
    for rel in impls:
        p = c.sb(rel)
        if not p.exists():
            return f"{rel} が存在しません"
        text = p.read_text(encoding="utf-8", errors="replace")

        # forbidden_patterns: [[正規表現, 説明], ...]
        # 新規機能では UnityEngine / Time.deltaTime / MonoBehaviour を禁じ、
        # 仮想時間を外から注入する形（決定論的 FSM）を強制する。
        for pattern, label in c.unit.get("forbidden_patterns", []):
            if re.search(pattern, text):
                return f"{label}: {rel} に「{pattern}」が含まれています"

        if c.unit["forbidden_leftover"] in text:
            return f"{c.unit['forbidden_leftover']} が残っています（{rel}）"
        m = re.search(c.unit["forbidden_skip_attribute_regex"], text)
        if m:
            return f"skip 属性の使用: [{m.group(1)}]（{rel}）"
    return None


def gate_static(c):
    if c.test_driven:
        ng = gate_static_common(c)
        if ng:
            return ng
        impls = c.unit.get("impl_files") or c.unit["whitelist"]
        text = "\n".join(c.sb(r).read_text(encoding="utf-8", errors="replace") for r in impls)
        missing = [s for s in c.unit["required_symbols"] if s not in text]
        if missing:
            return "シグネチャが揃っていません: " + ", ".join(missing)
        return None

    core = c.sb(c.unit["core_impl"])
    so = c.sb(c.unit["so_impl"])
    if not core.exists():
        return f"{c.unit['core_impl']} が存在しません"
    if not so.exists():
        return f"{c.unit['so_impl']} が存在しません"

    core_t = core.read_text(encoding="utf-8", errors="replace")
    so_t = so.read_text(encoding="utf-8", errors="replace")

    # --- Game.Core 側を先に見る。
    # コンパイル可否に直結するもの（Mathf の残存）を、配線の問題（委譲）より先に
    # 報告する。逆順にすると、委譲が未配線の間は Mathf の門に到達できず、
    # 門が効いているかを確かめられない（自己検査で実測した）。
    missing = [s for s in c.unit["required_symbols"] if s not in core_t]
    if missing:
        return "シグネチャが壊れています: " + ", ".join(missing)

    if re.search(c.unit["forbidden_in_core_regex"], core_t):
        return "Game.Core 側に Mathf が残っています（noEngineReferences で通りません）"

    if c.unit["forbidden_leftover"] in core_t:
        return f"{c.unit['forbidden_leftover']} が残っています（{c.unit['core_impl']}）"

    # --- SO 側
    so_missing = [s for s in c.unit["so_required_symbols"] if s not in so_t]
    if so_missing:
        return "インスペクタ結合が壊れています: " + ", ".join(so_missing)

    if c.unit["so_required_delegation"] not in so_t:
        return f"委譲されていません（{c.unit['so_required_delegation']} が無い）"

    if c.unit["forbidden_leftover"] in so_t:
        return f"{c.unit['forbidden_leftover']} が残っています（{c.unit['so_impl']}）"

    for text in (core_t, so_t):
        m = re.search(c.unit["forbidden_skip_attribute_regex"], text)
        if m:
            return f"skip 属性の使用: [{m.group(1)}]"
    return None


def gate_diff_lines(c, verbose=True):
    # 未追跡のファイルは git diff に出ない。新規単位では実装ファイルが
    # 丸ごと新規なので、これをやらないと差分が常に 0 になり門が発火しない
    # （実測で発覚。自己検査だけでなく本番の穴だった）。
    # -N は intent-to-add。中身はステージせず、diff に現れるようにするだけ。
    run(["git", "add", "-N", "--"] + c.unit["whitelist"], c.sandbox, c.ttl["git"], "intent-to-add")
    total = 0
    for rel in c.unit["whitelist"]:
        _, out, _ = run(["git", "diff", "--numstat", "HEAD", "--", rel],
                        c.sandbox, c.ttl["git"], "numstat")
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            add = 0 if parts[0] == "-" else int(parts[0])
            dele = 0 if parts[1] == "-" else int(parts[1])
            total += add + dele
    c.metrics["diff_lines"] = total
    if verbose:
        print(f"    差分: {total} 行")
    if total > c.unit["max_impl_lines"]:
        return f"差分超過: {total} 行 > {c.unit['max_impl_lines']}"
    return None


# ============================================================ 高速検査（Pure C#）

def run_fast_tests(c, tag):
    trx = c.out / f"{tag}.trx"
    if trx.exists():
        trx.unlink()
    run(["dotnet", "test", c.unit["fast_test_project"],
         "--nologo", "--logger", f"trx;LogFileName={tag}.trx",
         "--results-directory", str(c.out)],
        c.sandbox, c.ttl["dotnet_test"], f"dotnet test ({tag})")
    if not trx.exists():
        return None, "検査系故障: TRX が生成されませんでした（ビルド失敗の可能性）"

    root = ET.parse(trx).getroot()

    # TRX の testName はメソッド名だけで、クラス名は別要素の className にある。
    # required_tests はクラス名で書かれるので、メソッド名だけを探すと
    # 「1 件も実行されていない」と誤判定する（実測。実装は成功していた）。
    # className.methodName の形に組み立ててから照合する。
    fullname = {}
    for ut in root.iter(f"{TRX_NS}UnitTest"):
        tm = ut.find(f"{TRX_NS}TestMethod")
        if tm is not None and ut.get("name"):
            fullname[ut.get("name")] = f"{tm.get('className', '')}.{ut.get('name')}"

    results = {}
    for r in root.iter(f"{TRX_NS}UnitTestResult"):
        n = r.get("testName")
        results[fullname.get(n, n)] = r.get("outcome")

    counters = root.find(f"{TRX_NS}ResultSummary/{TRX_NS}Counters")
    if counters is not None:
        for k in ("total", "passed", "failed", "notExecuted"):
            c.metrics[f"fast_{k}"] = int(counters.get(k, 0))
    return results, None


# ============================================================ Unity 受入

def run_unity_tests(c, tag):
    xml = c.out / f"{tag}.xml"
    log = c.out / f"{tag}.log"
    if xml.exists():
        xml.unlink()

    import os
    env = dict(os.environ)
    # 同居する全ランナーが同じステージング先を見る。片方しか設定しないと
    # 他方が「ファイルが無い」で落ちる。
    for var in c.cfg["golden_dir_env_vars"]:
        env[var] = str(c.stage)

    # -testFilter は付けない。名前空間を指定すると NUnit が [Explicit] を
    # 「明示的な選択」とみなして実行してしまい、Skip されるはずの 20 件が
    # 走って落ちる（実測で発生した）。無指定なら正しく Skip される。
    unity = c.cfg["unity_exe"]
    rc, _, err = run([unity, "-batchmode", "-nographics",
                      "-projectPath", str(c.unity_proj),
                      "-runTests", "-testPlatform", "EditMode",
                      "-testResults", str(xml), "-logFile", str(log)],
                     c.sandbox, c.ttl["unity"], f"unity ({tag})", env=env)
    if not xml.exists():
        return None, f"検査系故障: 結果 XML が生成されませんでした（rc={rc} {err[:200]} log={log}）"

    x = ET.parse(xml).getroot()
    results = {}
    for n in x.iter("test-case"):
        results[n.get("fullname")] = n.get("result")
    return results, None


# ============================================================ 判定

def names_with(results, needle):
    return [n for n in results if n and needle in n]


def outcome_of(results, needle):
    for n, o in results.items():
        if n and needle in n:
            return o
    return None


def real_failures(results, ctrl_fail, failed_word):
    return [n for n, o in results.items()
            if o == failed_word and ctrl_fail not in (n or "")]


def golden_count(c, rel_or_abs):
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = c.sb(str(p))
    return len(json.loads(p.read_text(encoding="utf-8"))["cases"])


def stage_golden(c, with_holdout):
    """開示ゴールデンを「全部」置く。

    この単位のゴールデンだけを置くと、同じ Unity スイートに同居する他の
    ゴールデンランナー（MS2 の GoldenMasterEquivalenceTests など）が
    「ファイルが無い」で落ち、実装が正しくても REJECT になる（実測で発生した）。
    ステージング先は 1 つで、そこを全ランナーが見る。
    """
    c.stage.mkdir(parents=True, exist_ok=True)
    for p in c.stage.glob("golden_*.json"):
        p.unlink()

    src_dir = c.sb("tests/golden")
    staged = []
    for p in sorted(src_dir.glob("golden_*.json")):
        shutil.copyfile(p, c.stage / p.name)
        staged.append(p.name)
    if not staged:
        sys.exit(f"ABORT: 開示ゴールデンが 1 本もありません: {src_dir}")

    if with_holdout:
        shutil.copyfile(c.g["holdout_src"], c.stage / Path(c.g["holdout_rel"]).name)
        shutil.copyfile(c.g["holdout_src"], c.sb(c.g["holdout_rel"]))
    return staged


def check_acceptance_test_driven(c, fast, unity):
    """新規機能の判定。正解は分解役が先に書いたテスト。

    ゴールデンが無いので「既存挙動と一致するか」は問えない。代わりに
      1. 指定された新規テストが実際に実行され、全件通っていること
      2. 既存のテストが 1 件も壊れていないこと（非回帰）
    を要求する。1 が無いと「何も測っていない緑」になる。
    """
    ng = []
    required = c.unit["acceptance"]["required_tests"]

    for name in required:
        hits = names_with(fast, name) + names_with(unity, name)
        if not hits:
            return [f"検査系故障: 新規テスト {name} が 1 件も実行されていない"], True
        bad = [n for n in hits if (fast.get(n) or unity.get(n)) not in ("Passed",)]
        if bad:
            ng.append(f"新規テスト {name} が通っていない（{len(bad)} 件）")

    if outcome_of(unity, c.ctrl_pass) != "Passed":
        return ["検査系故障: 必ず通る対照群が通らなかった"], True

    skipped = [n for n, o in unity.items() if o in ("Skipped", "Inconclusive")]
    for name in required:
        if [n for n in skipped if name in n]:
            return [f"検査系故障: 新規テスト {name} が skip されている"], True
    if len(skipped) > c.cfg["unity_skip_baseline"]:
        ng.append(f"skip が既知の {c.cfg['unity_skip_baseline']} 件を超えた（{len(skipped)}）")

    f_fail = [n for n, o in fast.items() if o == "Failed"]
    u_fail = [n for n, o in unity.items() if o == "Failed"]
    if f_fail:
        ng.append(f"高速検査の失敗 {len(f_fail)} 件: " + ", ".join(f_fail[:3]))
    if u_fail:
        ng.append(f"Unity の失敗 {len(u_fail)} 件: " + ", ".join(u_fail[:3]))

    c.metrics["unity_total"] = len(unity)
    c.metrics["unity_skipped"] = len(skipped)
    c.metrics["new_tests"] = sum(len(names_with(fast, n) + names_with(unity, n)) for n in required)
    return ng, False


def check_acceptance(c, fast, unity, expect_holdout):
    """終了コードではなく個別結果で判定する。"""
    if c.test_driven:
        return check_acceptance_test_driven(c, fast, unity)
    ng = []
    d_tag, h_tag = c.g["disclosed_tag"], c.g["holdout_tag"]
    want_d = golden_count(c, c.g["disclosed_rel"])

    # --- Pure C# 側（純粋クラス直）
    hit = len(names_with(fast, d_tag + "_"))
    if hit < want_d:
        ng.append(f"高速検査で {d_tag} が {hit}/{want_d} 件しか実行されていない")
    f_fail = real_failures(fast, c.ctrl_fail, "Failed")
    if f_fail:
        ng.append(f"高速検査の不一致 {len(f_fail)} 件: " + ", ".join(f_fail[:3]))

    # --- Unity 側（SO 経由）
    if outcome_of(unity, c.ctrl_pass) != "Passed":
        return ["検査系故障: 必ず通る対照群が通らなかった"], True
    hit_u = len(names_with(unity, d_tag + "_"))
    if hit_u < want_d:
        return [f"検査系故障: Unity で {d_tag} が {hit_u}/{want_d} 件しか実行されていない"], True

    skipped = [n for n, o in unity.items() if o in ("Skipped", "Inconclusive")]
    for req in (d_tag, "MetaPointEquivalence"):
        if [n for n in skipped if req in n]:
            return [f"検査系故障: {req} が skip されている"], True
    if len(skipped) > c.cfg["unity_skip_baseline"]:
        ng.append(f"skip が既知の {c.cfg['unity_skip_baseline']} 件を超えた（{len(skipped)}）")

    leaked = names_with(unity, h_tag + "_")
    if not expect_holdout and leaked:
        return [f"検査系故障: 非開示が漏れ込んでいる（{len(leaked)} 件）"], True
    if expect_holdout:
        if not leaked:
            return ["検査系故障: 非開示が実行されていない"], True
        o = outcome_of(unity, c.ctrl_fail)
        if o is None:
            return ["検査系故障: 必ず落ちる対照群が実行されていない"], True
        if o != "Failed":
            return [f"検査系故障: 必ず落ちる対照群が落ちなかった（{o}）"], True

    u_fail = real_failures(unity, c.ctrl_fail, "Failed")
    if u_fail:
        ng.append(f"Unity の不一致 {len(u_fail)} 件: " + ", ".join(u_fail[:3]))

    # TIMELINE に載せる実測値。判定に使った数をそのまま残す。
    c.metrics["unity_total"] = len(unity)
    c.metrics["unity_skipped"] = len(skipped)
    c.metrics["unity_disclosed"] = hit_u
    if expect_holdout:
        c.metrics["unity_holdout"] = len(leaked)
        c.metrics["ctrl_fail"] = outcome_of(unity, c.ctrl_fail)

    return ng, False


# ============================================================ 1 周

def attempt(c, feedback):
    sandbox_reset(c)

    print("[1] 実装AI")
    ok, msg = call_implementer(c, feedback)
    if not ok:
        return "RETRY", msg

    escaped = gate_repo_untouched(c)
    if escaped:
        return "ABORT", ("実装AIがサンドボックス外（本体リポジトリ）を書き換えました: "
                         + ", ".join(escaped[:5]))

    print("[2] 静的機械判定")
    bad = gate_whitelist(c)
    if bad:
        return "RETRY", "許可外のファイル変更: " + ", ".join(bad[:5])
    ng = gate_static(c)
    if ng:
        return "RETRY", ng
    ng = gate_diff_lines(c)
    if ng:
        return "RETRY", ng

    print("[3] 高速検査（Pure C#）")
    fast, err = run_fast_tests(c, "impl_fast")
    if err:
        return "ABORT", err

    print("[4] Unity 受入（開示）")
    stage_golden(c, with_holdout=False)
    unity, err = run_unity_tests(c, "impl_disclosed")
    if err:
        return "ABORT", err
    ng, fatal = check_acceptance(c, fast, unity, expect_holdout=False)
    if fatal:
        return "ABORT", "; ".join(ng)
    if ng:
        return "RETRY", "; ".join(ng)

    if c.test_driven:
        print("[5] 非開示ゴールデンは無し（テスト駆動の単位）")
    else:
        failed = attempt_holdout(c, fast)
        if failed:
            return failed

    print("[6] 持ち出し")
    return carry_out_and_ci(c)


def attempt_holdout(c, fast):
    """非開示ゴールデンの検査。合格なら None、不合格なら (verdict, msg)。"""
    print("[5] Unity 受入（非開示）")
    try:
        stage_golden(c, with_holdout=True)
        unity2, err = run_unity_tests(c, "impl_holdout")
        if err:
            return "ABORT", err
        ng, fatal = check_acceptance(c, fast, unity2, expect_holdout=True)
        if fatal:
            return "ABORT", "; ".join(ng)
        if ng:
            # 内容は渡さない（非開示由来）
            return "RETRY", "非開示の受入条件に不合格でした（詳細は開示されません）"
    finally:
        purge_holdout(c)
    return None


def carry_out_and_ci(c):
    """ホワイトリストのファイルだけを本体へ運び、CI まで見届ける。"""
    # .meta を同伴させる。gate_whitelist は .meta を素通しにしてあるのに
    # 持ち出し側が運んでいなかった。その非対称のせいで、本体で次に Unity を
    # 起動した瞬間に .meta が生えて作業ツリーが汚れ、require_repo_clean が
    # ABORT する（Step 1 のマージ時に顕在化した）。
    carried = []
    for rel in c.unit["whitelist"]:
        src = c.sb(rel)
        if src.exists():
            dst = c.repo / rel.replace("/", "\\")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            carried.append(rel)

        # .meta は GUID を持つ。無条件に上書きすると、そのクラスを参照する
        # Prefab / Scene / .asset がすべて Missing (MonoScript) になる。
        #   - 本体に既にある場合は触らない（新規作成時だけ運ぶ）
        #   - ただし GUID がずれていたらそれ自体が事故なので ABORT する
        meta_src = c.sb(rel + ".meta")
        meta_dst = c.repo / (rel + ".meta").replace("/", "\\")
        if not meta_src.exists():
            continue              # tools/ 配下など .meta を持たないものもある
        if meta_dst.exists():
            g_repo, g_sb = guid_of(meta_dst), guid_of(meta_src)
            if g_repo != g_sb:
                return "ABORT", (f"GUID 不一致: {rel}.meta "
                                 f"(repo={g_repo} sandbox={g_sb})。"
                                 "上書きすると参照が壊れるため中止します")
            continue              # 一致しているなら上書きしない
        shutil.copyfile(meta_src, meta_dst)
        carried.append(rel + ".meta")
    print(f"    {len(carried)} ファイル: " + ", ".join(Path(x).name for x in carried))
    run(["git", "add", "--"] + carried, c.repo, c.ttl["git"], "add")
    run(["git", "commit", "-m",
         f"feat(ms3): implement {c.unit['id']} via pipeline"],
        c.repo, c.ttl["git"], "commit")
    rc, out, err = run(["git", "push"], c.repo, c.ttl["git"], "push")
    if rc != 0 and "no upstream branch" in (err + out):
        # 新しいブランチで初めて push するとき。追跡先を設定して張り直す。
        _, br, _ = run(["git", "branch", "--show-current"], c.repo, c.ttl["git"], "branch")
        rc, out, err = run(["git", "push", "--set-upstream", "origin", br.strip()],
                           c.repo, c.ttl["git"], "push -u")
    if rc != 0:
        return "ABORT", f"push できません: {(err or out)[:300]}"

    print("[7] CI 完了検知")
    rc, ci_msg = wait_for_ci(c)
    if rc != 0:
        print(f"    {ci_msg}")
        print("    CI が赤。自動で差し戻します。")
        run(["git", "revert", "--no-edit", "HEAD"], c.repo, c.ttl["git"], "revert")
        run(["git", "push"], c.repo, c.ttl["git"], "push revert")
        return "RETRY", "CI が赤でした（差し戻し済み）"

    print(f"    {ci_msg}")

    print("[8] TIMELINE へ追記")
    _, sha_out, _ = run(["git", "rev-parse", "HEAD"], c.repo, c.ttl["git"], "head")
    append_timeline(c, sha_out.strip())
    run(["git", "add", "--", "reports/TIMELINE.md"], c.repo, c.ttl["git"], "add timeline")
    run(["git", "commit", "-m", f"docs(timeline): record {c.unit['id']}"],
        c.repo, c.ttl["git"], "commit timeline")
    # この追記コミットの CI は watch しない（内容は Markdown のみ）。
    rc, out, err = run(["git", "push"], c.repo, c.ttl["git"], "push timeline")
    if rc != 0:
        # 実装は既に取り込まれているのに記録が残らない不整合。
        # 黙って成功にしない。作業ツリーが汚れたまま残るので、次回の
        # require_repo_clean が確実に拾う（静かに失われるより良い）。
        return "ABORT", ("実装は取り込まれましたが TIMELINE の push に失敗しました。"
                         "記録と実体が食い違っています: " + (err or out)[:200])

    return "SUCCESS", ""


# ============================================================ 自己検査

def selftest(c):
    """AI を呼ばずに、門が実際に赤を出すかを確認する。"""
    log = []

    def check(name, ok, detail=""):
        log.append(ok)
        print(f"  {'OK  ' if ok else 'NG  '} {name}" + (f"  -- {detail}" if detail else ""))

    print("=== 門の自己検査（実装AIは呼びません） ===")
    sandbox_reset(c)
    repo_h, sb_h, ok = heads_match(c)
    check("サンドボックスがリポジトリに追従している", ok, f"{sb_h[:8]} / {repo_h[:8]}")

    impls = [r for r in (c.unit.get("impl_files") or [c.unit.get("core_impl")]
                         or c.unit["whitelist"]) if r]
    existing = [r for r in impls if c.sb(r).exists()]

    if not existing:
        # 新規単位。実装ファイルがまだ無い（実装役がこれから作る）。
        # ベースラインは本質的に赤であり、戻すべき緑が存在しない。
        # ここで確かめられるのは「オラクルが置かれていて、かつ赤であること」まで。
        print("[A] ベースライン（新規単位。実装はまだ無い）")

        missing_tests = []
        for t in c.unit["acceptance"]["required_tests"]:
            found = list(c.sandbox.rglob(f"*{t}*.cs"))
            if not found:
                missing_tests.append(t)
        check("受入テストがサンドボックスに置かれている",
              not missing_tests,
              "見つからない: " + ", ".join(missing_tests) if missing_tests else "")

        fast, err = run_fast_tests(c, "self_fast")
        if err:
            check("実装が無いので赤", True, "ビルドが失敗（実装が無いので当然）")
        else:
            red = len(real_failures(fast, c.ctrl_fail, "Failed"))
            check("実装が無いので赤", red > 0, f"{red} 件 Failed")
    else:
        # 既存の実装がある単位。自分でスタブを書き、赤が出ることを確かめてから戻す。
        # 「今スタブである」ことを前提にしてはいけない。単位が一度でも成功すると
        # 本体に実装が入り、この検査は空振りして常に緑になる（実測で発覚）。
        print("[A] ベースライン（自分でスタブに戻して赤を確認する）")
        stubbed = []
        for rel in existing:
            p = c.sb(rel)
            stubbed.append((p, p.read_text(encoding="utf-8")))
            p.write_text(STUB_MARKER, encoding="utf-8")

        fast, err = run_fast_tests(c, "self_fast")
        if err:
            # スタブはコンパイルを壊すので、ビルド失敗も「赤が出た」に含める
            check("スタブで赤が出る", True, "ビルドが失敗（想定どおり）")
        else:
            red = len(real_failures(fast, c.ctrl_fail, "Failed"))
            check("スタブで赤が出る", red > 0, f"{red} 件 Failed")

        for p, orig in stubbed:
            p.write_text(orig, encoding="utf-8")
        sandbox_reset(c)
        fast, err = run_fast_tests(c, "self_fast_restored")
        if err:
            check("復元後の高速検査", False, err)
            return 2

        if not c.test_driven:
            d_tag = c.g["disclosed_tag"]
            want_d = golden_count(c, c.g["disclosed_rel"])
            hit = len(names_with(fast, d_tag + "_"))
            check("開示ゴールデンが実行されている", hit >= want_d, f"{hit}/{want_d}")
        check("復元後は緑に戻る", len(real_failures(fast, c.ctrl_fail, "Failed")) == 0,
              f"{len(real_failures(fast, c.ctrl_fail, 'Failed'))} 件 Failed")

    print("[B] ゲートの発火確認（わざと違反させます）")
    # 同期のずれを検出できるか。sandbox_reset を呼ばずに直接比較する
    # （呼ぶと先に直ってしまい、検出できたかが分からない）。
    run(["git", "reset", "--hard", "HEAD~1"], c.sandbox, c.ttl["git"], "desync")
    _, _, ok = heads_match(c)
    check("同期のずれを検出する", not ok)
    sandbox_reset(c)

    junk = c.sandbox / "junk_not_allowed.txt"
    junk.write_text("x", encoding="utf-8")
    check("ホワイトリストが許可外を弾く", len(gate_whitelist(c)) > 0)
    junk.unlink()

    core_rel = c.unit.get("core_impl") or (c.unit.get("impl_files") or c.unit["whitelist"])[0]
    core = c.sb(core_rel)
    # 新規単位では実装ファイルがまだ無い。門は「ファイルの中身」を見るので、
    # 検査のあいだだけ実体を作る。終わったら消す（作りっぱなしにすると
    # 次の gate_whitelist が許可外として拾う）。
    core_existed = core.exists()
    orig = core.read_text(encoding="utf-8") if core_existed else ""

    # 実装ファイルが複数ある単位では、1 本だけ作っても別の「存在しません」で
    # 門が落ち、何を検査しているのか分からなくなる（実測）。全部そろえる。
    created = []
    for rel in (c.unit.get("impl_files") or c.unit["whitelist"]):
        p = c.sb(rel)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// selftest probe placeholder\n", encoding="utf-8")
            created.append(p)

    core.write_text("namespace X { public class Empty { } }", encoding="utf-8")
    ng = gate_static(c)
    check("シグネチャ破壊を弾く", ng is not None and "シグネチャ" in ng, str(ng))

    # 禁止パターンの発火確認。
    # 正規表現から「違反する文字列」を組み立てようとして外した（実測）。
    # 単位定義に、違反そのものを literal で書かせる。導出しない。
    probe = c.unit.get("selftest_forbidden_probe")
    if not probe:
        check("禁止パターンを弾く", False,
              "単位定義に selftest_forbidden_probe がありません（何が違反かを宣言してください）")
    else:
        core.write_text(orig + f"\n// probe: {probe}\n", encoding="utf-8")
        ng = gate_static(c)
        check("禁止パターンを弾く", ng is not None, str(ng))

    core.write_text(orig + "\n" + "// filler\n" * (c.unit["max_impl_lines"] + 50),
                    encoding="utf-8")
    ng = gate_diff_lines(c, verbose=False)
    check("差分行数を弾く", ng is not None, str(ng))

    for p in created:
        p.unlink(missing_ok=True)      # 検査のために作った実体を残さない
    if not core_existed:
        core.unlink(missing_ok=True)
    core.write_text(orig, encoding="utf-8")
    sandbox_reset(c)

    if c.test_driven:
        # この単位には非開示ゴールデンが無い。[C][D] は非開示の投入と残留の検査
        # なので、成立しない。代わりに「新規テストが Unity 側でも実行されること」
        # を確かめる。ここを飛ばすと、Unity 側で 1 件も走らなくても気づけない。
        # 新規テストは tests/Core.Tests（dotnet 側）にある。Unity はそこを
        # コンパイルしないので、Unity 結果に現れないのが正常（実測で確認）。
        # Unity 側の役目はこの単位では非回帰だけ。実装前に既存が壊れていない
        # ことを確かめる。ここを飛ばすと、新規ファイルが Unity を巻き込んで
        # 壊していても気づけない。
        print("[C] 非開示は無し。Unity 側の非回帰だけを見る")
        stage_golden(c, with_holdout=False)
        unity, err = run_unity_tests(c, "self_td")
        if err:
            check("Unity が実行できる", False, err)
        else:
            failed = [n for n, o in unity.items() if o == "Failed"]
            skipped = [n for n, o in unity.items() if o in ("Skipped", "Inconclusive")]
            check("実装前でも Unity は緑", len(failed) == 0,
                  f"{len(failed)} 件 Failed / {len(unity)} 件中")
            check("skip が既定どおり", len(skipped) <= c.cfg["unity_skip_baseline"],
                  f"{len(skipped)} / 上限 {c.cfg['unity_skip_baseline']}")
        sandbox_reset(c)
        ng_count = sum(1 for ok in log if not ok)
        print()
        if ng_count:
            print(f"自己検査 NG: {ng_count} 件。門が効いていないので本番を回しません。")
            return 2
        print("自己検査 すべて OK。門は赤を出せる状態です。")
        return 0

    print("[C] 非開示の投入")
    try:
        stage_golden(c, with_holdout=True)
        unity, err = run_unity_tests(c, "self_holdout")
        if err:
            check("非開示の実行", False, err)
        else:
            h_tag = c.g["holdout_tag"]
            check("非開示が実行されている", len(names_with(unity, h_tag + "_")) > 0,
                  f"{len(names_with(unity, h_tag + '_'))} 件")
            check("必ず落ちる対照群が発見されている",
                  outcome_of(unity, c.ctrl_fail) is not None,
                  str(outcome_of(unity, c.ctrl_fail)))
    finally:
        purge_holdout(c)

    print("[D] 非開示の残留が無いこと")
    stage_golden(c, with_holdout=False)
    unity, err = run_unity_tests(c, "self_after_purge")
    if err:
        check("消去後の実行", False, err)
    else:
        leaked = names_with(unity, c.g["holdout_tag"] + "_")
        check("非開示が残留していない", len(leaked) == 0, f"{len(leaked)} 件")
    sandbox_reset(c)

    ng_count = sum(1 for ok in log if not ok)
    print()
    if ng_count:
        print(f"自己検査 NG: {ng_count} 件。門が効いていないので本番を回しません。")
        return 2
    print("自己検査 すべて OK。門は赤を出せる状態です。")
    return 0


# ============================================================ エントリ

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="projects/<id>（harness のプロジェクト ID）")
    ap.add_argument("--unit", required=True, help="単位定義 JSON（制約 1）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()

    proj = project.load(args.project)
    unit_path = Path(args.unit)
    if not unit_path.is_absolute() and not unit_path.exists():
        unit_path = Path(proj["repo_dir"]) / args.unit
    c = Ctx(project.pipeline_config(proj), unit_path)
    require_unit_safe(c)
    require_repo_clean(c)

    if args.selftest:
        return selftest(c)

    if not args.skip_selftest:
        rc = selftest(c)
        if rc != 0:
            print("\n自己検査が通らないため本番を実行しません。")
            return rc
        print()

    history = []
    feedback = ""
    max_retry = c.cfg["gates"]["max_retry"]
    for i in range(max_retry + 1):
        print(f"=== 試行 {i + 1}/{max_retry + 1} ===")
        c.metrics["attempt"] = i + 1
        verdict, msg = attempt(c, feedback)
        history.append((verdict, msg))

        if verdict == "SUCCESS":
            print("\nMS3 完走。人間の出番はありません。")
            return 0
        if verdict == "ABORT":
            print(f"\nABORT（検査系の故障）: {msg}")
            return 2

        print(f"REJECT: {msg}")
        feedback = "\n".join(l for l in str(msg).splitlines()
                             if "holdout" not in l.lower() and c.ctrl_fail not in l)[:2000]

    print("\n" + "=" * 56)
    print(f"不合格。{max_retry + 1} 回とも通りませんでした。")
    print("=" * 56)
    for i, (v, m) in enumerate(history, 1):
        print(f"  試行{i}: {v}  {m}")
    sandbox_reset(c)
    return 1


if __name__ == "__main__":
    # sys.exit("ABORT: ...") と未捕捉例外を rc=2 にそろえる（制約 3）。
    # そのままだと rc=1 になり、スケジューラが REJECT と取り違える。
    import exitcode
    sys.exit(exitcode.normalized(main))
