# 頼み：鎖の採点（harness/chain_score.py）の直し 3 つ（クラウドのパイロット 1 本目）

このセッションの設定：モデル Sonnet 5.5（claude-sonnet-5-5）、effort high。どちらも起動の旗（--model claude-sonnet-5-5 --effort high）で固めた。

あなたは公開のリポジトリ tamaroulet/game-harness の写しの中で、新しい枝 1 本に変更を入れて push するところまでを行う。PR は作らない（手元の会話の席が中身を確かめてから作る）。返事は日本語。

## 0. 最初にすること（満たさなければ、何も変えずに 6 の報告だけをして終える）

1. 次を 1 回の Bash で走らせ、出力をそのまま控える。
   `date -u +%Y-%m-%dT%H:%M:%SZ; echo "session=${CLAUDE_CODE_REMOTE_SESSION_ID:-unset} effort_now=${CLAUDE_EFFORT:-unset} effort_env=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"; python3 --version; git log --oneline -1`
1a. 1 を走らせたら、2・3 の判定の前に必ず、作業の枝 `claude/chain-score-at-start-subagents` を main から切り、`git commit --allow-empty` で空のコミットを作って、すぐその枝に push する（開始の印。会話の席が GitHub で読む）。メッセージは 1 行目を `開始の印`、本文に 1 の出力をそのままと、あなたのモデルの ID を書く。最後の行は 5 の Co-Authored-By。
2. `effort_now` が `unset` でなく、`high` でもなければ終える（`unset` なら進め、報告にそう書く）。`effort_env` は記録だけ。
3. あなたのモデルが Sonnet 5.5（claude-sonnet-5-5）でなければ終える。
4. 下請けのエージェント（Agent の道具）と、ネットの検索・取得の道具は使わない。
5. 開始から 2 時間を過ぎたら、その時点の変更を 5 の枝に push し、6 の報告をして終える。

## 1. 読むもの

- CLAUDE.md（このリポジトリの作業の決まり）
- harness/chain_score.py（全部）、harness/bench_chain.py の先頭の docstring、harness/bench_run.py の LOG_SPLIT・run_dir・manifest
- 試験の手本：tests/test_chain_grade.py、tests/test_chain_web.py、tests/test_chain_index.py

chain_score.py は「鎖」の段ごとの採点（finish）をする。段の走りの記録のディレクトリには、走りの git の束（git/run.bundle。枝 tip）と、What ごとの生ログ（items/<What>/implementer-*.log。要約・LOG_SPLIT・agy の stream-json・LOG_SPLIT・標準エラー）がある。finish_stage は、先端の木で隠しテストを採点し、道の検め（audit：作業の場所の外のパスと、ネットの道具）をする。

手元の非公開の置き場（隠しテスト・本物の走りの記録）は、あなたからは見えない。試験は、一時ディレクトリに作る偽の木・束・ログだけで書く。課題の名前や中身を推して書かない。

## 2. 直すこと（3 つ）

### (1) 開始の木での新しい試験の通過

- finish_stage で、先端の木の採点に加えて、段の開始のコミット（st["start"]）の木でも、段 k の隠しテストのファイル（hidden_files(chains, base, k) の最後の 1 つ）だけを、同じ gone で採点する。
- 公開の行（row）に `new_at_start: [通過, 件数]` を足す。開始のコミットが束から取り出せないときは `null`。境界の違反で 0 点にする決まりは new_at_start には掛けない。
- 試験ごとの結果（priv の s<k>.json）に `at_start`（段 k の試験の id → "ok" か理由の 1 行）を足す。
- 索引（index_text）に節「## 開始の木で通っていた新しい試験」を足し、`- x/y` の行と、通っていた試験の名（id の最後の部分）を並べる。
- 既存の項目（new・old・kept・violations・leaks・strays・web・invalid・reason・manifest_sha256 など）の値は変えない。

### (2) 実装役の下請けの記録を写して検める

実装役（agy）は下請けを起こすことがある。生ログの stream-json の 1 行の形（値は例）：

```json
{"event":"step_update","step_update":{"conversation_id":"main-1","step_index":50,"state":"DONE","step_type":"subagent","tool_name":"invoke_subagent","subagent_info":{"subagents":[{"type_name":"research","role":"Codebase Explorer","initial_prompt":"...","conversation_id":"sub-1","log_uri":"file:///C:/x/brain/sub-1/.system_generated/logs/transcript.jsonl","workspace_uris":["file:///C:/x/work/wt"]}]}}}
```

下請けの記録（transcript.jsonl）は 1 行に 1 つの JSON。道具の呼び出しはこの形（値は例。SYSTEM_MESSAGE・GENERIC など、ほかの形の行もある）：

```json
{"step_index":1,"source":"MODEL","type":"PLANNER_RESPONSE","status":"DONE","created_at":"2026-11-01T01:53:42Z","tool_calls":[{"name":"view_file","args":{"AbsolutePath":"C:\\x\\work\\wt\\README.md","toolAction":"...","toolSummary":"..."}}]}
```

- finish で、各 implementer-*.log から下請けの起動を拾う。log_uri（file:/// の URI。Windows のドライブ文字の形も Linux の形も扱う）の指すファイルが読めれば、同じ What のディレクトリに `subagent-<conversation_id>.jsonl` として写す（同じ中身が既にあれば写し直さない）。読めなければ、道の検めの当たり（found）に「下請けの記録が見つからない（検められない）」を足す。
- 写した下請けの記録の道具の呼び出しにも、実装役と同じ道の検め（strays と audit の決まり：NET_TOOL・paths_in・local の下で作業の場所の外）を掛ける。下請けの作業の場所は workspace_uris の最初（無ければ実装役の作業の場所）。当たりと調べものの「ログ」の欄は `subagent-<id>.jsonl`。
- 下請けの無い走りでは、記録のどの値も今と同じになること。

### (3) 自分の下請けの記録を読むのは道の外としない

- 実装役が、同じログで起こした下請けの log_uri と同じファイル（正規化して比べる）を読む呼び出しは、strays の当たりにしない（中身は (2) で検める）。別の会話の記録（ほかの id）を読むのは今どおり当たり。

## 3. 守ること

- 変えてよいのは harness/chain_score.py と、新しい試験のファイル（tests/ の下の新しい名前）だけ。足りなければ harness/ の下に新しいモジュールを 1 つ足してよい。既存の試験・config/・docs/・bench/・.github/・.claude/ は変えない。頼みに無い直し（ついでの整理・名前の変更）はしない。決めかねたら推して決めず、6 の 6 に書く。
- 規模の制約（config/size_limits.json）：差分の追加は 300 行まで、モジュールは 500 行まで（chain_score.py は今 393 行）、関数の循環的複雑度は 15 まで。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリだけ）。
- 足す試験：(1) 開始の木と先端の木で通過が違う偽の束で、new_at_start・at_start・索引の節。開始のコミットが無いときの null。(2) 下請けの起動の拾い方・写し・写しの中の道具の検め（作業の場所の外を読む・ネットの道具・作業の場所の中だけ）・記録が無いときの当たり。(3) 自分の下請けの記録を読むのは当たりにならず、別の id の記録は当たり。(4) 下請けの無い走りで、既存の項目が変わらない。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる（シェルの状態は命令をまたいで残らないことがある）：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通ることを確かめる。

## 5. 枝とコミット

- 枝：`claude/chain-score-at-start-subagents`（main から）。main にも、ほかの既存の枝にも push しない。
- コミットは、0 の開始の印のほかに 1〜3 個。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと（無ければ「無し」）
