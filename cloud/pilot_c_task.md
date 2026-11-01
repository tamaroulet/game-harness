# 頼み：最適化の面（harness/surface.py）（クラウドの 4 本目）

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

- CLAUDE.md、docs/design/direction_and_plan_v3.md の §7-7 と §7-8（周期の形と、最適化の面）
- harness/bench_chain.py（ChainStage）、harness/bench_run.py の Factory.cfg・effective・setup_clone・line_config
- harness/hline_protect.py（パスの照らし方の手本）、harness/hline_prompt.py（実装役への指示を組むコード）
- config/hline.json の implementer.budget と max_attempts

背景：外側の周期では、提案役が選んだ改善 1 つを、決まった「面」の中だけで入れる。面は 2 つ（v3 §7-8）。1 つ目は実装役への指示と、それを組むコード。2 つ目は面の設定のファイル 1 つ（つまみの値）。範囲と面の外枠は走らせ役のコードの側に置き、周期からは変えられないようにする。この頼みは、その外枠・つまみの当て方・差分の検査を作る。面に何を入れるかの最後の決めは、操縦士が L6 の前に 1 回承認する（この頼みでは決めない）。

## 2. 作るもの

### (1) 外枠：harness/surface.py（新しいモジュール。外枠はコードの中の定数）

- `FRAME`：面のパス。`harness/hline_prompt.py` と `config/surface.json` の 2 つだけ。harness/surface.py 自身は面の外。
- `KNOBS`：つまみの名前 → (config/hline.json の中の場所, 型, 最小, 最大)。次の 3 つだけ。
  - `implementer_max_turns` → implementer.budget.max_turns、int、5〜40
  - `implementer_resume_max_turns` → implementer.budget.resume_max_turns、int、3〜30
  - `max_attempts` → max_attempts、int、1〜4
- 型の照らしで、bool を int と見ない（Python では True が int に当たる）。

### (2) 面の設定：config/surface.json（新しいファイル）

- 3 つのつまみを、今の config/hline.json と同じ値で書く（15・8・2）。だから、入れた直後の走りの中身は変わらない。`_` で始まるキー（`_comment` など）は読み飛ばす。

### (3) 当て方：鎖の段の走りにだけ効かせる

- `surface.apply(cfg, path)`：cfg（hline の設定の dict）の写しに、面の設定の値を当てて返す（元の dict は変えない）。面の設定が無い・JSON として読めない・知らないキー・型の違い・範囲の外なら、止める（bench_start.BenchError を上げる。run_stage がそれを受けて ABORT にするので、走りは始まらない）。
- bench_chain.py の ChainStage で、Factory.effective を上書きし、`super().effective(cfg)` に apply を掛ける。走りの記録（self.run）に、当てた値と面の設定の sha256 を残す。ChainStage のほかの所と、bench_run.py は変えない。H ラインの常駐の走り・bench_run の factory（鎖でない走り）には効かせない。

### (4) 差分の検査

`python -m harness.surface check --repo <clone> --old <rev> --new <rev> [--chains <dir> --bases <B,...>]`

- 面の外：`git diff --name-status <old> <new>` のパスのうち、FRAME に無いもの。改名は、元の名と新しい名の両方を照らす。config/surface.json を消すのも当たり。
- 面の設定：<new> の config/surface.json を (3) と同じ決まりで照らし、合わなければ当たり。
- 漏れ：`--chains` と `--bases` があれば、`<chains>/<土台>/s*/hidden/*.py` の試験の関数の名（test_ で始まるもの）とクラスの名（6 字以上のもの）（ast で読む）、package の名（bench_chain stage と同じ読み方で）、土台の名を集め、どれも語の境で照らして、差分の足した行に出てくれば当たり（Meta-Harness の、課題に特有の文字列の漏れの検め）。漏れの当たりには、どの文字列かを出さず、ファイルと行の番号だけを出す（この出力は公開の記録に残りうる）。`--chains` が無ければ漏れの検めを飛ばし、そう 1 行出す。
- 当たりが無ければ 0、あれば 1、引数や git の誤りは 2。

## 3. 守ること

- 変えてよいのは、harness/surface.py（新）、config/surface.json（新）、harness/bench_chain.py の ChainStage（effective の上書きと記録の数行だけ）、新しいモジュールを登録するための config/arch_components.json と config/envscan_baseline.json（test_arch_inventory・test_envscan_ratchet が求める分だけ）、tests/ の下の新しい試験のファイルだけ。bench_run.py・chain_score.py・bench_pair.py・hline*.py・config/hline.json・config/protected_paths.json・既存の試験・docs/・bench/・.github/・.claude/ は変えない。
- 頼みに無い直しはしない。決めかねたら推して決めず、6 の 6 に書く。とくに、hline_prompt.py が読む指示の雛形のファイル（あれば）を面に入れるべきかは、決めずに 6 の 6 に書く（保護パスにある雛形なら、その旨も）。「役の有無の切り替え」（v3 §7-8）を今の設定で表せるかも、決めずに 6 の 6 に書く。
- 規模：harness/ の追加は 300 行まで（試験は数えない）、モジュールは 500 行まで、関数の循環的複雑度は 15 まで。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリと requirements.lock にあるものだけ）。
- 試験は Windows でも通る形で書く（手元は Windows。パスは Path で比べ、git のパスは / で比べる）。一時ディレクトリの偽のリポジトリと偽の鎖の置き場だけで書く。課題の名前や中身を推して書かない。

足す試験（少なくとも。意地悪な場合を含める）：
1. apply：値が当たる・元の dict が変わらない・ファイルが無い・壊れた JSON・知らないキー・True を int に渡す・範囲の端（最小と最大は通り、その外は止まる）・`_comment` は読み飛ばす。
2. ChainStage：effective に面が当たり、記録に値と sha256 が残る。bench_run.Factory（鎖でない走り）には当たらない。
3. check の面の外：FRAME の中だけ（0）・外のファイル（1）・FRAME から外への改名と外から FRAME への改名（どちらも 1）・config/surface.json の削除（1）・harness/surface.py を変える（1）。
4. check の面の設定：範囲の外の値を入れた差分で 1。
5. check の漏れ：隠しテストの関数の名を足した行で 1、出力にその名が出ない。5 字以下のクラスの名（例：T）は集めない。消した行に出るだけなら 0。土台の名が別の語の一部（例：B070）なら当たらない。`--chains` 無しで飛ばす旨の 1 行。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通ることを確かめる。

## 5. 枝とコミット

- 枝：`claude/optimization-surface`（main から）。main にも、ほかの既存の枝にも push しない。この枝に push できなければ、今いる枝に push し、6 の 5 にそう書く。
- コミットは 1〜3 個。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を、message の id の重なりを除いて足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと・読んで気づいた危うさ（無ければ「無し」）
