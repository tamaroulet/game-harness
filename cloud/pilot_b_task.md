# 頼み：対の走り（harness/bench_pair.py）（クラウドの 3 本目）

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
- harness/bench_chain.py（全部。とくに run_step・run_chain・s0_commit・fetch_tip・finish の流れ）
- harness/chain_score.py の finish_chain・finish_stage・grade・hidden_files・replaced・tip_tree・finish
- harness/bench_run.py の version_problem・run_dir・read_json
- 試験の手本：tests/test_bench_chain.py

背景：外側の周期では、ハーネスの改善 1 つ（新版）を、改善の前（旧版）と比べる。比べる段は、基準の走り（例：tag v0-r1）で狙いの失敗が出た段。どちらの版も、**基準の走りの段 k−1 の先端**から段 k だけを走らせる（段 1 なら段 0 の先端）。同じ ID のままモデルの中身が変わるのを相殺するため、旧版と新版を続けて走らせ、組ごとに先に走らせる版を入れ替える。

決まり（今のコードにある）：走らせ役のコードの harness/ の木と、走らせる版（--base）の harness/ の木が同じでなければ、走りは止まる（version_problem）。だから、1 つのプロセスで 2 つの版は走らせられない。版ごとのクローン（それぞれの版を checkout した作業コピー）で、その版のコードを子のプロセスとして起こす。

## 2. 作るもの：harness/bench_pair.py（新しいモジュール 1 つ）

### (1) run：基準の先端から段を 1 つずつ走らせる（bench のアカウント。版ごとのクローンで走る）

`python -m harness.bench_pair run --tag <t> --repo <clone> --base <rev> --staged <dir> --from <基準の tag> --stages <土台:段,...> [--out <dir>]`

- 基準の記録は `<out>/chains/<基準の tag>/<土台>.json`（bench_chain run が書く形）。段 k−1 の先端は、その記録の段 k−1 の試しのうち、最後の「無効でなく head のある」もの（finish_chain の last と同じ選び方）の走りの束から取る（bench_chain.fetch_tip）。段 1 なら bench_chain.s0_commit。
- 始める前に、bench_chain run と同じく version_problem（走らせ役のコードと --base の harness/ の木が同じか）を確かめ、違えば 2。
- `--staged` の chain.json の tag は、`--from`（基準の tag）と同じであること（材料は基準の走りのものを使う。`--tag` とは比べない）。
- 引数は bench_chain run に合わせる（--implementer など、run_step が使うものも受ける）。
- 段 k は bench_chain.run_step で走らせる（走り直し・中断の後の残りの扱いはそのまま）。記録は `<out>/chains/<t>/<土台>.json` に、`from`（基準の tag）・`set`（基準の記録の set）・`pairs`（この走りで頼まれた段の列）を足した、bench_chain run と同じ形。段ごとに 1 つずつ、終わるたびに書く（途中で止まっても、済んだ段が残る）。
- 同じ土台の段を 2 つ以上頼まれても、どれも基準の先端から走らせる（つながない）。
- 基準の記録が無い・段 k−1 に使える試しが無い（止まった・無効だけ）：その段を走らせず、記録に理由を書いて、ほかの段は続ける。
- `--stages` の形が違う（土台が chain.json に無い・段が 1 未満か段の数を超える・重なり）：何も走らせず終了コード 2。

### (2) pair：旧版と新版を交互に起こす（bench のアカウント）

`python -m harness.bench_pair pair --tag <p> --old <clone> --old-rev <rev> --new <clone> --new-rev <rev> --staged <dir> --from <基準の tag> --stages <土台:段,...> --out <dir> [--allow-same]`

- 組（土台:段）ごとに、旧版と新版の run を 1 段ずつ、子のプロセスで起こす。子は、その版のクローンを作業ディレクトリにした `<python> -m harness.bench_pair run --tag <p>-old|<p>-new --repo <その版の clone> --base <その版の rev> ...`。`--out` は両方とも同じ（基準の記録と束を読むため）。pair が受けた run 向けの引数（--implementer など）は、両方の子にそのまま渡す。2 つの子を同時に走らせない（H ラインの置き場を共にするので、必ず 1 つずつ）。
- 先に走らせる版は、組の並びの偶数番目は旧版、奇数番目は新版。並びと、起こした時刻・終了コードを `<out>/pairs/<p>.json` に書く（終わるたびに書く）。
- 2 つの版の harness/ の木が同じなら、`--allow-same` が無い限り何も走らせず 2（同じ版どうしの走りは、揺れの測りにだけ使う）。
- 子が異常で終わっても、残りの組は続ける。最後の終了コードは、全部 0 なら 0、ほかは 1。
- 子のプロセスの時間の上限は掛けない（段ごとの上限は走らせ役の側にある）。

### (3) finish：採点（操縦士のアカウント）

`python -m harness.bench_pair finish --tag <t> --repo <clone> [--chains <dir>] [--out <dir>]`

- `from` のある記録だけを採点する。段ごとに chain_score.finish_stage を呼ぶ。そのとき、保ちの起点（prev）は、その段の開始のコミットの木を、基準の finish が段 k−1 で使ったのと同じ組（段 1 なら finish_chain の段 0 と同じ組、段 2 以上なら hidden_files(chains, base, k−1) と replaced(chains, base, k−1)）で採点した結果にする（開始のコミットは、基準の段 k−1 の先端に仕様と台帳を足しただけなので、試験に関わる中身は同じ）。開始のコミットは走りの束から取る（chain_score.tip_tree の rev）。
- 試験ごとの結果と索引の置き場、公開の集計の置き場（`<clone>/bench/records/chains/<t>/<土台>.json`）は、bench_chain finish と同じ決まり（set が dev なら H ラインの out、ほかは oracles の下）。公開の集計に `from` を足す。

### (4) compare：比べ

`python -m harness.bench_pair compare --tag <p> --repo <clone>`

- `<clone>/bench/records/chains/<p>-old/` と `<p>-new/` の公開の集計を読み、組ごとに、両方の版の new・old・kept・new_at_start・invalid・reason・tokens・tries を並べる。
- 組ごとの「通った」：無効でなく、new が全部通り、kept が全部通る。新版だけが通った組を「直した」、旧版だけが通った組を「壊した」と数える。片方の記録が無い組は「欠け」で、どちらにも数えない。
- 結果を `<clone>/bench/records/pairs/<p>.json` に書き、標準出力に組ごとの 1 行と、最後に「直した N・壊した M・欠け L」の 1 行を出す。

## 3. 守ること

- 変えてよいのは、harness/bench_pair.py（新）、新しいモジュールを登録するための config/arch_components.json と config/envscan_baseline.json（test_arch_inventory・test_envscan_ratchet が求める分だけ）、tests/ の下の新しい試験のファイルだけ。harness/bench_chain.py・harness/chain_score.py・harness/bench_run.py・harness/hline*.py・config/hline.json・既存の試験・docs/・bench/・.github/・.claude/ は変えない。要る関数は import して使う。使いたい関数が import で使えない形なら、写さず、6 の 6 に書く。
- 頼みに無い直し（ついでの整理・名前の変更）はしない。決めかねたら推して決めず、6 の 6 に書く。
- 規模：harness/ の追加は 300 行まで（試験は数えない）、モジュールは 500 行まで、関数の循環的複雑度は 15 まで。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリと requirements.lock にあるものだけ）。
- 試験は Windows でも通る形で書く（手元は Windows。パスは文字列でなく Path で比べ、区切りの / と \ の違いで落ちないように。子のプロセスは sys.executable で起こす）。
- 試験は一時ディレクトリの偽のクローン・束・記録だけで書く。工廠（実装役）は走らせない（run_step と子のプロセスは差し替える）。課題の名前や中身を推して書かない。

足す試験（少なくとも。それぞれ、意地悪な場合を含める）：
1. run：chain.json の tag が `--from` と違えば 2。版が違えば 2。段 k−1 の先端の選び方（同じ段に走り直しが 2 つあり、最後のものが無効・その前が有効、のとき前のものを使う）。段 1 は段 0 の先端。同じ土台の段 2 と段 4 を頼むと、どちらも基準の先端から（段 4 は段 2 の結果につながない）。基準の段 k−1 が無い段は理由つきで飛ばし、ほかは走る。`--stages` の形の誤り（無い土台・段 0・段の数を超える・重なり）で 2。
2. pair：先に走らせる版が組ごとに入れ替わる。子に渡す引数（tag の -old・-new、版ごとの repo と rev、同じ out）。同じ harness/ の木で 2、`--allow-same` で走る。子の 1 つが落ちても残りを続け、最後は 1。
3. finish：保ちの起点が、記録の前の段の結果ではなく、開始のコミットの木での段 0〜k−1 の採点になる（記録の前の段の結果を使うと値が変わる偽の束で確かめる）。from の無い記録は採点しない。
4. compare：直した・壊した・欠けの数え方（無効は通らない・kept の欠けは通らない・片方だけの組は欠け）。

段の上限（STAGE_TOKENS）を poll の中でも効かせる直しは、この頼みに入れない（常駐ワーカーの区分なので別の頼み）。読んで気づいたことがあれば、6 の 6 に書く。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通ることを確かめる。

## 5. 枝とコミット

- 枝：`claude/bench-pair`（main から）。main にも、ほかの既存の枝にも push しない。この枝に push できなければ、今いる枝に push し、6 の 5 にそう書く。
- コミットは 1〜3 個。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を、message の id の重なりを除いて足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと・読んで気づいた危うさ（無ければ「無し」）
