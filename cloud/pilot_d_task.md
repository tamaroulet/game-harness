# 頼み：提案役の起こし方（harness/proposer_launch.py）（クラウドの 2 本目）

このセッションの設定：モデル Opus 5.5（claude-opus-5-5）、effort xhigh。どちらも起動の旗（--model claude-opus-5-5 --effort xhigh）で固めた。

あなたは公開のリポジトリ tamaroulet/game-harness の写しの中で、新しい枝 1 本に変更を入れて push するところまでを行う。PR は作らない（手元の会話の席が中身を確かめてから作る）。返事は日本語。

## 0. 最初にすること（満たさなければ、何も変えずに 6 の報告だけをして終える）

1. 次を 1 回の Bash で走らせ、出力をそのまま控える。
   `date -u +%Y-%m-%dT%H:%M:%SZ; echo "session=${CLAUDE_CODE_REMOTE_SESSION_ID:-unset} effort_now=${CLAUDE_EFFORT:-unset} effort_env=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"; python3 --version; git log --oneline -1`
2. `effort_now` が `unset` でなく、`xhigh` でもなければ終える（`unset` なら進め、報告にそう書く）。`effort_env` は記録だけ。
3. あなたのモデルが Opus 5.5（claude-opus-5-5）でなければ終える。
4. 下請けのエージェント（Agent の道具）と、ネットの検索・取得の道具は使わない。この箱で `claude` の CLI を起こさない（試験は偽の出力だけで書く）。
5. 開始から 2 時間を過ぎたら、その時点の変更を 5 の枝に push し、6 の報告をして終える。

## 1. 読むもの

- CLAUDE.md（このリポジトリの作業の決まり。とくに「エージェントのモデル」）
- 手本：harness/director_launch.py（全部）と tests/test_director_launch.py
- harness/model_pin.py（全部）、harness/hline_base.py の load_config・pinned_models・save_prompt・write_json、harness/hline_budget.py（全部）、harness/proc.py の run と resolve_cli
- harness/chain_score.py の先頭の docstring と finish（開発の組の試験ごとの結果と索引は `<config/hline.json の out>/chains/<tag>/<土台>/` に、段ごとに `s<k>.json` と索引で置かれる）
- config/hline.json の director の項

提案役は、外側の周期（ハーネスが自分の改善を選ぶ）の最初の手で、開発の組の走りの記録とハーネスのコードを読み、直す失敗の型を 1 つ選んで、決まった見出しの提案を書く。別の起動の Claude Code で、読むだけの道具（Read・Grep・Glob）しか使わない。今度の頼みは、その起こし方（関所・上限・モデルの固定・道の検め・提案の形の検め・記録）だけを作る。提案役への指示の本文（docs/prompts/proposer.md）は作らない（保護パス。操縦士の PR で別に入る）。

## 2. 作るもの

### (1) 設定：config/proposer.json（新しいファイル）

config/hline.json の director と同じ形で、値は次のとおり（config/hline.json は高リスクの区分なので触らない。out と oracles は config/hline.json から読むだけ）。

- `cli` "claude"、`headless_flag` "-p"、`model_flag` "--model"、`model` "claude-sonnet-5-5"、`auxiliary_models` ["claude-haiku-"]
- `extra_flags`：`--allowedTools` "Read,Grep,Glob" と、`--disallowedTools` "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent,Task"。`--permission-mode` は付けない（-p では、許していない道具は断られる）。
- `output_format_args`：["--output-format", "stream-json", "--verbose"]（道具の呼び出しを検めるため。director の json とは違う）
- `allowed_tools`：["Read", "Grep", "Glob"]（道の検めが照らす一覧。extra_flags と同じ並び）
- `proposer_budget`：`max_turns` 60、`ttl_seconds` 3600、`max_tokens` 5000000

### (2) モデルの固定の検査に入れる

- harness/model_pin.py の agent_configs で、config/decompose.json・config/spec.json と同じ並びに config/proposer.json を足す。`python -m harness.model_pin` が proposer も検めること。ほかの項の扱いは変えない。

### (3) 起こし方：harness/proposer_launch.py（新しいモジュール）

`python -m harness.proposer_launch --tag <tag> [--root <dir>]... [--config <hline.json>]`（提案役の設定は ROOT の config/proposer.json）

1. 関所（どれかに当たれば起こさず、記録に理由を書いて終了コード 1）：
   - model_pin.require と auxiliary_prefixes（director と同じ。設定の誤りは止める）
   - 指示のファイル docs/prompts/proposer.md が無い
   - 開発の組の記録 `<out>/chains/<tag>/` が無い
   - 読む根（下の 2）のどれかが、config の `oracles` の下にあるか、`oracles` を中に含む（保留の組と隠しテストを読ませない）
2. 読む根：ハーネスの木（ROOT。作業ディレクトリにする）、開発の組の記録 `<out>/chains/<tag>/`、`--root` で足したもの。ROOT 以外は CLI に `--add-dir <dir>` で渡す。
3. 渡す頼み：指示のファイルの全文の後に、節「## 材料」を足す（tag、読む根の一覧、開発の組の記録の中の索引のファイルの一覧、下の 5 の見出しの並び）。渡した全文は save_prompt で残す。
4. 起こす：hline_budget.capped_args で `--max-turns` を掛け、proc.run の時間の上限は `ttl_seconds`。生の出力は記録の .log に残す。
5. 終わった後（director の流れに合わせる）：
   - stream-json の最後の `"type": "result"` の行を取り出し、それを pinned_models・hline_budget.usage・turn_cutoff に渡す（どれも 1 つの JSON を読む）。result の行が無い・modelUsage が無いときは、止めて「CLI の誤り」（モデルを照合できない走りは使わない）。トークンは director と同じく後で照らし、超えたら「超過」と残す。
   - 道の検め：assistant の行の `tool_use` をすべて拾い、(i) 名前が `allowed_tools` の外、(ii) 入力の `file_path`・`path`、または絶対パスの形の `pattern` が、読む根のどれの下にも無い（`..` や大文字小文字、`\` と `/` の違いは正規化して比べる。Windows のドライブ文字の形も Linux の形も扱う）、を当たりにする。`path` が無い呼び出しは作業ディレクトリ（ROOT）と見る。当たりがあれば、提案は捨てる（記録に当たりの一覧）。
   - 提案の形の検め：result の行の `result` の文が、見出し `## 狙う失敗の型`・`## 出た段`・`## 直し`・`## 予想`・`## 補う弱さ`・`## 外す条件` を、この並びで各 1 回、中身を空にせずに持つこと。`## 出た段` の各行（`- ` で始まる）は `<土台> s<k>` の形で、`<out>/chains/<tag>/<土台>/s<k>.json` があること。合わなければ、何が合わないかを記録に書く。
   - 合えば、見出しから後の本文を `<stamp>.proposal.md` に書く。
6. 記録：`<out>/proposer/<stamp>.json`（起こした時刻、tag、読む根、起こしたか・起こさなかった理由、終了の理由、利用量、トークンの超過、道の当たり、提案の形の誤り、提案のファイル）。起こす前に一度書く（CLI が返らなくても起こした時刻が残る。director と同じ）。
7. 終了コード：起こして、正常に終わり、当たりが無く、形が合えば 0。ほかは 1。設定を読めない・model_pin の誤りは exitcode の決まりに合わせる。最後に 1 行の要約を標準出力に出す。

stream-json の行の形（値は例。ほかの形の行もある。決め打ちせず、無いキーで落ちない）：

```json
{"type":"system","subtype":"init","model":"claude-sonnet-5-5","cwd":"/x/repo","tools":["Read","Grep","Glob"]}
{"type":"assistant","message":{"model":"claude-sonnet-5-5","content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/x/out/chains/v1/B01/s2.json"}}]}}
{"type":"result","subtype":"success","is_error":false,"num_turns":12,"result":"## 狙う失敗の型\n...","usage":{"input_tokens":10,"output_tokens":20},"modelUsage":{"claude-sonnet-5-5":{"inputTokens":10,"outputTokens":20}}}
```

## 3. 守ること

- 変えてよいのは、harness/proposer_launch.py（新）、config/proposer.json（新）、harness/model_pin.py の agent_configs（proposer を足す所だけ）、新しいモジュールを登録するための config/arch_components.json と config/envscan_baseline.json（test_arch_inventory・test_envscan_ratchet が求める分だけ。ほかの値は変えない）、tests/ の下の新しい試験のファイルだけ。harness/chain_score.py は触らない。既存の試験・docs/・bench/・.github/・.claude/・config/hline.json は変えない。頼みに無い直し（ついでの整理・名前の変更）はしない。決めかねたら推して決めず、6 の 6 に書く。
- 規模：harness/ の追加は 300 行まで（試験は数えない）、モジュールは 500 行まで、関数の循環的複雑度は 15 まで（config/size_limits.json）。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリだけ）。director_launch.py と同じ部品（proc・hline_budget・model_pin・hline_base）を使い回し、同じことを書き直さない。
- 試験は Windows でも通る形で書く（手元は Windows。パスは文字列でなく Path で比べ、区切りの / と \ の違いで落ちないように）。
- 試験は一時ディレクトリの偽の設定・記録・指示と、proc.run と resolve_cli の差し替え（tests/test_director_launch.py の形）だけで書く。課題の名前や中身を推して書かない。

足す試験（少なくとも）：
1. 関所：指示が無い・記録が無い・読む根が oracles の下・読む根が oracles を含む・model が無い、のそれぞれで起こさない（proc.run が呼ばれない）。
2. 起こす引数：`--max-turns`・`--model`・`--allowedTools`・`--disallowedTools`・読む根ごとの `--add-dir` が入り、作業ディレクトリが ROOT。
3. 出力：result の行から利用量・モデルを読む。modelUsage が無い・result の行が無い・違うモデルで止める。ターンの上限・TTL（124）の終了の理由。トークンの超過。
4. 道の検め：読む根の中だけ（当たり無し）・根の外の Read・`..` で外に出る path・絶対パスの pattern・許していない道具（Bash）の、それぞれ。
5. 提案の形：合う・見出しが欠ける・並びが違う・中身が空・`## 出た段` の段の記録が無い。
6. `python -m harness.model_pin` が proposer の model の欠けを見つける。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる（シェルの状態は命令をまたいで残らないことがある）：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通ることを確かめる。`/tmp/venv/bin/python -m harness.model_pin` も走らせ、終了コードを控える。

## 5. 枝とコミット

- 枝：`claude/proposer-launch`（main から）。main にも、ほかの既存の枝にも push しない。
- コミットは 1〜3 個。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール。model_pin の終了コード
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと（無ければ「無し」）
