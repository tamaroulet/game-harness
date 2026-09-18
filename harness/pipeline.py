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

エンジン・言語に固有の処理は adapters/ にある（engine と fast）。ここには置かない。
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import adapters
import contract
import fileops
import oracle
import project
import telemetry
from proc import resolve_cli, run


class Ctx:
    """1 単位分の文脈。グローバル状態を持たない（制約 5）。"""

    def __init__(self, cfg, unit_path):
        self.cfg = cfg
        self.unit = json.loads(Path(unit_path).read_text(encoding="utf-8"))

        p = self.cfg["paths"]
        self.repo = Path(p["repo"])
        try:
            self.engine = adapters.load("engine", self.cfg["adapters"]["engine"])
            self.fast = adapters.load("fast", self.cfg["adapters"]["fast"])
        except (KeyError, adapters.AdapterError) as e:
            sys.exit(f"ABORT: アダプタを選べません: {e}")
        self.out = Path(p["out_dir"]) / "pipeline"
        self.sandbox = Path(p.get("sandbox", r"C:\src\.local\wt\ms3-sandbox"))
        self.stage = self.out / "golden"

        self.ttl = self.cfg["ttl_seconds"]
        self.ttl.update(self.unit.get("ttl_seconds", {}))  # 単位側で上書き可（制約 7）

        try:
            self.oracle = oracle.load(self.cfg.get("control_groups"), self.cfg.get("oracle"))
        except oracle.OracleError as e:
            sys.exit(f"ABORT: オラクル設定が不正です: {e}")
        self.ctrl_fail = self.oracle["must_fail"]
        self.ctrl_pass = self.oracle["must_pass"]
        # 実装前（base）の測定結果。establish_base が 1 回だけ埋める。無ければ判定しない。
        self.base = None
        # 契約（.harness.toml）の [static]。main の apply_contract が 1 回だけ埋める。
        # 無いまま静的な門に来たら止まる（contract.effective_forbidden が ContractError）。
        self.contract = None

        # テレメトリ（判定には使わない）。main が --telemetry のときに差し替える。
        # cur は実行中の試行の記録、gate は試行が今いる門（self.stage はゴールデンの置き場なので
        # 別の名前にしている）、line_sets は試行ごとの追加行（retry_entropy 用。書き出さない）。
        self.tel = {"attempts": []}
        self.tel_path = None
        self.cur = None
        self.gate = None
        self.line_sets = []
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


# ============================================================ サンドボックス

def sandbox_reset(c):
    """冪等（制約 6）。中断が残っていても自力で復帰する。

    git clean -fd は ignored を消さないので、エンジンのキャッシュ（ignored）は残る。
    消すとエンジンのフルインポート（実測 11.9 分）が毎回走る。
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
    # reset / clean の戻り値だけでは足りない。ほかのプロセス（TTL で止めた子の孫、
    # エンジンやビルドの残り）がファイルを掴んでいると消し残りが出て、次の試行の
    # gate_whitelist に「許可外の変更」として現れ、原因が隠れる。
    # 結果（作業ツリーが空か）で確かめ、掴まれている間は待ってやり直す。
    leftover = []
    for delay in (0,) + fileops.DELAYS:
        if delay:
            time.sleep(delay)
        run(["git", "reset", "--hard", target], c.sandbox, c.ttl["git"], "reset to repo head")
        run(["git", "clean", "-fd"], c.sandbox, c.ttl["git"], "clean")
        _, status, _ = run(["git", "status", "--porcelain"], c.sandbox, c.ttl["git"], "status after reset")
        leftover = [l for l in status.splitlines() if l.strip()]
        if not leftover:
            break
    if leftover:
        sys.exit("ABORT: サンドボックスをリセットできません（ファイルが掴まれている可能性）: "
                 + ", ".join(l[3:] for l in leftover[:5]))
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
        f"- **{c.engine.LABEL}**: 総数 {g('engine_total')} / skip {g('engine_skipped')} / "
        f"開示 {g('engine_disclosed')} / 非開示 {g('engine_holdout')} / "
        f"control_must_fail={g('ctrl_fail')}\n"
        f"- **二相判定**: F2P {g('f2p_tests', '-')} 件 / P2P 維持 {g('p2p_kept')}/{g('p2p_base')} / "
        f"隔離 {g('quarantined')}\n"
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
    # 既定の 3 秒間隔だと API を多く使う（レート制限）。scheduler の CI 待ちと同じ間隔にそろえる。
    interval = str(c.cfg.get("ci_watch_interval_seconds", 15))
    rc, _, err = run(["gh", "run", "watch", run_id, "--exit-status", "--interval", interval],
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


def require_unit_safe(c):
    """単位定義そのものを検査する。

    新規機能では、分解役（LLM）が書いたテストが唯一のオラクルになる。
    そのテストがホワイトリストに入っていれば、実装役はテストを書き換えて
    通せる（報酬ハッキング）。単位定義も LLM が生成するので、
    **監査に任せず機械で弾く。** これは推奨ではなく前提条件。
    """
    bad = [p for p in c.unit["whitelist"] if c.fast.TEST_PATH_RE.search(p)]
    if bad:
        sys.exit("ABORT: ホワイトリストにテストまたはゴールデンが含まれています。"
                 "実装役がオラクルを書き換えられるため実行しません: " + ", ".join(bad))

    if not c.unit["whitelist"]:
        sys.exit("ABORT: ホワイトリストが空です")

    if c.test_driven and not c.unit.get("acceptance", {}).get("required_tests"):
        sys.exit("ABORT: テスト駆動の単位に acceptance.required_tests がありません。"
                 "正解が定義されていないため、合否を判定できません")


def apply_contract(c):
    """契約をゲームのリポジトリの main の先頭から 1 回だけ読む。読めなければ ABORT。"""
    base = (c.cfg.get("project") or {}).get("base_branch")
    if not base:
        sys.exit("ABORT: project.json に base_branch がありません（契約の読み込みに必要）")
    try:
        c.contract = contract.load(c.repo, base, c.ttl["git"])
    except contract.ContractError as e:
        sys.exit(f"ABORT: 契約を読めません: {e}")
    c.tel["contract_sha"] = c.contract["sha"]
    c.tel["contract_forbidden_count"] = len(c.contract["forbidden"])
    print(f"契約: {base} @ {c.contract['sha'][:8]}（禁止パターン {len(c.contract['forbidden'])} 件）")


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
    name = Path(c.g["holdout_rel"]).name
    copies = [p for g in c.fast.build_output_globs(name) for p in c.sandbox.glob(g)]
    for p in [c.sb(c.g["holdout_rel"])] + copies + list(c.stage.glob(name)):
        if p.exists():
            fileops.unlink(p)
            removed.append(str(p))
    return removed


# ============================================================ 実装AI

def implementer_log_dir(c):
    """実装役の生ログの置き場。テレメトリと同じ所に置く（1 回の実行の記録をばらさない）。"""
    return c.tel_path.parent if c.tel_path is not None else c.out


def write_implementer_log(c, n, prompt, rc, out, err):
    """実装役に渡した指示と返ってきた生出力を、試行ごとにそのまま残す。

    **rc == 0 でも捨てない。** 実装役が「なぜ書かなかったか」を書くのは応答本文だけで、
    終了コードにも差分にも出ない。2026-09-18 の Issue #12 は 3 試行とも rc=0・差分ゼロで
    落ちたが、応答を捨てていたため実行記録からは理由を追えなかった（実装役の常駐規約が
    ファイル編集を禁じていて、実装をチャット本文に貼るだけで終わっていた）。

    観測のための機能であって判定には使わない。書けなくても実行は止めない。
    """
    imp = c.cfg["implementer"]
    body = "\n".join([
        f"# 実装役 試行 {n}",
        f"- cli: {imp['cli']}",
        f"- model: {imp['model_name']}",
        f"- rc: {rc}",
        f"- cwd: {c.sandbox}",
        "",
        "=== プロンプト ===",
        prompt,
        "",
        "=== stdout ===",
        out,
        "",
        "=== stderr ===",
        err,
        "",
    ])
    path = implementer_log_dir(c) / f"implementer_attempt_{n}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as e:
        print(f"    実装役のログを書けませんでした（判定には影響しません）: {e}")
        return None
    print(f"    実装役の応答: {path}")
    return path


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
    # 利用量を取るための出力形式。無ければ利用量は不明（null）として記録するだけで、判定は変えない。
    args += imp.get("output_format_args", [])
    t0 = time.monotonic()
    rc, out, err = run(args, c.sandbox, c.ttl["implementer"], "実装AI")
    write_implementer_log(c, c.metrics.get("attempt", 0), prompt, rc, out, err)
    if c.cur is not None:
        usage = (telemetry.cli_usage(out, imp["usage_format"])
                 if imp.get("usage_format") and imp.get("output_format_args")
                 else telemetry.usage_unknown("implementer に output_format_args / usage_format が無い"))
        c.cur["implementer"] = {"rc": rc, "seconds": round(time.monotonic() - t0, 1), "usage": usage}
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


def changed_entries(cwd, ttl):
    """作業ツリーの変更 [(状態 2 文字, パス)]。改名・複製は新旧の両方のパスを並べる。

    -z: 空白や日本語のパスを引用符やエスケープで崩さない（core.quotePath の影響も受けない）。
    --untracked-files=all: 新しいフォルダを `dir/` にまとめず、ファイル単位で出す。
    まとめられると、新しいフォルダに作った正当なファイルが「許可外」に見え、
    逆に許可外のファイルがフォルダ名に隠れる。
    """
    _, out, _ = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd, ttl, "status")
    parts, entries, i = out.split("\0"), [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        entries.append((code, path.replace("\\", "/")))
        if code[0] in "RC" and i < len(parts):   # -z では改名元・複製元が次の要素に続く
            entries.append((code, parts[i].replace("\\", "/")))
            i += 1
    return entries


def gate_whitelist(c):
    """サンドボックスの変更のうち、許されていないパス（contract.md §5.3）。

    - 通常のファイル: 単位定義の whitelist に完全一致するものだけ
    - エンジンの付随ファイル（どれが付随ファイルかは engine アダプタが決める）: 本体が whitelist にあり、
      かつ新規（未追跡か追加）のものだけ。既存の付随ファイルの変更・削除は許さない（エンジンの参照が
      静かに切れうる。サンドボックスのエンジンのテストだけを通す改ざんにもなる）。無関係なファイルの
      付随ファイルも許さない
    """
    allowed = set(c.unit["whitelist"])
    bad = []
    for code, path in changed_entries(c.sandbox, c.ttl["git"]):
        if c.engine.is_companion(path):
            owner = any(path in c.engine.companions(a) for a in allowed)
            is_new = code == "??" or code[0] == "A"
            if not (owner and is_new):
                bad.append(path)
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

        # 禁止パターン: [[正規表現, 説明], ...]
        # 新規機能ではエンジン API・グローバル時間・非決定な乱数を禁じ、
        # 仮想時間を外から注入する形（決定論的 FSM）を強制する。
        # 何を禁じるかは、契約（ゲームのリポジトリの main）と単位定義の和。単位定義は足せるが、
        # 契約のパターンは消せない（分解役が forbidden_patterns を出力して既定を差し替える穴を塞ぐ）。
        for pattern, label in contract.effective_forbidden(c.contract, c.unit):
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

    # --- core 側（エンジン非依存の純粋な実装）を先に見る。
    # コンパイル可否に直結するもの（core で使えない API の残存）を、配線の問題（委譲）より
    # 先に報告する。逆順にすると、委譲が未配線の間はこの門に到達できず、
    # 門が効いているかを確かめられない（自己検査で実測した）。
    missing = [s for s in c.unit["required_symbols"] if s not in core_t]
    if missing:
        return "シグネチャが壊れています: " + ", ".join(missing)

    if re.search(c.unit["forbidden_in_core_regex"], core_t):
        return (f"core 側に使えない API が残っています"
                f"（{c.unit['forbidden_in_core_regex']}。core のアセンブリでは通りません）")

    if c.unit["forbidden_leftover"] in core_t:
        return f"{c.unit['forbidden_leftover']} が残っています（{c.unit['core_impl']}）"

    # --- ラッパー側（エンジンの型から core へ委譲する側）
    so_missing = [s for s in c.unit["so_required_symbols"] if s not in so_t]
    if so_missing:
        return "ラッパー側の結合（シリアライズされる項目など）が壊れています: " + ", ".join(so_missing)

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


# ============================================================ テストの実行（アダプタ）

def run_fast_tests(c, tag):
    """高速検査（純粋なコードのテスト）。実体は fast アダプタ。"""
    return c.fast.run_tests(c, tag)


def run_engine_tests(c, tag):
    """エンジンの受入テスト。実体は engine アダプタ。"""
    return c.engine.run_tests(c, tag)


# ============================================================ 判定

def names_with(results, needle):
    return oracle.hits(results, needle)


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

    この単位のゴールデンだけを置くと、同じエンジンのテストスイートに同居する他の
    ゴールデンランナー（MS2 のゴールデン等価テストなど）が
    「ファイルが無い」で落ち、実装が正しくても REJECT になる（実測で発生した）。
    ステージング先は 1 つで、そこを全ランナーが見る。

    **1 本も無いことを異常とするのは、リファクタリングの単位だけ。** テスト駆動の単位には
    ゴールデンがそもそも無い（正解は分解役が書いたテスト）。ここで一律に止めると、
    Pure C# のプロジェクトは 1 単位も通せない（falling-blocks Issue #12、2026-09-18）。

    **それでも掃除は必ず行う。** 「テスト駆動なら何もせず戻る」と書くと、前のランの
    ゴールデンが staging に残り、全ランナーがそれを拾う。ここを飛ばしてよい理由は無い。
    """
    c.stage.mkdir(parents=True, exist_ok=True)
    for p in c.stage.glob("golden_*.json"):
        fileops.unlink(p)

    src_dir = c.sb("tests/golden")
    staged = []
    for p in sorted(src_dir.glob("golden_*.json")):
        shutil.copyfile(p, c.stage / p.name)
        staged.append(p.name)
    if not staged and not c.test_driven:
        sys.exit(f"ABORT: 開示ゴールデンが 1 本もありません: {src_dir}")

    if with_holdout:
        shutil.copyfile(c.g["holdout_src"], c.stage / Path(c.g["holdout_rel"]).name)
        shutil.copyfile(c.g["holdout_src"], c.sb(c.g["holdout_rel"]))
    return staged


def new_test_files(c):
    """受入テストのファイル（サンドボックス内）。

    1 つのファイルに複数の受入テストが書かれうるので、パスの集合にしてから返す。
    受入テストごとに消すと、2 件目で「もう無い」に当たって誤って ABORT する。
    """
    found = [p.resolve()
             for t in c.unit["acceptance"]["required_tests"]
             for p in c.sandbox.rglob(c.fast.test_file_glob(t))]
    return sorted(set(found))


def establish_base(c):
    """実装前（base）を 1 回だけ測る。戻り値 (None | "REJECT" | "ABORT", 理由)。

    base はリポジトリの HEAD。テスト駆動の単位では、分解役の受入テストは入っていて
    実装はまだ無い。ここで決めるもの:
      - F2P の前半: 受入テストが base で通っていないこと。通っていれば偽テスト
        （実装役には直せないので、試行ループに入る前に REJECT する）
      - P2P の基準 P_base: base で Passed だった全テストの名前
      - base が健全であること: 制御群が正しく、想定外の失敗が無いこと（無ければ ABORT）

    エンジンの受入は 1 回に数分かかるので、試行ごとではなく 1 回だけ測る
    （base はコミットで決まり、試行のあいだ変わらない）。
    """
    E, F, o = c.engine, c.fast, c.oracle
    required = c.unit["acceptance"]["required_tests"] if c.test_driven else []
    print("[0] base（実装前）の測定")
    sandbox_reset(c)
    try:
        fast, fast_err = run_fast_tests(c, "base_fast")
        fast_p2p = fast
        if fast_err:
            if not c.test_driven:
                return "ABORT", f"base の高速検査が実行できません: {fast_err}"
            # 実装がまだ無いので、受入テストはビルドできない（Passed ではありえない）。
            # ただし環境の故障によるビルド失敗と区別できないので、受入テストを除いた
            # base がビルドできることを確かめ、その結果を P2P の基準にする。
            files = new_test_files(c)
            if not files:
                return "ABORT", ("base がビルドできず、除くべき受入テストのファイルも"
                                 f"見つかりません: {fast_err}")
            for p in files:
                fileops.unlink(p)
            print(f"    base は未ビルド。受入テスト {len(files)} ファイルを除いて測り直します")
            fast_p2p, isolated_err = run_fast_tests(c, "base_fast_isolated")
            if isolated_err:
                return "ABORT", ("受入テストを除いても base がビルドできません"
                                 f"（受入テスト以外の故障）: {isolated_err}")
            left = [n for t in required for n in oracle.hits(fast_p2p, t)]
            if left:
                return "ABORT", "受入テストを除いたのに実行されています: " + ", ".join(left[:3])
            sandbox_reset(c)   # 除いた受入テストを戻す

        stage_golden(c, with_holdout=False)
        engine, err = run_engine_tests(c, "base_engine")
        if err:
            return "ABORT", f"base の {E.LABEL} が実行できません: {err}"

        q = o["quarantine"]
        aborts = (oracle.controls(fast_p2p, F, o["must_pass"], o["must_fail"], False)
                  + oracle.controls(engine, E, o["must_pass"], o["must_fail"], False))
        broken = (oracle.unexpected_failures(fast_p2p, F, o["must_fail"], q, required)
                  + oracle.unexpected_failures(engine, E, o["must_fail"], q, required))
        if broken:
            aborts.append(f"base（実装前）で既に失敗しているテスト {len(broken)} 件: "
                          + ", ".join(broken[:3]))
        fast_base = None if fast_err else fast
        fake, f2p_aborts = oracle.f2p_base(required, [(F, fast_base), (E, engine)])
        aborts += f2p_aborts
        if aborts:
            return "ABORT", "検査系故障（base）: " + "; ".join(aborts)
        if fake:
            return "REJECT", (f"偽テスト: 実装前から通っている受入テスト {len(fake)} 件: "
                              + ", ".join(fake[:5]))

        c.base = SimpleNamespace(fast=fast_base, fast_p2p=fast_p2p, engine=engine)
        c.metrics["p2p_base"] = (oracle.p2p_count(fast_p2p, F, q)
                                 + oracle.p2p_count(engine, E, q))
        c.metrics["quarantined"] = len(q)
        state = "未ビルド" if fast_base is None else "Failed"
        print(f"    P_base {c.metrics['p2p_base']} 件 / 受入テストの base: {state}")
        return None, ""
    finally:
        sandbox_reset(c)


def check_p2p(c, fast, engine):
    """P2P。base で Passed だった全件が、今も Passed か（消えたものも破壊）。"""
    q = c.oracle["quarantine"]
    broken = (oracle.p2p(c.base.fast_p2p, fast, c.fast, q)
              + oracle.p2p(c.base.engine, engine, c.engine, q))
    c.metrics["p2p_kept"] = c.metrics.get("p2p_base", 0) - len(broken)
    if isinstance(getattr(c, "cur", None), dict):
        # 同じ試行で開示・非開示の 2 回判定することがある。多いほうを残す。
        c.cur["p2p_broken"] = max(c.cur.get("p2p_broken") or 0, len(broken))
        c.cur["p2p_base"] = c.metrics.get("p2p_base")
    if broken:
        return f"先祖返り（P2P 破壊）{len(broken)} 件: " + ", ".join(broken[:3])
    return None


def check_acceptance_test_driven(c, fast, engine):
    """新規機能の判定。正解は分解役が先に書いたテスト。

    ゴールデンが無いので「既存挙動と一致するか」は問えない。代わりに、テスト名ごとに
      1. F2P: 受入テストの各件が、base で通っておらず（establish_base）、今は Passed
      2. P2P: base で Passed だった全件が、今も Passed（quarantine だけ除外できる）
      3. 制御群: 必ず通るものが通り、必ず落ちるものが（あれば）落ちている
    を要求する。件数の合算では入れ替わり（1 件直して 1 件壊す）を見逃す。
    """
    if c.base is None:
        return ["検査系故障: base（実装前）が測定されていない"], True
    E, F, o = c.engine, c.fast, c.oracle
    q = o["quarantine"]
    required = c.unit["acceptance"]["required_tests"]

    aborts = (oracle.controls(fast, F, o["must_pass"], o["must_fail"], False)
              + oracle.controls(engine, E, o["must_pass"], o["must_fail"], False))
    if aborts:
        return ["検査系故障: " + a for a in aborts], True

    not_passed, aborts = oracle.f2p_impl(required, [(F, c.base.fast, fast),
                                                    (E, c.base.engine, engine)])
    if aborts:
        return ["検査系故障: " + a for a in aborts], True

    ng = []
    if not_passed:
        ng.append(f"受入テストが通っていない {len(not_passed)} 件: " + ", ".join(not_passed[:3]))

    skipped = [n for n, x in engine.items() if x in E.SKIPPED]
    if len(skipped) > E.skip_baseline(c):
        ng.append(f"skip が既知の {E.skip_baseline(c)} 件を超えた（{len(skipped)}）")

    msg = check_p2p(c, fast, engine)
    if msg:
        ng.append(msg)

    f_fail = oracle.unexpected_failures(fast, F, o["must_fail"], q)
    e_fail = oracle.unexpected_failures(engine, E, o["must_fail"], q)
    if f_fail:
        ng.append(f"高速検査の失敗 {len(f_fail)} 件: " + ", ".join(f_fail[:3]))
    if e_fail:
        ng.append(f"{E.LABEL} の失敗 {len(e_fail)} 件: " + ", ".join(e_fail[:3]))

    c.metrics["engine_total"] = len(engine)
    c.metrics["engine_skipped"] = len(skipped)
    c.metrics["f2p_tests"] = sum(len(names_with(fast, n) + names_with(engine, n)) for n in required)
    return ng, False


def check_acceptance(c, fast, engine, expect_holdout):
    """終了コードではなく個別結果で判定する。"""
    if c.test_driven:
        return check_acceptance_test_driven(c, fast, engine)
    if c.base is None:
        return ["検査系故障: base（実装前）が測定されていない"], True
    E, F, o = c.engine, c.fast, c.oracle
    q = o["quarantine"]
    ng = []
    d_tag, h_tag = c.g["disclosed_tag"], c.g["holdout_tag"]
    want_d = golden_count(c, c.g["disclosed_rel"])

    # --- 制御群。必ず落ちる対照群は非開示に入っているので、非開示を投入したときだけ必須
    aborts = (oracle.controls(fast, F, o["must_pass"], o["must_fail"], False)
              + oracle.controls(engine, E, o["must_pass"], o["must_fail"], expect_holdout))
    if aborts:
        return ["検査系故障: " + a for a in aborts], True

    # --- 高速検査側（純粋クラスを直接）
    hit = len(names_with(fast, d_tag + "_"))
    if hit < want_d:
        ng.append(f"高速検査で {d_tag} が {hit}/{want_d} 件しか実行されていない")
    f_fail = oracle.unexpected_failures(fast, F, o["must_fail"], q)
    if f_fail:
        ng.append(f"高速検査の不一致 {len(f_fail)} 件: " + ", ".join(f_fail[:3]))

    # --- エンジン側（ラッパー経由）
    hit_e = len(names_with(engine, d_tag + "_"))
    if hit_e < want_d:
        return [f"検査系故障: {E.LABEL} で {d_tag} が {hit_e}/{want_d} 件しか実行されていない"], True

    skipped = [n for n, x in engine.items() if x in E.SKIPPED]
    # skip されてはいけないスイート。単位固有の名前はコアに書かず、設定から受け取る。
    for req in [d_tag] + list(c.cfg.get("golden_required_engine_suites", [])):
        if [n for n in skipped if req in n]:
            return [f"検査系故障: {req} が skip されている"], True
    if len(skipped) > E.skip_baseline(c):
        ng.append(f"skip が既知の {E.skip_baseline(c)} 件を超えた（{len(skipped)}）")

    leaked = names_with(engine, h_tag + "_")
    if not expect_holdout and leaked:
        return [f"検査系故障: 非開示が漏れ込んでいる（{len(leaked)} 件）"], True
    if expect_holdout and not leaked:
        return ["検査系故障: 非開示が実行されていない"], True

    e_fail = oracle.unexpected_failures(engine, E, o["must_fail"], q)
    if e_fail:
        ng.append(f"{E.LABEL} の不一致 {len(e_fail)} 件: " + ", ".join(e_fail[:3]))

    msg = check_p2p(c, fast, engine)
    if msg:
        ng.append(msg)

    # TIMELINE に載せる実測値。判定に使った数をそのまま残す。
    c.metrics["engine_total"] = len(engine)
    c.metrics["engine_skipped"] = len(skipped)
    c.metrics["engine_disclosed"] = hit_e
    if expect_holdout:
        c.metrics["engine_holdout"] = len(leaked)
        c.metrics["ctrl_fail"] = outcome_of(engine, c.ctrl_fail)

    return ng, False


# ============================================================ 1 周

def capture_diff(c):
    """実装役の差分を記録する（テレメトリ。判定には使わない）。

    記録するのはハッシュと追加行の件数だけ。追加行の集合はメモリに持ち、
    retry_entropy の計算にだけ使う（実装の中身をログへ書き出さない）。
    """
    if c.cur is None:
        return
    wl = c.unit["whitelist"]
    run(["git", "add", "-N", "--"] + wl, c.sandbox, c.ttl["git"], "intent-to-add (telemetry)")
    rc, out, err = run(["git", "diff", "HEAD", "--"] + wl, c.sandbox, c.ttl["git"], "diff (telemetry)")
    if rc != 0:
        telemetry.put(c.cur, "diff_sha256", None, f"git diff が失敗: {(err or out)[:120]}")
        telemetry.put(c.cur, "added_lines", None, "git diff が失敗")
        return
    lines = telemetry.added_lines(out)
    c.line_sets.append(lines)
    c.cur["diff_sha256"] = telemetry.sha256_text(out)
    c.cur["added_lines"] = len(lines)


def attempt(c, feedback):
    sandbox_reset(c)

    print("[1] 実装AI")
    c.gate = "implementer"
    ok, msg = call_implementer(c, feedback)
    if not ok:
        return "RETRY", msg

    c.gate = "escape"
    escaped = gate_repo_untouched(c)
    if escaped:
        return "ABORT", ("実装AIがサンドボックス外（本体リポジトリ）を書き換えました: "
                         + ", ".join(escaped[:5]))
    capture_diff(c)

    print("[2] 静的機械判定")
    c.gate = "whitelist"
    bad = gate_whitelist(c)
    if bad:
        return "RETRY", "許可外のファイル変更: " + ", ".join(bad[:5])
    c.gate = "static"
    ng = gate_static(c)
    if ng:
        return "RETRY", ng
    c.gate = "diff"
    ng = gate_diff_lines(c)
    if ng:
        return "RETRY", ng

    print(f"[3] 高速検査（{c.fast.LABEL}）")
    c.gate = "fast"
    fast, err = run_fast_tests(c, "impl_fast")
    if err:
        return "ABORT", err

    print(f"[4] {c.engine.LABEL} 受入（開示）")
    c.gate = "acceptance"
    stage_golden(c, with_holdout=False)
    engine, err = run_engine_tests(c, "impl_disclosed")
    if err:
        return "ABORT", err
    ng, fatal = check_acceptance(c, fast, engine, expect_holdout=False)
    if fatal:
        return "ABORT", "; ".join(ng)
    if ng:
        return "RETRY", "; ".join(ng)

    if c.test_driven:
        print("[5] 非開示ゴールデンは無し（テスト駆動の単位）")
    else:
        c.gate = "holdout"
        failed = attempt_holdout(c, fast)
        if failed:
            return failed

    print("[6] 持ち出し")
    c.gate = "carry"
    return carry_out_and_ci(c)


def attempt_holdout(c, fast):
    """非開示ゴールデンの検査。合格なら None、不合格なら (verdict, msg)。"""
    print(f"[5] {c.engine.LABEL} 受入（非開示）")
    try:
        stage_golden(c, with_holdout=True)
        engine2, err = run_engine_tests(c, "impl_holdout")
        if err:
            return "ABORT", err
        ng, fatal = check_acceptance(c, fast, engine2, expect_holdout=True)
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
    # エンジンの付随ファイルを同伴させる。gate_whitelist は付随ファイルを素通しに
    # してあるのに持ち出し側が運んでいなかった。その非対称のせいで、本体で次に
    # エンジンを起動した瞬間に付随ファイルが生えて作業ツリーが汚れ、
    # require_repo_clean が ABORT する（実測）。運ぶかどうかの規則は engine アダプタが持つ。
    carried = []
    for rel in c.unit["whitelist"]:
        src = c.sb(rel)
        if src.exists():
            dst = c.repo / rel.replace("/", "\\")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            carried.append(rel)

        for comp in c.engine.companions(rel):
            comp_src = c.sb(comp)
            comp_dst = c.repo / comp.replace("/", "\\")
            action, why = c.engine.carry_companion(comp_src, comp_dst)
            if action == "abort":
                return "ABORT", why
            if action == "copy":
                shutil.copyfile(comp_src, comp_dst)
                carried.append(comp)
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
    c.gate = "ci"
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
            found = list(c.sandbox.rglob(c.fast.test_file_glob(t)))
            if not found:
                missing_tests.append(t)
        check("受入テストがサンドボックスに置かれている",
              not missing_tests,
              "見つからない: " + ", ".join(missing_tests) if missing_tests else "")

        fast, err = run_fast_tests(c, "self_fast")
        if err:
            check("実装が無いので赤", True, "ビルドが失敗（実装が無いので当然）")
        else:
            red = len(real_failures(fast, c.ctrl_fail, c.fast.FAILED))
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
            p.write_text(c.fast.STUB_SOURCE, encoding="utf-8")

        fast, err = run_fast_tests(c, "self_fast")
        if err:
            # スタブはコンパイルを壊すので、ビルド失敗も「赤が出た」に含める
            check("スタブで赤が出る", True, "ビルドが失敗（想定どおり）")
        else:
            red = len(real_failures(fast, c.ctrl_fail, c.fast.FAILED))
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
        check("復元後は緑に戻る", len(real_failures(fast, c.ctrl_fail, c.fast.FAILED)) == 0,
              f"{len(real_failures(fast, c.ctrl_fail, c.fast.FAILED))} 件 Failed")

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
    fileops.unlink(junk)
    for comp in c.engine.companions("junk_not_allowed.txt"):
        junk_comp = c.sb(comp)
        junk_comp.write_text("x", encoding="utf-8")
        check("ホワイトリストが無関係な付随ファイルを弾く", len(gate_whitelist(c)) > 0)
        fileops.unlink(junk_comp)

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
    # 契約の probe も発火を確かめる（単位定義の probe と別の違反を宣言していることがある）
    cprobe = (c.contract or {}).get("probe")
    if cprobe and cprobe != probe:
        core.write_text(orig + f"\n// probe: {cprobe}\n", encoding="utf-8")
        ng = gate_static(c)
        check("契約の禁止パターンを弾く", ng is not None, str(ng))

    core.write_text(orig + "\n" + "// filler\n" * (c.unit["max_impl_lines"] + 50),
                    encoding="utf-8")
    ng = gate_diff_lines(c, verbose=False)
    check("差分行数を弾く", ng is not None, str(ng))

    for p in created:
        fileops.unlink(p)      # 検査のために作った実体を残さない
    if not core_existed:
        fileops.unlink(core)
    core.write_text(orig, encoding="utf-8")
    sandbox_reset(c)

    if c.test_driven:
        # この単位には非開示ゴールデンが無い。[C][D] は非開示の投入と残留の検査
        # なので、成立しない。
        # 新規テストは高速検査側にある。エンジンはそこをコンパイルしないので、
        # エンジンの結果に現れないのが正常（実測で確認）。
        # エンジン側の役目はこの単位では非回帰だけ。実装前に既存が壊れていない
        # ことを確かめる。ここを飛ばすと、新規ファイルがエンジン側を巻き込んで
        # 壊していても気づけない。
        E = c.engine
        print(f"[C] 非開示は無し。{E.LABEL} 側の非回帰だけを見る")
        stage_golden(c, with_holdout=False)
        engine, err = run_engine_tests(c, "self_td")
        if err:
            check(f"{E.LABEL} が実行できる", False, err)
        else:
            failed = [n for n, o in engine.items() if o == E.FAILED]
            skipped = [n for n, o in engine.items() if o in E.SKIPPED]
            check(f"実装前でも {E.LABEL} は緑", len(failed) == 0,
                  f"{len(failed)} 件 Failed / {len(engine)} 件中")
            check("skip が既定どおり", len(skipped) <= E.skip_baseline(c),
                  f"{len(skipped)} / 上限 {E.skip_baseline(c)}")
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
        engine, err = run_engine_tests(c, "self_holdout")
        if err:
            check("非開示の実行", False, err)
        else:
            h_tag = c.g["holdout_tag"]
            check("非開示が実行されている", len(names_with(engine, h_tag + "_")) > 0,
                  f"{len(names_with(engine, h_tag + '_'))} 件")
            check("必ず落ちる対照群が発見されている",
                  outcome_of(engine, c.ctrl_fail) is not None,
                  str(outcome_of(engine, c.ctrl_fail)))
    finally:
        purge_holdout(c)

    print("[D] 非開示の残留が無いこと")
    stage_golden(c, with_holdout=False)
    engine, err = run_engine_tests(c, "self_after_purge")
    if err:
        check("消去後の実行", False, err)
    else:
        leaked = names_with(engine, c.g["holdout_tag"] + "_")
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

def save_telemetry(c):
    if c.tel_path is None:
        return
    telemetry.summarize_attempts(c.tel, c.line_sets)
    telemetry.write(c.tel_path, c.tel)


def record_attempt(c, n, verdict, msg, seconds, feedback):
    """1 試行を記録する。SUCCESS でも不合格でも ABORT でも残す（生存者バイアスを作らない）。"""
    a = c.cur or {}
    reason = "" if msg is None else str(msg)
    entry = {"n": n, "verdict": verdict, "stage": c.gate, "reason": reason[:500],
             "reason_sha256": telemetry.sha256_text(reason), "seconds": seconds,
             "feedback_chars": len(feedback)}
    if "implementer" in a:
        entry["implementer"] = a["implementer"]
    else:
        telemetry.put(entry, "implementer", None, "実装役を呼ぶ前に終了した")
    for key, why in (("diff_sha256", "差分を取る前に終了した"),
                     ("added_lines", "差分を取る前に終了した"),
                     ("p2p_broken", "受入判定まで到達していない"),
                     ("p2p_base", "受入判定まで到達していない")):
        if key in a:
            entry[key] = a[key]
            if a[key] is None and key + "_null_reason" in a:
                entry[key + "_null_reason"] = a[key + "_null_reason"]
        else:
            telemetry.put(entry, key, None, why)
    c.tel["attempts"].append(entry)
    c.cur = None
    save_telemetry(c)


def run_unit(c, args):
    if args.selftest:
        return selftest(c)

    if not args.skip_selftest:
        t0 = time.monotonic()
        rc = selftest(c)
        c.tel["selftest"] = {"rc": rc, "seconds": round(time.monotonic() - t0, 1)}
        save_telemetry(c)
        if rc != 0:
            print("\n自己検査が通らないため本番を実行しません。")
            return rc
        print()
    else:
        telemetry.put(c.tel, "selftest", None, "--skip-selftest で省略した")

    t0 = time.monotonic()
    verdict, msg = establish_base(c)
    c.tel["base"] = {"verdict": verdict or "OK", "seconds": round(time.monotonic() - t0, 1),
                     "reason": (msg or "")[:500]}
    if c.base is not None:
        c.tel["base"].update(fast_built=c.base.fast is not None, p2p_base=c.metrics.get("p2p_base"),
                             quarantined=c.metrics.get("quarantined"))
    save_telemetry(c)
    if verdict == "ABORT":
        print(f"\nABORT（検査系の故障）: {msg}")
        return 2
    if verdict == "REJECT":
        # 偽テストは実装役には直せない。試行を回さずに不合格にする。
        print(f"\nREJECT: {msg}")
        return 1

    history = []
    feedback = ""
    max_retry = c.cfg["gates"]["max_retry"]
    for i in range(max_retry + 1):
        print(f"=== 試行 {i + 1}/{max_retry + 1} ===")
        c.metrics["attempt"] = i + 1
        c.cur, c.gate = {}, None
        t0 = time.monotonic()
        verdict, msg = "ABORT", "例外か sys.exit で試行が中断した"
        try:
            verdict, msg = attempt(c, feedback)
        except SystemExit as e:
            msg = f"sys.exit で中断: {e.code}"
            raise
        finally:
            record_attempt(c, i + 1, verdict, msg, round(time.monotonic() - t0, 1), feedback)
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


def git_head(cwd, ttl):
    rc, out, _ = run(["git", "rev-parse", "HEAD"], cwd, ttl, "rev-parse (telemetry)")
    return out.strip() if rc == 0 and out.strip() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="projects/<id>（harness のプロジェクト ID）")
    ap.add_argument("--unit", required=True, help="単位定義 JSON（制約 1）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--telemetry", help="テレメトリの書き出し先（JSON）。判定には使わない")
    ap.add_argument("--repo-dir", help="Issue の worktree。持ち出し・コミット・push をここで行う"
                                       "（既定は project.json の repo_dir）")
    args = ap.parse_args()

    tel = {"schema": telemetry.SCHEMA, "tool": "pipeline",
           "started": datetime.now().isoformat(timespec="seconds"), "attempts": []}
    tel_path = Path(args.telemetry) if args.telemetry else None
    t_start = time.monotonic()
    rc, c = None, None
    try:
        proj = project.load(args.project)
        if args.repo_dir:
            proj["repo_dir"] = args.repo_dir
        unit_path = Path(args.unit)
        if not unit_path.is_absolute() and not unit_path.exists():
            unit_path = Path(proj["repo_dir"]) / args.unit
        c = Ctx(project.pipeline_config(proj), unit_path)
        c.tel, c.tel_path = tel, tel_path
        imp = c.cfg["implementer"]
        tel.update(unit_id=c.unit.get("id"),
                   unit_sha256=hashlib.sha256(unit_path.read_bytes()).hexdigest(),
                   implementer={"cli": imp["cli"], "model_name": imp["model_name"]})
        for key, cwd in (("harness_sha", project.ROOT), ("repo_head", c.repo)):
            telemetry.put(tel, key, git_head(cwd, c.ttl["git"]), f"git rev-parse が失敗: {cwd}")
        require_unit_safe(c)
        apply_contract(c)
        require_repo_clean(c)
        rc = run_unit(c, args)
        return rc
    finally:
        telemetry.put(tel, "exit_code", rc,
                      "sys.exit か例外で終了した（終了コードは呼び出し側の記録を見る）")
        tel["total_seconds"] = round(time.monotonic() - t_start, 1)
        if tel_path is not None:
            telemetry.summarize_attempts(tel, c.line_sets if c is not None else [])
            telemetry.write(tel_path, tel)


if __name__ == "__main__":
    # sys.exit("ABORT: ...") と未捕捉例外を rc=2 にそろえる（制約 3）。
    # そのままだと rc=1 になり、スケジューラが REJECT と取り違える。
    import exitcode
    sys.exit(exitcode.normalized(main))
