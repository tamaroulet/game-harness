# 通しの経路と継ぎ目（D-086）

部品ではなく、データが最終の判定まで流れる経路を単位にして考えるための文書。

（2026-10-24：W-169 の表 `docs/design/rules_inventory.md`（game-harness#286）で「消す」とした行（開く時機・直す時期・R5 の仕様の書式の決まり）を消した版）

- 書くもの：経路ごとの段の順と、段と段の継ぎ目。継ぎ目ごとに、前段が出す形と後段が受ける形を、読んだコードの `path:行` つきで書き、両側を読んだ結果（読み）と、前段の実物の出力を後段に通す照合の名前を書く
- 書かないもの：照合の合否（正は report.md の「## 照合」）、タスクの状態（正は report.md）
- 読みの 4 つ：**一致**（両側のコードを読み、形が合う）・**不一致**（両側を読み、合わない）・**未読**（片側か両側を読んでいない）・**未作成**（片側の道具がまだ無い。台帳の acceptance の文言だけがある）
- 関門の通る条件：その関門が使う経路の継ぎ目に、不一致と未読が残っていないこと（未作成は、作るタスクがその関門の前に台帳にあること）。照合を置ける継ぎ目は照合で確かめる

主張の 3 段：**実在**（ファイル・関数がある）＜ **契約一致**（この文書の読みが一致）＜ **接続実測**（照合が合格）。「使える」「繋がる」「既にある」と言えるのは接続実測だけ。それより下は段の名前で言う。

## 1. ベンチの経路（関門 R3-c の結合。W-48 の乾式の通しがこの経路の端から端までの実測）

| 継ぎ目 | 前段の出力（形・出典） | 後段の入力（形・出典） | 読み（10-14） | 照合 | 解く関門 |
|:--|:--|:--|:--|:--|:--|
| B-1 | 課題の仕様 `docs/bench/<id>/spec.md`：行頭の `- REQ-nn：本文`（B06 で 8 件を読んだ） | 要件の網羅 `harness/reqcov.py`：定義は行頭の `- REQ-nn：`（`:5`・`:20-22`） | 一致 | `join-spec-reqcov`（全課題の仕様を reqcov の定義の読みに通す） | R3-c |
| B-2 | 課題の仕様 | 計画役 W-42：`--spec` の仕様の全文を入力に入れる（W-42 の What） | 一致（W-42 の What で受ける形を決めた） | ― | ― |
| B-3 | 計画役の What：要件の行 | `reqcov --whats`：`要件:` の行に出る ID だけを数える（`harness/reqcov.py:6`・`:23`）。取り込みの見出しの読みは「マイルストーン・タスク・依存・検証」だけで（`harness/hline_queue.py:27`）、`要件:` の行は本文として通る | 前段が不一致：今の `docs/prompts/planner.md` は要件の ID を「本文に書く」としか言わない | ― | 操縦士の PR `pilot-pr-planner-prompt.md`（`要件:` の行を求める） |
| B-4 | 計画役の `ledger.yaml`：各タスクに `files` | 計画の受け入れの検査 W-76：`files` が 1〜5 件。W-42 の出力の確かめも `files` を求める | 前段が不一致：今の `planner.md` の `ledger.yaml` の形に `files` が無い。件数も `planner.md` は 1〜4 件、W-76 は 2〜8 件。W-42 の受信箱の確かめも P-01〜P-04 だけを許す（`harness/planner.py` の `MAX_TASKS = 4`、10-15 に読んだ）ので、つないだ実の上限は 4 件 | ― | `pilot-pr-planner-prompt.md`（`files` を足し、件数を 2〜4 件に） |
| B-5 | 計画役の `ledger.yaml`（タスク P-01〜）→ 開始のコミット W-44 でベンチの base の `docs/progress.yaml` に入る | H ラインの取り込み：base の台帳にある未完了の `by: hline` のタスク（ID の形は縛らない。`harness/hline_queue.py:116-121`）、設定の `milestones` にあるマイルストーン（`:140`）。台帳の検査は `project_goal`・`nodes`・`tasks`・`constraints` と、各タスクの `id`・`title`・`group`（`nodes` にあること）・`target_repo`・`status` を求める（`harness/progress.py:128-150`） | 一致（取り込みと台帳の検査を読んだ）。残る継ぎ目：計画役の `ledger.yaml` に無い欄（`group`・`target_repo`・`status`・`nodes`）は W-44 が補う。計画役の What の `マイルストーン: bench` は、ベンチの設定（W-41）の `milestones` に要る | ― | W-41・W-44 の What で名指す |
| B-10 | 計画役の What と `ledger.yaml` | 実装役への指示（W-80：台帳の欄を直に渡す） | 前段が不一致：今の `planner.md` は acceptance を「What の合格条件と同じ中身」とし、写しの構造を持ち込む（D-081 ②） | ― | `pilot-pr-planner-prompt.md` |
| B-11 | Gate 1 の毎回の試験（W-82 の `gate_always_modules`） | ベンチの台帳（計画役のタスクだけで横木が無い） | 不一致になりうる：`tests.test_ledger_double_check` は横木と `docs/tasks.md` の行を求め、ベンチの台帳では必ず落ちる | ― | W-82 の What で欄を設定ごとにし、W-41 のベンチの設定には入れない |
| B-6 | 計画役の What | 実装役への指示（W-80：作業ツリーの `docs/progress.yaml` から、宣言したタスクの欄を入れる） | 未作成（W-80） | ― | W-80 の試験に、`P-nn` の ID の台帳を含める |
| B-7 | ベンチの統合ブランチの木。非公開オラクルの置き場 `<oracles>/bench/<id>/hidden/test_hidden_*.py`（`W-20/tasks10.md` §2）と参照実装 `<oracles>/bench/<id>/reference/<パッケージ>/`（W-86 の What で決めた。W-36 で操縦士がここに置く） | 採点役 W-34：`--tree <dir>` の木の写しで隠しテストの通過率。指紋は `docs/bench/<id>/oracle.sha256`。床の検査 W-86：写しの根の直下に `<パッケージ>/` を置いた木と消した木を採点 | 未作成（W-34・W-86。受ける形は両方の What で決めた） | ― | W-34・W-86。W-36 の PR の完成形に、参照実装の置き場を名指す |
| B-8 | 採点・走行の記録 | 目的までの距離 `harness/goal_distance.py`（W-90 で直した後は、R4 が `factory-base-*` の記録を数える。下の行番号は W-90 の前の版）：`bench/records/<課題>/floor.json` の `passed` が `true`（`:147-160`）、`chat-*`・`factory-*` の記録の `invalid` が偽（`:127-128`・`:156-158`）、`freeze.json` の `base` と記録の `base`（`:163-174`）。記録は ref の木の `git ls-tree` で読む（`:51-53`。コミットされた記録だけを数える） | 一致（後段を読んだ。前段の `floor.json` は W-86 の What でこの形に合わせた。D-095（`retired/2026-10-16/decisions.md`）で W-34 から分けた） | ― | W-46・W-88 の What で記録の名前（`factory-` で始まり、基準の走行は `factory-base-`）と `base`・`invalid` を名指す（D-109 でチャット型の記録は作らない） |
| B-9 | 記録 | 検め直し W-47 → 凍結 W-50 → 基準の走行 W-89 → 非劣性の検査 W-57（D-109 で W-49・W-52 を外した） | 未作成。受ける形は W-46（記録の欄・走行のディレクトリ）・W-47・W-50 の What で決めた（10-16） | ― | R3-c・R4 |
| B-12 | ベンチの clone の H ライン（`run_line` の `check_code`：走るハーネスの `HEAD` と統合ブランチの先端を比べる。`harness/hline.py:91-96`） | 走らせ役 W-46 の poll の回し方 | 未作成。W-46 の What で、poll の前ごとに clone を統合ブランチの先端（無ければ開始のコミット）へ checkout することを名指した（本線の runner と同じ手当て。D-107） | ― | W-48 で実測 |
| B-14 | 開始のコミットの台帳（`harness/bench_start.py:76`。根のノードは `id`・`parent` だけ） | clone の H ラインが report.md を書く `progress.tree`（`harness/progress.py:250,284` が `title` を読む）。検査 `progress.validate` は `title` を見ない | 一致（#220・`3d450ce` で、作る側 2 か所に `title`、`progress.validate` がノードの `title` を確かめる。差分を読んだ、10-16）。接続実測は W-48 の run-id 6 | ― | W-48 の run-id 6 |
| B-15 | 計画役の出力（CLI が受信箱に書いた `P-*.md`。sha256 は `planner.json` の `files`） | キューの写し `<out>/queue/<名前>.md`（`harness/hline_queue.py:162`）→ 記録の `queue_sha256` → `bench_verify` の人の介入の照合 | 不一致（テキストのモードの書き直しで、Windows では改行が CRLF になる。W-48 の run-id 6）。直しは `pilot-pr-D114.md`（バイト列をそのまま写す） | ― | W-48 の run-id 7 |
| B-13 | 走行（制限のアカウント。非公開オラクルを読めない） | 採点・漏れの走査（非公開オラクル・canary を読む） | W-46 の `factory`（制限）→ `factory/run.json` → `finish`（操縦士）に分けた（D-112）。W-88 の `stage` と `stage --finish` も同じ | ― | W-48 で実測 |

## 2. 本線の経路（総監督の What が main に入るまで）

| 継ぎ目 | 前段の出力 | 後段の入力 | 読み | 照合 | 解く関門 |
|:--|:--|:--|:--|:--|:--|
| M-1 | 総監督の What（受信箱の直下の `*.md`） | 取り込み：マイルストーン → タスク → `検証:` の行なし → 台帳 → 機械の監修 → LLM の監修（INDEX の落とし穴） | 一致 | 運用の実績（report.md のキューの済み） | ― |
| M-2 | What の本文 | 実装役への指示 `harness/hline_prompt.py` の `build_prompt`（What の本文だけを渡す） | 一致。W-80 で台帳の節が加わる | ― | W-80 |
| M-3 | 実装役の変更 | Gate 1 `harness/hline_gate.py:30-51`：保護パス・構文・影響テスト（無ければ `canary_modules`） | 一致 | 運用の実績 | ― |
| M-4 | Gate 1 を通った変更（統合ブランチ） | 統合 PR の CI：`tests/test_*.py` の全件 | 不一致：全体を走査する試験は Gate 1 で走らず、CI で初めて掛かる（統合 PR #186 の赤） | ― | W-82（承認待ち） |
| M-5 | タスク `game-harness-runner`（runner を起こし直す。D-107） | runner `run_night_loop.ps1`（poll の前に統合ブランチの先端へ checkout。poll のタスク 2 つを無効にする） | 未作成 | ― | 操縦士の登録（D-107）。`harness/hline_cron.py` の `game-harness-hline-poll` は経路に入れない |
| M-6 | 未収束の最後の試行の失敗の出力（`harness/hline_resume.py` の `keep` が `last-failure.txt` に書き、`store` が `out/resume/<名前>/` に移す。中身は Gate 1 の出力で、公開の試験だけ） | report.md の「## キュー」の未収束の行（今は分類と再開の材料の行数だけ） | 前段は一致、後段は未作成 | ― | W-83（承認待ち） |

## 3. 製品の経路（R5。台帳に足すのは段 4 の後。C5 の比べはここに付け足す。D-109）

| 継ぎ目 | 前段の出力 | 後段の入力 | 読み | 照合 | 解く関門 |
|:--|:--|:--|:--|:--|:--|
| P-1 | 構造化仕様（`harness/gdd_check.py` の `SECTIONS`：LP・ST・RL・IF・PR・HC の表） | 計画役・要件の網羅（`harness/reqcov.py:20`：`REQ-nn` だけ） | 不一致。加えて `gdd_check.py` は falling-blocks（C#・Unity）の錨（`docs/design/spec_pipeline.md` §5・§11）と `projects/<id>/` の設定に依り、Python・MuJoCo の課題に当てられるかは未読 | ― | R5 の台帳の起草 |
| P-2 | G1 の仕様（`- REQ-nn：`）・判定役・参照実装 | 照合（参照実装で全部通り、本体なしで全部落ちる。W-37 の `w37-*` と同じ手順） | 一致（ベンチの B02〜B04 で接続実測済みの手順） | G1 を起草するときに `g1-*` を宣言 | R5 |
| P-3 | 仕様の外部パラメーターの要件の行 | 本体が読む `rollmaze/params.json` と、値を変えると振る舞いが変わる判定役の試験 | 未作成 | ― | R5 |

## 4. 負債（不一致と未読のうち、解く関門より前に手を付けるもの）

| 負債 | 継ぎ目 | いつまでに |
|:--|:--|:--|
| 計画役への指示が下流の受ける形と食い違う（`要件:` の行・`files`・件数・写し） | B-3・B-4・B-10 | W-43 の前（操縦士の PR `pilot-pr-planner-prompt.md`） |
| 全体を走査する試験が Gate 1 に掛からない | M-4 | W-22 の直後の周期（W-82） |
| 定期起動の名前と無効 | M-5 | 今 |
| 仕様の書式が 2 つある（`gdd_check.py` と reqcov） | P-1 | R5 を台帳に足すとき |
