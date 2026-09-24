"""変異テスト。門の判定を 1 つずつわざと壊し、テストが赤になることを確かめる。

    python tests/mutate.py            # 全件
    python tests/mutate.py M1 M13     # 指定したものだけ

**なぜ要るか**: 一発で緑になったテストは、何も測っていない可能性がある
（自己検査の True 直書き・「汚れを残す ABORT」しか試しておらず rc=2 の取り違えが
隠れていた実例がある）。壊しても緑のままの変異は、テストの穴を指している。

元のファイルはバイト列で退避し、finally で書き戻す（git checkout は使わない）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "harness"
GATE = "templates/game-repo/.github/scripts/ms4_approval_gate.py"
WORKFLOW = "templates/game-repo/.github/workflows/approval.yml"

M = [
    ("M1 pipeline rc2 を REJECT 扱い", "scheduler.py",
     '        if rc == 1:\n            raise Reject("実装パイプライン',
     '        if rc in (1, 2):\n            raise Reject("実装パイプライン'),
    ("M2 不合格時の汚れ検査を外す", "scheduler.py",
     '        dirty = (self.igit or self.git).changed_paths()\n        if dirty:\n            raise Abort("不合格の後始末',
     '        dirty = (self.igit or self.git).changed_paths()\n        if False:\n            raise Abort("不合格の後始末'),
    ("M3 再試行前の読み直しを外す", "scheduler.py",
     "if done is not None and done():", "if False:"),
    ("M4 文字列 exit を 1 にする", "exitcode.py",
     "    print(code, file=sys.stderr)\n    return ABORT", "    print(code, file=sys.stderr)\n    return 1"),
# M5（ローカルの merge --abort）は削除した。A2 で PR 経由のマージにしてから呼ばれない
# 死にコードになり、壊しても何も起きず変異が生き残ったため（変異テストで発見）。
    ("M6 CI の conclusion を見ない", "scheduler.py",
     'if conclusion != "success":', 'if conclusion == "never":'),
    ("M7 想定外パス検査を外す", "scheduler.py",
     "        bad = [p for p in changed if not any(a(p) for a in allowed)]", "        bad = []"),
    ("M8 着手後 ABORT でもロックを消す", "scheduler.py",
     "            if self.touched:", "            if False:"),
    ("M9 既存ブランチ検査を外す", "scheduler.py",
     "if self.issue_branch_taken(branch):", "if False:"),
    ("M10 ABORT 時に main へ戻す（掃除する）", "scheduler.py",
     '        try:\n            self.gh.comment(n, f"ms4:{n}:abort:',
     '        self.git._git("switch", "-f", self.base, check=False)\n        try:\n            self.gh.comment(n, f"ms4:{n}:abort:'),
    ("M11 再試行回数を 0 に", "scheduler.py",
     "            if failures >= retries:", "            if failures >= 0:"),
    ("M12 TTL で木ごと殺さない（待ち続ける）", "scheduler.py",
     "                return proc.wait(timeout=min(tick, remaining))", "                return proc.wait()"),
    ("M13 decompose rc2 を不合格扱い", "scheduler.py",
     '        if rc == 1:\n            raise Reject("分解役',
     '        if rc in (1, 2):\n            raise Reject("分解役'),
    ("M14 共通設定の重複キー検査を外す", "scheduler.py",
     "    if dup:\n        raise project.ProjectError(", "    if False:\n        raise project.ProjectError("),
    ("M15 pipeline.json の repo 再掲検査を外す", "project.py",
     "        if k in paths:\n", "        if False:\n"),
    ("M16 taskkill に TTL を付けない", "scheduler.py",
     "creationflags=_NO_WINDOW, timeout=60)", "creationflags=_NO_WINDOW)"),

    # ---- 承認ゲート（A3）
    ("M17 synchronize でラベルを外さない", GATE,
     '            api("DELETE", f"/repos/{repo}/issues/{number}/labels/{label}", allow_404=True)\n', ""),
    ("M18 head SHA の照合を外す", GATE,
     '    if current["head"]["sha"] != event_sha:', "    if False:"),
    ("M19 承認者の照合を外す", GATE,
     "    if not who or who.lower() not in approvers:", "    if not who:"),
    ("M20 承認者が空でも通す", GATE,
     "    if not approvers:\n        return False", "    if False:\n        return False"),
    ("M21 最初にラベルを付けた人で判定する", GATE,
     "                actor = (ev.get(\"actor\") or {}).get(\"login\")",
     "                actor = actor or (ev.get(\"actor\") or {}).get(\"login\")"),

    ("M31 承認ワークフローが base.sha（PR 作成時点）を checkout する", WORKFLOW,
     "with:\n          ref: ${{ github.event.repository.default_branch }}",
     "with:\n          ref: ${{ github.event.pull_request.base.sha }}"),
    ("M32 承認ワークフローが PR の head を checkout する", WORKFLOW,
     "with:\n          ref: ${{ github.event.repository.default_branch }}",
     "with:\n          ref: ${{ github.event.pull_request.head.sha }}"),

    # ---- PR 経由のマージ（A2）
    ("M22 --match-head-commit を外す", "scheduler.py",
     '"--merge", "--match-head-commit", sha]', '"--merge"]'),
    ("M23 squash でマージする", "scheduler.py",
     '"--merge", "--match-head-commit", sha]', '"--squash", "--match-head-commit", sha]'),
    ("M24 必須チェックを見ない", "scheduler.py",
     '            not_ok = {k: v for k, v in states.items() if v != "success"}\n            if not_ok:\n                print(f"  PR #{num}: 承認ラベル',
     '            not_ok = {}\n            if not_ok:\n                print(f"  PR #{num}: 承認ラベル'),
    ("M25 同名 check-run の古いほうを採る", "scheduler.py",
     'r["id"] > latest[name]["id"]', 'r["id"] < latest[name]["id"]'),
    ("M26 空のテスト一覧でも承認依頼を出す", "scheduler.py",
     "        if total == 0:", "        if False:"),
    ("M27 承認後の push を ABORT 扱いにする", "scheduler.py",
     '                if now["state"] != "MERGED" and now["sha"] != pr["sha"]:\n                    print(f"  承認後に push されました（{pr[\'sha\'][:8]} → {now[\'sha\'][:8]}）。承認待ちに戻します")\n                    rec["result"] = "WAITING"',
     '                if False:\n                    print(f"  承認後に push されました（{pr[\'sha\'][:8]} → {now[\'sha\'][:8]}）。承認待ちに戻します")\n                    rec["result"] = "WAITING"'),
    ("M28 承認を待たずに PR を出した直後にマージする", "scheduler.py",
     "        if L[\"declined\"] not in names and L[\"approved\"] not in names:\n            return \"WAITING\"\n        if L[\"declined\"] not in names:",
     "        if False:\n            return \"WAITING\"\n        if L[\"declined\"] not in names:"),
    ("M29 承認依頼を今の SHA に紐付けない", "scheduler.py",
     'self.gh.comment(num, f"ms4:approval-request:{head}", summary, kind="pr")',
     'self.gh.comment(num, "ms4:approval-request", summary, kind="pr")'),
    ("M30 抽出器が複数行の Assert を途中で切る", "adapters/csharp_tests.py",
     '            stop = scan_until(body, am.start(), ";")', '            stop = body.find("\\n", am.start())'),

    # ---- エンジン別アダプタ（Step 2）
    ("M33 Unity の結果を全部 Passed と読む", "adapters/unity.py",
     'return {n.get("fullname"): n.get("result") for n in x.iter("test-case")}',
     'return {n.get("fullname"): "Passed" for n in x.iter("test-case")}'),
    ('M142 完全修飾名から単純名を取り出さない', "adapters/dotnet.py",
     '''    return f"*{test_name.split('.')[-1]}*.cs"''',
     '''    return f"*{test_name}*.cs"'''),
    ("M34 TRX でクラス名を付けない", "adapters/dotnet.py",
     "        results[fullname.get(n, n)] = r.get(\"outcome\")",
     "        results[n] = r.get(\"outcome\")"),
    ("M35 GUID が違っても止めない", "adapters/unity.py",
     "        if g_repo != g_sb:", "        if False:"),
    ("M36 既存の付随ファイルを上書きする", "adapters/unity.py",
     "    if dst.exists():\n        g_repo", "    if False:\n        g_repo"),
    ("M37 知らないアダプタ名を拒否しない", "adapters/__init__.py",
     "    if name not in KNOWN[kind]:", "    if False:"),
    ("M38 アダプタの必要関数を確かめない", "adapters/__init__.py",
     "    if missing:\n        raise AdapterError", "    if False:\n        raise AdapterError"),
    ("M39 pipeline がアダプタを選べなくても止まらない", "pipeline.py",
     '            sys.exit(f"ABORT: アダプタを選べません: {e}")', "            raise"),
    ("M40 コアにエンジンの語を戻す", "pipeline.py",
     'print(f"[4] {c.engine.LABEL} 受入（開示）")', 'print("[4] Unity 受入（開示）")'),
    ("M41 project.json の adapters の形を確かめない", "project.py",
     '    if not (isinstance(a, dict) and', '    if False and (isinstance(a, dict) and'),

    # ---- 二相判定（Step 3）
    ("M42 F2P で base の Passed（偽テスト）を見逃す", "oracle.py",
     "                if o == A.PASSED:\n                    fake.append(n)",
     "                if False:\n                    fake.append(n)"),
    ("M43 F2P で実装後の Passed を確かめない", "oracle.py",
     "                if o != A.PASSED:\n                    ng.append(n)",
     "                if False:\n                    ng.append(n)"),
    ("M44 F2P で base と名前を突き合わせない", "oracle.py",
     "                if base is not None and n not in base:", "                if False:"),
    ("M45 P2P の破壊を見落とす", "oracle.py",
     "    return sorted(n for n in kept if impl.get(n) != A.PASSED)", "    return []"),
    ("M46 P2P で消えたテストを見落とす", "oracle.py",
     "    return sorted(n for n in kept if impl.get(n) != A.PASSED)",
     "    return sorted(n for n in kept if impl.get(n) == A.FAILED)"),
    ("M47 quarantine の approved_in 検査を外す", "oracle.py",
     "        if not (isinstance(pr, int) and not isinstance(pr, bool) and pr > 0):", "        if False:"),
    ("M48 quarantine を部分一致にする", "oracle.py",
     '    return any(name == t or name.endswith("." + t) for t in quarantine)',
     "    return any(t in name for t in quarantine)"),
    ("M49 必ず通る対照群の不合格を見逃す", "oracle.py",
     "        bad = [n for n in ok_hits if results[n] != A.PASSED]", "        bad = []"),
    ("M50 必ず落ちる対照群が通っても止めない", "oracle.py",
     "    wrong = [n for n in ng_hits if results[n] != A.FAILED]", "    wrong = []"),
    ("M51 必ず落ちる対照群の不在を見逃す", "oracle.py",
     "    if require_fail and not ng_hits:", "    if False:"),
    ("M52 base 未ビルド時に分離ビルドの成功を確かめない", "pipeline.py",
     "            if isolated_err:", "            if False:"),
    ("M53 base の想定外の失敗を見逃す", "pipeline.py",
     '        if broken:\n            aborts.append(f"base（実装前）',
     '        if False:\n            aborts.append(f"base（実装前）'),
    ("M54 非公開シードで破れた性質を見逃す", "pipeline.py",
     '    if failed:\n        print(f"    非公開シードで破れた', '    if False:\n        print(f"    非公開シードで破れた'),
    ("M55 受入判定が P2P の破壊を無視する", "pipeline.py",
     '    if broken:\n        return f"先祖返り', '    if False:\n        return f"先祖返り'),
    ("M56 受入テストのファイルを重複排除しない", "pipeline.py",
     "    return sorted(set(found))", "    return sorted(found)"),

    # ---- テレメトリ（Step 4。レビューの方針により要所の 2 件だけ）
    ("M57 取れない利用量を 0 にする", "telemetry.py",
     "    return v if ok and not isinstance(v, bool) else None",
     "    return v if ok and not isinstance(v, bool) else 0"),
    ("M58 テレメトリの無いステップを空の辞書にする", "scheduler.py",
     '        telemetry.put(entry, "telemetry", data, why)',
     '        telemetry.put(entry, "telemetry", data or {}, why)'),

    # ---- 常駐（Step 5。要所の 3 件だけ）
    ("M59 死んだロックの判定で pid の生死を見ない", "scheduler.py",
     "        if pid_alive(pid):\n            return None", "        if False:\n            return None"),
    ("M60 レート制限を通常の失敗と同じ 5 秒の再試行にする", "scheduler.py",
     "        if on_limited is not None and is_rate_limited(detail):", "        if False:"),
    ("M61 サンドボックスのリセット後に空であることを確かめない", "pipeline.py",
     "    if leftover:\n        sys.exit(", "    if False:\n        sys.exit("),

    # ---- プレイ確認（Step 6。要所の 2 件だけ）
    ("M62 playtest: required でもビルドしない", "scheduler.py",
     '            if any(c["playtest"] for c in checked):\n                self.build_playtest(num, head, rec)',
     '            if False:\n                self.build_playtest(num, head, rec)'),
    ("M63 ビルド後の非破壊の確認を外す", "playtest.py",
     "    if changed:\n        raise Stop(2,", "    if False:\n        raise Stop(2,"),
    # ---- 契約の最小の読み込み（docs/design/spec_pipeline.md §11.2）
    ("M64 契約を作業ツリーから読む", "contract.py",
     '    rc, out, err = run(["git", "show", f"{sha}:{PATH}"], repo, ttl, "git show (contract)")',
     '    rc, out, err = 0, open(str(repo) + "/" + PATH, encoding="utf-8").read(), ""'),
    ("M65 契約の前に fetch しない", "contract.py",
     '    rc, out, err = run(["git", "fetch", "origin", base], repo, ttl, "git fetch (contract)")',
     '    rc, out, err = 0, "", ""'),
    ("M66 契約のパターンを和から外す", "contract.py",
     'list(contract["forbidden"]) + [tuple(x)', '[] + [tuple(x)'),
    ("M67 契約が無くても単位定義だけで続ける", "contract.py",
     '        raise ContractError("契約が読み込まれていません")',
     '        return [tuple(x) for x in unit.get("forbidden_patterns", [])]'),
    ("M68 probe の空振りを見ない", "contract.py",
     "        if not any(re.search(p, probe) for p, _ in forbidden):",
     "        if False:"),
    ("M69 未対応のキーを黙って無視する", "contract.py",
     "        if extra:", "        if False:"),
    ("M70 正規表現を検査しない", "contract.py",
     '            re.compile(item["pattern"])', '            pass'),
    ("M71 パイプラインが契約を読まない", "pipeline.py",
     "        require_unit_safe(c)\n        apply_contract(c)\n", "        require_unit_safe(c)\n"),
    # ---- 網羅検査器（docs/design/spec_pipeline.md §4〜§6）
    ('M72 網羅を見ない', "gdd_check.py",
     '    missing = sorted(targets - cited_all)',
     '    missing = []'),
    ('M73 フェンスの中身も分母から外す', "gdd_check.py",
     '            targets.add(n)   # フェンスの中身は外さない\n',
     ''),
    ('M74 分母から外した行だけの根拠を許す', "gdd_check.py",
     '        if not span & targets:',
     '        if False:'),
    ('M75 根拠の範囲を見ない', "gdd_check.py",
     '        if not (1 <= a <= b <= nlines):',
     '        if False:'),
    ('M76 実在しない ID の参照を許す', "gdd_check.py",
     '                if m.group(0) not in ids:',
     '                if False:'),
    ('M77 禁止語の例外を GDD 全体に広げる', "gdd_check.py",
     'if rx.search(text) and not rx.search(cited_text):',
     'if rx.search(text) and not rx.search(gdd_text):'),
    ('M78 英字の禁止語の単語境界を外す', "gdd_check.py",
     '        return re.compile(rf"(?<![A-Za-z0-9]){t}(?![A-Za-z0-9])", re.I)',
     '        return re.compile(t, re.I)'),
    ('M79 仮をマージ可否に入れない', "gdd_check.py",
     'not provisional and ',
     ''),
    ('M80 質問をマージ可否に入れない', "gdd_check.py",
     'not questions and ',
     ''),
    ('M81 錨の欠落をマージ可否に入れない', "gdd_check.py",
     ' and not anchors_missing,',
     ','),
    ('M82 HC の近似検査を外す', "gdd_check.py",
     '            m = hc_rx.search(r["内容"])',
     '            m = None'),
    ('M83 消えた番号の再利用を許す', "gdd_check.py",
     '            if int(num) <= top.get(p, 0):',
     '            if False:'),
    ('M84 gdd-sha256 を照合しない', "gdd_check.py",
     '            if meta[2] != gdd_sha:',
     '            if False:'),
    ('M85 表の列を見ない', "gdd_check.py",
     '    if split_row(header) != columns:',
     '    if False:'),
    ('M86 形状の座標の形を見ない', "gdd_check.py",
     '        if len(coords) != 4 or rest or len(set(coords)) != 4:',
     '        if False:'),
    ('M87 公開インターフェースの秒を許す', "gdd_check.py",
     '        m = re.search(a["if_forbidden_time_regex"], r["公開する状態・操作"] + " " + r["型・範囲"])',
     '        m = None'),
    ('M88 System.Random の記述を許す', "gdd_check.py",
     '        m = re.search(a["forbidden_rng_regex"], " ".join(v for k, v in r.items() if not k.startswith("_")))',
     '        m = None'),
    ('M89 ID の重複を許す', "gdd_check.py",
     '        if r["ID"] in ids:',
     '        if False:'),
    # ---- 出所の明示（CWA。docs/design/spec_pipeline.md §14）
    ('M139 知らない出所を名乗っても通す', "gdd_check.py",
     '        if origin not in origins:',
     '        if False:'),
    ('M140 出所つきの行を要約に出さない', "gdd_check.py",
     '        if origin:\n            derived.append(',
     '        if False:\n            derived.append('),
    ('M141 出所タグだけで根拠を省けるようにする', "gdd_check.py",
     '        if not value:\n            problems.append(f"{where}: 根拠がありません（出所だけでは根拠になりません。"',
     '        if False:\n            problems.append(f"{where}: 根拠がありません（出所だけでは根拠になりません。"'),

    # ---- 構造化役（docs/design/spec_pipeline.md §2・§8）
    ('M90 不合格の理由を次の試行に渡さない', "spec.py",
     '        feedback = (problems[:cfg["max_feedback_problems"]], text)',
     '        feedback = None'),
    ('M91 不合格でも書き出す', "spec.py",
     '        if rc == 0:\n            out = Path(a.out_dir)',
     '        if True:\n            out = Path(a.out_dir)'),
    ('M92 LLM が書いたメタデータを残す', "spec.py",
     '    while lines and (META_LINE_RE.match(lines[0]) or not lines[0].strip()):',
     '    while lines and not lines[0].strip():'),
    ('M93 使われたモデルを照合しない', "spec.py",
     '    if used is None:\n        return',
     '    if True:\n        return'),
    ('M134 内部処理用の許可を全モデルに広げる', "spec.py",
     '    stray = [m for m in models if not m.startswith(cfg["model"]) and not m.startswith(aux)]',
     '    stray = []'),
    ('M135 固定したモデルが使われていなくても通す', "spec.py",
     '    if not any(m.startswith(cfg["model"]) for m in models):',
     '    if False:'),
    ('M137 許す接頭辞の既定値をコードに持つ', "spec.py",
     '    aux = cfg.get("auxiliary_models")',
     '    aux = cfg.get("auxiliary_models") or ["claude-haiku-"]'),
    ('M138 固定モデルを飲み込む接頭辞を許す', "spec.py",
     '    bad = [x for x in aux if cfg["model"].startswith(x)]',
     '    bad = []'),
    ('M136 生の応答を残さない', "spec.py",
     '        Path(prompt_path).with_name("cli.json").write_text(out, encoding="utf-8")',
     '        pass'),
    ('M94 --model を渡さない', "spec.py",
     '                                       cfg["model_flag"], cfg["model"]]',
     '                                       ]'),
    ('M95 試行の上限を 1 回減らす', "spec.py",
     '    for n in range(1, cfg["max_attempts"] + 1):',
     '    for n in range(1, cfg["max_attempts"]):'),
    ('M96 CLI の異常終了を見ない', "spec.py",
     '    if rc != 0:\n        raise Env(f"構造化役の CLI が異常終了しました',
     '    if False:\n        raise Env(f"構造化役の CLI が異常終了しました'),
    ('M97 前の版の spec をプロンプトに渡さない', "spec.py",
     '    if previous_spec is not None:\n        prev = (',
     '    if False:\n        prev = ('),
    ('M98 GDD の project を照合しない', "spec.py",
     '    if metas[0].group(1) != project_id:',
     '    if False:'),
    # ---- GDD の PR の処理（B-2d）
    ('M99 処理済みの GDD を再処理する', 'scheduler.py',
     '        if any(marker in c for c in self.gh.view(num, "pr")["comments"]):\n            return "WAITING"',
     '        if False:\n            return "WAITING"'),
    ('M100 錨の事前検査を飛ばす', 'scheduler.py',
     '            missing = gdd_check.precheck(text)',
     '            missing = []'),
    ('M101 docs/spec 以外の変更を許す', 'scheduler.py',
     '            bad = [c for c in changed if c not in SPEC_FILES]',
     '            bad = []'),
    ('M102 マージ不可でも承認依頼を出す', 'scheduler.py',
     '        if summary["mergeable"]:',
     '        if True:'),
    ('M103 ms4:questions の PR もマージする', 'scheduler.py',
     '        if "questions" in self.cfg["labels"] and self.labels_name("questions") in names:',
     '        if False:'),
    ('M104 worktree を消さない', 'scheduler.py',
     '            if not self.git.worktree_remove(wt):',
     '            if False:'),
    ('M105 構造化の失敗を処理済みにしない', 'scheduler.py',
     '                self.gh.comment(num, marker,\n                                f"**ms4: 構造化に失敗しました**',
     '                self.gh.comment(num, "ms4:gdd-failure",\n                                f"**ms4: 構造化に失敗しました**'),
    ('M106 GDD の PR で必須チェックを見ない', 'scheduler.py',
     '            if not_ok:\n                print(f"  GDD の PR #{num}: 承認ラベルはあるが必須チェックが揃っていません: {not_ok}")',
     '            if False:\n                print(f"  GDD の PR #{num}: 承認ラベルはあるが必須チェックが揃っていません: {not_ok}")'),
    ('M107 作業用のローカルブランチを残す', 'scheduler.py',
     '            self.git._git("branch", "-D", branch, check=False)',
     '            pass'),
    ('M108 事前検査で座標の組を数えない', 'gdd_check.py',
     '    if pairs < pc["min_coordinate_pairs"]:',
     '    if False:'),
    # ---- 門と持ち出し先（PR 1: S22・S23）
    ('M143 ゴールデンの無いテスト駆動の単位でも ABORT する', 'pipeline.py',
     '    if not staged and not c.test_driven:',
     '    if not staged:'),
    ('M109 未追跡をフォルダにまとめて読む', 'pipeline.py',
     '"--untracked-files=all"], cwd, ttl, "status")',
     '"--untracked-files=normal"], cwd, ttl, "status")'),
    ('M110 無関係な .meta を許す', 'pipeline.py',
     '            if not (owner and is_new):',
     '            if not is_new:'),
    ('M111 既存の .meta の変更を許す', 'pipeline.py',
     '            is_new = code == "??" or code[0] == "A"',
     '            is_new = True'),
    ('M112 改名元のパスを読み捨てない', 'pipeline.py',
     '        if code[0] in "RC" and i < len(parts):',
     '        if False:'),
    ('M113 実装パイプラインを本体の clone で動かす', 'scheduler.py',
     '        rc, log = self.step(rec, "pipeline", cwd=wt, unit=unit)',
     '        rc, log = self.step(rec, "pipeline", unit=unit)'),
    ('M114 実装の前に受入テストを読めるか確かめない', 'scheduler.py',
     '        rec["test_count"] = self.summarize_tests(n, tests)[0]\n',
     ''),
    ('M115 pipeline が --repo-dir を無視する', 'pipeline.py',
     '        if args.repo_dir:\n            proj["repo_dir"] = args.repo_dir',
     '        if False:\n            proj["repo_dir"] = args.repo_dir'),
    ('M116 decompose が --repo-dir を無視する', 'decompose.py',
     '    if repo_dir:\n        p["repo_dir"] = repo_dir',
     '    if False:\n        p["repo_dir"] = repo_dir'),
    ('M117 audit が --repo-dir を無視する', 'audit.py',
     '    ROOT = Path(a.repo_dir or project.load(a.project)["repo_dir"])',
     '    ROOT = Path(project.load(a.project)["repo_dir"])'),

    # ---- 統合ブランチ直接 push（PR 2: S24。docs/design/spec_pipeline.md §13 の 1・3）
    ('M118 統合ブランチが無くても作らない', 'scheduler.py',
     '        if not self.git.remote_branch_exists(b):',
     '        if False:'),
    ('M119 push が通らなくてもマージを取り消さない', 'scheduler.py',
     '        except Abort as e:\n            self.igit.reset_hard(base_sha)',
     '        except Abort as e:\n            pass'),
    ('M120 マージが衝突しても進む', 'scheduler.py',
     '        if not ok:\n            self.igit.merge_abort()',
     '        if False:\n            self.igit.merge_abort()'),
    ('M121 ms4:integrated の Issue を対象から外さない', 'scheduler.py',
     '("running", "failed", "awaiting", "integrated")',
     '("running", "failed", "awaiting")'),
    ('M122 マージコミットに実行の ID を書かない', 'scheduler.py',
     '"Run-Id": self.run_id,',
     '"Run-Id": "unknown",'),
    ('M123 統合 PR の本文に Closes を書かない', 'scheduler.py',
     '            ", ".join(f"Closes #{n}" for n in issues) + "\\n\\n"',
     '            "" + "\\n\\n"'),
    ('M124 コミットと記録の食い違いを黙って通す', 'scheduler.py',
     '            checked.append({"issue": n, "merge": m, "run": hit, "problem": problem,',
     '            checked.append({"issue": n, "merge": m, "run": hit, "problem": None,'),
    ('M125 下限に達していなくても統合 PR を作る', 'scheduler.py',
     '        if len(merges) < least and more_ready:',
     '        if False:'),
    ('M126 固定 worktree を掃除せずに使う', 'scheduler.py',
     '        g.reset_hard("HEAD")\n        g.clean_fdx()',
     '        g.reset_hard("HEAD")'),
    ('M127 fast-forward でマージする', 'scheduler.py',
     'self._git("merge", "--no-ff", "-m", message, branch, check=False)',
     'self._git("merge", "-m", message, branch, check=False)'),
    ('M128 契約が読めなくても統合ブランチへ入れる', 'scheduler.py',
     '        if not sha:\n            raise Abort(',
     '        if False:\n            raise Abort('),
    ('M129 読み取れない監査の判定を ok にする', 'scheduler.py',
     '            verdicts.append(got)',
     '            verdicts.append(got if got in VERDICT_ORDER else "ok")'),
    ('M130 Issue を統合ブランチではなく base から切る', 'scheduler.py',
     '        wt, self.igit = self.prepare_runner(branch, f"origin/{integ}")',
     '        wt, self.igit = self.prepare_runner(branch, f"origin/{self.base}")'),
    ('M131 マージした統合ブランチを消さない', 'scheduler.py',
     '            self.git.delete_remote_branch(branch)\n            rec["result"] = "PASSED"',
     '            rec["result"] = "PASSED"'),
    ('M133 読めない判定で reject を覆い隠す', 'scheduler.py',
     'VERDICT_RANK = {"ok": 0, VERDICT_UNKNOWN: 1, "concern": 2, "reject": 3}',
     'VERDICT_RANK = {"ok": 0, VERDICT_UNKNOWN: 4, "concern": 2, "reject": 3}'),
    ('M132 積む本数の上限を見ない', 'scheduler.py',
     '            issues = ready[:max(0, min(limit, room))]',
     '            issues = ready[:limit]'),
]


# ---- 追記先（対象ファイルごと）
#
# 新しい変異は、下の対象ファイル別のリストへ足す。**上の M の末尾には足さない。**
# 末尾はどの PR も同じ 1 行を触るので、並行した PR が必ず競合する
# （2026-09-19 に 2 度踏んだ。#28 × #29、その前に #23 × #24）。
# 対象ファイルが違えば追記位置も分かれるので、競合しない。
#
# 上の M は設計の関心ごと（承認ゲート・二相判定・網羅検査器…）に並んでいる。
# その並びには意味があるので、対象ファイル別へは並べ替えない。ここは追記先だけの話である。
#
# 対象ファイルごとの整理は tests/test_mutate_table.py が検査する。

M_PIPELINE = [
    # ---- 実装役の観測（docs/design/spec_pipeline.md）
    ('M144 実装役のログを rc != 0 のときだけ書く', 'pipeline.py',
     '    write_implementer_log(c, c.metrics.get("attempt", 0), prompt, rc, out, err, cwd=workdir)',
     '    if rc != 0:\n        write_implementer_log(c, c.metrics.get("attempt", 0), prompt, rc, out, err, cwd=workdir)'),
    # ---- required_symbols の照合（docs/design/spec_pipeline.md）
    # 置換元は 1 行に収める。mutate.py は対象をバイト列で読むので、CRLF のファイルでは
    # 複数行の置換元が当たらない（表の検査は改行を正規化して読むため素通りしてしまう）
    ('M145 修飾名を解釈せず生の部分一致だけにする', 'pipeline.py',
     '    if not QUALIFIED_SYMBOL.match(symbol):',
     '    if True:'),
    ('M146 修飾名の照合から語境界を外す', 'pipeline.py',
     '    return all(re.search(r"\\b" + re.escape(part) + r"\\b", text)',
     '    return all(re.search(re.escape(part), text)'),
    # ---- 自己検査 [A] の復元段（機能追加の単位）
    ('M153 除外が効いたかの健全性検査を外す', 'pipeline.py',
     '    left = [n for t in c.unit["acceptance"]["required_tests"] for n in oracle.hits(fast, t)]',
     '    left = []'),
    ('M154 受入テストを除いても壊れているのに緑として返す', 'pipeline.py',
     '        return None, f"受入テストを除いても復元後がビルドできません（受入テスト以外の故障）: {err}"',
     '        return fast, None'),
]

M_SCHEDULER = [
    # ---- 起動可否の報告（--preflight）
    ('M155 ロックの ABORT 理由を落とす', 'scheduler.py',
     '        aborted = next((r["aborted"] for r in records if isinstance(r, dict) and "aborted" in r), None)',
     '        aborted = None'),
    ('M156 worktree の把持を阻害と見なさない', 'scheduler.py',
     '        rows.append(("worktree", held is None,',
     '        rows.append(("worktree", True,'),
    # ---- マージ直前の監査の対象（docs/design/contract.md）
    ('M147 付随ファイルも実装として数える', 'scheduler.py',
     '                if not any(x.startswith(s) for s in skip) and not self.engine.is_companion(x)]',
     '                if not any(x.startswith(s) for s in skip)]'),
    ('M148 Audit-Verdict にオラクル監査の判定を運ばず ok を直書きする', 'scheduler.py',
     '        verdict = rec.get("audit_verdict") or VERDICT_SKIPPED',
     '        verdict = "ok"'),
]

M_DECOMPOSE = [
    # ---- 分解役の観測（docs/design/telemetry.md）
    ('M149 TTL 超過で打ち切り時点の出力を捨てる', 'decompose.py',
     '        return 124, e.stdout or "", f"TTL超過 ({ttl}s): {label}\\n" + (e.stderr or "")',
     '        return 124, "", f"TTL超過 ({ttl}s): {label}"'),
    ('M150 分解役のログを rc == 0 のときだけ書く', 'decompose.py',
     '    write_decompose_log(prompt, rc, out, err)',
     '    if rc == 0: write_decompose_log(prompt, rc, out, err)'),
]

M_DOTNET = [
    # ---- 高速検査のビルド失敗（adapters/dotnet.py）
    ('M151 TRX が無いときにビルドの出力を捨てる', 'adapters/dotnet.py',
     '        return None, "検査系故障: TRX が生成されませんでした" + build_failure_detail(c, tag, rc, out, err)',
     '        return None, "検査系故障: TRX が生成されませんでした（ビルド失敗の可能性）"'),
    ('M152 エラー行を畳まず先頭も絞らない', 'adapters/dotnet.py',
     '    uniq = list(dict.fromkeys(lines))[:3]',
     '    uniq = lines'),
]

M += M_PIPELINE + M_SCHEDULER + M_DECOMPOSE + M_DOTNET

def read_source(path):
    """対象ソースを、改行を正規化して読む。**数える側も当てる側もここを通す。**

    バイト列のまま照合すると、CRLF のファイルでは複数行の置換元が 1 か所も当たらない。
    変異表の検査（tests/test_mutate_table.py）は改行を正規化して数えていたため、
    「表は緑なのに、その変異は一度も実行できない」という食い違いが起きていた。
    実測で 6 件が該当した（M53 / M54 / M55 / M61 / M71 / M115。harness/pipeline.py が CRLF、
    harness/scheduler.py が LF で、pipeline.py 側だけが黙って落ちていた）。

    数える側と当てる側で読み方が分かれていると必ずまたずれるので、入口を 1 つにする。
    """
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def write_source(path, text, crlf):
    """変異を当てたソースを書く。改行は元のファイルの形に戻す。"""
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main(only):
    survived = []
    for name, fn, old, new in M:
        if only and name.split()[0] not in only:
            continue
        p = T / fn
        orig = p.read_bytes()
        text = read_source(p)
        if text.count(old) != 1:
            print(f"{name}: 変異の置換元が {text.count(old)} 箇所（1 箇所であるべき）。変異表が古い")
            return 2
        try:
            write_source(p, text.replace(old, new), b"\r\n" in orig)
            # 変異の実行中は、変異表そのものを確かめるテストを飛ばす（ソースが書き換わっていて
            # 必ず赤になり、どの変異も無条件に「検出」になってしまう）
            env = {**os.environ, "HARNESS_MUTATING": "1"}
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                               cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, env=env)
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
