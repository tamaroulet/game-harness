# 頼み：残りの小さな直しと、検査の道具の意地悪な試験（クラウドの 5 本目）

このセッションの設定：モデル Opus 5.5（claude-opus-5-5）、effort high。どちらも起動の旗（--model claude-opus-5-5 --effort high）で固めた。

あなたは公開のリポジトリ tamaroulet/game-harness の写しの中で、新しい枝 1 本に変更を入れて push するところまでを行う。PR は作らない（手元の会話の席が中身を確かめてから作る）。返事は日本語。

## 0. 最初にすること（満たさなければ、何も変えずに 6 の報告だけをして終える）

1. 次を 1 回の Bash で走らせ、出力をそのまま控える。
   `date -u +%Y-%m-%dT%H:%M:%SZ; echo "session=${CLAUDE_CODE_REMOTE_SESSION_ID:-unset} effort_now=${CLAUDE_EFFORT:-unset} effort_env=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"; python3 --version; git log --oneline -1; git branch --show-current`
2. `effort_now` が `unset` でなく、`high` でもなければ終える（`unset` なら進め、報告にそう書く）。
3. あなたのモデルが Opus 5.5（claude-opus-5-5）でなければ終える。
4. 下請けのエージェント（Agent の道具）と、ネットの検索・取得の道具は使わない。
5. 開始から 2 時間を過ぎたら、その時点の変更を 5 の枝に push し、6 の報告をして終える。

## 1. 読むもの

- CLAUDE.md
- harness/argon_eval.py の _parse_args・main・evaluate と、プロンプトを組む所
- harness/hline_halt.py（全部）と、harness/bench_run.py の line_config
- harness/chain_score.py の paths_in・strays・audit・NET_TOOL・DRIVE_PATH・REL_UP と tests/test_chain_web.py・tests/test_chain_subagent.py
- harness/bench_guard.py の漏れの走査（leaks とその周り）と、その試験

## 2. 直すこと

### (1) 評価役の道具（harness/argon_eval.py）

- 起きたこと：`python -m harness.argon_eval post --help` を使い方の表示のつもりで打つと、`--help` を対象として読み、評価を 1 回呼んだ。写しの全部を詰めた 1,370 万字の頼みが届かず、モデルには入力 15,402 トークンしか渡らなかった（誤りにならず、評価の文が返った）。
- 直し：
  - 対象（2 つ目の引数）が `-` で始まるなら、評価を呼ばずに使い方を出して終了コード 2。`-h`・`--help` がどこにあっても、使い方を出して 0。
  - 組んだ頼みが `INLINE_MAX_CHARS`（新しい定数。1,000,000 字）を超えたら、モデルを呼ぶ前に止め、字数と、`--with` で材料を絞るか tools の読み方にするよう 1 行出して 2。読み方（inline・tools）と `--with` のどれでも同じに掛ける。

### (2) 止まりの Issue（harness/hline_halt.py）

- 起きたこと：同じ理由の止まりが 2 回続くと、H ラインは公開のリポジトリに Issue を開こうとする。理由の文にはこの PC のパスが入りうる。ベンチの走り（bench_run の factory と bench_chain の段）でも開こうとする（今は bench に GitHub のログインが無いので開かれていないだけ）。
- 直し：
  - 設定に `open_issue` が false なら、Issue を開かない（report.md の節と続いた回数の記録は今どおり）。無いときは今どおり開く。
  - bench_run.line_config の上書きの欄に `"open_issue": False` を足す。`"open_pr": False` と同じ行に足し、bench_run.py の行の数は増やさない（このファイルは行の上限を超えていて、増やせない）。
  - Issue を開くとき（H ラインの常駐の走り）、題と本文から、ドライブからのパス（`C:\...`・`C:/...`）とホームの下のパス（`/home/<名>/...`・`/Users/<名>/...`）を `<path>` に伏せる。同じ題の開いた Issue があるかの照らしは、伏せた後の題で行う。

### (3) 道の検めのパスの読み取り（harness/chain_score.py の paths_in）

- 起きたこと：今の paths_in は、ドライブからのパスが 1 つでもあれば、ほかの形（`/` で始まるパス・`..` を含む相対のパス）を見ない。`/` で始まるパスは、引数の文字列の全体が `/` で始まるときしか拾わない（`cat /a/b /c/d` の 2 つを見逃す）。
- 直し：引数の文字列を語に分け（空白・引用符・`=`・`(` で区切る）、語ごとに、ドライブからのパス・`/` で始まるパス・`..` を含む相対のパス（作業の場所から正規化）を、全部拾う。URL（`https://...` など、`://` を含む語）はパスにしない。
- 既存の試験の値は変えない。手元の会話の席が、走りの記録の採点し直しで、公開の集計が変わらないかを確かめる。

### (4) 検査の道具の意地悪な試験

- 道の検め（chain_score の strays・audit）と漏れの走査（bench_guard の leaks）に、すり抜けを狙う試験を書く。少なくとも次を試す。
  - 道の検め：区切りと大文字小文字の混ぜ（`C:/SRC/...` と `c:\src\...`）、`\?\C:\...` の長いパスの頭、引数の中の `file:///C:/...` の URI、入れ子の JSON の引数の奥のパス、`%USERPROFILE%`・`$HOME`・`~` から始まるパス、ネットを見る道具の名が NET_TOOL の形に当たらないもの（`fetch`・`http_get` など）。
  - 漏れの走査：その道具が見つけるべき印が、改行・空白・大文字小文字の違いで分かれたもの（道具の決まりを読んで、当たるべき場合を選ぶ）。
- すり抜けた試験は、直しが 30 行以内なら直す。それより大きい直しは入れず、その試験を `unittest.expectedFailure` にして、6 の 6 に「何がすり抜けるか・直すなら何が要るか」を書く。決めかねる場合（例：`~` を誰のホームと見るか）は推して決めず、同じく expectedFailure にして 6 に書く。

## 3. 守ること

- 変えてよいのは、harness/argon_eval.py・harness/hline_halt.py・harness/chain_score.py（paths_in と、(4) の 30 行以内の直し）・harness/bench_guard.py（(4) の 30 行以内の直し）・harness/bench_run.py の line_config の 1 行（行を増やさない）、tests/ の下の新しい試験のファイルだけ。harness/surface.py・harness/bench_chain.py・config/ は、別のセッションが同時に変えているので触らない。既存の試験・docs/・bench/・.github/・.claude/ も変えない。
- chain_score.py が 500 行を超えそうなら、(3) を優先し、(4) の直しは expectedFailure に回す。
- 頼みに無い直しはしない。決めかねたら推して決めず、6 の 6 に書く。
- 規模：harness/ の追加は 300 行まで（試験は数えない）、関数の循環的複雑度は 15 まで。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリと requirements.lock にあるものだけ）。
- 試験は Windows でも通る形で書く（手元は Windows。パスは Path で比べる。Windows の形のパスの試験は、どの OS でも同じに通る文字の形で書く）。一時ディレクトリの偽の記録だけで書く。gh と評価役のモデルは呼ばない（差し替える）。

足す試験（少なくとも）：(1) 対象が `-` で始まる・`--help` の位置・字数の上限の端（ちょうどは通り、1 字超えで止まる）、(2) open_issue が false で開かない・無いときは開く・伏せた題と本文・伏せた題で重なりを照らす・line_config に open_issue が入る、(3) 上の「起きたこと」の場合と URL と `--path=/x/y`、(4) 上の各すり抜け。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通る（expectedFailure は期待どおりに落ちる）ことを確かめる。

## 5. 枝とコミット

- 枝：`claude/small-fixes-e`（main から）。main にも、ほかの既存の枝にも push しない。この枝に push できなければ、今いる枝に push し、6 の 5 にそう書く。
- コミットは 1〜4 個（直しの種類ごとに分けてよい）。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数（うち expectedFailure の件数）
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を、message の id の重なりを除いて足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと・すり抜けたまま残したもの（無ければ「無し」）
